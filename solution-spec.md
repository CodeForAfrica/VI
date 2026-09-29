# Move local model execution out of Lambda

## Scope: change location, not behavior

The strategic-intent and tone models are too large to run reliably in Lambda.
Run those classifiers in a persistent Dokku container on the existing Ollama
host and replace their in-memory calls with HTTPS requests.

Everything else stays in the caller: preprocessing, Groq requests and parsing,
prediction selection, entity extraction, scoring, canonicalization, ingestion,
and database writes. Do not redesign these rules as part of this change.

The model server does not need a Groq key. Lambda still needs its existing
`GROQ_API_KEY` and `GROQ_MODEL`. Hosting on the Ollama machine does not make
Ollama a dependency of either classifier.

## Implementation boundary

- `MLInferenceService` retains the existing business methods and local backend
  for dashboard/management-command callers. Heavy classifier dependencies load
  only when its local-model constructor runs.
- `RemoteInferenceService` inherits those business methods, skips local model
  initialization, and supplies strategic/tone results through `InferenceClient`.
- The API calls only `perform_local_inference`, never `perform_inference`.
  It does not call Groq, extract entities, score, canonicalize, or access the DB.
- Lambda calls the inherited pipeline, then writes the same classification
  fields as the existing `fill_missing_intents` command.

## Traffic flow

1. Lambda runs the existing MediaCloud ingestion and quality checks.
2. It selects unprocessed rows with missing strategic intent, using the existing
   `ml_processed_at` marker. No new state columns or migrations are introduced.
3. The caller preprocesses article text as before.
4. Where a local classifier used to run, the HTTP adapter sends the text to
   `https://vi-model-inference.codeforafrica.org/api/v1/inference`.
5. The server returns raw strategic/tone predictions and confidence values.
6. Lambda executes the original Groq arbitration and the rest of the pipeline.
7. Lambda applies the original canonical mapping and saves strategic intent,
   confidence, tone, and `ml_processed_at`.

The public hostname routes through the existing ALB to Dokku. Lambda needs HTTPS
egress and the API key, not an ingress security-group relationship with the host.
Any existing VPC configuration needed to reach PostgreSQL remains separate.

## API contract

```http
POST /api/v1/inference
Content-Type: application/json
X-API-Key: <shared-secret>

{
  "request_id": "31e39d5f-96eb-4405-82d4-065582822118",
  "article_text": "Text already preprocessed by the existing caller pipeline"
}
```

Example successful local-model response (not the final arbitrated result):

```json
{
  "request_id": "31e39d5f-96eb-4405-82d4-065582822118",
  "article_id": null,
  "strategic_intent": "Economic",
  "strategic_intent_confidence": 0.87654321,
  "tone": "Factual",
  "tone_confidence": 0.72,
  "confidence": 0.87654321,
  "prediction_source": "model",
  "lang_detect": "en",
  "model_version": "configured-version",
  "processing_time_ms": 120
}
```

Raw labels, including `unknown`, must survive transport. Do not canonicalize or
round confidence before arbitration: either could change the chosen prediction.
The final database mapping remains on the caller. In particular, the original
Neutral-to-NULL mapping remains, with `ml_processed_at` marking completion.

- `GET /healthz`: process liveness, no model loading or authentication.
- `GET /readyz`: 503 until required models are loaded, then 200.
- HTTP 400/413/422: malformed, oversized, or invalid requests.
- HTTP 401: missing or invalid API key.
- HTTP 429: rate limited; caller uses bounded retries.
- HTTP 503: service not ready; caller uses bounded retries.
- HTTP 500: unexpected server error.

## Preserve failure behavior

The original strategic model defaults to `unknown/0.0` when unavailable; tone
defaults to `neutral/0.3`. Preserve those defaults and the existing arbitration
rules, including ties, low-confidence matches, missing Groq credentials, and
Groq failures. A transport failure is treated like an unavailable local backend
by the existing caller pipeline, not a reason to silently remove Groq logic.

HTTP retries are bounded by configured attempts and the remaining Lambda time.
No new terminal-failure database states or changes to ingestion/deduplication
rules are part of this move. Save failures leave the existing pending marker.

## Authentication and limits

The server reads `VI_INFERENCE_ACCEPTED_KEYS`, a JSON array of
`{"caller":"lambda-prod","key":"<secret>"}` entries. One entry is sufficient.
Lambda sends the matching secret in `X-API-Key`; the server compares it using a
constant-time comparison and logs the caller identifier, never the secret.

HTTPS protects the API key in transit. This is a bearer credential: anyone who
obtains it can call the API, so keep it in encrypted configuration and never put
it in URLs or logs. If it leaks, manually replace it in both places. No automatic
rotation, HMAC, nonce, Redis, or Valkey is required.

Use an in-process requests-per-minute limit, one model worker, bounded concurrent
inference, a request-size limit, and explicit HTTP timeouts. A process restart
resets the limiter; multiple workers would each have their own counter.

## Runtime and deployment

- One persistent Dokku API process loads model weights once and uses a mounted
  `/models` cache; the host can read the model S3 bucket.
- The inference process has no PostgreSQL or external cache credentials.
- IaC stores only the Django signing secret and accepted API keys as encrypted
  Pulumi configuration. Do not regenerate them during deployment.
- Lambda holds the endpoint URL, matching API key, existing DB/MediaCloud
  credentials, and its existing Groq configuration.
- Lambda dependencies exclude Torch/Transformers classifier weights. Existing
  spaCy entity extraction remains in the caller; bundle its model in the image
  rather than downloading into Lambda's read-only filesystem.
- Image builds/deployments remain manual. Do not treat PR tests as deployment
  authorization. No inference-only schema migration is required.

## Logs

Log every API access, including health checks, authentication failures, rate
limits, invalid requests, 404/405 responses, successes, and exceptions. Include
request/trace ID, method/path, status, caller when authenticated, and duration.
Do not log full article text, request/response bodies, keys, or passwords.

Log caller invocation, database connection, ingestion, local-model HTTP requests,
retries, Groq arbitration start/completion, saves, and failures. Model-server
logs cover startup, model download/load/readiness and both classifier calls.
Failure logs identify the stage and fallback; do not label a fallback as a
successful model prediction. The API and Lambda request IDs must correlate.

## Acceptance tests

Compare in-process and HTTP-backed execution with identical mocked model/Groq
outputs: agreeing predictions, low-confidence matches, disagreements, ties,
Neutral, raw label variants, absent/failed local models, and transport failure.
Assert equal pipeline results, Groq input text, and scoring inputs. Verify
confidence precision, unchanged database fields/mapping, no Groq/scoring calls
on the server, and no Torch/Transformers imports in the Lambda process.

Also test authentication, request validation, rate limits, health/readiness,
bounded retries, log redaction, and database failure handling. Real-image and
real-model deployment verification is separate and must be reported explicitly.
