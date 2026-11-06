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


def _softmax(x, axis=-1):
    x = np.asarray(x, dtype=np.float64)
    x = x - x.max(axis=axis, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=axis, keepdims=True)


class TemperatureScalingCalibrator:
    """Temperature scaling: softmax(logits / T).

    Matches the object the fine-tuning notebook saved (attributes
    ``temperature`` - a float or a 1-element torch Parameter - and
    ``calibrated``). T < 1 sharpens probabilities, T > 1 softens them.
    """
    uses_logits = True

    def __init__(self, temperature=1.0):
        self.temperature = temperature
        self.calibrated = True

    @property
    def temperature_value(self):
        t = self.temperature
        if hasattr(t, 'detach'):          # torch tensor / Parameter
            t = t.detach().cpu().reshape(-1)[0].item()
        t = float(t)
        if not np.isfinite(t) or t <= 0:
            raise ValueError(f"Invalid calibration temperature: {t}")
        return t

    def calibrate_logits(self, logits):
        """Preferred path: scale the ensemble's averaged logits."""
        return _softmax(np.asarray(logits) / self.temperature_value, axis=1)

    def calibrate(self, probabilities):
        """Fallback when only probabilities are available.

        softmax(log(p) / T) equals softmax(logits / T) for a single model; for
        an averaged ensemble it is a close approximation.
        """
        p = np.clip(np.asarray(probabilities, dtype=np.float64), 1e-12, 1.0)
        return _softmax(np.log(p) / self.temperature_value, axis=1)


class _RepoUnpickler(pickle.Unpickler):
    """Load calibrators saved from a notebook.

    A notebook records its own classes as ``__main__.<Name>``, which does not
    exist in production. Map those names to the repo's equivalent classes.
    """
    _MAIN_ALIASES = {
        'ProbabilitiesEstimator': ProbabilitiesEstimator,
        'ToneProbabilitiesEstimator': ProbabilitiesEstimator,
        'TemperatureScalingCalibrator': TemperatureScalingCalibrator,
    }

    def find_class(self, module, name):
        if module == '__main__':
            if name in self._MAIN_ALIASES:
                return self._MAIN_ALIASES[name]
            if name == 'VennAbersStrategicCalibrator':
                return VennAbersStrategicCalibrator
        return super().find_class(module, name)


def load_calibrator_pickle(path):
    """pickle.load that understands calibrators saved from a notebook."""
    with open(path, 'rb') as f:
        return _RepoUnpickler(f).load()


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
    
    @classmethod
    def from_saved(cls, obj):
        """Rebuild a calibrator from whatever a calibrator.pkl file contained.

        ``save`` above writes the *bare* VennAbersMultiClass object, not this
        wrapper. Callers check ``.calibrated`` and call ``.calibrate()``, which
        the bare object does not have, so it must be wrapped again on load.
        Accepted inputs:
          * an instance of this class (older pickles)          -> returned as is
          * a dict of attributes with a 'va_multi' entry      -> rebuilt
          * a bare VennAbersMultiClass (what ``save`` writes) -> wrapped
          * {'method': 'temperature', 'calibrator': TemperatureScalingCalibrator}
            or a bare TemperatureScalingCalibrator            -> that object
        """
        # Temperature scaling (saved by the fine-tuning notebook).
        if isinstance(obj, dict) and obj.get('method') == 'temperature':
            obj = obj.get('calibrator')
        if isinstance(obj, TemperatureScalingCalibrator):
            obj.temperature_value          # validates the stored temperature
            obj.calibrated = True
            return obj
        if isinstance(obj, cls):
            if obj.va_multi is not None:
                obj.calibrated = True
            return obj
        instance = cls()
        if isinstance(obj, dict):
            instance.__dict__.update(obj)
            instance.calibrated = instance.va_multi is not None
            return instance
        if not hasattr(obj, 'predict_proba'):
            raise TypeError(
                f"Calibrator file holds {type(obj).__name__}, which is neither a "
                f"{cls.__name__} nor a fitted Venn-Abers object"
            )
        instance.va_multi = obj
        instance.calibrated = True
        return instance

    @classmethod
    def load(cls, load_path):
        """Load a calibrator saved by ``save`` (or an older wrapper pickle)."""
        instance = cls.from_saved(load_calibrator_pickle(load_path))
        if isinstance(instance, TemperatureScalingCalibrator):
            print(f"✅ Temperature-scaling calibrator loaded from {load_path} "
                  f"(T={instance.temperature_value:.4f})")
        else:
            print(f"✅ Venn-Abers calibrator loaded from {load_path}")
        return instance
