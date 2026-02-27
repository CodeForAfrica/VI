import os
import json
import pickle
import numpy as np
import torch
import torch.nn.functional as F
from transformers import AutoTokenizer, AutoModelForSequenceClassification
from sklearn.preprocessing import LabelEncoder
import joblib

class ToneProbabilitiesEstimator:
    """Helper class for tone calibration"""
    def __init__(self):
        self.classes_ = None
        self.n_classes_ = None
    
    def fit(self, X, y):
        self.classes_ = np.unique(y)
        self.n_classes_ = len(self.classes_)
        return self
    
    def predict_proba(self, X):
        return X
    
    def predict(self, X):
        return np.argmax(X, axis=1)
class StackedEnsemble:
    """Simplified Stacked Ensemble for inference only (from your notebook)"""
    def __init__(self, base_models, tokenizers, label_encoder, meta_model, device=None):
        self.base_models = base_models
        self.tokenizers = tokenizers
        self.label_encoder = label_encoder
        self.meta_model = meta_model
        self.num_classes = len(label_encoder.classes_)
        self.device = device or torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.is_fitted = True  # We're loading a trained ensemble
        
        # Move all models to device
        for model in self.base_models:
            model.to(self.device)
            model.eval()
        print(f"✅ Stacked Ensemble Initialized")
        print(f"  Base models: {len(self.base_models)}")
        print(f"  Meta-model: {type(self.meta_model).__name__}")
        print(f"  Device: {self.device}")

    def _get_base_predictions(self, texts, batch_size=8):
        """Get predictions from all base models"""
        all_model_probs = []
        for model_idx, (model, tokenizer) in enumerate(zip(self.base_models, self.tokenizers)):
            model_probs = []
            for i in range(0, len(texts), batch_size):
                batch_texts = texts[i:i+batch_size]
                inputs = tokenizer(
                    batch_texts,
                    truncation=True,
                    padding=True,
                    max_length=512,
                    return_tensors="pt"
                ).to(self.device)
                with torch.no_grad():
                    outputs = model(**inputs)
                    batch_probs = F.softmax(outputs.logits, dim=-1)
                    model_probs.append(batch_probs.cpu().numpy())
            model_probs = np.vstack(model_probs)
            all_model_probs.append(model_probs)
        
        # Stack horizontally: (n_samples, n_models * n_classes)
        meta_features = np.hstack(all_model_probs)
        return meta_features

    def predict_proba(self, texts, batch_size=8):
        """Get probability predictions"""
        if len(texts) == 0:
            return np.zeros((0, self.num_classes))
        meta_features = self._get_base_predictions(texts, batch_size)
        return self.meta_model.predict_proba(meta_features)

    def predict(self, texts, batch_size=8, return_probs=True):
        """Get predictions and probabilities"""
        meta_features = self._get_base_predictions(texts, batch_size)
        predictions = self.meta_model.predict(meta_features)
        if return_probs:
            probabilities = self.meta_model.predict_proba(meta_features)
            return predictions, probabilities
        return predictions

class VennAbersCalibrator:
    """Venn-Abers calibrator for tone ensemble (from your notebook)"""
    def __init__(self):
        self.va_multi = None
        self.calibrated = False

    def fit(self, probabilities, labels):
        try:
            from venn_abers import VennAbersMultiClass
            import venn_abers.venn_abers
            from sklearn.model_selection import train_test_split as sklearn_tts
            
            # PATCH: Fix for sklearn InvalidParameterError (shuffle=None) in venn-abers library
            def custom_tts(*args, **kwargs):
                if 'shuffle' in kwargs and kwargs['shuffle'] is None:
                    kwargs['shuffle'] = True
                return sklearn_tts(*args, **kwargs)
            venn_abers.venn_abers.train_test_split = custom_tts            
            estimator = ToneProbabilitiesEstimator()
            estimator.fit(probabilities, labels)
            
            self.va_multi = VennAbersMultiClass(estimator=estimator, inductive=True)
            self.va_multi.fit(probabilities, labels)
            self.calibrated = True
            print(f"✅ Venn-Abers calibrator fitted on {len(probabilities)} samples")
            return self
        except Exception as e:
            print(f"❌ Venn-Abers calibration failed: {e}")
            return self

    def calibrate(self, probabilities):
        """Calibrate probabilities"""
        if not self.calibrated or self.va_multi is None:
            return probabilities
        calibrated_probs = self.va_multi.predict_proba(probabilities)
        
        # Handle output format
        if isinstance(calibrated_probs, tuple):
            if len(calibrated_probs) >= 2:
                p0, p1 = calibrated_probs[:2]
                calibrated_probs = p1 / (p0 + p1 + 1e-15)
        
        # Ensure probabilities sum to 1
        if isinstance(calibrated_probs, np.ndarray) and calibrated_probs.ndim == 2:
            calibrated_probs = calibrated_probs / calibrated_probs.sum(axis=1, keepdims=True)
        
        return calibrated_probs

    def save(self, save_path):
        """Save calibrator"""
        if self.calibrated:
            with open(save_path, 'wb') as f:
                pickle.dump(self.va_multi, f)
            print(f"✅ Venn-Abers calibrator saved to {save_path}")

    def load(self, load_path):
        """Load calibrator"""
        with open(load_path, 'rb') as f:
            self.va_multi = pickle.load(f)
        self.calibrated = True
        print(f"✅ Venn-Abers calibrator loaded from {load_path}")
        return self

class CalibratedStackedEnsemble:
    """
    Complete calibrated ensemble that can be saved/loaded (from your notebook)
    """
    def __init__(self, ensemble, calibrator):
        self.ensemble = ensemble
        self.calibrator = calibrator

    def predict_proba(self, texts, calibrated=True, batch_size=8):
        """Get probabilities (calibrated or not)"""
        probs = self.ensemble.predict_proba(texts, batch_size)
        if calibrated and self.calibrator.calibrated:
            probs = self.calibrator.calibrate(probs)
        return probs

    def predict(self, texts, calibrated=True, batch_size=8):
        """Get predicted classes"""
        probs = self.predict_proba(texts, calibrated, batch_size)
        return np.argmax(probs, axis=1)

    def save(self, save_dir):
        """Save entire calibrated ensemble"""
        os.makedirs(save_dir, exist_ok=True)
        
        # Save base models
        for i, (model, tokenizer) in enumerate(zip(self.ensemble.base_models, self.ensemble.tokenizers)):
            model_dir = os.path.join(save_dir, f'base_model_{i}')
            os.makedirs(model_dir, exist_ok=True)
            model.save_pretrained(model_dir)
            tokenizer.save_pretrained(model_dir)
        
        # Save meta-model
        joblib.dump(self.ensemble.meta_model, os.path.join(save_dir, 'meta_model.pkl'))
        
        # Save label encoder info
        label_info = {
            'classes': self.ensemble.label_encoder.classes_.tolist()
        }
        with open(os.path.join(save_dir, 'label_info.json'), 'w') as f:
            json.dump(label_info, f)
        
        # Save calibrator
        if self.calibrator.calibrated:
            self.calibrator.save(os.path.join(save_dir, 'venn_abers_calibrator.pkl'))
        
        print(f"✅ Calibrated ensemble saved to {save_dir}")

    @classmethod
    def load(cls, load_dir):
        """Load calibrated ensemble"""
        # Find base models
        import glob
        base_model_patterns = glob.glob(os.path.join(load_dir, 'base_model_*'))
        base_model_dirs = sorted([os.path.basename(d) for d in base_model_patterns])
        
        base_models = []
        tokenizers = []
        for model_dir in base_model_dirs:
            full_path = os.path.join(load_dir, model_dir)
            tokenizer = AutoTokenizer.from_pretrained(full_path)
            model = AutoModelForSequenceClassification.from_pretrained(full_path)
            model.eval()
            tokenizers.append(tokenizer)
            base_models.append(model)
        
        # Load meta-model
        meta_model = joblib.load(os.path.join(load_dir, 'meta_model.pkl'))
        
        # Create label encoder
        with open(os.path.join(load_dir, 'label_info.json'), 'r') as f:
            label_info = json.load(f)
        label_encoder = LabelEncoder()
        label_encoder.classes_ = np.array(label_info['classes'])
        
        # Create ensemble
        ensemble = StackedEnsemble(base_models, tokenizers, label_encoder, meta_model)
        
        # Load calibrator
        calibrator = VennAbersCalibrator()
        calibrator_path = os.path.join(load_dir, 'venn_abers_calibrator.pkl')
        if os.path.exists(calibrator_path):
            calibrator.load(calibrator_path)
        
        print(f"✅ Calibrated ensemble loaded from {load_dir}")
        return cls(ensemble, calibrator)
