"""Small OpenRouter chat client using the already pinned requests dependency."""
import logging
import time
from types import SimpleNamespace

import requests

logger = logging.getLogger(__name__)
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterError(RuntimeError):
    pass


class OpenRouterAuthenticationError(OpenRouterError):
    pass


class OpenRouterRateLimitError(OpenRouterError):
    pass


class OpenRouterTimeout(OpenRouterError):
    pass


class OpenRouterClient:
    def __init__(self, api_key=None, timeout=20.0):
        self.api_key = api_key
        self.timeout = timeout
        self.chat = SimpleNamespace(completions=self)

    def create(self, **params):
        if not self.api_key or not self.api_key.strip():
            raise OpenRouterAuthenticationError("OpenRouter API key is not configured")
        if "max_completion_tokens" in params:
            params["max_tokens"] = params.pop("max_completion_tokens")
        if params.get("response_format") == {"type": "json_object"}:
            # Scout's available endpoint supports schemas, not legacy json_object mode.
            params["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "analysis", "strict": False,
                "schema": {"type": "object", "additionalProperties": True},
            }}
        model = params.get("model")
        started = time.monotonic()
        logger.info("OpenRouter request started model=%s messages=%d", model, len(params.get("messages", [])))
        try:
            response = requests.post(
                ENDPOINT, json=params,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                timeout=self.timeout, allow_redirects=False,
            )
        except requests.Timeout:
            logger.warning("OpenRouter request failed model=%s reason=timeout", model)
            raise OpenRouterTimeout("OpenRouter request timed out") from None
        except requests.RequestException:
            logger.warning("OpenRouter request failed model=%s reason=connection", model)
            raise OpenRouterError("OpenRouter connection failed") from None
        if response.status_code != 200:
            logger.warning("OpenRouter request failed model=%s http_status=%d", model, response.status_code)
            error = {401: OpenRouterAuthenticationError, 403: OpenRouterAuthenticationError,
                     429: OpenRouterRateLimitError}.get(response.status_code, OpenRouterError)
            raise error(f"OpenRouter returned HTTP {response.status_code}")
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            if body.get("error") or not isinstance(content, str) or not content.strip():
                raise ValueError("empty/error response")
        except (ValueError, KeyError, TypeError, IndexError):
            logger.warning("OpenRouter request failed model=%s reason=invalid_response", model)
            raise OpenRouterError("OpenRouter returned an empty or invalid response") from None
        logger.info("OpenRouter request completed model=%s duration_ms=%d", model, (time.monotonic() - started) * 1000)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
