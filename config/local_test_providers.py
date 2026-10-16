"""Strict, observable providers for the explicitly selected local test WSGI."""
from threading import Lock
from types import SimpleNamespace


class ProviderTracker:
    def __init__(self, aws_live=False, groq_live=False):
        self.lock = Lock()
        self.providers = {name: {"mode": "live" if live else "controlled", "succeeded": 0, "failed": 0}
                          for name, live in (("aws_translate", aws_live), ("groq", groq_live))}

    def call(self, provider, operation, validate):
        try:
            result = operation()
            validate(result)
        except Exception as error:
            with self.lock:
                self.providers[provider]["failed"] += 1
            # SDK exception strings can include response/request content. Only
            # the class is safe to return through the app's existing fallbacks.
            raise RuntimeError(f"Local {provider} verification failed ({type(error).__name__}); check credentials, permissions, region/model and provider quota") from None
        with self.lock:
            self.providers[provider]["succeeded"] += 1
        return result

    def snapshot(self):
        with self.lock:
            return {name: dict(state) for name, state in self.providers.items()}


class ControlledTranslate:
    def translate_text(self, **kwargs):
        return {"TranslatedText": kwargs["Text"]}


class ControlledGroq:
    def __init__(self, **kwargs):
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content="Local test analysis: synthetic fixture articles describe economic cooperation."
        ))])


class TrackedTranslate:
    def __init__(self, client, tracker):
        self.client, self.tracker = client, tracker

    def translate_text(self, **kwargs):
        def validate(result):
            if not isinstance(result.get("TranslatedText"), str) or not result["TranslatedText"].strip():
                raise ValueError("Empty translated text")
        return self.tracker.call("aws_translate", lambda: self.client.translate_text(**kwargs), validate)


class TrackedGroq:
    def __init__(self, client, tracker, clean_answer):
        self.client, self.tracker, self.clean_answer = client, tracker, clean_answer
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        return self.tracker.call("groq", lambda: self.client.chat.completions.create(**kwargs),
                                 lambda result: self.clean_answer(result.choices[0].message.content))
