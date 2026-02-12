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
                Key='models/contextual_all_intents_v2.py'
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
        """Calculate vulnerability index using contextual module and PPI approach (from your notebook)"""
        try:
            # Load contextual module from S3 - WE'RE CALLING THE ORIGINAL contextual_all_intents_v2.py
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
            
            # Use contextual module if available (from your notebook) - WE'RE USING THE ORIGINAL MODULE
            try:
                # Compute g, R, CA using functions from the ORIGINAL contextual_all_intents_v2.py
                g = contextual_mod.compute_gs()
                R = contextual_mod.compute_R(g)
                CA = contextual_mod.compute_CAs(g, R)
                
                # Get the contextual risk for this intent-category-target_country combination
                contextual_risk = 0.0
                # Use the most relevant intent category based on strategic intent
                intent_category = strategic_intent  # Use the original intent name
                
                if intent_category in CA:
                    # Convert target country to match the EXACT format in your contextual module
                    # Countries in your contextual_all_intents_v2.py: Senegal, DRC, CoteIvoire, Ethiopia, South Africa
                    country_mapping = {
                        "senegal": "Senegal",
                        "drc": "DRC", 
                        "congo": "DRC",  # Alternative name for DRC
                        "democraticrepublicofcongo": "DRC",
                        "democratic republic of congo": "DRC",
                        "coteivoire": "CoteIvoire",  # Exact format from your module (no apostrophe)
                        "coted'ivoire": "CoteIvoire",  # With apostrophe
                        "ivorycoast": "CoteIvoire",
                        "ivory coast": "CoteIvoire",
                        "ethiopia": "Ethiopia",
                        "southafrica": "South Africa",  # Space in the name
                        "south africa": "South Africa"
                    }
                    
                    # Normalize the target country name
                    target_clean = target_country.lower().replace(" ", "").replace("'", "").replace("-", "").replace("_", "")
                    
                    # Find the exact format used in your contextual module
                    formatted_country = None
                    for key, expected_format in country_mapping.items():
                        key_clean = key.lower().replace(" ", "").replace("'", "").replace("-", "").replace("_", "")
                        if target_clean == key_clean:
                            formatted_country = expected_format
                            break
                    
                    # If still not found, try direct matching
                    if formatted_country is None:
                        available_countries = ["Senegal", "DRC", "CoteIvoire", "Ethiopia", "South Africa"]
                        for country in available_countries:
                            country_clean = country.lower().replace(" ", "").replace("'", "").replace("-", "").replace("_", "")
                            if target_clean == country_clean:
                                formatted_country = country
                                break

                    # Now get the contextual risk if we found a match
                    if formatted_country and formatted_country in CA[intent_category]:
                        # Use inferred actor or default to a known actor from your module
                        # Actors in your contextual module: China, France, UnitedStates, Russia, Rwanda, Saudi, Turkey, UAE, Israel, Iran, NonState
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
                            "government": "China",  # Default to China for government
                            "opposition": "NonState",  # Default to NonState for opposition
                            "media": "NonState"  # Default to NonState for media
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
                
                # Calculate composite risk using PPI approach (from your notebook)
                strategic_weight = 0.35
                tone_weight = 0.25
                confidence_weight = 0.2
                contextual_weight = 0.2  # Contextual risk from geopolitical model
                
                # Map intent and tone to risk scores (from your notebook logic)
                intent_risk = self._map_intent_to_risk(strategic_intent, CA)
                tone_risk = self._map_tone_to_risk(tone)
                
                # Normalize scores
                normalized_intent = min(max(intent_risk, 0), 1)
                normalized_tone = min(max(tone_risk, 0), 1)
                normalized_confidence = min(max(confidence, 0), 1)
                normalized_contextual = min(max(contextual_risk, 0), 1)
                
                vulnerability_index = (
                    (normalized_intent * strategic_weight) +
                    (normalized_tone * tone_weight) +
                    (normalized_confidence * confidence_weight) +
                    (normalized_contextual * contextual_weight)
                )
                
                return min(vulnerability_index, 1.0)
                
            except Exception as e:
                logger.error(f"Error in contextual vulnerability calculation: {e}")
                # Fallback to simple calculation
                intent_scores = {
                    'hostile': 1.0, 'aggressive': 0.9, 'manipulative': 0.8,
                    'deceptive': 0.8, 'misleading': 0.7, 'concerning': 0.6,
                    'suspicious': 0.5, 'neutral': 0.3, 'informative': 0.2,
                    'positive': 0.1, 'supportive': 0.0
                }
                
                tone_scores = {
                    'very_negative': 1.0, 'negative': 0.8, 'critical': 0.7,
                    'skeptical': 0.6, 'neutral': 0.3, 'positive': 0.1,
                    'very_positive': 0.0, 'supportive': 0.0, 'praising': 0.0
                }
                
                intent_score = intent_scores.get(strategic_intent.lower(), 0.3)
                tone_score = tone_scores.get(tone.lower(), 0.3)
                
                return (intent_score * 0.6 + tone_score * 0.3 + confidence * 0.1)
            
        except Exception as e:
            logger.error(f"Error calculating vulnerability index: {e}")
            # Return simple fallback
            intent_scores = {
                'hostile': 1.0, 'aggressive': 0.9, 'manipulative': 0.8,
                'deceptive': 0.8, 'misleading': 0.7, 'concerning': 0.6,
                'suspicious': 0.5, 'neutral': 0.3, 'informative': 0.2,
                'positive': 0.1, 'supportive': 0.0
            }
            
            tone_scores = {
                'very_negative': 1.0, 'negative': 0.8, 'critical': 0.7,
                'skeptical': 0.6, 'neutral': 0.3, 'positive': 0.1,
                'very_positive': 0.0, 'supportive': 0.0, 'praising': 0.0
            }
            
            intent_score = intent_scores.get(strategic_intent.lower(), 0.3)
            tone_score = tone_scores.get(tone.lower(), 0.3)
            
            return (intent_score * 0.6 + tone_score * 0.3 + confidence * 0.1)
    
    def _map_intent_to_risk(self, intent, CA):
        """Map strategic intent to risk score using contextual analysis (from your notebook)"""
        if not CA:
            # Simple mapping if contextual analysis unavailable
            intent_scores = {
                'hostile': 1.0, 'aggressive': 0.9, 'manipulative': 0.8,
                'deceptive': 0.8, 'misleading': 0.7, 'concerning': 0.6,
                'suspicious': 0.5, 'neutral': 0.3, 'informative': 0.2,
                'positive': 0.1, 'supportive': 0.0
            }
            return intent_scores.get(intent.lower(), 0.3)
        
        # Use contextual risk assessment if available (from your notebook)
        # This would typically involve matching intent to CA keys
        for ca_intent, risk_dict in CA.items():
            if intent.lower() in ca_intent.lower() or ca_intent.lower() in intent.lower():
                # Return the average risk across all countries and actors
                total_risk = 0.0
                count = 0
                for country_risks in risk_dict.values():
                    for risk_value in country_risks.values():
                        total_risk += risk_value
                        count += 1
                if count > 0:
                    return min(total_risk / count, 1.0)
        
        return 0.3  # Default neutral risk
    
    def _map_tone_to_risk(self, tone):
        """Map tone to risk score (from your notebook)"""
        tone_scores = {
            'very_negative': 1.0, 'negative': 0.8, 'critical': 0.7,
            'skeptical': 0.6, 'neutral': 0.3, 'positive': 0.1,
            'very_positive': 0.0, 'supportive': 0.0, 'praising': 0.0
        }
        return tone_scores.get(tone.lower(), 0.3)
    
    def cleanup(self):
        """Clean up temporary directories"""
        import shutil
        for temp_dir in self._temp_dirs:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except:
                pass
        self._temp_dirs.clear()

# NO GLOBAL INSTANCE - ONLY CREATE WHEN NEEDED
def get_ml_service():
    """Get ML service instance - creates it only when needed"""
    return MLInferenceService()
