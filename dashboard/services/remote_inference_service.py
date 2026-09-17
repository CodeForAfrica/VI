"""HTTP-backed local classifiers; all other pipeline behavior is inherited unchanged."""
import logging
import time
import uuid
from urllib.parse import urlsplit

from .ml_inference_service import MLInferenceService

logger = logging.getLogger(__name__)


class RemoteInferenceService(MLInferenceService):
    def __init__(self, client, *, deadline=None, event_logger=None):
        # Do not initialize/download any local models, tokenizer, or S3 client.
        self.client = client
        self.deadline = deadline
        self.event_logger = event_logger or (lambda *args, **kwargs: None)
        self._csv_risk_df = self._load_csv_risks()
        self._result = None
        self._error = None

    def perform_inference(self, article_text):
        self._result = None
        self._error = None
        self._request_id = None
        started = time.monotonic()
        self.event_logger("INFO", "article_inference_started", input_characters=len(article_text))
        result = super().perform_inference(article_text)
        self.event_logger("INFO", "article_inference_completed",
                          request_id=self._request_id,
                          duration_ms=int((time.monotonic() - started) * 1000),
                          local_models_available=self._error is None,
                          **{key: result.get(key) for key in
                             ("strategic_intent", "tone", "confidence")})
        return result

    def _remote_result(self, text):
        if self._error is not None:
            raise self._error
        if self._result is None:
            request_id = str(uuid.uuid4())
            self._request_id = request_id
            started = time.monotonic()
            # Never log URL credentials/query parameters, headers or article bodies.
            base_url = getattr(self.client, "base_url", "")
            endpoint = urlsplit(base_url if isinstance(base_url, str) else "")
            self.event_logger("INFO", "local_inference_request_started",
                              request_id=request_id, method="POST", host=endpoint.hostname,
                              path=endpoint.path.rstrip("/") + "/api/v1/inference",
                              input_characters=len(text),
                              timeout_seconds=self.client.timeout,
                              max_attempts=self.client.max_attempts)

            def on_retry(attempt, max_attempts, error):
                self.event_logger("WARNING", "local_inference_retry",
                                  request_id=request_id, attempt=attempt,
                                  max_attempts=max_attempts,
                                  error_type=type(error).__name__, error_code=error.code,
                                  http_status=error.status_code,
                                  attempt_duration_ms=error.duration_ms)

            try:
                self._result = self.client.infer(
                    request_id=request_id, article_text=text, deadline=self.deadline,
                    on_retry=on_retry)
                self.event_logger("INFO", "local_inference_request_completed",
                                  request_id=request_id, http_status=200,
                                  duration_ms=int((time.monotonic() - started) * 1000),
                                  attempts=self._result.get("_client_attempts"),
                                  attempt_duration_ms=self._result.get("_client_attempt_duration_ms"),
                                  **{key: self._result.get(key) for key in
                                     ("strategic_intent", "strategic_intent_confidence",
                                      "tone", "tone_confidence", "confidence",
                                      "model_version", "processing_time_ms")})
            except Exception as exc:
                self._error = exc
                self.event_logger("WARNING", "local_inference_request_failed",
                                  request_id=request_id, error_type=type(exc).__name__,
                                  error_code=getattr(exc, "code", None),
                                  http_status=getattr(exc, "status_code", None),
                                  attempts=getattr(exc, "attempts", None),
                                  duration_ms=int((time.monotonic() - started) * 1000),
                                  fallback="existing_pipeline_defaults")
                raise
        return self._result

    def _load_strategic_classifier(self):
        return self

    def predict(self, texts, **kwargs):
        result = self._remote_result(texts[0])
        return [result["strategic_intent"]], [[result["strategic_intent_confidence"]]]

    def _decode_label(self, label):
        return label

    def perform_tone_inference(self, article_text):
        try:
            result = self._remote_result(article_text)
            return result["tone"], result["tone_confidence"]
        except Exception:
            # Exact fallback used by the original local tone implementation.
            logger.warning("Remote tone inference unavailable; using original neutral/0.3 fallback")
            return "neutral", 0.3

    def _get_llm_strategic_intent(self, text):
        started = time.monotonic()
        self.event_logger("INFO", "arbitration_started", provider="groq", request_id=self._request_id)
        result = super()._get_llm_strategic_intent(text)
        self.event_logger("INFO", "arbitration_completed", provider="groq",
                          request_id=self._request_id,
                          duration_ms=int((time.monotonic() - started) * 1000),
                          strategic_intent=result[0], confidence=result[1],
                          available=result[2] != "API key missing" and not str(result[2]).startswith("Error:"))
        return result
