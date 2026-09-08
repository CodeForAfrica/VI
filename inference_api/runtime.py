"""Model lifecycle + inference call for the API (solution-spec 637-665).

Models load once, in a background thread started at process boot, so `/healthz`
stays responsive while loading and `/readyz` flips to ready only when the
ensemble is actually usable. A semaphore bounds concurrent inference because the
process runs a single model copy.
"""
import os
import threading
import time

from .logs import log_event

MODEL_VERSION = os.getenv("MODEL_VERSION", "2026-09-04")

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
    try:
        from dashboard.services.ml_inference_service import get_ml_service
        service = get_ml_service()
        service.perform_inference("warmup")  # forces lazy model loads
        _service_holder["service"] = service
        _ready.set()
        log_event(
            "INFO", "model_load_completed",
            model_version=MODEL_VERSION,
            duration_ms=int((time.time() - started) * 1000),
        )
    except Exception as e:  # noqa: BLE001 - stay down, don't crash the web process
        log_event(
            "ERROR", "model_load_failed",
            model_version=MODEL_VERSION,
            error_type=type(e).__name__,
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
    canonical = map_to_canonical_intent(raw)
    if canonical:
        return canonical
    # No canonical match: treat as Neutral (an explicit, processed outcome)
    # rather than returning null.
    return "Neutral"


def run_inference(article_text):
    """Run the ensemble and shape the result to the API response contract
    (spec 327-343). Raises if the underlying service is unavailable so the view
    can return 500 rather than a bogus Neutral (spec 449-450)."""
    service = _service_holder["service"]
    if service is None:
        raise RuntimeError("model service not initialised")

    started = time.time()
    with _inference_slot:
        result = service.perform_inference(article_text)
    elapsed_ms = int((time.time() - started) * 1000)

    intent = _map_intent(result.get("strategic_intent"))
    si_conf = result.get("strategic_intent_confidence", result.get("confidence", 0.0))
    return {
        "strategic_intent": intent,
        "strategic_intent_confidence": round(float(si_conf), 4),
        "tone": result.get("tone", "Factual"),
        "tone_confidence": round(float(result.get("tone_confidence", 0.0)), 4),
        "confidence": round(float(result.get("confidence", 0.0)), 4),
        "prediction_source": result.get("prediction_source", "ensemble"),
        "model_version": MODEL_VERSION,
        "processing_time_ms": elapsed_ms,
    }
