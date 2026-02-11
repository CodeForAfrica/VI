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
DetectorFactory.seed = 0

logger = logging.getLogger(__name__)

class MLInferenceService:
    def __init__(self):
        # Only initialize S3 client if AWS credentials are available
        if hasattr(settings, 'AWS_ACCESS_KEY_ID') and settings.AWS_ACCESS_KEY_ID and \
           hasattr(settings, 'AWS_SECRET_ACCESS_KEY') and settings.AWS_SECRET_ACCESS_KEY and \
           hasattr(settings, 'S3_MODELS_BUCKET') and settings.S3_MODELS_BUCKET:
            self.s3_client = boto3.client(
                's3',
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                region_name=getattr(settings, 'AWS_S3_REGION_NAME', 'eu-west-1')
            )
            self.bucket_name = settings.S3_MODELS_BUCKET
        else:
            self.s3_client = None
            self.bucket_name = None
        
        self._model_cache = {}  # Cache loaded models
        self._temp_dirs = set()  # Track temp directories
        self._contextual_module = None
        self._strategic_label_encoder = None
        self._tone_label_encoder = None
        self._strategic_vocab = None
    
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
    
    def _download_directory_from_s3(self, s3_prefix, local_dir):
        """Download entire directory from S3 to local directory"""
        if not self.s3_client:
            raise Exception("AWS S3 credentials not configured")
        
        try:
            paginator = self.s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket_name, Prefix=s3_prefix)
            
            for page in pages:
                for obj in page.get('Contents', []):
                    key = obj['Key']
                    if key.endswith('/'):
                        continue  # Skip directories
                    
                    rel_path = key[len(s3_prefix):].lstrip('/')
                    local_file_path = os.path.join(local_dir, rel_path)
                    
                    os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
                    self.s3_client.download_file(self.bucket_name, key, local_file_path)
            
            return True
        except Exception as e:
            logger.error(f"Error downloading directory {s3_prefix}: {e}")
            return False
    
    def _load_strategic_classifier(self):
        """Load calibrated strategic classifier from S3 (matches your notebook)"""
        if 'strategic' in self._model_cache:
            return self._model_cache['strategic']
        
        # Create temporary directory
        temp_dir = tempfile.mkdtemp(prefix='strategic_model_')
        self._temp_dirs.add(temp_dir)
        
        # Download the entire strategic model directory (from your notebook)
        if not self._download_directory_from_s3('models/calibrated_contrastive_peft/', temp_dir):
            raise Exception("Failed to download strategic classifier from S3")
        
        # Load the calibrated classifier (using your notebook's class)
        from dashboard.services.calibrated_ensemble import CalibratedStrategicClassifier
        classifier = CalibratedStrategicClassifier.load(temp_dir)
        
        # Cache for reuse
        self._model_cache['strategic'] = classifier
        
        # Load label encoder separately
        label_enc_path = os.path.join(temp_dir, 'label_encoder.pkl')
        if os.path.exists(label_enc_path):
            with open(label_enc_path, 'rb') as f:
                self._strategic_label_encoder = pickle.load(f)
        
        return classifier
    
    def _load_tone_classifier(self):
        """Load calibrated tone classifier from S3 (matches your notebook)"""
        if 'tone' in self._model_cache:
            return self._model_cache['tone']
        
        # Create temporary directory
        temp_dir = tempfile.mkdtemp(prefix='tone_model_')
        self._temp_dirs.add(temp_dir)
        
        # Download the entire tone model directory (from your notebook)
        if not self._download_directory_from_s3('models/calibrated_stacked_ensemble/', temp_dir):
            raise Exception("Failed to download tone classifier from S3")
        
        # Load the calibrated classifier (using your notebook's class)
        from dashboard.services.tone_ensemble import CalibratedStackedEnsemble
        classifier = CalibratedStackedEnsemble.load(temp_dir)
        
        # Cache for reuse
        self._model_cache['tone'] = classifier
        
        # Load label encoder separately
        label_enc_path = os.path.join(temp_dir, 'label_info.json')
        if os.path.exists(label_enc_path):
            with open(label_enc_path, 'r') as f:
                label_info = json.load(f)
            from sklearn.preprocessing import LabelEncoder
            self._tone_label_encoder = LabelEncoder()
            self._tone_label_encoder.classes_ = np.array(label_info['classes'])
        
        return classifier
    
    def _load_contextual_module_from_s3(self):
        """Load contextual module from S3 (from your notebook) - YES, WE'RE IMPORTING IT"""
        if self._contextual_module is not None:
            return self._contextual_module
        
        if not self.s3_client:
            raise Exception("AWS S3 credentials not configured")
        
        try:
            # Download contextual module from S3 - THIS IS THE ORIGINAL contextual_all_intents_v2.py FILE
            response = self.s3_client.get_object(
                Bucket=self.bucket_name,
                Key='models/contextual_all_intents_v2 (1).py'
            )
            
            # Save temporarily and import - WE'RE LOADING THE EXISTING PYTHON FILE
            import tempfile
            import importlib.util
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(response['Body'].read().decode('utf-8'))
                temp_py_path = f.name
            
            spec = importlib.util.spec_from_file_location('contextual_mod', temp_py_path)
            contextual_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(contextual_mod)
            
            self._contextual_module = contextual_mod
            return contextual_mod
        except Exception as e:
            logger.error(f"Error loading contextual module from S3: {e}")
            return None
    
    def preprocess_text(self, text):
        """Replicate preprocessing from notebook"""
        if not text or text.strip() == "":
            return ""
        
        text = str(text).strip()
        if len(text) > 4000:  # From your notebook
            text = text[:4000]
        
        return text
    
    def detect_language(self, text):
        """Detect language as in notebook"""
        try:
            return detect(text[:200])  # From your notebook
        except (LangDetectException, Exception):
            return 'und'
    
    def is_low_resource_lang(self, lang_code):
        """Check if language is low resource as in notebook"""
        lowres_langs = ['ha','yo','am','sw','wo','ig','ff','pt','bm','mg','zu']  # From your notebook
        return lang_code in lowres_langs
    
    def perform_strategic_intent_inference(self, article_text):
        """Perform strategic intent inference using calibrated ensemble (from your notebook)"""
        try:
            # Load classifier
            classifier = self._load_strategic_classifier()
            
            # Perform prediction (matching your notebook's logic)
            predictions, probabilities = classifier.predict(
                texts=[article_text],
                batch_size=1,
                calibrated=True,
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
        """Perform tone inference using calibrated ensemble (from your notebook)"""
        try:
            # Load classifier
            classifier = self._load_tone_classifier()
            
            # Perform prediction (matching your notebook's logic)
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
            return "neutral", 0.0
    
    def perform_inference(self, article_text):
        """Main inference method that returns all predictions (matching your notebook)"""
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
        
        # Detect language (from your notebook)
        lang_code = self.detect_language(processed_text)
        is_lowres = self.is_low_resource_lang(lang_code)
        
        # Perform predictions (from your notebook)
        strategic_intent, si_confidence = self.perform_strategic_intent_inference(processed_text)
        tone, tone_confidence = self.perform_tone_inference(processed_text)
        
        # Use the higher confidence for overall confidence
        confidence = max(si_confidence, tone_confidence)
        
        return {
            'strategic_intent': strategic_intent,
            'tone': tone,
            'confidence': confidence,
            'lang_detect': lang_code,
            'use_afrolm': is_lowres,
            'strategic_intent_conf': si_confidence,
            'strategic_intent_source': 'model'  # From your notebook's logic
        }
    
    def calculate_vulnerability_index(self, strategic_intent, tone, target_country, inferred_actor, confidence):
        """Calculate vulnerability index using contextual module and PPI approach"""
        try:
            # Load contextual module from S3
            contextual_mod = self._load_contextual_module_from_s3()
            if not contextual_mod:
                # Fallback simple calculation
                intent_scores = {
                    'hostile': 1.0, 'aggressive': 0.9, 'manipulative': 0.8, 'deceptive': 0.8,
                    'misleading': 0.7, 'concerning': 0.6, 'suspicious': 0.5,
                    'neutral': 0.3, 'informative': 0.2, 'positive': 0.1, 'supportive': 0.0
                }
                tone_scores = {
                    'very_negative': 1.0, 'negative': 0.8, 'critical': 0.7, 'skeptical': 0.6,
                    'neutral': 0.3, 'positive': 0.1, 'very_positive': 0.0,
                    'supportive': 0.0, 'praising': 0.0
                }
                
                intent_score = intent_scores.get(strategic_intent.lower(), 0.3)
                tone_score = tone_scores.get(tone.lower(), 0.3)
                
                return (intent_score * 0.5 + tone_score * 0.3 + confidence * 0.2)
            
            # Use contextual module if available
            try:
                # Compute g, R, CA using functions from the contextual module
                g = contextual_mod.compute_gs()
                R = contextual_mod.compute_R(g)
                CA = contextual_mod.compute_CAs(g, R)
                
                # Get the contextual risk for this intent-category-target_country combination
                contextual_risk = 0.0
                intent_category = strategic_intent
                
                if intent_category in CA:
                    # Convert target country to match the EXACT format in contextual module
                    country_mapping = {
                        "senegal": "Senegal",
                        "drc": "DRC", 
                        "congo": "DRC",
                        "democraticrepublicofcongo": "DRC",
                        "coteivoire": "CoteIvoire",
                        "coted'ivoire": "CoteIvoire",
                        "ivorycoast": "CoteIvoire",
                        "ethiopia": "Ethiopia",
                        "southafrica": "South Africa"
                    }
                    
                    target_clean = target_country.lower().replace(" ", "").replace("'", "").replace("-", "").replace("_", "")
                    
                    formatted_country = None
                    for key, expected_format in country_mapping.items():
                        key_clean = key.lower().replace(" ", "").replace("'", "").replace("-", "").replace("_", "")
                        if target_clean == key_clean:
                            formatted_country = expected_format
                            break
                    
                    if formatted_country is None:
                        available_countries = ["Senegal", "DRC", "CoteIvoire", "Ethiopia", "South Africa"]
                        for country in available_countries:
                            country_clean = country.lower().replace(" ", "").replace("'", "").replace("-", "").replace("_", "")
                            if target_clean == country_clean:
                                formatted_country = country
                                break
    
                    # Now get the contextual risk if we found a match
                    if formatted_country and formatted_country in CA[intent_category]:
                        # Use inferred actor or default to a known actor
                        actor_mapping = {
                            "china": "China",
                            "france": "France", 
                            "unitedstates": "UnitedStates",
                            "russia": "Russia",
                            "rwanda": "Rwanda",
                            "saudi": "Saudi",
                            "turkey": "Turkey",
                            "uae": "UAE",
                            "israel": "Israel",
                            "iran": "Iran",
                            "nonstate": "NonState",
                            "government": "China",
                            "opposition": "NonState",
                            "media": "NonState"
                        }
                        
                        actor_clean = inferred_actor.lower().replace(" ", "").replace("-", "").replace("_", "")
                        formatted_actor = None
                        for key, expected_format in actor_mapping.items():
                            key_clean = key.lower().replace(" ", "").replace("-", "").replace("_", "")
                            if actor_clean == key_clean:
                                formatted_actor = expected_format
                                break
                        
                        # If actor still not found, use first available actor as fallback
                        if formatted_actor is None:
                            available_actors = list(CA[intent_category][formatted_country].keys()) if formatted_country in CA[intent_category] else []
                            if available_actors:
                                formatted_actor = available_actors[0]
                        
                        # Get the contextual risk value
                        if formatted_country in CA[intent_category] and formatted_actor:
                            if formatted_actor in CA[intent_category][formatted_country]:
                                contextual_risk = CA[intent_category][formatted_country][formatted_actor]
                            else:
                                # Actor not found - use average of all actors for this country-intent
                                available_actors = list(CA[intent_category][formatted_country].keys())
                                if available_actors:
                                    contextual_risk = sum(
                                        CA[intent_category][formatted_country][a] 
                                        for a in available_actors
                                    ) / len(available_actors)
                                else:
                                    contextual_risk = 0.0
                        else:
                            contextual_risk = 0.0
                return contextual_risk
            except Exception as e:
                logger.error(f"Error using contextual module: {e}")
                return 0.0
        except Exception as e:
            logger.error(f"Error calculating vulnerability index: {e}")
            return 0.0
