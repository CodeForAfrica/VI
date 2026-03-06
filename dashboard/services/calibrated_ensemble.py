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
from dashboard.services.calibrators import VennAbersStrategicCalibrator

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
            probabilities = calibrated_probs # Update probabilities to calibrated ones

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

        # Save label encoder using skops if possible, fallback to pickle
        label_enc_path_pkl = os.path.join(save_dir, 'label_encoder.pkl')
        label_enc_path_skops = os.path.join(save_dir, 'label_encoder.skops')

        try:
            logger.info(f"Saving LabelEncoder using skops to {label_enc_path_skops}...")
            sio.dump(self.ensemble.label_encoder, label_enc_path_skops)
            logger.info(f"✅ LabelEncoder saved using skops.")
        except Exception as e_skops:
            logger.warning(f"Skops save failed for LabelEncoder: {e_skops}. Falling back to pickle.")
            try:
                logger.info(f"Saving LabelEncoder using pickle to {label_enc_path_pkl}...")
                with open(label_enc_path_pkl, 'wb') as f:
                    pickle.dump(self.ensemble.label_encoder, f)
                logger.info(f"✅ LabelEncoder saved using pickle.")
            except Exception as e_pickle:
                logger.error(f"Pickle save also failed for LabelEncoder: {e_pickle}")
                raise # Propagate error if both fail

        # Save calibrator using its own save method (likely uses pickle)
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

        # Load label encoder using skops first, then fallback to pickle
        label_enc_path_skops = os.path.join(save_dir, 'label_encoder.skops')
        label_enc_path_pkl = os.path.join(save_dir, 'label_encoder.pkl')

        label_encoder = None

        if os.path.exists(label_enc_path_skops):
            logger.info(f"Loading LabelEncoder from {label_enc_path_skops} using skops...")
            try:
                # Attempt to load with skops, specifying trusted types if needed
                # Common types for sklearn components: 'builtins.type', 'numpy.dtype', 'numpy.ndarray',
                # 'sklearn.preprocessing._label.LabelEncoder', etc.
                # It's safest to use sio.get_untrusted_types on your saved file to see what's needed.
                # trusted_types = sio.get_untrusted_types(file=label_enc_path_skops)
                # print("Trusted types needed:", trusted_types)
                # Then use them: trusted=trusted_types
                # For now, using a broad default for LabelEncoder, refine as needed.
                trusted_types = [
                    "builtins.type", "numpy.dtype", "numpy.ndarray",
                    "sklearn.preprocessing._label.LabelEncoder",
                    # Add other types reported by get_untrusted_types if necessary
                ]
                label_encoder = sio.load(label_enc_path_skops, trusted=trusted_types("✅ LabelEncoder loaded using skops.")
            except Exception as e_skops:
                logger.warning(f"Skops load failed for LabelEncoder ({e_skops}), falling back to pickle.")

        if label_encoder is None and os.path.exists(label_enc_path_pkl):
            logger.info(f"Loading LabelEncoder from {label_enc_path_pkl} using pickle...")
            try:
                with open(label_enc_path_pkl, 'rb') as f:
                    label_encoder = pickle.load(f)
                logger.info("✅ LabelEncoder loaded using pickle.")
            except Exception as e_pickle:
                logger.error(f"Failed to load LabelEncoder with pickle: {e_pickle}")
                raise # Or handle more gracefully if possible

        if label_encoder is None:
            raise FileNotFoundError("Neither label_encoder.skops nor label_encoder.pkl found in the model directory.")

        # Get number of labels from the loaded label encoder
        num_labels = len(label_encoder.classes_)

        # Load models ONE AT A TIME and keep them on CPU
        models = []
        tokenizers = []

        for i in range(info['num_models']):
            print(f"  Loading model {i+1}/{info['num_models']}...", end='\r')
            model_dir = os.path.join(save_dir, f'ensemble_model_{i}')

            # Load tokenizer from LOCAL directory
            tokenizer = AutoTokenizer.from_pretrained(model_dir, use_fast=False) # Use local path

            # Get PEFT config
            from peft import PeftConfig
            peft_config = PeftConfig.from_pretrained(model_dir)

            # Determine base model path (assuming local base model is preferred)
            base_model_local_path = os.path.join(save_dir, 'microsoft_deberta-v3-large') # Or the specific name used during saving
            base_model_name = base_model_local_path if os.path.exists(base_model_local_path) else peft_config.base_model_name_or_path
            print(f"  Using base model: {base_model_name}")

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
                torch_dtype=torch.float32,
                low_cpu_mem_usage=True
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
            logger.info(f"Loading calibrator from {calibrator_path}...")
            try:
                # Attempt to load calibrator using its own method (which likely uses pickle internally)
                # This might still face version issues if the calibrator object itself contains incompatible scikit-learn parts
                # For now, we just wrap it in a try-catch.
                # If VennAbersStrategicCalibrator.load uses skops internally or has a skops-compatible save/load, update that class too.
                calibrator = VennAbersStrategicCalibrator.load(calibrator_path)
                logger.info(f"✅ Calibrator loaded.")
            except Exception as e_cal:
                 logger.error(f"Failed to load calibrator: {e_cal}")
                 # Depending on your tolerance for running without calibration:
                 # Option 1: Raise the error
                 # raise
                 # Option 2: Continue without calibrator (set calibrator to a default instance or None)
                 calibrator = VennAbersStrategicCalibrator() # Or handle differently
                 logger.warning("Continuing without calibrator due to load error.")
        else:
            logger.warning(f"Calibrator file {calibrator_path} not found.")

        print(f"✅ Ensemble loaded successfully")
        return cls(ensemble, calibrator)
