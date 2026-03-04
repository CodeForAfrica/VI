# dashboard/services/ml_inference_service.py

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
DetectorFactory.seed = 0

logger = logging.getLogger(__name__)

class MLInferenceService:
    def __init__(self):
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
                region_name=aws_region
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
                logger.info(f"✅ Loaded CSV risk data from fallback path: {fallback_path}")
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

    def _download_directory_from_s3(self, s3_prefix, local_dir, max_retries=3):
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
                print(f"📋 Response: {response}")
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
                        self.s3_client.download_file(self.bucket_name, key, local_file_path)
                        print(f"✅ Downloaded: {key}")
                        break
                    except Exception as e:
                        if attempt < max_retries - 1:
                            print(f"⚠️ Retry {attempt + 1}/{max_retries} for {key}: {e}")
                            time.sleep(2 ** attempt)  # Exponential backoff
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
    def _load_strategic_classifier(self):
        """Load calibrated strategic classifier from S3"""
        if 'strategic' in self._model_cache:
            return self._model_cache['strategic']
        
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
                    base_prefix = 'microsoft_deberta-v3-large/'
                    print(f"📂 Looking for base model in S3: {base_prefix}")
                    pages = self.s3_client.list_objects_v2(Bucket=self.bucket_name, Prefix=base_prefix)
                    contents = pages.get('Contents', [])
                    print(f"   Found {len(contents)} files")
                    
                    for obj in contents:
                        key = obj['Key']
                        filename = key.replace(base_prefix, '')
                        local_path = os.path.join(base_model_dir, filename)
                        os.makedirs(os.path.dirname(local_path), exist_ok=True)
                        self.s3_client.download_file(self.bucket_name, key, local_path)
                    print(f"✅ Downloaded base model to {base_model_dir}")
                    print(f"   Files: {os.listdir(base_model_dir)}")
                except Exception as e:
                    print(f"❌ Could not download base model: {e}")
        
        # Check what we have
        print(f"📂 Files in temp_dir: {os.listdir(temp_dir)}")
        
        # Now download the classifier
        classifier_prefix = 'calibrated_contrastive_peft/'
        print(f"📂 Downloading classifier from: {classifier_prefix}")
        
        if not self._download_directory_from_s3(classifier_prefix, temp_dir):
            raise Exception("Failed to download strategic classifier from S3")
        
        print(f"📂 Files after classifier download: {os.listdir(temp_dir)}")
        
        # Load the classifier
        from dashboard.services.calibrated_ensemble import CalibratedStrategicClassifier
        classifier = CalibratedStrategicClassifier.load(temp_dir)
        
        self._model_cache['strategic'] = classifier
        
        # Load label encoder
        label_enc_path = os.path.join(temp_dir, 'label_encoder.pkl')
        if os.path.exists(label_enc_path):
            with open(label_enc_path, 'rb') as f:
                self._strategic_label_encoder = pickle.load(f)
        
        print(f"✅ Strategic classifier loaded!")
        return classifier
        
    def _load_tone_classifier(self):
        """Load calibrated tone classifier from S3"""
        # ✅ Check cache first - return immediately if cached
        if 'tone' in self._model_cache:
            print("✅ Using cached tone classifier")
            return self._model_cache['tone']
        
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
                
                print(f"✅ SUCCESS: Tone classifier ready!")
                return classifier
        
        except Exception as e:
            print(f"❌ Error loading tone classifier: {e}")
            import traceback
            traceback.print_exc()
        
        logger.warning("Could not download tone classifier from S3, using fallback")
        return None
        
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
        
        return classifier
    
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
            'bbc': 'USA',
            'cnn': 'USA',
            'nytimes': 'USA',
            'washington post': 'USA',
            'reuters': 'USA',
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
            'new times': 'Rwanda',
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
                
            probs = classifier.predict_proba([article_text], calibrated=True, batch_size=1)
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
                'strategic_intent_source': 'model'
            }

        lang_code = self.detect_language(processed_text)
        is_lowres = self.is_low_resource_lang(lang_code)

        strategic_intent, si_confidence = self.perform_strategic_intent_inference(processed_text)
        tone, tone_confidence = self.perform_tone_inference(processed_text)

        confidence = max(si_confidence, tone_confidence)

        return {
            'strategic_intent': strategic_intent,
            'tone': tone,
            'confidence': confidence,
            'lang_detect': lang_code,
            'use_afrolm': is_lowres,
            'strategic_intent_conf': si_confidence,
            'strategic_intent_source': 'model'
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
                'economic': 'Economic',
                'economic dependency': 'Economic',
                'sovereignty': 'Sovereignty',
                'lgbtq': 'LGBTQ',
                'lgbt': 'LGBTQ',
                'religious': 'Religious',
                'religion': 'Religious',
                'election': 'ElectionInfluence',
                'election influence': 'ElectionInfluence',
                'political interference': 'ElectionInfluence',
                'military': 'MilitaryPresence',
                'military presence': 'MilitaryPresence',
                'resource dependency': 'ResourceDependency',
                'social fragility': 'SocialFragility',
                'ethnic': 'SocialFragility'
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
