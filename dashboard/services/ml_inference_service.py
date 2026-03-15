import boto3
import tempfile
import os
import pickle
import numpy as np
import torch
import torch.nn.functional as F
from django.conf import settings
import logging
from sklearn.preprocessing import LabelEncoder
import joblib
import json
import importlib.util
from langdetect import detect, DetectorFactory, LangDetectException
import pandas as pd
import time
import botocore
from dashboard.services.tone_ensemble import ProbabilitiesEstimator
from pathlib import Path
import re

TransferConfig = None
DetectorFactory.seed = 0

logger = logging.getLogger(__name__)

class MLInferenceService:
    def __init__(self):
        print("MLInferenceService.__init__ called")
        # Only initialize S3 client if AWS credentials are available
        # Check Django settings first, then fall back to environment variables
        aws_key = getattr(settings, 'AWS_ACCESS_KEY_ID', None) or os.environ.get('AWS_ACCESS_KEY_ID')
        aws_secret = getattr(settings, 'AWS_SECRET_ACCESS_KEY', None) or os.environ.get('AWS_SECRET_ACCESS_KEY')
        aws_bucket = getattr(settings, 'S3_MODELS_BUCKET', None) or os.environ.get('S3_MODELS_BUCKET')
        aws_region = getattr(settings, 'AWS_S3_REGION_NAME', None) or os.environ.get('AWS_S3_REGION_NAME', 'eu-west-1')
        
        if aws_key and aws_secret and aws_bucket:
            self.s3_client = boto3.client(
                's3',
                aws_access_key_id=aws_key,
                aws_secret_access_key=aws_secret,
                region_name=aws_region,
                config=botocore.config.Config(
                    retries={
                        'max_attempts': 10,
                        'mode': 'adaptive'
                    },
                    connect_timeout=60,
                    read_timeout=300
                )
            )
            self.bucket_name = aws_bucket
        else:
            self.s3_client = None
            self.bucket_name = None
        
        self._model_cache = {}  # Cache loaded models
        self._temp_dirs = set()  # Track temp directories
        self._strategic_label_encoder = None
        self._tone_label_encoder = None
        self._strategic_vocab = None

        # ✅ Add persistent cache directory (this is the old one, might become secondary)
        self.model_cache_dir = Path(settings.BASE_DIR) / 'model_cache'
        self.model_cache_dir.mkdir(exist_ok=True)

        # ✅ NEW: Add the specific directory where the archive was extracted
        self.local_models_dir = Path("/Users/hannateshager/Vulnerability_index_tool/model_cache")
        
        # Load CSV risk data once at initialization
        self._csv_risk_df = self._load_csv_risks()

    def _load_csv_risks(self):
        """Load pre-calculated risk scores using an absolute path from settings.BASE_DIR"""
        try:
            # This ensures we look in the project root, no matter where the script is called from
            csv_filename = 'final_risk_by_actor_intent_country.csv'
            path = os.path.join(settings.BASE_DIR, csv_filename)
            
            if os.path.exists(path):
                df = pd.read_csv(path)
                logger.info(f"✅ Successfully loaded CSV risk data from absolute path: {path}")
                return df
            
            # Fallback check: try one level up from BASE_DIR if BASE_DIR is pointing to 'config'
            fallback_path = os.path.join(os.path.dirname(settings.BASE_DIR), csv_filename)
            if os.path.exists(fallback_path):
                df = pd.read_csv(fallback_path)
                logger.info(f"✅ from fallback path: {fallback_path}")
                return df

            logger.warning(f"⚠️ CSV risk file not found. Looked in: {path} and {fallback_path}")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"❌ Failed to load CSV risk data: {e}")
            return pd.DataFrame()

    def _download_from_s3(self, s3_key, local_path):
        """Download single file from S3"""
        if not self.s3_client:
            raise Exception("AWS S3 credentials not configured")
        
        try:
            self.s3_client.download_file(self.bucket_name, s3_key, local_path)
            return True
        except Exception as e:
            logger.error(f"Error downloading {s3_key}: {e}")
            return False

    def _download_directory_from_s3(self, s3_prefix, local_dir, max_retries=5):
        """Download entire directory from S3 to local directory with retry logic"""
        print(f"\n🔍 === DOWNLOAD DEBUG ===")
        print(f"📍 Prefix: {s3_prefix}")
        print(f"📁 Local dir: {local_dir}")
        print(f"🪣 Bucket: {self.bucket_name}")
        
        if not self.s3_client:
            print("❌ S3 client is None!")
            raise Exception("AWS S3 credentials not configured")
        
        try:
            print(f"📂 Listing objects with prefix: '{s3_prefix}'")
            response = self.s3_client.list_objects_v2(Bucket=self.bucket_name, Prefix=s3_prefix)
            
            if 'Contents' not in response:
                print(f"❌ No files found with prefix: {s3_prefix}")
                return False
            
            print(f"✅ Found {len(response['Contents'])} files")
            
            for obj in response.get('Contents', []):
                key = obj['Key']
                if key.endswith('/'):
                    continue
                
                rel_path = key[len(s3_prefix):].lstrip('/')
                local_file_path = os.path.join(local_dir, rel_path)
                
                os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
                print(f"⬇️ Downloading: {key} -> {local_file_path}")
                
                # Retry logic for each file
                for attempt in range(max_retries):
                    try:
                        # Use default download (no TransferConfig)
                        self.s3_client.download_file(self.bucket_name, key, local_file_path)
                        print(f"✅ Downloaded: {key}")
                        break
                    except Exception as e:
                        if attempt < max_retries - 1:
                            print(f"⚠️ Retry {attempt + 1}/{max_retries} for {key}: {e}")
                            time.sleep(2 ** attempt)
                        else:
                            print(f"❌ Failed to download {key} after {max_retries} retries")
                            raise
            
            print(f"✅ Successfully downloaded all files")
            return True
        except Exception as e:
            print(f"❌ Error downloading directory {s3_prefix}: {e}")
            import traceback
            traceback.print_exc()
            return False
            
    def _load_from_persistent_cache(self, model_type):
        """Load model from persistent cache directory (OLD CACHE PATH)"""
        cache_path = self.model_cache_dir / f'{model_type}_model'
        if cache_path.exists():
            print(f"✅ Loading {model_type} model from OLD persistent cache: {cache_path}")
            
            if model_type == 'strategic':
                # Load strategic classifier from cache
                from dashboard.services.calibrated_ensemble import CalibratedStrategicClassifier
                classifier = CalibratedStrategicClassifier.load(str(cache_path))
                self._model_cache['strategic'] = classifier
                
                # Load label encoder
                label_enc_path = cache_path / 'label_encoder.pkl'
                if label_enc_path.exists():
                    with open(label_enc_path, 'rb') as f:
                        self._strategic_label_encoder = pickle.load(f)
                
                print(f"✅ Strategic classifier loaded from OLD cache!")
                return True
            
            elif model_type == 'tone':
                # Load tone classifier from cache
                from dashboard.services.tone_ensemble import CalibratedStackedEnsemble
                classifier = CalibratedStackedEnsemble.load(str(cache_path))
                self._model_cache['tone'] = classifier
                
                # Load label encoder
                label_enc_path = cache_path / 'label_info.json'
                if label_enc_path.exists():
                    with open(label_enc_path, 'r') as f:
                        label_info = json.load(f)
                    from sklearn.preprocessing import LabelEncoder
                    self._tone_label_encoder = LabelEncoder()
                    self._tone_label_encoder.classes_ = np.array(label_info['classes'])
                
                print(f"✅ Tone classifier loaded from OLD cache!")
                return True
        
        return False

    def _load_from_local_archive_cache(self, model_type):
        """Load model from the specific archive cache directory (NEW CACHE PATH)"""
        # Define the expected path based on model type within the archive cache
        if model_type == 'strategic':
            # The strategic model components (adapters, label encoder) should be in:
            # /home/ubuntu/Vulnerability_index_tool/app/models/model_cache/strategic_model/
            archive_model_path = self.local_models_dir / 'strategic_model'
        elif model_type == 'tone':
            # The tone model components should be in:
            # /home/ubuntu/Vulnerability_index_tool/app/models/model_cache/tone_model/
            archive_model_path = self.local_models_dir / 'tone_model'
        else:
            print(f"⚠️ Unsupported model type for archive cache: {model_type}")
            return False # Unsupported model type for this cache
    
        if archive_model_path.exists():
            print(f"✅ Loading {model_type} model from ARCHIVE cache: {archive_model_path}")
            
            try:
                if model_type == 'strategic':
                    # Load strategic classifier from the archive path
                    from dashboard.services.calibrated_ensemble import CalibratedStrategicClassifier
                    classifier = CalibratedStrategicClassifier.load(str(archive_model_path))
                    self._model_cache['strategic'] = classifier
                    
                    # Load label encoder - assume it's in the archive model path
                    label_enc_path = archive_model_path / 'label_encoder.pkl'
                    if label_enc_path.exists():
                        with open(label_enc_path, 'rb') as f:
                            self._strategic_label_encoder = pickle.load(f)
                    
                    print(f"✅ Strategic classifier loaded from ARCHIVE cache!")
                    return True
                
                elif model_type == 'tone':
                    # Load tone classifier from the archive path
                    from dashboard.services.tone_ensemble import CalibratedStackedEnsemble
                    classifier = CalibratedStackedEnsemble.load(str(archive_model_path))
                    self._model_cache['tone'] = classifier
                    
                    # Load label encoder info - assume it's in the archive model path
                    label_enc_path = archive_model_path / 'label_info.json'
                    if label_enc_path.exists():
                        with open(label_enc_path, 'r') as f:
                            label_info = json.load(f)
                        from sklearn.preprocessing import LabelEncoder
                        self._tone_label_encoder = LabelEncoder()
                        self._tone_label_encoder.classes_ = np.array(label_info['classes'])
                    
                    print(f"✅ Tone classifier loaded from ARCHIVE cache!")
                    return True
            except Exception as e:
                print(f"❌ Error loading {model_type} model from ARCHIVE cache: {e}")
                import traceback
                traceback.print_exc()
                return False
        else:
            print(f"⚠️ Archive cache path does not exist: {archive_model_path}")
        
        return False # Model not found in archive cache
        
    def _get_llm_strategic_intent(self, text: str):
        """
        Calls the LLM (Groq) to predict strategic intent for a single text.

        Args:
            text (str): The article text.

        Returns:
            tuple: (predicted_intent: str, confidence: float, notes: str or None)
                   Returns (None, 0.0, error_message) on failure.
        """
        groq_api_key = getattr(settings, 'GROQ_API_KEY', '')
        if not groq_api_key:
            logger.error("GROQ_API_KEY not found in settings.")
            return None, 0.0, "API key not configured"

        try:
            client = Groq(api_key=groq_api_key)
            system_message = {
                "role": "system",
                "content": (
                    "Analyze the provided text to identify the primary strategic intent "
                    "related to foreign influence on the target country mentioned in the text. "
                    "Label options include: Economic, Sovereignty, LGBTQ, Religious, "
                    "ElectionInfluence, MilitaryPresence, ResourceDependency, SocialFragility. "
                    "Respond ONLY with a JSON object containing the keys: "
                    "'strategic_intent' (the label), 'strategic_intent_conf' (a float between 0 and 1 "
                    "indicating your confidence in this label), and 'notes' (optional brief reasoning). "
                    "If the text does not clearly relate to any of these intents, label it 'Neutral'."
                )
            }
            user_message = {"role": "user", "content": text[:4000]} # Limit text length

            GROQ_MODEL = getattr(settings, 'GROQ_MODEL', 'llama3-8b-8192') # Use a default or configurable model
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[system_message, user_message],
                temperature=0.0 # Low temperature for consistency
            )

            text_response = response.choices[0].message.content.strip()
            # Extract JSON from the response (in case it includes other text)
            json_match = re.search(r'\{.*\}', text_response, re.DOTALL)
            if not json_match:
                logger.warning(f"Could not extract JSON from LLM response: {text_response}")
                return None, 0.0, "Invalid JSON response from LLM"

            js = json.loads(json_match.group(0))

            intent = js.get('strategic_intent')
            conf = js.get('strategic_intent_conf')
            notes = js.get('notes')

            # Validate confidence
            if not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0):
                 logger.warning(f"Invalid confidence from LLM: {conf}. Defaulting to 0.0.")
                 conf = 0.0

            return intent, float(conf), notes

        except json.JSONDecodeError as je:
            logger.error(f"JSON decode error from LLM: {je}, Response: {text_response}")
            return None, 0.0, f"JSON decode error: {str(je)}"
        except Exception as e:
            logger.error(f"Error calling LLM for text: {str(e)[:100]}...") # Log first 100 chars of error
            return None, 0.0, f"LLM API error: {str(e)}"

    # Modify the main strategic intent method
    def perform_strategic_intent_inference(self, article_text):
        """
        Performs strategic intent inference using both the calibrated model and LLM,
        compares results, and returns the final intent, confidence, and source.

        Args:
            article_text (str): The article text.

        Returns:
            tuple: (final_intent: str, final_confidence: float, prediction_source: str)
        """
        # Get model prediction using existing logic
        # Call the underlying prediction method that returns pred_label and confidence
        # This adapts the current logic inside the existing perform_strategic_intent_inference
        # to return (pred_label, confidence).
        try:
            classifier = self._load_strategic_classifier() # Load the model (uses caching)
            if classifier is None:
                logger.error("Failed to load strategic classifier.")
                return "unknown", 0.0, "error_loading_model"

            # Prepare input
            inputs = self.tokenizer(
                article_text,
                truncation=True,
                padding=True,
                max_length=512,
                return_tensors="pt"
            ).to(classifier.device)

            # Perform prediction
            with torch.no_grad():
                outputs = classifier(**inputs)
                logits = outputs.logits
                probabilities = torch.softmax(logits, dim=-1)
                predicted_class_id = torch.argmax(probabilities, dim=-1).item()
                model_confidence = torch.max(probabilities).item() # Max probability as confidence

            # Decode label
            try:
                model_intent = classifier.label_encoder.inverse_transform([predicted_class_id])[0]
            except (IndexError, AttributeError) as e:
                logger.error(f"Error decoding model prediction: {e}")
                model_intent = "unknown"

        except Exception as e_model:
            logger.error(f"Error during model inference: {e_model}")
            model_intent = "unknown"
            model_confidence = 0.0

        logger.info(f"Model Prediction: {model_intent}, Confidence: {model_confidence}")

        # Get LLM prediction
        llm_intent, llm_confidence, llm_notes = self._get_llm_strategic_intent(article_text)
        logger.info(f"LLM Prediction: {llm_intent}, Confidence: {llm_confidence}, Notes: {llm_notes}")

        # --- COMPARISON AND DECISION LOGIC ---
        final_intent = None
        final_confidence = 0.0
        prediction_source = "unknown" # Default

        # Define a threshold for trusting a match
        CONFIDENCE_THRESHOLD_FOR_MATCH = 0.6 # Example: Require at least 0.6 confidence even for a match

        if model_intent and llm_intent:
            # Both predictions available
            if model_intent.lower() == llm_intent.lower(): # Case-insensitive match check
                max_conf_of_match = max(model_confidence, llm_confidence)
                if max_conf_of_match >= CONFIDENCE_THRESHOLD_FOR_MATCH:
                    # Predictions match AND confidence is sufficiently high
                    final_intent = model_intent # Or llm_intent, they are the same semantically
                    final_confidence = max_conf_of_match
                    prediction_source = "ensemble_matched_confirmed" # Different source to indicate threshold met
                    logger.info(f"Model and LLM predictions MATCHED with sufficient confidence (>={CONFIDENCE_THRESHOLD_FOR_MATCH}). Final Confidence: {final_confidence}")
                else:
                    # Predictions match BUT confidence is low - treat as disagreement, choose higher confidence
                    # This prevents trusting low-confidence agreements
                    if model_confidence >= llm_confidence:
                        final_intent = model_intent
                        final_confidence = model_confidence
                        prediction_source = "model_selected_after_low_match"
                        logger.info(f"Model and LLM predictions MATCHED but confidence was low (< {CONFIDENCE_THRESHOLD_FOR_MATCH}). Choosing Model based on higher confidence ({model_confidence}).")
                    else:
                        final_intent = llm_intent
                        final_confidence = llm_confidence
                        prediction_source = "llm_selected_after_low_match"
                        logger.info(f"Model and LLM predictions MATCHED but confidence was low (< {CONFIDENCE_THRESHOLD_FOR_MATCH}). Choosing LLM based on higher confidence ({llm_confidence}).")
            else:
                # Predictions differ - choose based on confidence
                if model_confidence >= llm_confidence:
                    final_intent = model_intent
                    final_confidence = model_confidence
                    prediction_source = "model"
                    logger.info("Model and LLM predictions DIFFER. Model confidence higher, using MODEL prediction.")
                else:
                    final_intent = llm_intent
                    final_confidence = llm_confidence
                    prediction_source = "llm"
                    logger.info("Model and LLM predictions DIFFER. LLM confidence higher, using LLM prediction.")
        elif model_intent:
            # Only model prediction available
            final_intent = model_intent
            final_confidence = model_confidence
            prediction_source = "model_only"
            logger.info("Only model prediction available, using MODEL prediction.")
        elif llm_intent:
            # Only LLM prediction available
            final_intent = llm_intent
            final_confidence = llm_confidence
            prediction_source = "llm_only"
            logger.info("Only LLM prediction available, using LLM prediction.")
        else:
            # Neither prediction worked
            final_intent = "unknown"
            final_confidence = 0.0
            prediction_source = "error"
            logger.error("Both model and LLM failed to predict strategic intent.")

        logger.info(f"Final Decision: Intent={final_intent}, Confidence={final_confidence}, Source={prediction_source}")
        return final_intent, final_confidence, prediction_source
        
    def _save_to_persistent_cache(self, model_type, model, label_encoder=None):
        """Save model to persistent cache directory (OLD CACHE PATH)"""
        cache_path = self.model_cache_dir / f'{model_type}_model'
        cache_path.mkdir(parents=True, exist_ok=True)
        
        if model_type == 'strategic':
            # Save classifier to cache
            model.save(str(cache_path))
            
            # Save label encoder
            if self._strategic_label_encoder:
                label_enc_path = cache_path / 'label_encoder.pkl'
                with open(label_enc_path, 'wb') as f:
                    pickle.dump(self._strategic_label_encoder, f)
            
            print(f"✅ Saved strategic model to OLD persistent cache: {cache_path}")
        
        elif model_type == 'tone':
            # Save classifier to cache
            model.save(str(cache_path))
            
            # Save label encoder info
            if self._tone_label_encoder:
                label_info = {
                    'classes': self._tone_label_encoder.classes_.tolist()
                }
                label_enc_path = cache_path / 'label_info.json'
                with open(label_enc_path, 'w') as f:
                    json.dump(label_info, f)
            
            print(f"✅ Saved tone model to OLD persistent cache: {cache_path}")
    
    def _load_strategic_classifier(self):
        """Load calibrated strategic classifier from caches or S3"""
        print(f"_load_strategic_classifier called. Cache keys: {list(self._model_cache.keys())}") 
        
        # ✅ CRITICAL: Check in-memory cache FIRST
        if 'strategic' in self._model_cache:
            print("   ✅ Found strategic model in memory cache, returning.")
            return self._model_cache['strategic']
    
        # ✅ NEW: Check ARCHIVE cache FIRST (Priority 1) - This path should contain BOTH base model and adapters
        # Expected path: /home/ubuntu/Vulnerability_index_tool/app/models/model_cache/strategic_model/
        archive_strategic_model_path = self.local_models_dir / 'strategic_model'
        if archive_strategic_model_path.exists():
            print(f"✅ Loading strategic model from ARCHIVE cache: {archive_strategic_model_path}")
            try:
                from dashboard.services.calibrated_ensemble import CalibratedStrategicClassifier
                # Attempt to load the classifier directly from the archive path
                # This assumes the directory contains the base model (e.g., microsoft_mdeberta-v3-base)
                # and the adapter components (e.g., ensemble_model_0, label_encoder.pkl) within it.
                classifier = CalibratedStrategicClassifier.load(str(archive_strategic_model_path))
                
                self._model_cache['strategic'] = classifier
                
                # Load label encoder
                label_enc_path = archive_strategic_model_path / 'label_encoder.pkl'
                if label_enc_path.exists():
                    with open(label_enc_path, 'rb') as f:
                        self._strategic_label_encoder = pickle.load(f)
                
                print(f"✅ Strategic classifier loaded from ARCHIVE cache!")
                # ✅ Save to OLD persistent cache (as a fallback for future runs if archive cache is moved)
                self._save_to_persistent_cache('strategic', classifier)
                return classifier
            except Exception as e:
                print(f"❌ Error loading strategic classifier from ARCHIVE cache: {e}")
                import traceback
                traceback.print_exc()
                print("⚠️ Falling back to other cache mechanisms or S3 download.")
                # Continue to other cache mechanisms or S3 download if this fails
                # Do not return None here yet, let other methods try.
                # logger.warning("Strategic model from archive cache failed, trying other methods...") # Optional log
    
        # ✅ Check OLD persistent cache NEXT (Priority 2)
        if self._load_from_persistent_cache('strategic'):
            print("   ✅ Found strategic model in OLD persistent cache, returning.")
            return self._model_cache['strategic']
        
        # If neither local cache exists, proceed with S3 download as fallback (Priority 3)
        # This involves downloading base model and adapters separately into a temp directory
        print(f"⚠️  Strategic model not in local caches, attempting S3 download...")
        temp_dir = tempfile.mkdtemp(prefix='strategic_model_')
        self._temp_dirs.add(temp_dir)
    
        print(f"\n🔍 === STRATEGIC MODEL DOWNLOAD ===")
        print(f"📁 Temp dir: {temp_dir}")
    
        # Download the base model from S3 first
        base_model_dir = os.path.join(temp_dir, 'microsoft_mdeberta-v3-base')
        if not os.path.exists(base_model_dir):
            os.makedirs(base_model_dir, exist_ok=True)
            if self.s3_client:
                try:
                    base_prefix = 'microsoft_mdeberta-v3-base/'
                    print(f"📂 Looking for base model in S3: {base_prefix}")
                    pages = self.s3_client.list_objects_v2(Bucket=self.bucket_name, Prefix=base_prefix)
                    contents = pages.get('Contents', [])
                    print(f"   Found {len(contents)} files")
    
                    for obj in contents:
                        key = obj['Key']
                        if key.endswith('/'):
                            continue
                        filename = key.replace(base_prefix, '')
                        local_path = os.path.join(base_model_dir, filename)
                        os.makedirs(os.path.dirname(local_path), exist_ok=True)
    
                        try:
                            self.s3_client.download_file(self.bucket_name, key, local_path)
                            print(f"   Downloaded {key} -> {local_path}")
                        except Exception as e_download_file:
                            print(f"   WARNING: Could not download {key} from S3: {e_download_file}. Skipping.")
                            continue
    
                    print(f"✅ Downloaded base model to {base_model_dir}")
                    print(f"   Files: {os.listdir(base_model_dir)}")
                except Exception as e:
                    print(f"❌ Could not download base model: {e}")
    
        # Check what we have
        print(f"📂 Files in temp_dir: {os.listdir(temp_dir)}")
    
        # Now download the classifier adapters/config
        classifier_prefix = 'calibrated_contrastive_peft/'
        print(f"📂 Downloading classifier components from: {classifier_prefix}")
    
        if not self._download_directory_from_s3(classifier_prefix, temp_dir):
            raise Exception("Failed to download strategic classifier components from S3")
    
        print(f"📂 Files after classifier download: {os.listdir(temp_dir)}")
    
        # Load the classifier with error handling
        try:
            from dashboard.services.calibrated_ensemble import CalibratedStrategicClassifier
            # This load call expects the base model directory and adapter files to be present in temp_dir
            classifier = CalibratedStrategicClassifier.load(temp_dir)
    
            self._model_cache['strategic'] = classifier
    
            # Load label encoder
            label_enc_path = os.path.join(temp_dir, 'label_encoder.pkl')
            if os.path.exists(label_enc_path):
                with open(label_enc_path, 'rb') as f:
                    self._strategic_label_encoder = pickle.load(f)
    
            print(f"✅ Strategic classifier loaded from S3 download!")
            # ✅ Save to OLD persistent cache (as a fallback for future runs)
            self._save_to_persistent_cache('strategic', classifier)
            return classifier
        except Exception as e:
            print(f"❌ Error loading strategic classifier from S3 download: {e}")
            import traceback
            traceback.print_exc()
            logger.warning("Strategic model failed to load from S3, using keyword fallback")
            return None # Return None as a fallback if all loading attempts fail
        
    def _load_tone_classifier(self):
        """Load calibrated tone classifier from caches or S3"""
        print(f"_load_tone_classifier called. Cache keys: {list(self._model_cache.keys())}") 
        # ✅ Check cache first - return immediately if cached
        if 'tone' in self._model_cache:
            print("✅ Using cached tone classifier")
            return self._model_cache['tone']

        # ✅ NEW: Check ARCHIVE cache FIRST (Priority 1) - This path should contain the full tone model ensemble
        # Expected path: /home/ubuntu/Vulnerability_index_tool/app/models/model_cache/tone_model/
        archive_tone_model_path = self.local_models_dir / 'tone_model'
        if archive_tone_model_path.exists():
            print(f"✅ Loading tone classifier from ARCHIVE cache: {archive_tone_model_path}")
            try:
                from dashboard.services.tone_ensemble import CalibratedStackedEnsemble
                # Attempt to load the classifier directly from the archive path
                classifier = CalibratedStackedEnsemble.load(str(archive_tone_model_path))
                self._model_cache['tone'] = classifier

                # Load label encoder info
                label_enc_path = archive_tone_model_path / 'label_info.json'
                if label_enc_path.exists():
                    with open(label_enc_path, 'r') as f:
                        label_info = json.load(f)
                    from sklearn.preprocessing import LabelEncoder
                    self._tone_label_encoder = LabelEncoder()
                    self._tone_label_encoder.classes_ = np.array(label_info['classes'])
                    print(f"✅ Tone classifier loaded with labels from ARCHIVE cache: {label_info['classes']}")

                print(f"✅ SUCCESS: Tone classifier loaded from ARCHIVE cache!")
                # ✅ Save to OLD persistent cache (as a fallback for future runs if archive cache is moved)
                self._save_to_persistent_cache('tone', classifier)
                return classifier
            except Exception as e:
                print(f"❌ Error loading tone classifier from ARCHIVE cache: {e}")
                import traceback
                traceback.print_exc()
                print("⚠️ Falling back to other cache mechanisms or S3 download.")
                # Continue to other cache mechanisms or S3 download if this fails
                # Do not return None here yet, let other methods try.
                # logger.warning("Tone model from archive cache failed, trying other methods...") # Optional log

        # ✅ Check OLD persistent cache NEXT (Priority 2)
        if self._load_from_persistent_cache('tone'):
            print("   Found tone model in OLD persistent cache, returning.")
            return self._model_cache['tone']

        # ✅ Check if tone model exists in the KNOWN LOCAL PATH (Priority 3 - existing check)
        # This path might be redundant if the archive cache is the primary source now,
        # but keeping it for compatibility if this specific path was used previously.
        KNOWN_TONE_MODEL_PATH = "/Users/hannateshager/Vulnerability_index_tool/app/models/model_cache/tone_model"
        if os.path.exists(KNOWN_TONE_MODEL_PATH):
            print(f"✅ Loading tone classifier from KNOWN LOCAL PATH: {KNOWN_TONE_MODEL_PATH}")

            # Load the classifier
            from dashboard.services.tone_ensemble import CalibratedStackedEnsemble
            classifier = CalibratedStackedEnsemble.load(KNOWN_TONE_MODEL_PATH)
            self._model_cache['tone'] = classifier

            # Load label encoder info
            label_enc_path = os.path.join(KNOWN_TONE_MODEL_PATH, 'label_info.json')
            if os.path.exists(label_enc_path):
                with open(label_enc_path, 'r') as f:
                    label_info = json.load(f)
                from sklearn.preprocessing import LabelEncoder
                self._tone_label_encoder = LabelEncoder()
                self._tone_label_encoder.classes_ = np.array(label_info['classes'])
                print(f"✅ Tone classifier loaded with labels: {label_info['classes']}")

            print(f"✅ SUCCESS: Tone classifier loaded from KNOWN LOCAL PATH!")
            # ✅ Save to OLD persistent cache (as a fallback for future runs)
            self._save_to_persistent_cache('tone', classifier)
            return classifier
        else:
            print(f"⚠️  Local tone model not found at {KNOWN_TONE_MODEL_PATH}, attempting S3 download...")

        # If local paths don't exist, proceed with S3 download as fallback (Priority 4)
        temp_dir = tempfile.mkdtemp(prefix='tone_model_')
        self._temp_dirs.add(temp_dir)

        # ONLY use the working prefix - NO LOOP
        tone_prefix = 'calibrated_stacked_ensemble/'

        print(f"\n🔍 === TONE MODEL DOWNLOAD ===")
        print(f"📍 Prefix: {tone_prefix}")
        print(f"📁 Local dir: {temp_dir}")

        try:
            # Download the model
            if self._download_directory_from_s3(tone_prefix, temp_dir):
                print(f"✅ Tone model downloaded successfully!")

                # Check what files we have
                print(f"   Files in temp_dir: {os.listdir(temp_dir)}")

                # Load the classifier
                from dashboard.services.tone_ensemble import CalibratedStackedEnsemble
                classifier = CalibratedStackedEnsemble.load(temp_dir)
                self._model_cache['tone'] = classifier

                # Load label encoder
                label_enc_path = os.path.join(temp_dir, 'label_info.json')
                if os.path.exists(label_enc_path):
                    with open(label_enc_path, 'r') as f:
                        label_info = json.load(f)
                    from sklearn.preprocessing import LabelEncoder
                    self._tone_label_encoder = LabelEncoder()
                    self._tone_label_encoder.classes_ = np.array(label_info['classes'])
                    print(f"✅ Tone classifier loaded with labels: {label_info['classes']}")

                print(f"✅ SUCCESS: Tone classifier ready from S3 download!")
                # ✅ Save to OLD persistent cache (as a fallback for future runs)
                self._save_to_persistent_cache('tone', classifier)
                return classifier

        except Exception as e:
            print(f"❌ Error loading tone classifier from S3 download: {e}")
            import traceback
            traceback.print_exc()

        logger.warning("Could not download tone classifier from S3, using fallback")
        return None # Return None as a fallback if all loading attempts fail
        
    def preprocess_text(self, text):
        """Replicate preprocessing from notebook"""
        if not text or text.strip() == "":
            return ""
        text = str(text).strip()
        if len(text) > 4000:
            text = text[:4000]
        return text

    def detect_language(self, text):
        """Detect language as in notebook"""
        try:
            return detect(text[:200])
        except (LangDetectException, Exception):
            return 'und'
            
    def normalize_intent(intent_str):
        if not isinstance(intent_str, str):
            return None
        # Lowercase, strip, collapse whitespace
        intent_clean = re.sub(r'\s+', ' ', intent_str.strip().lower())
        # Optional: remove common suffixes/prefixes
        intent_clean = re.sub(r'(narrative|strategy|influence|erosion|interference|war)$', '', intent_clean).strip()
        return intent_clean   
        
    def extract_entities_from_content(self, text):
        """
        Extract target country and foreign actor from article content using spaCy NER
        """
        try:
            import spacy
            # Load spaCy model
            try:
                nlp = spacy.load("en_core_web_sm")
            except:
                # Download if not exists
                import subprocess
                subprocess.run(["python", "-m", "spacy", "download", "en_core_web_sm"], check=True)
                nlp = spacy.load("en_core_web_sm")
            
            doc = nlp(text[:5000])  # Limit text length for performance
            
            # Extract GPE (countries/cities)
            gpe_entities = [ent.text for ent in doc.ents if ent.label_ in ['GPE', 'LOC', 'FAC']]
            
            # Extract ORG (organizations)
            org_entities = [ent.text for ent in doc.ents if ent.label_ == 'ORG']
            
            # Extract PERSON
            person_entities = [ent.text for ent in doc.ents if ent.label_ == 'PERSON']
            
            return {
                'countries': gpe_entities,
                'organizations': org_entities,
                'persons': person_entities
            }
        except Exception as e:
            logger.error(f"Error in entity extraction: {e}")
            return {'countries': [], 'organizations': [], 'persons': []}

    def is_low_resource_lang(self, lang_code):
        """Check if language is low resource"""
        lowres_langs = ['ha','yo','am','sw','wo','ig','ff','pt','bm','mg','zu']
        return lang_code in lowres_langs
        
    def get_actor_from_media_outlet(self, media_outlet):
        """Extract foreign actor from media outlet name"""
        if not media_outlet:
            return 'Unknown'
        
        media_outlet_lower = media_outlet.lower()
        
        # Map media outlets to actors
        media_actor_mapping = {
            # France
            'france24': 'France',
            'france 24': 'France',
            'le monde': 'France',
            'lefigaro': 'France',
            'french': 'France',
            'france': 'France',
        
            # China
            'china daily': 'China',
            'xinhua': 'China',
            'cgtn': 'China',
            'china': 'China',
            'chinese': 'China',
            'cctv': 'China',
        
            # USA
            'bbc': 'USA', # Note: BBC is UK, this might be a mistake in the mapping itself, but structurally OK
            'cnn': 'USA',
            'nytimes': 'USA',
            'washington post': 'USA',
            'reuters': 'USA', # Note: Reuters is international, this might be a mistake
            'ap news': 'USA',
            'associated press': 'USA',
            'voa': 'USA',
            'american': 'USA',
            'usa': 'USA',
        
            # Russia
            'rt': 'Russia',
            'sputnik': 'Russia',
            'tass': 'Russia',
            'russia today': 'Russia',
            'russian': 'Russia',
            'moscow times': 'Russia',
        
            # Saudi
            'saudi': 'Saudi',
            'arab news': 'Saudi',
        
            # Turkey
            'turkish': 'Turkey',
            'turkey': 'Turkey',
            'anadolu': 'Turkey',
        
            # UAE
            'uae': 'UAE',
            'emirates': 'UAE',
            'gulf news': 'UAE',
        
            # Israel
            'israeli': 'Israel',
            'israel': 'Israel',
            'times of israel': 'Israel',
        
            # Iran
            'iranian': 'Iran',
            'iran': 'Iran',
            'tehran times': 'Iran',
        
            # Rwanda
            'rwandan': 'Rwanda',
            'rwanda': 'Rwanda',
            'new times': 'Rwanda', # <-- Make sure this line has the comma and the overall dict has the closing '}'
        }            
            # Check for partial matches
        for media_name, actor in media_actor_mapping.items():
            if media_name in media_outlet_lower:
                return actor
        
        return 'Unknown'
        
    def extract_actor_from_content(self, text, organizations=None, persons=None):
        """Extract foreign actor from article content using NER and keywords"""
        try:
            text_lower = text.lower()
            
            # Priority 1: Check organizations from NER
            if organizations:
                for org in organizations:
                    org_lower = org.lower()
                    if 'china' in org_lower or 'chinese' in org_lower:
                        return 'China'
                    if 'russia' in org_lower or 'russian' in org_lower:
                        return 'Russia'
                    if 'france' in org_lower or 'french' in org_lower:
                        return 'France'
                    if 'united states' in org_lower or 'usa' in org_lower or 'american' in org_lower:
                        return 'USA'
                    if 'saudi' in org_lower:
                        return 'Saudi'
                    if 'turkey' in org_lower or 'turkish' in org_lower:
                        return 'Turkey'
                    if 'uae' in org_lower or 'emirates' in org_lower:
                        return 'UAE'
                    if 'israel' in org_lower or 'israeli' in org_lower:
                        return 'Israel'
                    if 'iran' in org_lower or 'iranian' in org_lower:
                        return 'Iran'
                    if 'rwanda' in org_lower or 'rwandan' in org_lower:
                        return 'Rwanda'
            
            # Priority 2: Check persons from NER
            if persons:
                for person in persons:
                    person_lower = person.lower()
                    if 'xi' in person_lower or 'jinping' in person_lower:
                        return 'China'
                    if 'putin' in person_lower:
                        return 'Russia'
                    if 'macron' in person_lower:
                        return 'France'
                    if 'trump' in person_lower or 'biden' in person_lower:
                        return 'USA'
                    if 'erdogan' in person_lower:
                        return 'Turkey'
                    if 'netany' in person_lower:
                        return 'Israel'
                    if 'raisi' in person_lower or 'khamenei' in person_lower:
                        return 'Iran'
                    if 'kagame' in person_lower:
                        return 'Rwanda'
            
            # Priority 3: Keyword search in full text
            actor_keywords = {
                'china': ['china', 'chinese', 'beijing', 'xi jinping', 'cgtn', 'xinhua'],
                'russia': ['russia', 'russian', 'moscow', 'putin', 'kremlin', 'sputnik', 'rt'],
                'france': ['france', 'french', 'paris', 'macron', 'le monde', 'france24'],
                'usa': ['united states', 'usa', 'america', 'american', 'washington', 'cnn', 'bbc', 'reuters'],
                'saudi': ['saudi', 'riyadh', 'king salman', 'crown prince'],
                'turkey': ['turkey', 'turkish', 'ankara', 'erdogan', 'anadolu'],
                'uae': ['uae', 'emirates', 'dubai', 'abu dhabi'],
                'israel': ['israel', 'israeli', 'tel aviv', 'netanyahu'],
                'iran': ['iran', 'iranian', 'tehran', 'raisi', 'khamenei'],
                'rwanda': ['rwanda', 'rwandan', 'kigali', 'kagame'],
            }
            
            for actor, keywords in actor_keywords.items():
                for keyword in keywords:
                    if keyword in text_lower:
                        return actor.title()
            
            return 'Unknown'
        except Exception as e:
            logger.error(f"Error extracting actor from content: {e}")
            return 'Unknown'
            
    def perform_strategic_intent_inference(self, article_text):
        """Perform strategic intent inference"""
        try:
            classifier = self._load_strategic_classifier()
            predictions, probabilities = classifier.predict(
                texts=[article_text],
                batch_size=1,
                calibrated=False,
                return_probs=True
            )
            pred_idx = predictions[0]
            if self._strategic_label_encoder:
                pred_label = self._strategic_label_encoder.inverse_transform([pred_idx])[0]
            else:
                pred_label = str(pred_idx)
            confidence = float(np.max(probabilities[0]))
            return str(pred_label), confidence
        except Exception as e:
            logger.error(f"Error in strategic intent inference: {e}")
            return "unknown", 0.0

    def perform_tone_inference(self, article_text):
        """Perform tone inference"""
        try:
            classifier = self._load_tone_classifier()
            
            # If classifier failed to load, return fallback
            if classifier is None:
                logger.warning("Tone classifier not available, returning neutral")
                return 'neutral', 0.3
                
            probs = classifier.predict_proba([article_text])
            pred_idx = np.argmax(probs[0])
            if self._tone_label_encoder:
                pred_label = self._tone_label_encoder.inverse_transform([pred_idx])[0]
            else:
                pred_label = str(pred_idx)
            confidence = float(np.max(probs[0]))
            return str(pred_label), confidence
        except Exception as e:
            logger.error(f"Error in tone inference: {e}")
            return 'neutral', 0.3

        def perform_inference(self, article_text):
            """Main inference method"""
            processed_text = self.preprocess_text(article_text)
            if not processed_text:
                return {
                    'strategic_intent': 'unknown',
                    'tone': 'neutral',
                    'confidence': 0.0,
                    'lang_detect': 'und',
                    'use_afrolm': False,
                    'strategic_intent_conf': 0.0,
                    'strategic_intent_source': 'model' # Still default for empty text if needed
                }
    
            lang_code = self.detect_language(processed_text)
            is_lowres = self.is_low_resource_lang(lang_code)
    
            # --- NEW: Wrap strategic intent call in try-except ---
            try:
                strategic_intent_result = self.perform_strategic_intent_inference(processed_text)
                # Ensure the result is a tuple with 3 elements
                if isinstance(strategic_intent_result, tuple) and len(strategic_intent_result) == 3:
                    strategic_intent, si_confidence, si_source = strategic_intent_result
                else:
                    # Handle unexpected result from perform_strategic_intent_inference
                    logger.error(f"perform_strategic_intent_inference returned unexpected result: {strategic_intent_result}")
                    strategic_intent = 'unknown'
                    si_confidence = 0.0
                    si_source = 'error_perform_strategic_intent_inference'
            except Exception as e_si:
                logger.error(f"Error in perform_strategic_intent_inference: {e_si}")
                import traceback
                traceback.print_exc() # Print full traceback for debugging
                strategic_intent = 'unknown'
                si_confidence = 0.0
                si_source = 'error_perform_strategic_intent_inference'
    
            # --- NEW: Wrap tone inference call in try-except (should already be done in perform_tone_inference, but good practice here too) ---
            try:
                tone_result = self.perform_tone_inference(processed_text)
                # Ensure the result is a tuple with 2 elements
                if isinstance(tone_result, tuple) and len(tone_result) == 2:
                    tone, tone_confidence = tone_result
                else:
                    logger.error(f"perform_tone_inference returned unexpected result: {tone_result}")
                    tone = 'neutral'
                    tone_confidence = 0.3
            except Exception as e_tone:
                logger.error(f"Error in perform_tone_inference: {e_tone}")
                import traceback
                traceback.print_exc() # Print full traceback for debugging
                tone = 'neutral'
                tone_confidence = 0.3
    
            # --- NEW: Calculate confidence safely ---
            try:
                # Use the confidence values obtained, defaulting to 0.0 if they were set to 0.0 due to errors
                confidence = max(si_confidence, tone_confidence)
            except NameError:
                # This block might be redundant now due to the above error handling,
                # but added as a safeguard if si_confidence/tone_confidence somehow remain undefined despite the try blocks above.
                logger calculation failed due to undefined si_confidence or tone_confidence.")
                confidence = 0.0
                si_confidence = 0.0 # Ensure it's defined for the return dictionary
                si_source = 'error_conf_calculation' # Update source if calculation failed
                tone_confidence = 0.3 # Ensure it's defined for the return dictionary
    
            return {
                'strategic_intent': strategic_intent,
                'tone': tone,
                'confidence': confidence, # Use the calculated confidence
                'lang_detect': lang_code,
                'use_afrolm': is_lowres,
                'strategic_intent_conf': si_confidence, # Use the SI-specific confidence
                'strategic_intent_source': si_source # Use the determined source
            }

    def calculate_vulnerability_index(self, strategic_intent, tone, target_country, inferred_actor, confidence):
        """
        Finalized version using exact keys from final_risk_by_actor_intent_country.csv
        """
        try:
            df = self._csv_risk_df
            if df.empty:
                logger.warning("CSV Dataframe is empty! Check file path.")
                return self._calculate_fallback_vulnerability_index(strategic_intent, tone, confidence)

            # 1. EXACT MAPPINGS BASED ON YOUR CSV CONTENT
            country_mapping = {
                "senegal": "Senegal",
                "drc": "DRC",
                "democratic republic of congo": "DRC",
                "ethiopia": "Ethiopia",
                "South Africa": "South Africa",
                "south africa": "South Africa",
                "cote d'ivoire": "CoteIvoire",
                "côte d'ivoire": "CoteIvoire",
                "ivory coast": "CoteIvoire"
            }
            
            actor_mapping = {
                "china": "China",
                "france": "France",
                "russia": "Russia",
                "united states": "UnitedStates",
                "us": "UnitedStates",
                "usa": "UnitedStates",
                "saudi": "Saudi",
                "saudi arabia": "Saudi",
                "turkey": "Turkey",
                "uae": "UAE",
                "united arab emirates": "UAE",
                "israel": "Israel",
                "iran": "Iran",
                "rwanda": "Rwanda"

            }
            
            intent_mapping = {
                # Direct matches (if ML outputs match CSV exactly)
                "economic": "Economic",
                "sovereignty": "Sovereignty",
                "lgbtq": "LGBTQ",
                "religious": "Religious",
                "electioninfluence": "ElectionInfluence", 
                "militarypresence": "MilitaryPresence", 
                "resourcedependency": "ResourceDependency", 
                "socialfragility": "SocialFragility", 
        
                # Common variations/spelling from ML model output (case-insensitive)
                "economic dependency": "Economic",
                "sovereignty erosion": "Sovereignty",
                "sovereignty threat": "Sovereignty",
                "lgbtq rights": "LGBTQ",
                "lgbt advocacy": "LGBTQ", # <-- CORRECTED: Added comma here
                "religious influence": "Religious",
                "religious polarisation": "Religious",
                "election influence": "ElectionInfluence", 
                "election interference": "ElectionInfluence",
                "electoral interference": "ElectionInfluence",
                "military presence": "MilitaryPresence", 
                "military base": "MilitaryPresence",
                "resource dependency": "ResourceDependency", 
                "resource control": "ResourceDependency",
                "social fragility": "SocialFragility", 
                "social unrest": "SocialFragility",
                "information warfare": "SocialFragility", 
                "human rights advocacy": "SocialFragility", 
                "debt trap diplomacy": "Economic", 
                "cultural influence": "SocialFragility", 
                "centralization of power": "Sovereignty",
                "cultural exchange": "Economic",
                "cultural hegemony": "Sovereignty",
                "democratic interference": "ElectionInfluence",
                "diplomatic cooperation": "Economic",
                "diplomatic influence": "Sovereignty",
            }


            # Normalize inputs
            c_clean = target_country.lower().strip()
            a_clean = inferred_actor.lower().strip()
            i_clean = strategic_intent.lower().strip()

            formatted_country = country_mapping.get(c_clean, target_country)
            formatted_actor = actor_mapping.get(a_clean, inferred_actor)
            formatted_intent = intent_mapping.get(i_clean, "Sovereignty") # Default to Sovereignty if unknown

            # --- DEBUG LOGS (for EC2 Terminal) ---
            print(f"\n--- VULNERABILITY LOOKUP ---")
            print(f"INPUT: {target_country} | {inferred_actor} | {strategic_intent}")
            print(f"MAPPED: {formatted_country} | {formatted_actor} | {formatted_intent}")

            # 2. STRATEGY A: EXACT MATCH (Case Insensitive)
            # Strategy A: EXACT MATCH (Aggressive Cleaning)
            mask = (df['country'].str.strip().str.lower() == formatted_country.strip().lower()) & \
                   (df['actor'].str.strip().str.lower() == formatted_actor.strip().lower()) & \
                   (df['intent'].str.strip().str.lower() == formatted_intent.strip().lower())
            
            matching_row = df[mask]

            if not matching_row.empty:
                score = float(matching_row.iloc[0]['FinalRisk'])
                print(f"✅ SUCCESS: Found exact match. Score: {score}")
                return score

            # 3. STRATEGY B: COUNTRY-ACTOR MAX (If intent doesn't match)
            print(f"⚠️ No exact intent match. Trying Country-Actor fallback...")
            fallback_mask = (df['country'].str.lower() == formatted_country.lower()) & \
                            (df['actor'].str.lower() == formatted_actor.lower())
            
            matching_rows = df[fallback_mask]
            if not matching_rows.empty:
                score = float(matching_rows['FinalRisk'].max())
                print(f"✅ SUCCESS: Using Max Risk for {formatted_country}-{formatted_actor}: {score}")
                return score

            # 4. FINAL FALLBACK (If Country or Actor is not in CSV)
            print(f"❌ CRITICAL: {formatted_country} or {formatted_actor} NOT in CSV. Using generic math.")
            return self._calculate_fallback_vulnerability_index(strategic_intent, tone, confidence)

        except Exception as e:
            logger.error(f"Error in calculate_vulnerability_index: {e}")
            return self._calculate_fallback_vulnerability_index(strategic_intent, tone, confidence)
            
    def _calculate_fallback_vulnerability_index(self, strategic_intent, tone, confidence):
        """Standard fallback if the CSV lookup fails completely."""
        intent_scores = {
            'Economic': 0.6, 
            'Sovereignty': 0.8, 
            'LGBTQ': 0.4,
            'Religious': 0.5, 
            'ElectionInfluence': 0.8,  # Added this
            'MilitaryPresence': 0.7,
            'ResourceDependency': 0.6, 
            'SocialFragility': 0.9
        }
        # Use the intent to get a base score, default to 0.5 if not found
        # Note: strategic_intent here should be the 'formatted_intent' for best results
        base = intent_scores.get(strategic_intent, 0.5)
        return round(base, 2)

    def cleanup(self):
        """Clean up temporary directories"""
        import shutil
        for temp_dir in self._temp_dirs:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except:
                pass
        self._temp_dirs.clear()

# NO GLOBAL INSTANCE — create on demand
def get_ml_service():
    """Get ML service instance"""
    return MLInferenceService()
