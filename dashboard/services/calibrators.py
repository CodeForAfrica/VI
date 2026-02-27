import pickle
import numpy as np

class ProbabilitiesEstimator:
    """Helper class for Venn-Abers calibration"""
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


class VennAbersStrategicCalibrator:
    """Venn-Abers calibrator for strategic intent"""
    def __init__(self):
        self.va_multi = None
        self.calibrated = False
    
    def fit(self, probabilities, labels):
        """Fit Venn-Abers calibrator"""
        try:
            from venn_abers import VennAbersMultiClass
            import venn_abers.venn_abers
            from sklearn.model_selection import train_test_split as sklearn_tts
            
            def custom_tts(*args, **kwargs):
                if 'shuffle' in kwargs and kwargs['shuffle'] is None:
                    kwargs['shuffle'] = True
                return sklearn_tts(*args, **kwargs)
            venn_abers.venn_abers.train_test_split = custom_tts
            
            # Use the module-level class
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
        
        if isinstance(calibrated_probs, tuple):
            if len(calibrated_probs) >= 2:
                p0, p1 = calibrated_probs[:2]
                calibrated_probs = p1 / (p0 + p1 + 1e-15)
        
        if isinstance(calibrated_probs, np.ndarray) and calibrated_probs.ndim == 2:
            calibrated_probs = calibrated_probs / calibrated_probs.sum(axis=1, keepdims=True)
        
        return calibrated_probs
    
    def save(self, save_path):
        """Save calibrator"""
        if self.calibrated:
            with open(save_path, 'wb') as f:
                pickle.dump(self.va_multi, f)
            print(f"✅ Venn-Abers calibrator saved to {save_path}")
    
    @staticmethod
    def load(load_path):
        """Load calibrator - static method to avoid pickle import issues"""
        instance = VennAbersStrategicCalibrator()
        with open(load_path, 'rb') as f:
            instance.va_multi = pickle.load(f)
        instance.calibrated = True
        print(f"✅ Venn-Abers calibrator loaded from {load_path}")
        return instance
