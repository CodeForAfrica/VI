"""HTTP client the ingestion Lambda uses to call the inference API
(solution-spec 564-635).

Pure and dependency-light (requests + stdlib) so it can be unit-tested without
AWS, a database, or a live API - pass a fake ``session`` whose ``post`` returns
canned responses. Retry/no-retry status classification follows spec 613-628.
"""
import random
import time

import requests

# Retry these; do not retry the rest (spec 613-628). Timeouts/connection errors
# are always retryable. Unexpected 5xx (e.g. 500) are treated as retryable too:
# a transient server error should get another attempt rather than drop the row.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
PERMANENT_STATUS = {400, 401, 403, 413}

ALLOWED_INTENTS = {
    "Economic", "Sovereignty", "LGBTQ", "Religious", "ElectionInfluence",
    "MilitaryPresence", "ResourceDependency", "SocialFragility", "Neutral",
}


class InferenceError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class RetryableInferenceError(InferenceError):
    """Transient - the row should stay pending and be retried later."""


class PermanentInferenceError(InferenceError):
    """The API rejected the request or returned garbage - retrying won't help."""


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
              target_country=None, inferred_actor=None, on_retry=None):
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
            try:
                resp = self.session.post(url, json=payload, headers=headers,
                                         timeout=self.timeout)
            except (requests.Timeout, requests.ConnectionError) as e:
                last_error = RetryableInferenceError(str(e))
            else:
                if resp.status_code == 200:
                    return self._validate(resp, request_id)
                if resp.status_code in PERMANENT_STATUS:
                    raise PermanentInferenceError(
                        f"HTTP {resp.status_code}", code=resp.status_code)
                # retryable or unexpected status
                last_error = RetryableInferenceError(
                    f"HTTP {resp.status_code}",
                    code=resp.status_code if resp.status_code in RETRYABLE_STATUS else None)

            if attempt < self.max_attempts:
                if on_retry:
                    on_retry(attempt, getattr(last_error, "code", None))
                # exponential backoff (capped) + jitter (spec 630).
                self._sleep(min(2 ** (attempt - 1), 8) + random.uniform(0, 0.5))

        raise last_error

    @staticmethod
    def _validate(resp, request_id):
        try:
            data = resp.json()
        except ValueError:
            raise PermanentInferenceError("response body is not JSON")
        intent = data.get("strategic_intent")
        if intent not in ALLOWED_INTENTS:
            # A 200 with an out-of-enum intent is a contract violation, not a
            # transient blip - do not retry it forever.
            raise PermanentInferenceError(f"invalid strategic_intent: {intent!r}")
        return data
