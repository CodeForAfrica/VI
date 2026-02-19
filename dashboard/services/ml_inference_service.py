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
import pandas as pd # Added for CSV handling

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
        self._strategic_label_encoder = None
        self._tone_label_encoder = None
        self._strategic_vocab = None
        self._csv_risk_df = None # Cache for the CSV risk data
        self._load_csv_risks() # Load CSV data once during initialization

    def _load_csv_risks(self):
        """Load the pre-calculated risk scores from the CSV file."""
        try:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            # Assuming the CSV is in the parent directory (project root)
            csv_file_path = os.path.join(current_dir, '..', 'final_risk_by_actor_intent_country.csv')
            
            # Also check the alternative name if the primary one doesn't exist
            if not os.path.exists(csv_file_path):
                 csv_file_path = os.path.join(current_dir, '..', 'final_risk_by_actor_intent_country (1).csv')
            
            if os.path.exists(csv_file_path):
                self._csv_risk_df = pd.read_csv(csv_file_path)
                logger.info(f"Loaded CSV risk data from {csv_file_path}. Shape: {self._csv_risk_df.shape}")
            else:
                logger.warning(f"CSV risk file not found at {csv_file_path} or alternative name.")
                self._csv_risk_df = pd.DataFrame() # Initialize as empty DataFrame

        except Exception as e:
            logger.error(f"Error loading CSV risk data: {e}")
            self._csv_risk_df = pd.DataFrame() # Initialize as empty DataFrame on error


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
        """Return None since we're using pre-calculated CSV scores instead"""
        logger.info("Using pre-calculated CSV scores instead of contextual module")
        return None
        # --- OLD CODE ---
        # try:
        #     # Download contextual module from S3 - USE CORRECT PATH
        #     response = self.s3_client.get_object(
        #         Bucket=self.bucket_name,
        #         Key='contextual_all_intents_v2.py'  # SIMPLIFIED PATH - JUST THE FILE NAME
        #     )
        #     
        #     # Save temporarily and import
        #     with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        #         f.write(response['Body'].read().decode('utf-8'))
        #         temp_py_path = f.name
        #     
        #     spec = importlib.util.spec_from_file_location('contextual_mod', temp_py_path)
        #     contextual_mod = importlib.util.module_from_spec(spec)
        #     spec.loader.exec_module(contextual_mod)
        #     
        #     return contextual_mod
        # except Exception as e:
        #     logger.warning(f"S3 contextual file not found, trying local: {e}")
        #     # Try to load from local file
        #     try:
        #         import os
        #         current_dir = os.path.dirname(os.path.abspath(__file__))
        #         local_file = os.path.join(current_dir, '..', '..', 'contextual_all_intents_v2.py')
        #         
        #         if os.path.exists(local_file):
        #             spec = importlib.util.spec_from_file_location('contextual_mod', local_file)
        #             contextual_mod = importlib.util.module_from_spec(spec)
        #             spec.loader.exec_module(contextual_mod)
        #             return contextual_mod
        #     except Exception as local_error:
        #         logger.error(f"Local contextual file also failed: {local_error}")
        #     return None
    
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
        """Calculate vulnerability index using pre-calculated CSV scores instead of contextual module"""
        try:
            # Load your pre-calculated CSV file - using the cached DataFrame
            df = self._csv_risk_df

            if df.empty:
                logger.warning("CSV risk data is empty, using fallback calculation")
                # Fallback to simple calculation if CSV is not available
                return self._calculate_fallback_vulnerability_index(strategic_intent, tone, confidence)

            # Normalize inputs to match CSV format
            country_mapping = {
                "south africa": "South Africa",
                "senegal": "Senegal", 
                "drc": "DRC",
                "cote d'ivoire": "CoteIvoire",
                "cote ivoire": "CoteIvoire",
                "ivory coast": "CoteIvoire",
                "ethiopia": "Ethiopia"
            }

            actor_mapping = {
                "uae": "UAE",
                "china": "China",
                "france": "France",
                "us": "UnitedStates",
                "united states": "UnitedStates",
                "russia": "Russia",
                "saudi": "Saudi",
                "turkey": "Turkey",
                "israel": "Israel",
                "iran": "Iran",
                "rwanda": "Rwanda",
                "nonstate": "NonState"
            }

            intent_mapping = {
                'economic dependency': 'Economic',
                'economic': 'Economic',
                'sovereignty': 'Sovereignty',
                'lgbtq': 'LGBTQ',
                'religious': 'Religious',
                'military presence': 'MilitaryPresence',
                'military': 'MilitaryPresence',
                'resource dependency': 'ResourceDependency',
                'social fragility': 'SocialFragility',
                'social': 'SocialFragility',
                'election influence': 'ElectionInfluence',
                'election': 'ElectionInfluence',
            }

            formatted_country = country_mapping.get(target_country.lower(), target_country)
            formatted_actor = actor_mapping.get(inferred_actor.lower(), inferred_actor)
            formatted_intent = intent_mapping.get(strategic_intent.lower(), strategic_intent)

            # Look up the pre-calculated risk score from CSV
            matching_row = df[
                (df['country'] == formatted_country) &
                (df['actor'] == formatted_actor) &
                (df['intent'] == formatted_intent)
            ]

            if not matching_row.empty:
                # Get the pre-calculated FinalRisk score from your CSV
                csv_risk_score = float(matching_row.iloc[0]['FinalRisk'])

                # Weight the CSV score with the ML confidence
                # The CSV score is the main contextual risk, ML confidence adds a small adjustment
                weighted_score = (
                    csv_risk_score * 0.8 +  # 80% weight to pre-calculated CSV score
                    confidence * 0.2         # 20% weight to ML confidence
                )

                return min(weighted_score, 1.0)

            else:
                # If no exact match found, try to find the closest match
                # First, find all scores for this country-actor pair regardless of intent
                country_actor_matches = df[
                    (df['country'] == formatted_country) &
                    (df['actor'] == formatted_actor)
                ]

                if not country_actor_matches.empty:
                    # Use the average of all intents for this country-actor pair
                    avg_risk = country_actor_matches['FinalRisk'].mean()
                    weighted_score = (
                        avg_risk * 0.7 +      # 70% weight to average CSV score
                        confidence * 0.3      # 30% weight to ML confidence
                    )
                    return min(weighted_score, 1.0)
                else:
                    # If no country-actor match, use the fallback calculation
                    logger.warning(f"No CSV match found for {target_country}-{inferred_actor}-{strategic_intent}")
                    return self._calculate_fallback_vulnerability_index(strategic_intent, tone, confidence)

        except Exception as e:
            logger.error(f"Error using CSV for vulnerability index: {e}")
            # Fallback to simple calculation if anything goes wrong
            return self._calculate_fallback_vulnerability_index(strategic_intent, tone, confidence)

    def _calculate_fallback_vulnerability_index(self, strategic_intent, tone, confidence):
        """Fallback calculation if CSV is not available"""
        # Map intent and tone to risk scores using your existing mappings
        intent_scores = {
            'hostile': 1.0, 'aggressive': 0.9, 'manipulative': 0.8,
            'deceptive': 0.8, 'misleading': 0.7, 'concerning': 0.6,
            'suspicious': 0.5, 'neutral': 0.3, 'informative': 0.2,
            'positive': 0.1, 'supportive': 0.0,
            # Your specific intent categories
            'economic': 0.6, 'sovereignty': 0.8, 'lgbtq': 0.4,
            'religious': 0.5, 'military': 0.7, 'militarypresence': 0.7,
            'resourcedependency': 0.6, 'socialfragility': 0.9,
            'electioninfluence': 0.8
        }

        tone_scores = {
            'very_negative': 1.0, 'negative': 0.8, 'critical': 0.7,
            'skeptical': 0.6, 'neutral': 0.3, 'positive': 0.1,
            'very_positive': 0.0, 'supportive': 0.0, 'praising': 0.0
        }

        intent_score = intent_scores.get(strategic_intent.lower(), 0.3)
        tone_score = tone_scores.get(tone.lower(), 0.3)

        # Simple weighted combination
        return (intent_score * 0.4 + tone_score * 0.3 + confidence * 0.3)
    
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
