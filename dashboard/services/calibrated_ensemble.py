import os
import json
import pickle
import numpy as np
import torch
import torch.nn.functional as F
from transformers import AutoTokenizer, AutoModelForSequenceClassification
from peft import PeftModel
from sklearn.preprocessing import LabelEncoder
import joblib
from dashboard.services.calibrated_ensemble import VennAbersStrategicCalibrator

class VennAbersStrategicCalibrator:
    """Venn-Abers calibrator for strategic intent (from your notebook)"""
    def __init__(self):
        self.va_multi = None
        self.calibrated = False
    
    def fit(self, probabilities, labels):
        """Fit Venn-Abers calibrator"""
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
            
            # Create an estimator wrapper for your pre-computed probabilities
            class ProbabilitiesEstimator:
                def __init__(self):
                    self.classes_ = None
                    self.n_classes_ = None
                def fit(self, X, y):
                    # X is ignored (we already have probabilities)
                    # We just store the class labels
                    self.classes_ = np.unique(y)
                    self.n_classes_ = len(self.classes_)
                    return self
                def predict_proba(self, X):
                    """Return pre-computed probabilities."""
                    return X
                def predict(self, X):
                    probs = self.predict_proba(X)
                    return np.argmax(probs, axis=1)
            
            estimator = ProbabilitiesEstimator()
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

class StrategicEnsemble:
    """
    Ensemble for strategic intent classification (supports PEFT models)
    MEMORY EFFICIENT: Keeps models on CPU, moves to GPU only during inference
    """
    def __init__(self, models, tokenizers, label_encoder, device=None):
        self.models = models
        self.tokenizers = tokenizers
        self.label_encoder = label_encoder
        self.device = device or torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        
        # IMPORTANT: Keep models on CPU to save memory
        # They will be moved to GPU one at a time during inference
        for model in self.models:
            model.to('cpu')
            model.eval()
    
    def predict(self, texts, batch_size=1, voting='soft', return_logits=False):
        """
        Predict using ensemble (memory efficient)
        Moves models to GPU one at a time
        """
        all_probs = []
        all_logits = []
        
        # Process each model separately to avoid OOM
        for model_idx, (model, tokenizer) in enumerate(zip(self.models, self.tokenizers)):
            print(f"  Processing model {model_idx + 1}/{len(self.models)}...", end='\r')
            
            # Move this model to GPU
            model.to(self.device)
            model.eval()
            
            model_probs = []
            model_logits = []
            
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
                    batch_probs = torch.softmax(outputs.logits, dim=-1)
                    model_probs.append(batch_probs.cpu())
                    model_logits.append(outputs.logits.cpu())
            
            model_probs = torch.cat(model_probs, dim=0)
            all_probs.append(model_probs)
            model_logits = torch.cat(model_logits, dim=0)
            all_logits.append(model_logits)
            
            # Move model back to CPU to free GPU memory
            model.to('cpu')
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        
        print()  # New line after progress
        
        all_probs = torch.stack(all_probs)  # [n_models, n_samples, n_classes]
        all_logits = torch.stack(all_logits)
        avg_logits = torch.mean(all_logits, dim=0)
        
        if return_logits:
            return avg_logits.numpy()
        elif voting == 'soft':
            avg_probs = torch.mean(all_probs, dim=0)
            predictions = torch.argmax(avg_probs, dim=-1)
        elif voting == 'hard':
            model_preds = torch.argmax(all_probs, dim=-1)
            predictions = torch.mode(model_preds, dim=0).values
            avg_probs = torch.mean(all_probs, dim=0)
        else:
            raise ValueError(f"Unknown voting method: {voting}")
        
        return predictions.numpy(), avg_probs.numpy()

class CalibratedStrategicClassifier:
    """Wrapper combining ensemble + calibration"""
    def __init__(self, ensemble, calibrator=None):
        self.ensemble = ensemble
        self.calibrator = calibrator or VennAbersStrategicCalibrator()
    
    def predict(self, texts, batch_size=1, calibrated=True, return_probs=False):
        """Make predictions with optional calibration"""
        predictions, probabilities = self.ensemble.predict(
            texts, batch_size=batch_size
        )
        
        if calibrated and self.calibrator.calibrated:
            calibrated_probs = self.calibrator.calibrate(probabilities)
            predictions = np.argmax(calibrated_probs, axis=1)
            probabilities = calibrated_probs
        
        if return_probs:
            return predictions, probabilities
        return predictions
    
    def save(self, save_dir):
        os.makedirs(save_dir, exist_ok=True)
        
        # Save each model
        for i, (model, tokenizer) in enumerate(zip(self.ensemble.models, self.ensemble.tokenizers)):
            model_dir = os.path.join(save_dir, f'ensemble_model_{i}')
            os.makedirs(model_dir, exist_ok=True)
            # Save PEFT model (this saves both adapter and config)
            model.save_pretrained(model_dir)
            tokenizer.save_pretrained(model_dir)
        
        # Save label encoder
        with open(os.path.join(save_dir, 'label_encoder.pkl'), 'wb') as f:
            pickle.dump(self.ensemble.label_encoder, f)
        
        # Save calibrator
        if self.calibrator.calibrated:
            self.calibrator.save(os.path.join(save_dir, 'calibrator.pkl'))
        
        # Save metadata with num_labels
        ensemble_info = {
            'num_models': len(self.ensemble.models),
            'num_labels': len(self.ensemble.label_encoder.classes_),
            'label_classes': self.ensemble.label_encoder.classes_.tolist(),
            'calibrator_type': type(self.calibrator).__name__,
            'has_calibrator': self.calibrator.calibrated,
            'training_method': 'contrastive_peft',
            'contrastive_loss': 'supcon'
        }
        
        with open(os.path.join(save_dir, 'ensemble_info.json'), 'w') as f:
            json.dump(ensemble_info, f, indent=2)
        print(f"✅ Calibrated strategic ensemble saved to {save_dir}")
    
    @classmethod
    def load(cls, save_dir, device=None):
        """Load calibrated ensemble (MEMORY EFFICIENT)"""
        print(f"\n📂 Loading calibrated ensemble from {save_dir}")
        
        # Clear GPU memory before loading
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        
        # Load metadata
        with open(os.path.join(save_dir, 'ensemble_info.json'), 'r') as f:
            info = json.load(f)
        print(f"  Models: {info['num_models']}")
        print(f"  Classes: {info['num_labels']}")
        
        # Load label encoder
        with open(os.path.join(save_dir, 'label_encoder.pkl'), 'rb') as f:
            label_encoder = pickle.load(f)
        
        # Get number of labels from label encoder
        num_labels = len(label_encoder.classes_)
        
        # Load models ONE AT A TIME and keep them on CPU
        models = []
        tokenizers = []
        
        for i in range(info['num_models']):
            print(f"  Loading model {i+1}/{info['num_models']}...", end='\r')
            model_dir = os.path.join(save_dir, f'ensemble_model_{i}')
            
            # Load tokenizer
            tokenizer = AutoTokenizer.from_pretrained(model_dir)
            
            # Load the PEFT config to get base model name
            from peft import PeftConfig
            peft_config = PeftConfig.from_pretrained(model_dir)
            base_model_name = peft_config.base_model_name_or_path
            
            # Load base model config with correct num_labels
            from transformers import AutoConfig
            config = AutoConfig.from_pretrained(
                base_model_name,
                num_labels=num_labels
            )
            
            # Load base model ON CPU to save GPU memory
            from transformers import AutoModelForSequenceClassification
            base_model = AutoModelForSequenceClassification.from_pretrained(
                base_model_name,
                config=config,
                ignore_mismatched_sizes=True,
                torch_dtype=torch.float32,  # Use float32 for better compatibility
                low_cpu_mem_usage=True      # Memory efficient loading
            )
            
            # Load PEFT adapter on top
            from peft import PeftModel
            model = PeftModel.from_pretrained(base_model, model_dir)
            
            # Keep model on CPU
            model.to('cpu')
            model.eval()
            
            models.append(model)
            tokenizers.append(tokenizer)
            
            # Clear cache after each model
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        
        print()  # New line after progress
        
        # Create ensemble (models stay on CPU)
        ensemble = StrategicEnsemble(models, tokenizers, label_encoder, device=device)
        
        # Load calibrator
        calibrator = None
        calibrator_path = os.path.join(save_dir, 'calibrator.pkl')
        if os.path.exists(calibrator_path):
            with open(calibrator_path, 'rb') as f:
                save_dict = pickle.load(f)
            # Check if it's a HybridCalibrator
            if isinstance(save_dict, dict) and 'method' in save_dict:
                from dashboard.services.hybrid_calibrator import HybridCalibrator
                calibrator = HybridCalibrator.load(calibrator_path)
                print(f"✅ Hybrid calibrator loaded")
            else:
                # Legacy VennAbersStrategicCalibrator
                calibrator = VennAbersStrategicCalibrator.load(calibrator_path)
                print(f"✅ Venn-Abers calibrator loaded")
        else:
            # Fallback to old path for backward compatibility
            old_calibrator_path = os.path.join(save_dir, 'venn_abers_calibrator.pkl')
            if os.path.exists(old_calibrator_path):
                calibrator = VennAbersStrategicCalibrator.load(old_calibrator_path)
                print(f"✅ Calibrator loaded (legacy)")
        
        print(f"✅ Ensemble loaded successfully")
        return cls(ensemble, calibrator)
