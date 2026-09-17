"""HTTP-backed local classifiers; all other pipeline behavior is inherited unchanged."""
import logging
import uuid

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
        return super().perform_inference(article_text)

    def _remote_result(self, text):
        if self._error is not None:
            raise self._error
        if self._result is None:
            request_id = str(uuid.uuid4())
            self.event_logger("INFO", "local_inference_request_started", request_id=request_id)
            try:
                self._result = self.client.infer(
                    request_id=request_id, article_text=text, deadline=self.deadline)
                self.event_logger("INFO", "local_inference_request_completed", request_id=request_id)
            except Exception as exc:
                self._error = exc
                self.event_logger("WARNING", "local_inference_request_failed",
                                  request_id=request_id, error_type=type(exc).__name__,
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
        self.event_logger("INFO", "arbitration_started", provider="groq")
        result = super()._get_llm_strategic_intent(text)
        self.event_logger("INFO", "arbitration_completed", provider="groq",
                          available=result[2] != "API key missing" and not str(result[2]).startswith("Error:"))
        return result
