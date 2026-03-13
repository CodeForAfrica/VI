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
import logging
import skops.io as sio  

logger = logging.getLogger(__name__)

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
                trusted_types = [
                    "builtins.type", "numpy.dtype", "numpy.ndarray",
                    "sklearn.preprocessing._label.LabelEncoder",
                ]
                label_encoder = sio.load(label_enc_path_skops, trusted=trusted_types)
                logger.info("✅ LabelEncoder loaded using skops.")
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
                raise

        if label_encoder is None:
            raise FileNotFoundError("Neither label_encoder.skops nor label_encoder.pkl found in the model directory.")

        # Get number of labels from the loaded label encoder
        num_labels = len(label_encoder.classes_)

        # - Correct path definitions to avoid NameError ---
        KNOWN_MODELS_DIR_EC2 = "/home/ubuntu/Vulnerability_index_tool/app/models"
        expected_base_model_dir_name = 'microsoft_mdeberta-v3-base' 
        
        # Define paths before checking existence
        base_model_in_save_dir = os.path.join(save_dir, expected_base_model_dir_name)
        base_model_local_path_ec2 = os.path.join(KNOWN_MODELS_DIR_EC2, expected_base_model_dir_name)

        if os.path.exists(base_model_in_save_dir):
            base_model_name = base_model_in_save_dir
            print(f"  ✅ Using downloaded base model from temp: {base_model_name}")
        elif os.path.exists(base_model_local_path_ec2):
            base_model_name = base_model_local_path_ec2
            print(f"  ✅ Using local base model: {base_model_name}")
        else:
            print(f"  ❌ Cannot proceed. Base model {expected_base_model_dir_name} not found in {save_dir} or {KNOWN_MODELS_DIR_EC2}")
            raise FileNotFoundError(f"Required base model {expected_base_model_dir_name} not found.")

        # Load base model config with correct num_labels
        from transformers import AutoConfig
        config = AutoConfig.from_pretrained(
            base_model_name,
            num_labels=num_labels
        )

        # Load base model ON CPU to save GPU memory
        from transformers import AutoModelForSequenceClassification
        shared_base_model = AutoModelForSequenceClassification.from_pretrained(
            base_model_name,
            config=config,
            ignore_mismatched_sizes=True,
            torch_dtype=torch.float32,
            low_cpu_mem_usage=True,
        )
        print(f"  ✅ Shared base model loaded from: {base_model_name}")

        models = []
        tokenizers = []

        for i in range(info['num_models']):
            print(f"  Loading model {i+1}/{info['num_models']}...", end='\r')
            model_dir = os.path.join(save_dir, f'ensemble_model_{i}')
            tokenizer = AutoTokenizer.from_pretrained(model_dir, use_fast=False)

            from peft import PeftModel, PeftConfig
            import copy

            # --- THE FIX: Create a fresh copy of the base for each ensemble member ---
            # This prevents "Multiple Adapters" warnings and head-mismatch errors
            base_model_for_this_adapter = copy.deepcopy(shared_base_model)

            try:
                # Attach adapter to the FRESH copy
                model = PeftModel.from_pretrained(
                    base_model_for_this_adapter,
                    model_dir,
                    is_trainable=False
                )
                print(f"  ✅ Model {i+1} adapter attached successfully.")
            except Exception as e:
                print(f"⚠️  PEFT loading failed for model {i+1}: {e}. Trying fallback...")
                # Fallback: manually inject weights if standard loading fails
                from safetensors.torch import load_file
                adapter_path = os.path.join(model_dir, 'adapter_model.safetensors')
                adapter_state_dict = load_file(adapter_path)
                
                # Filter and load
                model_state_dict = base_model_for_this_adapter.state_dict()
                filtered_state_dict = {k: v for k, v in adapter_state_dict.items() if k in model_state_dict}
                base_model_for_this_adapter.load_state_dict(filtered_state_dict, strict=False)
                
                peft_config = PeftConfig.from_pretrained(model_dir)
                model = PeftModel(base_model_for_this_adapter, peft_config)

            model.to('cpu')
            model.eval()
            models.append(model)
            tokenizers.append(tokenizer)
            
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        print() 

        # Create ensemble
        ensemble = StrategicEnsemble(models, tokenizers, label_encoder, device=device)

        # Load calibrator
        calibrator = None
        calibrator_path = os.path.join(save_dir, 'calibrator.pkl')
        if os.path.exists(calibrator_path):
            logger.info(f"Loading calibrator from {calibrator_path}...")
            try:
                calibrator = VennAbersStrategicCalibrator.load(calibrator_path)
                logger.info(f"✅ Calibrator loaded.")
            except Exception as e_cal:
                logger.error(f"Failed to load calibrator: {e_cal}")
                calibrator = VennAbersStrategicCalibrator()
        else:
            logger.warning(f"Calibrator file {calibrator_path} not found.")

        print(f"✅ Ensemble loaded successfully")
        return cls(ensemble, calibrator)
