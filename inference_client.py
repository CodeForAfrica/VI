"""HTTP client the ingestion Lambda uses to call the inference API
(solution-spec 564-635).

Pure and dependency-light (requests + stdlib) so it can be unit-tested without
AWS, a database, or a live API - pass a fake ``session`` whose ``post`` returns
canned responses. Retry/no-retry status classification follows spec 613-628.
"""
import random
import time

import requests

# These statuses warrant another attempt during the current Lambda invocation.
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
# These responses are caused by the article/request and will not be repaired by
# credentials, routing, or service configuration changing later.
PERMANENT_STATUS = {400, 413, 422}
# Model-output contract failures may be returned as 500 by an older API release.
# Treat them as terminal even while a rolling deployment has mixed versions.
PERMANENT_ERROR_CODES = {
    "unknown_strategic_intent",
    "unsupported_strategic_intent",
    "invalid_model_result",
    "invalid_strategic_intent_confidence",
    "invalid_tone",
    "invalid_tone_confidence",
    "invalid_confidence",
    "invalid_prediction_source",
}

ALLOWED_INTENTS = {
    "Economic", "Sovereignty", "LGBTQ", "Religious", "ElectionInfluence",
    "MilitaryPresence", "ResourceDependency", "SocialFragility", "Neutral",
}


class InferenceError(Exception):
    def __init__(self, message, code=None, status_code=None, attempts=None,
                 duration_ms=None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.attempts = attempts
        self.duration_ms = duration_ms


class RetryableInferenceError(InferenceError):
    """Transient - the row should stay pending and be retried later."""


class DeferredInferenceError(RetryableInferenceError):
    """Do not retry now, but leave the row pending for a later invocation."""


class PermanentInferenceError(InferenceError):
    """The API rejected the request or returned garbage - retrying won't help."""


def _connection_error_code(error):
    detail = repr(error).lower()
    dns_markers = ("nameresolutionerror", "name resolution", "failed to resolve",
                   "nodename nor servname", "temporary failure in name resolution")
    if any(marker in detail for marker in dns_markers):
        return "inference_dns_failed"
    return "inference_connection_failed"


class InferenceClient:
    def __init__(self, base_url, api_key, timeout=180, max_attempts=3,
                 session=None, sleep=time.sleep):
        if not base_url:
            raise ValueError("inference API base_url is required")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.session = session or requests.Session()
        self._sleep = sleep  # injectable so tests don't actually wait

    def infer(self, request_id, article_text, article_id=None,
              target_country=None, inferred_actor=None, on_retry=None,
              deadline=None):
        """Return the validated prediction dict, or raise Retryable/Permanent.

        Reuses one ``request_id`` across every attempt of this logical request
        so the call can be traced across Lambda and API (spec 630-631).
        """
        payload = {"request_id": request_id, "article_text": article_text}
        if article_id is not None:
            payload["article_id"] = article_id
        if target_country:
            payload["target_country"] = target_country
        if inferred_actor:
            payload["inferred_actor"] = inferred_actor

        url = f"{self.base_url}/api/v1/inference"
        headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}

        last_error = None
        for attempt in range(1, self.max_attempts + 1):
            attempt_started = time.time()
            request_timeout = self.timeout
            if deadline is not None:
                remaining = deadline - time.time()
                if remaining <= 1:
                    raise RetryableInferenceError("Lambda time budget exhausted",
                                                  code="time_budget_exhausted")
                request_timeout = min(request_timeout, max(1, remaining - 1))
            try:
                resp = self.session.post(url, json=payload, headers=headers,
                                         timeout=request_timeout)
            except requests.Timeout as e:
                last_error = RetryableInferenceError(
                    str(e), code="inference_timeout", attempts=attempt,
                    duration_ms=int((time.time() - attempt_started) * 1000))
            except requests.exceptions.SSLError as e:
                last_error = RetryableInferenceError(
                    str(e), code="inference_tls_failed", attempts=attempt,
                    duration_ms=int((time.time() - attempt_started) * 1000))
            except requests.ConnectionError as e:
                last_error = RetryableInferenceError(
                    str(e), code=_connection_error_code(e), attempts=attempt,
                    duration_ms=int((time.time() - attempt_started) * 1000))
            else:
                if resp.status_code == 200:
                    try:
                        data = self._validate(resp, request_id, article_id)
                    except PermanentInferenceError as error:
                        error.status_code = 200
                        error.attempts = attempt
                        error.duration_ms = int(
                            (time.time() - attempt_started) * 1000)
                        raise
                    data["_client_attempts"] = attempt
                    data["_client_attempt_duration_ms"] = int(
                        (time.time() - attempt_started) * 1000)
                    return data
                error_code, error_message = self._response_error(resp)
                if (resp.status_code in PERMANENT_STATUS
                        or error_code in PERMANENT_ERROR_CODES):
                    raise PermanentInferenceError(
                        error_message, code=error_code, status_code=resp.status_code,
                        attempts=attempt,
                        duration_ms=int((time.time() - attempt_started) * 1000))
                if resp.status_code not in RETRYABLE_STATUS:
                    # Authentication, routing, and other configuration failures
                    # should not hammer the API, but must not permanently discard
                    # the article either. A later invocation can recover after
                    # the deployment/configuration is corrected.
                    raise DeferredInferenceError(
                        error_message, code=error_code, status_code=resp.status_code,
                        attempts=attempt,
                        duration_ms=int((time.time() - attempt_started) * 1000))
                last_error = RetryableInferenceError(
                    error_message, code=error_code, status_code=resp.status_code,
                    attempts=attempt,
                    duration_ms=int((time.time() - attempt_started) * 1000))

            if attempt < self.max_attempts:
                if on_retry:
                    on_retry(attempt, self.max_attempts, last_error)
                # exponential backoff (capped) + jitter (spec 630).
                delay = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.5)
                if deadline is not None and time.time() + delay >= deadline - 1:
                    break
                self._sleep(delay)

        raise last_error

    @staticmethod
    def _response_error(resp):
        code = f"http_{resp.status_code}"
        message = f"HTTP {resp.status_code}"
        try:
            body = resp.json()
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict):
                supplied_code = error.get("code")
                supplied_message = error.get("message")
                if isinstance(supplied_code, str) and supplied_code:
                    code = supplied_code[:64]
                if isinstance(supplied_message, str) and supplied_message:
                    message = f"HTTP {resp.status_code}: {supplied_message[:300]}"
        except ValueError:
            pass
        return code, message

    @staticmethod
    def _validate(resp, request_id, article_id=None):
        try:
            data = resp.json()
        except ValueError:
            raise PermanentInferenceError("response body is not JSON",
                                          code="response_not_json")
        if not isinstance(data, dict):
            raise PermanentInferenceError("response body is not a JSON object",
                                          code="response_not_object")
        if data.get("request_id") != request_id:
            raise PermanentInferenceError("response request_id does not match request",
                                          code="response_request_id_mismatch")
        if data.get("article_id") != article_id:
            raise PermanentInferenceError("response article_id does not match request",
                                          code="response_article_id_mismatch")
        intent = data.get("strategic_intent")
        if intent not in ALLOWED_INTENTS:
            # A 200 with an out-of-enum intent is a contract violation, not a
            # transient blip - do not retry it forever.
            raise PermanentInferenceError(f"invalid strategic_intent: {intent!r}",
                                          code="invalid_strategic_intent")
        for field in ("strategic_intent_confidence", "tone_confidence", "confidence"):
            value = data.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise PermanentInferenceError(f"invalid {field}",
                                              code=f"invalid_{field}")
            if not 0.0 <= float(value) <= 1.0:
                raise PermanentInferenceError(f"out-of-range {field}",
                                              code=f"invalid_{field}")
        for field in ("tone", "lang_detect", "prediction_source", "model_version"):
            if not isinstance(data.get(field), str) or not data[field].strip():
                raise PermanentInferenceError(f"invalid {field}",
                                              code=f"invalid_{field}")
        processing_time_ms = data.get("processing_time_ms")
        if (isinstance(processing_time_ms, bool)
                or not isinstance(processing_time_ms, (int, float))
                or processing_time_ms < 0):
            raise PermanentInferenceError("invalid processing_time_ms",
                                          code="invalid_processing_time_ms")
        return data
