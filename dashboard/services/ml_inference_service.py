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
import sys
from groq import Groq
from transformers import AutoTokenizer

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpx").propagate = False
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpcore").propagate = False
logging.getLogger("urllib3").setLevel(logging.WARNING)

# Silence Transformers loading bars and shard messages
logging.getLogger("transformers").setLevel(logging.ERROR)
os.environ["TQDM_DISABLE"] = "True" 

# Standard logger for your service
logger = logging.getLogger(__name__)

TransferConfig = None
DetectorFactory.seed = 0

class MLInferenceService:
    def __init__(self):
        print("MLInferenceService.__init__ called")
        # GPU SUPPORT
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"🚀 ML Service detected device: {self.device}")
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
        # ✅ Initialize tokenizer here
        try:
            self.tokenizer = AutoTokenizer.from_pretrained("microsoft/mdeberta-v3-base") # Or the correct base model path
        except Exception as e:
            logger.error(f"Failed to initialize tokenizer: {e}")
            self.tokenizer = None # Or handle the error as appropriate
            
    def lookup_risk(self, country, intent):
        """
        Maps model/LLM outputs to canonical labels and looks up risk scores.
        """
        # 1. Immediate guard for non-strategic content
        if not intent or str(intent).lower() in ['neutral', 'unknown', 'none']:
            return 0.0

        # 2. Intent Mapping Dictionary
        intent_mapping = {
            "economic": "Economic",
            "sovereignty": "Sovereignty",
            "lgbtq": "LGBTQ",
            "religious": "Religious",
            "electioninfluence": "ElectionInfluence", 
            "militarypresence": "MilitaryPresence", 
            "resourcedependency": "ResourceDependency", 
            "socialfragility": "SocialFragility", 
            "economic dependency": "Economic",
            "sovereignty erosion": "Sovereignty",
            "sovereignty threat": "Sovereignty",
            "lgbtq rights": "LGBTQ",
            "lgbt advocacy": "LGBTQ",
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

        # 3. Apply Mapping
        # Normalize input to lowercase and strip whitespace for matching
        normalized_intent = str(intent).lower().strip()
        mapped_intent = intent_mapping.get(normalized_intent, intent) # Default to original if not in map

        try:
            df = self._csv_risk_df
            
            # Use the mapped_intent for the CSV search
            match = df[
                (df['country'].str.strip() == country) & 
                (df['intent'].str.strip() == mapped_intent)
            ]
            
            if not match.empty:
                score = float(match.iloc[0]['risk_score'])
                # Log the mapping for transparency
                if mapped_intent != intent:
                    logger.info(f"🔄 Mapped '{intent}' -> '{mapped_intent}' | Score: {score}")
                return score
            
            logger.warning(f"⚠️ No CSV match for {country} | {mapped_intent} (Original: {intent})")
            return 0.0
            
        except Exception as e:
            logger.error(f"Error during risk lookup: {e}")
            return 0.0
            
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
        
    def _get_tone(self, article_text):
        """
        Internal helper for the pipeline to get tone prediction.
        Falls back to 'Factual' on any error.
        """
        try:
            # 1. Load the classifier
            classifier = self._load_tone_classifier()
            if not classifier:
                return "Factual", 0.0

            # 2. Get the prediction (removing return_probs to fix the previous error)
            predictions = classifier.predict([article_text])
            
            # 3. Attempt to get confidence, but don't crash if it fails
            confidence = 0.0
            try:
                if hasattr(classifier, 'predict_proba'):
                    probs = classifier.predict_proba([article_text])
                    confidence = float(np.max(probs[0]))
            except Exception:
                pass # Keep confidence at 0.0

            # 4. Decode Label
            predicted_val = predictions[0]
            
            # If it's already a string (like 'Alarmist'), use it
            if isinstance(predicted_val, str):
                return predicted_val, confidence
                
            # If it's an index, try to decode it
            if self._tone_label_encoder:
                tone_label = self._tone_label_encoder.inverse_transform([predicted_val])[0]
                return str(tone_label), confidence
            
            # Manual fallback map if encoder is missing
            classes = ['Alarmist', 'Cynical', 'Factual', 'Sensationalist']
            if isinstance(predicted_val, (int, np.integer)) and predicted_val < len(classes):
                return classes[predicted_val], confidence

            return "Factual", confidence

        except Exception as e:
            logger.error(f"❌ Tone inference failed, falling back to Factual: {e}")
            return "Factual", 0.0
            
            
    def _get_llm_strategic_intent(self, text: str):
        """
        Robustly extracts intent from LLM responses silently.
        Fixes JSON errors without logging the recovery steps.
        """
        groq_api_key = getattr(settings, 'GROQ_API_KEY', '')
        if not groq_api_key:
            return "unknown", 0.0, "API key missing"

        try:
            client = Groq(api_key=groq_api_key)
            system_msg = (
                "Analyze strategic intent. Respond ONLY with JSON. "
                "Labels: Economic, Sovereignty, LGBTQ, Religious, ElectionInfluence, "
                "MilitaryPresence, ResourceDependency, SocialFragility, Neutral."
            )
            
            response = client.chat.completions.create(
                model=getattr(settings, 'GROQ_MODEL', 'meta-llama/llama-4-scout-17b-16e-instruct'),
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": text[:4000]}
                ],
                temperature=0.0 
            )

            raw_content = response.choices[0].message.content.strip()
            
            # 1. Extract all JSON-like blocks
            json_blocks = re.findall(r'\{.*?\}', raw_content, re.DOTALL)
            
            if not json_blocks:
                clean_text = re.sub(r'```json|```', '', raw_content).strip()
                js = json.loads(clean_text)
            else:
                last_block = json_blocks[-1]
                try:
                    js = json.loads(last_block)
                except json.JSONDecodeError:
                    # Attempt silent fix for unescaped quotes
                    try:
                        fixed_block = re.sub(r'(?<!\\)"', r'\"', last_block) 
                        fixed_block = fixed_block.replace('\"strategic_intent\"', '"strategic_intent"')
                        fixed_block = fixed_block.replace('\"strategic_intent_conf\"', '"strategic_intent_conf"')
                        fixed_block = fixed_block.replace('\"notes\"', '"notes"')
                        
                        if fixed_block.startswith('\"'): fixed_block = '{' + fixed_block[2:]
                        if fixed_block.endswith('\"'): fixed_block = fixed_block[:-2] + '}'
                        
                        js = json.loads(fixed_block)
                    except:
                        js = json.loads(json_blocks[0])

            # 2. Extract values
            intent = js.get('strategic_intent', 'Neutral')
            conf = js.get('strategic_intent_conf', 0.0)
            notes = js.get('notes', '')

            try:
                conf = float(conf)
            except (TypeError, ValueError):
                conf = 0.0

            return intent, conf, notes

        except Exception as e:
            # We only log if it fails COMPLETELY after all recovery attempts
            logger.error(f"❌ LLM Final Parse Failure: {str(e)}")
            return "Neutral", 0.0, f"Error: {str(e)}"
        
    # Modify the main strategic intent method
    def perform_strategic_intent_batch(self, article_texts):
        """
        FAST BATCH INFERENCE
        Used by the pipeline loop to process 20 articles at a time.
        """
        try:
            classifier = self._load_strategic_classifier()
            if not classifier:
                # Return 'unknown' for every article in the failed batch
                return [("unknown", 0.0)] * len(article_texts)

            # Ensemble prediction on the whole batch
            # Ensure your classifier supports batch_size and return_probs
            predictions, probabilities = classifier.predict(
                article_texts, 
                batch_size=len(article_texts), 
                calibrated=True, 
                return_probs=True
            )

            results = []
            for i in range(len(predictions)):
                intent = self._decode_label(predictions[i])
                # Ensure we handle probability extraction safely
                conf = float(np.max(probabilities[i])) if probabilities is not None else 0.0
                results.append((intent, conf))
            
            return results
            
        except Exception as e:
            logger.error(f"❌ Batch inference failed: {e}")
            # Robust fallback: return a list of same length as input
            return [("unknown", 0.0)] * len(article_texts)

    def perform_strategic_intent_inference(self, article_text):
        """
        SINGLE INFERENCE WITH LLM FALLBACK
        Used when batch confidence is low (< 0.6) or for single-article processing.
        Includes full logging and decision logic.
        """
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)

        model_intent = "unknown"
        model_confidence = 0.0
        
        # 1. Get Model Prediction
        try:
            classifier = self._load_strategic_classifier()
            if classifier:
                predictions, probabilities = classifier.predict(
                    [article_text], 
                    batch_size=1, 
                    calibrated=True, 
                    return_probs=True
                )
                model_intent = self._decode_label(predictions[0])
                model_confidence = float(np.max(probabilities[0]))
        except Exception as e:
            logger.error(f"Model inference failed: {e}")

        # 2. Get LLM Prediction
        llm_intent, llm_confidence, llm_notes = self._get_llm_strategic_intent(article_text)
        
        # 3. Decision Logic (Your exact logic)
        final_intent = "unknown"
        final_confidence = 0.0
        prediction_source = "unknown"
        CONFIDENCE_THRESHOLD_FOR_MATCH = 0.6

        if model_intent and llm_intent:
            if model_intent.lower() == llm_intent.lower():
                max_conf = max(model_confidence, llm_confidence)
                if max_conf >= CONFIDENCE_THRESHOLD_FOR_MATCH:
                    final_intent, final_confidence = model_intent, max_conf
                    prediction_source = "ensemble_matched_confirmed"
                else:
                    # Low confidence match - pick higher
                    if model_confidence >= llm_confidence:
                        final_intent, final_confidence = model_intent, model_confidence
                        prediction_source = "model_selected_after_low_match"
                    else:
                        final_intent, final_confidence = llm_intent, llm_confidence
                        prediction_source = "llm_selected_after_low_match"
            else:
                # Disagreement - pick higher
                if model_confidence >= llm_confidence:
                    final_intent, final_confidence = model_intent, model_confidence
                    prediction_source = "model"
                else:
                    final_intent, final_confidence = llm_intent, llm_confidence
                    prediction_source = "llm"
        
        logger.info(f"Final Decision: Intent={final_intent}, Confidence={final_confidence}, Source={prediction_source}")
        return final_intent, final_confidence

    def _decode_label(self, label):
        """Helper to convert model class IDs to string labels."""
        if self._strategic_label_encoder:
            try:
                if isinstance(label, (int, np.integer)):
                    return self._strategic_label_encoder.inverse_transform([label])[0]
                return label
            except:
                return str(label)
        return str(label)
        
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
            import sys # Ensure sys is imported
            # Load spaCy model
            try:
                nlp = spacy.load("en_core_web_sm")
            except OSError: # It's more specific than a generic 'except'
                # Download if not exists
                import subprocess
                # Use sys.executable instead of "python"
                subprocess.run([sys.executable, "-m", "spacy", "download", "en_core_web_sm"], check=True)
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
            # Return empty lists on failure, as the calling code likely expects a dictionary
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
        """🚀 MAIN PIPELINE METHOD - Called by run_pipeline.py"""
        processed_text = self.preprocess_text(article_text)
        if not processed_text:
            return {
                'strategic_intent': strategic_intent,
                'strategic_intent_conf': si_confidence,      # ✅ NEW
                'strategic_intent_source': 'model',          # ✅ NEW  
                'confidence': max(si_confidence, tone_confidence),
                'tone': tone,
                'vulnerability_index': float(vi_score),
                'inferred_actor': inferred_actor,
                'target_country': target_country
            }

        try:
            # 1. STRATEGIC INTENT
            strategic_intent, si_confidence = self.perform_strategic_intent_inference(processed_text)
            
            # 2. TONE
            tone, tone_confidence = self.perform_tone_inference(processed_text)
            
            # 3. EXTRACT ACTOR/COUNTRY
            entities = self.extract_entities_from_content(processed_text)
            inferred_actor = self.extract_actor_from_content(
                processed_text, 
                entities.get('organizations', []), 
                entities.get('persons', [])
            )
            
            # Default country (update based on your data)
            target_country = 'Ethiopia'  # or detect from text
            
            # 4. VULNERABILITY INDEX
            vi_score = self.calculate_vulnerability_index(
                strategic_intent=strategic_intent,
                tone=tone,
                target_country=target_country,
                inferred_actor=inferred_actor,
                confidence=max(si_confidence, tone_confidence)
            )
            
            return {
                'strategic_intent': strategic_intent,
                'confidence': max(si_confidence, tone_confidence),
                'tone': tone,
                'vulnerability_index': float(vi_score),  # ✅ FLOAT - No NoneType errors!
                'inferred_actor': inferred_actor,
                'target_country': target_country
            }
            
        except Exception as e:
            logger.error(f"Pipeline inference error: {e}")
            return {
                'strategic_intent': 'unknown',
                'tone': 'neutral',
                'confidence': 0.0,
                'vulnerability_index': 0.0,  # ✅ SAFE DEFAULT
                'inferred_actor': 'Unknown',
                'target_country': 'Unknown'
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
