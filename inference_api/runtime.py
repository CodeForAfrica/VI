"""Model lifecycle + inference call for the API (solution-spec 637-665).

Models load once, in a background thread started at process boot, so `/healthz`
stays responsive while loading and `/readyz` flips to ready only when the
ensemble is actually usable. A semaphore bounds concurrent inference because the
process runs a single model copy.
"""
import os
import threading
import time
from pathlib import Path

from .logs import log_event, safe_error, safe_traceback

MODEL_VERSION = os.getenv("MODEL_VERSION", "2026-09-04")
ALLOWED_INTENTS = {
    "Economic", "Sovereignty", "LGBTQ", "Religious", "ElectionInfluence",
    "MilitaryPresence", "ResourceDependency", "SocialFragility", "Neutral",
}
MODEL_CONTRACT_ERROR_CODES = {
    "unknown_strategic_intent",
    "unsupported_strategic_intent",
    "invalid_model_result",
    "invalid_strategic_intent_confidence",
    "invalid_tone",
    "invalid_tone_confidence",
    "invalid_confidence",
    "invalid_prediction_source",
}


class InferenceRuntimeError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

# One concurrent inference at a time (single loaded ensemble). threads>1 just
# queue here rather than each touching the model (spec 642-644).
_inference_slot = threading.BoundedSemaphore(
    int(os.getenv("VI_MAX_CONCURRENT_INFERENCE", "1"))
)

_ready = threading.Event()
_warmup_started = threading.Lock()
_warmup_kicked = False


def is_ready():
    return _ready.is_set()


def _warmup():
    """Import + construct the ML service and run one tiny inference to force the
    lazy model loads, then mark ready. Import is deferred to here so importing
    this module (e.g. in unit tests) does not drag in torch."""
    log_event("INFO", "model_load_started", model_version=MODEL_VERSION)
    started = time.time()
    cache_root = Path(os.getenv("LOCAL_MODELS_DIR")
                      or os.getenv("MODEL_CACHE_DIR", "/models"))
    cache_present = all(
        (cache_root / name).exists() for name in ("strategic_model", "tone_model")
    )
    if cache_present:
        log_event("INFO", "model_cache_hit", model_version=MODEL_VERSION)
    else:
        log_event("INFO", "model_download_started", model_version=MODEL_VERSION)
    required_models_loaded = False
    failure_stage = "service_initialization"
    try:
        from dashboard.services.ml_inference_service import get_ml_service
        service = get_ml_service()
        # Readiness means both required local classifiers can actually load. The
        # legacy service has fallback paths, so merely receiving a dict from
        # perform_inference() is not a sufficient readiness check.
        failure_stage = "strategic_model_load"
        if service._load_strategic_classifier() is None:
            raise RuntimeError("strategic classifier failed to load")
        failure_stage = "tone_model_load"
        if service._load_tone_classifier() is None:
            raise RuntimeError("tone classifier failed to load")
        required_models_loaded = True
        if not cache_present:
            log_event("INFO", "model_download_completed", model_version=MODEL_VERSION,
                      duration_ms=int((time.time() - started) * 1000))
        failure_stage = "warmup_inference"
        warmup_result = service.perform_inference("warmup")
        failure_stage = "warmup_response_validation"
        _shape_result(warmup_result, "warmup")
        _service_holder["service"] = service
        _ready.set()
        log_event(
            "INFO", "model_load_completed",
            model_version=MODEL_VERSION,
            duration_ms=int((time.time() - started) * 1000),
        )
    except Exception as e:  # noqa: BLE001 - stay down, don't crash the web process
        if not cache_present and not required_models_loaded:
            log_event("ERROR", "model_download_failed", model_version=MODEL_VERSION,
                      failure_stage=failure_stage, error_type=type(e).__name__,
                      error_code="model_download_failed", error_detail=safe_error(e))
        log_event(
            "ERROR", "model_load_failed",
            model_version=MODEL_VERSION,
            failure_stage=failure_stage,
            error_type=type(e).__name__,
            error_code="model_load_failed",
            error_detail=safe_error(e),
            stack_trace=safe_traceback(),
        )


_service_holder = {"service": None}


def start_warmup():
    """Kick the background load once. Safe to call from the wsgi entrypoint.

    VI_SKIP_WARMUP=1 boots the web process without loading models: /healthz
    stays 200 and /readyz stays 503. Used by the boot smoke harness and any
    health-only boot; do not set it in the real inference deployment."""
    global _warmup_kicked
    if os.getenv("VI_SKIP_WARMUP") == "1":
        log_event("INFO", "model_warmup_skipped")
        return
    with _warmup_started:
        if _warmup_kicked:
            return
        _warmup_kicked = True
    threading.Thread(target=_warmup, name="model-warmup", daemon=True).start()


def _map_intent(raw):
    """Canonicalize, but keep Neutral explicit - NULL means unprocessed, a
    correctly-Neutral article is a processed result (spec 358-360)."""
    from dashboard.utils import map_to_canonical_intent
    if isinstance(raw, str) and raw.strip().lower() == "neutral":
        return "Neutral"
    canonical = map_to_canonical_intent(raw)
    if canonical:
        return canonical
    raise InferenceRuntimeError("unknown_strategic_intent",
                                "model returned an unknown strategic intent")


def _detect_language(article_text):
    try:
        from langdetect import LangDetectException, detect
    except ImportError:
        return "unknown"
    try:
        return detect(article_text)
    except (LangDetectException, ValueError):
        return "unknown"


def _as_confidence(result, field, fallback=None):
    value = result.get(field, result.get(fallback)) if fallback else result.get(field)
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise InferenceRuntimeError(
            f"invalid_{field}", f"model returned invalid {field}") from exc
    if not 0.0 <= value <= 1.0:
        raise InferenceRuntimeError(
            f"invalid_{field}", f"model returned out-of-range {field}")
    return round(value, 4)


def _shape_result(result, article_text):
    if not isinstance(result, dict):
        raise InferenceRuntimeError("invalid_model_result",
                                    "model returned an invalid result")
    intent = _map_intent(result.get("strategic_intent"))
    if intent not in ALLOWED_INTENTS:
        raise InferenceRuntimeError("unsupported_strategic_intent",
                                    "model returned an unsupported strategic intent")
    tone = result.get("tone")
    if not isinstance(tone, str) or not tone.strip():
        raise InferenceRuntimeError("invalid_tone", "model returned an invalid tone")
    prediction_source = result.get("prediction_source")
    if not isinstance(prediction_source, str) or not prediction_source.strip():
        raise InferenceRuntimeError("invalid_prediction_source",
                                    "model returned an invalid prediction source")
    return {
        "strategic_intent": intent,
        "strategic_intent_confidence": _as_confidence(
            result, "strategic_intent_confidence", "confidence"),
        "tone": tone,
        "tone_confidence": _as_confidence(result, "tone_confidence"),
        "confidence": _as_confidence(result, "confidence"),
        "lang_detect": _detect_language(article_text),
        "prediction_source": prediction_source,
        "model_version": MODEL_VERSION,
    }


def run_inference(article_text):
    """Run the ensemble and shape the result to the API response contract
    (spec 327-343). Raises if the underlying service is unavailable so the view
    can return 500 rather than a bogus Neutral (spec 449-450)."""
    service = _service_holder["service"]
    if service is None:
        raise InferenceRuntimeError("model_service_unavailable",
                                    "model service not initialised")

    started = time.time()
    with _inference_slot:
        result = service.perform_inference(article_text)
    elapsed_ms = int((time.time() - started) * 1000)

    shaped = _shape_result(result, article_text)
    shaped["processing_time_ms"] = elapsed_ms
    return shaped
