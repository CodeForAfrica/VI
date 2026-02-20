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
                        continue
                    
                    rel_path = key[len(s3_prefix):].lstrip('/')
                    local_file_path = os.path.join(local_dir, rel_path)
                    
                    os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
                    self.s3_client.download_file(self.bucket_name, key, local_file_path)
            
            return True
        except Exception as e:
            logger.error(f"Error downloading directory {s3_prefix}: {e}")
            return False

    def _load_strategic_classifier(self):
        """Load calibrated strategic classifier from S3"""
        if 'strategic' in self._model_cache:
            return self._model_cache['strategic']
        
        temp_dir = tempfile.mkdtemp(prefix='strategic_model_')
        self._temp_dirs.add(temp_dir)
        
        if not self._download_directory_from_s3('models/calibrated_contrastive_peft/', temp_dir):
            raise Exception("Failed to download strategic classifier from S3")
        
        from dashboard.services.calibrated_ensemble import CalibratedStrategicClassifier
        classifier = CalibratedStrategicClassifier.load(temp_dir)
        
        self._model_cache['strategic'] = classifier
        
        # Load label encoder
        label_enc_path = os.path.join(temp_dir, 'label_encoder.pkl')
        if os.path.exists(label_enc_path):
            with open(label_enc_path, 'rb') as f:
                self._strategic_label_encoder = pickle.load(f)
        
        return classifier

    def _load_tone_classifier(self):
        """Load calibrated tone classifier from S3"""
        if 'tone' in self._model_cache:
            return self._model_cache['tone']
        
        temp_dir = tempfile.mkdtemp(prefix='tone_model_')
        self._temp_dirs.add(temp_dir)
        
        if not self._download_directory_from_s3('models/calibrated_stacked_ensemble/', temp_dir):
            raise Exception("Failed to download tone classifier from S3")
        
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

    def is_low_resource_lang(self, lang_code):
        """Check if language is low resource"""
        lowres_langs = ['ha','yo','am','sw','wo','ig','ff','pt','bm','mg','zu']
        return lang_code in lowres_langs

    def perform_strategic_intent_inference(self, article_text):
        """Perform strategic intent inference"""
        try:
            classifier = self._load_strategic_classifier()
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
        """Perform tone inference"""
        try:
            classifier = self._load_tone_classifier()
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
        Calculate vulnerability index using ONLY your pre-calculated CSV scores.
        Main intent categories: Economic, Sovereignty, LGBTQ, Religious, MilitaryPresence, ResourceDependency, SocialFragility
        """
        try:
            df = self._csv_risk_df
            if df.empty:
                logger.warning("CSV risk data not loaded — using fallback")
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
                "iran": "Iran"
            }

            # Map ML-predicted intent to CSV intent format (7 main categories only)
            intent_mapping = {
                # Economic
                'economic dependency': 'Economic',
                'economic': 'Economic',
                'resource dependency': 'ResourceDependency',
                'resourcedependency': 'ResourceDependency',
                # Sovereignty
                'sovereignty': 'Sovereignty',
                'democratic interference': 'Sovereignty',
                'election influence': 'Sovereignty',
                'election': 'Sovereignty',
                # Identity
                'lgbtq': 'LGBTQ',
                'lgbt': 'LGBTQ',
                'sexual orientation': 'LGBTQ',
                'religious': 'Religious',
                'religion': 'Religious',
                # Security
                'military presence': 'MilitaryPresence',
                'military': 'MilitaryPresence',
                'defense': 'MilitaryPresence',
                # Fragility
                'social fragility': 'SocialFragility',
                'social': 'SocialFragility',
                'ethnic': 'SocialFragility',
                'sectarian': 'SocialFragility',
                # Default fallback
                'unknown': 'Unknown',
                'neutral': 'Unknown'
            }

            formatted_country = country_mapping.get(target_country.lower(), target_country)
            formatted_actor = actor_mapping.get(inferred_actor.lower(), inferred_actor)
            formatted_intent = intent_mapping.get(strategic_intent.lower(), strategic_intent)

            # Ensure we only use the 7 main intents
            main_intents = {"Economic", "Sovereignty", "LGBTQ", "Religious", "MilitaryPresence", "ResourceDependency", "SocialFragility"}
            if formatted_intent not in main_intents:
                # If not a main intent, try to find any match for this country-actor pair
                logger.warning(f"Intent '{formatted_intent}' not in main intents. Falling back to country-actor average.")
                matching_rows = df[
                    (df['country'] == formatted_country) &
                    (df['actor'] == formatted_actor)
                ]
                if not matching_rows.empty:
                    avg_risk = matching_rows['FinalRisk'].mean()
                    return min(avg_risk * 0.7 + confidence * 0.3, 1.0)
                else:
                    return self._calculate_fallback_vulnerability_index(strategic_intent, tone, confidence)

            # Exact match lookup
            matching_row = df[
                (df['country'] == formatted_country) &
                (df['actor'] == formatted_actor) &
                (df['intent'] == formatted_intent)
            ]

            if not matching_row.empty:
                csv_risk_score = float(matching_row.iloc[0]['FinalRisk'])
                # Weight: 80% CSV (contextual), 20% ML confidence
                return min(csv_risk_score * 0.8 + confidence * 0.2, 1.0)

            # Fallback: nearest match by country-actor pair
            country_actor_matches = df[
                (df['country'] == formatted_country) &
                (df['actor'] == formatted_actor)
            ]
            if not country_actor_matches.empty:
                avg_risk = country_actor_matches['FinalRisk'].mean()
                return min(avg_risk * 0.7 + confidence * 0.3, 1.0)

            # Final fallback: use ML-only score
            logger.warning(f"No CSV match for {formatted_country}-{formatted_actor}-{formatted_intent}")
            return self._calculate_fallback_vulnerability_index(strategic_intent, tone, confidence)

        except Exception as e:
            logger.error(f"Error in calculate_vulnerability_index: {e}")
            return self._calculate_fallback_vulnerability_index(strategic_intent, tone, confidence)

    def _calculate_fallback_vulnerability_index(self, strategic_intent, tone, confidence):
        """Fallback: simple weighted combination of intent/tone/confidence"""
        # Intent scores (for main 7 categories)
        intent_scores = {
            'Economic': 0.6, 'Sovereignty': 0.8, 'LGBTQ': 0.4,
            'Religious': 0.5, 'MilitaryPresence': 0.7,
            'ResourceDependency': 0.6, 'SocialFragility': 0.9,
            'unknown': 0.3, 'neutral': 0.3
        }
        # Tone scores
        tone_scores = {
            'very_negative': 1.0, 'negative': 0.8, 'critical': 0.7,
            'skeptical': 0.6, 'neutral': 0.3, 'positive': 0.1,
            'very_positive': 0.0, 'supportive': 0.0, 'praising': 0.0
        }

        intent_score = intent_scores.get(strategic_intent, 0.3)
        tone_score = tone_scores.get(tone, 0.3)

        # Weight: 40% intent, 30% tone, 30% confidence
        return min(intent_score * 0.4 + tone_score * 0.3 + confidence * 0.3, 1.0)

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
