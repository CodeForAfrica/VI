# VI Model Inference API Solution Specification

## Summary

The Vulnerability Index pipeline currently combines media ingestion and a large
machine-learning workload inside one AWS Lambda deployment. The inference stack
is too large and too long-lived for Lambda: the ensemble requires roughly 13 GB
at runtime, its container and dependencies approach or exceed Lambda limits, and
model downloads and cold starts make executions slow and fragile.

The proposed solution separates the pipeline into:

1. A small ingestion Lambda that queries MediaCloud, scrapes articles, stores
   them, calls an HTTPS inference API, and persists successful predictions.
2. A long-running model-inference API deployed with Dokku on the existing
   Open WebUI/Ollama EC2 host.
3. PostgreSQL as the system of record consumed by the existing dashboard.

The proposed public service address is:

```text
https://vi-model-inference.codeforafrica.org
```

The inference service should be stateless with respect to the VI database. It
receives article content, returns a prediction, and does not hold PostgreSQL
credentials. The ingestion Lambda remains responsible for database writes.
It must not require Redis, Valkey, a queue, or another persistence service.

## Original problem: monolithic Lambda

The original Lambda performs all of the following in one process:

1. Query MediaCloud.
2. Download and scrape articles.
3. Load several machine-learning models.
4. Run strategic-intent and tone inference.
5. Call an LLM for strategic-intent arbitration.
6. Calculate derived values.
7. Write results to PostgreSQL.

This design causes several operational problems:

- Lambda container images have a 10 GB limit.
- Lambda executions have a maximum duration of 15 minutes.
- The model ensemble requires approximately 13 GB when loaded.
- Large model downloads make cold starts extremely slow.
- A Lambda execution cannot reliably keep models warm between invocations.
- Incomplete model downloads may have to be repeated.
- The workload cannot take advantage of the existing GPU server.
- A model failure can prevent ingestion from completing.
- Ingestion and inference must be built and deployed together even though they
  have different runtime requirements.

In simple terms, a large, long-running ML workload is being run inside a small,
short-lived serverless function.

## Proposed architectural split

```text
                    +------------------+
                    |    MediaCloud    |
                    +---------+--------+
                              |
                              v
                    +------------------+
                    | Ingestion Lambda |
                    |                  |
                    | - query          |
                    | - scrape         |
                    | - deduplicate    |
                    +-------+---+------+
                            |   |
             insert pending |   | HTTPS inference request
                            |   v
                            |  +-----------------------------+
                            |  | vi-model-inference Dokku app|
                            |  |                             |
                            |  | - persistent model cache    |
                            |  | - strategic-intent model    |
                            |  | - tone model                |
                            |  | - Groq/Ollama arbitration   |
                            |  +--------------+--------------+
                            |                 | JSON result
                            |   <-------------+
                            v
                    +------------------+
                    |    PostgreSQL    |
                    |                  |
                    | article + result |
                    +---------+--------+
                              |
                              v
                    +------------------+
                    | Django dashboard |
                    +------------------+
```

Deployment on the Ollama host does not require the inference API to use Ollama
immediately. The first version can retain Groq arbitration. Replacing Groq with
local Ollama should be a separate, measurable change.

## Responsibilities after the split

### Ingestion Lambda

The Lambda is responsible for:

- Querying MediaCloud.
- Scraping and validating article text.
- Deduplicating articles by URL.
- Inserting articles into PostgreSQL in a pending state.
- Calling the inference API over HTTPS.
- Validating inference responses.
- Updating articles with successful predictions.
- Leaving unsuccessful articles pending for later retry.
- Retrying a bounded number of pending articles on later invocations.

The Lambda image should contain ingestion and HTTP-client dependencies only. It
must not contain PyTorch, Transformers, model weights, or model-loading code.

### Model-inference API

The Dokku application is responsible for:

- Loading the inference models once when the process starts.
- Keeping loaded models in memory between requests.
- Caching model files on persistent storage mounted at `/models`.
- Authenticating every inference request.
- Validating and bounding input data.
- Running strategic-intent and tone inference.
- Optionally performing Groq or Ollama arbitration.
- Returning a versioned, structured JSON response.
- Reporting liveness and readiness separately.

The inference API should not connect to the VI PostgreSQL database. This keeps
the service reusable and prevents a public-facing service from receiving
database credentials.

### PostgreSQL and dashboard

PostgreSQL remains the source of truth. The existing dashboard continues to read
articles and their completed classifications from the database.

## Traffic flow

For each new article:

1. Lambda retrieves article metadata from MediaCloud.
2. Lambda downloads and extracts the article text.
3. Lambda checks whether the URL already exists.
4. Lambda inserts the article with an unprocessed state.
5. Lambda sends the article to `POST /api/v1/inference`.
6. The API authenticates and validates the request.
7. The API runs the loaded models.
8. The API returns the prediction as JSON.
9. Lambda validates the response and updates the article.
10. If inference fails, the article remains pending and ingestion continues.
11. A later invocation retries a bounded number of pending articles.

The important ordering is **insert first, infer second**. An inference outage
must not cause newly discovered articles to be lost.

## API contract

### Liveness endpoint

```http
GET /healthz
```

```json
{
  "status": "ok"
}
```

This endpoint only indicates that the web process is alive. It must respond
quickly and must not trigger model loading.

### Readiness endpoint

```http
GET /readyz
```

Ready response:

```json
{
  "status": "ready",
  "models_loaded": true,
  "model_version": "2026-09-04"
}
```

While models are unavailable or still loading:

```http
HTTP/1.1 503 Service Unavailable
```

```json
{
  "status": "loading",
  "models_loaded": false
}
```

### Inference endpoint

```http
POST /api/v1/inference
Content-Type: application/json
```

Example request:

```json
{
  "request_id": "7d86d12d-dc93-44da-8e03-06ba1aab36cc",
  "article_id": 12345,
  "article_text": "The complete extracted article text...",
  "target_country": "Senegal",
  "inferred_actor": "France",
  "media_outlet": "Example News"
}
```

Required fields:

- `request_id`: a UUID generated by the caller.
- `article_text`: the extracted text to classify.

Optional fields:

- `article_id`: the caller's database identifier, echoed without trusting it.
- `target_country`: a known country hint.
- `inferred_actor`: a known actor hint.
- `media_outlet`: an optional classification hint.

Example successful response:

```json
{
  "request_id": "7d86d12d-dc93-44da-8e03-06ba1aab36cc",
  "article_id": 12345,
  "strategic_intent": "Economic",
  "strategic_intent_confidence": 0.87,
  "tone": "Factual",
  "tone_confidence": 0.79,
  "confidence": 0.87,
  "lang_detect": "en",
  "prediction_source": "ensemble_matched",
  "model_version": "2026-09-04",
  "processing_time_ms": 4280
}
```

Allowed strategic-intent values are:

```text
Economic
Sovereignty
LGBTQ
Religious
ElectionInfluence
MilitaryPresence
ResourceDependency
SocialFragility
Neutral
```

`Neutral` must be stored as an explicit successfully processed result. It must
not be converted back to `NULL`, because `NULL` should mean unprocessed.

## Error responses

Error responses use one consistent envelope.

### Invalid request

```http
HTTP/1.1 400 Bad Request
```

```json
{
  "error": {
    "code": "invalid_request",
    "message": "article_text is required",
    "request_id": "7d86d12d-dc93-44da-8e03-06ba1aab36cc"
  }
}
```

### Authentication failure

```http
HTTP/1.1 401 Unauthorized
```

```json
{
  "error": {
    "code": "unauthorized",
    "message": "Authentication failed"
  }
}
```

The response must not disclose which key was expected or why authentication
failed.

### Payload too large

```http
HTTP/1.1 413 Content Too Large
```

```json
{
  "error": {
    "code": "payload_too_large",
    "message": "The request body exceeds the configured limit",
    "request_id": "7d86d12d-dc93-44da-8e03-06ba1aab36cc"
  }
}
```

### Model service not ready

```http
HTTP/1.1 503 Service Unavailable
Retry-After: 30
```

```json
{
  "error": {
    "code": "models_not_ready",
    "message": "The inference service is not ready",
    "request_id": "7d86d12d-dc93-44da-8e03-06ba1aab36cc"
  }
}
```

### Inference failure

```http
HTTP/1.1 500 Internal Server Error
```

```json
{
  "error": {
    "code": "inference_failed",
    "message": "Inference could not be completed",
    "request_id": "7d86d12d-dc93-44da-8e03-06ba1aab36cc"
  }
}
```

An internal failure must never be returned as a successful `Neutral`
classification.

## API security

The service has a public URL, but the inference endpoint is private at the
application layer. A direct Lambda-to-EC2 security-group rule is not required:
Lambda calls the HTTPS domain like any other client. A VPC-attached Lambda must
still have working HTTPS egress, normally through NAT.

Do not depend on a Lambda source-IP allowlist. Lambda source addresses are not
stable unless all traffic is deliberately routed through fixed egress.

### Baseline controls

- Serve the API over HTTPS only.
- Accept inference through `POST` only.
- Require `Content-Type: application/json`.
- Limit the complete request body, initially to 256 KB.
- Limit article-text length again before tokenization.
- Rate-limit by authenticated client identity.
- Never log authentication headers or full article text.
- Log request ID, result status, duration, and model version.
- Keep health responses free of sensitive data.
- Store all authentication material in AWS Secrets Manager.
- Support secret rotation without downtime.

### Recommended authentication: API key

Use one long, random API key sent in one request header:

```http
X-API-Key: <random-api-key>
```

Generate each key from at least 32 random bytes. Store the Lambda's key in AWS
Secrets Manager and inject it into the Lambda at deployment time. Store the
API's accepted-key configuration in AWS Secrets Manager and inject it into the
Dokku application.

The API keeps an accepted-key list so each key also identifies its caller:

```json
[
  {"caller": "vi-lambda-prod", "key": "<random-api-key>"},
  {"caller": "vi-developer-test", "key": "<different-random-api-key>"}
]
```

When a request arrives, the API compares the supplied key against the accepted
values. A matching entry authenticates the request and provides the caller name
for rate limiting and audit logs.

### Why each authentication part exists

| Part | Reason |
|---|---|
| `X-API-Key` header | Gives the Lambda one simple place to send its credential. |
| Random API key | Proves that the caller knows a secret shared with the API. A long randomly generated value cannot be practically guessed. |
| Accepted-key list | Allows the API to identify callers, revoke one caller without affecting others, and accept an old and new key during rotation. |
| AWS Secrets Manager | Keeps keys out of source control, Docker images, Terraform output, and ordinary configuration files. |
| HTTPS | Encrypts the header, article text, and response in transit. The API key is present in the HTTP request, but TLS prevents network observers from reading it. |
| Constant-time comparison | Avoids leaking useful information through small timing differences when keys are compared. |
| Rate limiting | Limits abuse and cost if a valid key is accidentally exposed. |
| Log redaction | Prevents a valid key from being copied into application, proxy, or error logs. |

This intentionally does not use nonces or request signatures. The tradeoff is
that a stolen API key can be reused until it is revoked. HTTPS, secret storage,
log redaction, rate limiting, monitoring, and straightforward key rotation are
the controls for that simpler design.

The API must:

- Require exactly one `X-API-Key` header.
- Compare the supplied value against all currently accepted keys using a
  constant-time comparison.
- Return the same generic `401` for every authentication failure.
- Record the matched caller name for rate limiting and audit logs without
  recording the key itself.
- Support an old and new key during credential rotation.

### Rate limiting

Apply a simple per-caller requests-per-minute limit after the API key has been
authenticated. Configure it through an environment variable:

```text
VI_RATE_LIMIT_REQUESTS_PER_MINUTE=60
```

Because the inference API initially runs as one Dokku instance with one model
worker, keep the counters in that process's memory. No Redis, Valkey, database,
queue, or other service is required. It is acceptable for counters to reset when
the container restarts.

When a caller exceeds the limit, return:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 60
```

```json
{
  "error": {
    "code": "rate_limit_exceeded",
    "message": "Too many inference requests",
    "request_id": "7d86d12d-dc93-44da-8e03-06ba1aab36cc"
  }
}
```

The rate limiter must count requests by the matched caller name, must not retain
article bodies, and must not reveal the caller's API key in metrics or logs.

## Lambda implementation behavior

The Lambda reads a configurable base URL:

```text
VI_INFERENCE_API_URL=https://vi-model-inference.codeforafrica.org
```

The domain must not be hardcoded in Python.

Illustrative flow:

```python
article_id = insert_pending_article(article)

try:
    result = inference_client.infer(
        request_id=str(uuid.uuid4()),
        article_id=article_id,
        article_text=article.text,
        target_country=article.target_country,
        inferred_actor=article.inferred_actor,
    )

    update_article_with_inference(
        article_id=article_id,
        result=result,
        inference_status="completed",
        ml_processed_at=now(),
    )
except RetryableInferenceError:
    mark_article_pending(article_id)
except PermanentInferenceError as error:
    mark_article_failed(article_id, error.code)
```

Recommended inference states are:

```text
pending
processing
completed
failed
```

If adding a status column is out of scope, `ml_processed_at IS NULL` can remain
the pending marker, but an explicit state is easier to operate and debug.

### Lambda retry policy

Retry:

- Connection failures.
- Timeouts.
- HTTP 429.
- HTTP 502.
- HTTP 503.
- HTTP 504.

Do not retry:

- HTTP 400.
- HTTP 401.
- HTTP 403.
- HTTP 413.

Use two or three attempts with exponential backoff and random jitter. Reuse the
same `request_id` for every attempt of one logical request.

At the beginning of each ingestion invocation, retry a bounded number of pending
articles. This allows recovery from a temporary inference outage without making
ingestion unbounded.

## Inference-server implementation behavior

The inference service must:

- Run as a long-lived web process rather than a one-shot management command.
- Load models once per process.
- Start with one application worker.
- Limit concurrent inference with an application semaphore.
- Keep `/healthz` responsive while models load.
- Return `503` from `/readyz` until all required models are usable.
- Reuse persistent `/models` storage across restarts.
- Avoid downloading models during individual requests.
- Report the loaded model version in every successful response.
- Stop accepting new work during graceful shutdown.

An initial process command may look like:

```bash
gunicorn \
  --workers 1 \
  --threads 2 \
  --timeout 180 \
  --bind 0.0.0.0:8000 \
  config.wsgi:application
```

The final timeout must be based on measured inference latency and aligned with
the load balancer timeout. Do not add multiple workers until memory usage has
been measured because each worker may load its own copy of the ensemble.

The API should use a small, dedicated URL configuration. It should not import
the dashboard's large view module merely to expose the inference route.

## Dokku and infrastructure requirements

Create a public HTTP Dokku application on the existing Open WebUI/Ollama host.

Suggested configuration:

```text
Dokku app:      vi-model-inference
Domain:         vi-model-inference.codeforafrica.org
Container port: 8000
Health check:   /healthz
Readiness:      /readyz
Model cache:    /models
```

Infrastructure must provide:

- A shared-host application definition.
- Public ALB routing for the domain.
- DNS and TLS certificate coverage.
- A dedicated ECR repository.
- GitHub OIDC deployment permissions for `CodeForAfrica/VI`.
- Persistent model-cache storage mounted at `/models`.
- Read-only S3 model-bucket permissions for the EC2 host.
- Secrets Manager access for inference authentication secrets.
- Dokku environment configuration.
- Application log limits and retention.
- GPU Docker configuration if the image uses CUDA-enabled PyTorch.

The service must not receive VI PostgreSQL credentials when Lambda owns database
updates.

The first classifier-image build and deployment workflow should be manual. This
is a large and expensive image; automatic builds should only be enabled after
build caching, storage use, and runner cost are understood.

## Groq and Ollama phases

Running on the Ollama host does not automatically replace Groq.

### Phase 1: retain Groq

```text
PyTorch ensemble -> Groq arbitration -> API response
```

This isolates the infrastructure and API migration from model-quality changes.

### Phase 2: configurable arbitration provider

Add provider configuration:

```text
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=<selected-model>
```

Supported values should include:

```text
LLM_PROVIDER=groq
LLM_PROVIDER=ollama
```

Expose one internal interface regardless of provider:

```python
class StrategicIntentArbitrator:
    def classify(self, text: str) -> ArbitrationResult:
        ...
```

Groq and Ollama become implementations of that interface. Before switching
production traffic, compare both providers against a fixed labelled article set
and verify allowed labels, confidence values, latency, and failure behavior.

## Capacity and GPU considerations

The existing host is a `g4dn.xlarge` with 16 GiB of system memory and one NVIDIA
T4 with 16 GB of GPU memory. It already runs Ollama, Open WebUI, shared Valkey
workloads, Dokku, and the operating system. These are co-located host workloads,
not dependencies of the inference API.

The inference ensemble reportedly requires approximately 13 GB resident memory.
The current classifier Dockerfile installs CPU-only PyTorch, so it cannot use the
T4 even though the Python code checks for CUDA.

Before production deployment:

1. Build the production API image.
2. Run it on the real host with the real model files.
3. Record startup time, system memory, GPU memory, and per-article latency.
4. Test while Ollama is also serving requests.
5. Resize the host if there is insufficient headroom.
6. Use a CUDA-enabled PyTorch image if GPU inference is required.
7. Decide whether Ollama and VI inference may run concurrently or must be
   serialized.

## Required tests

### API tests

- A valid authenticated request returns the documented schema.
- Missing authentication returns `401`.
- An unknown API key returns `401`.
- A revoked API key returns `401`.
- Invalid JSON returns `400`.
- Missing article text returns `400`.
- An oversized payload returns `413`.
- Exceeding the configured per-caller request limit returns `429`.
- Unavailable models return `503`.
- Internal inference errors return `500`, not `Neutral`.
- Every successful intent belongs to the allowed enum.
- Article text and authentication data do not appear in logs.

### Lambda tests

- A successful response updates the article.
- A timeout leaves the article pending.
- Retryable status codes are retried.
- Permanent status codes are not retried.
- The configured API key is sent only in the `X-API-Key` header.
- Neutral results are stored as processed results.
- Duplicate article URLs are not inserted again.
- A temporary API outage does not fail the ingestion run.

### Deployment tests

- `/healthz` works through the public domain.
- `/readyz` changes from `503` to `200` after model loading.
- Unauthenticated inference requests fail.
- A request with an accepted API key succeeds.
- Models are not downloaded again after a container restart.
- Dokku restart preserves the model cache.
- Only one copy of the model ensemble is loaded.
- A failed model load keeps the service out of readiness.
- System and GPU memory stay within safe limits under expected traffic.

## Acceptance criteria

The solution is complete when:

- `vi-model-inference.codeforafrica.org` is served over HTTPS.
- The inference endpoint accepts only authenticated requests.
- Models load once and remain cached across requests and restarts.
- Lambda sends newly ingested articles to the API.
- Successful predictions are persisted in PostgreSQL.
- API failures do not prevent article ingestion.
- Failed requests can be retried safely.
- `Neutral` is distinguishable from unprocessed.
- The inference server has no VI database credentials.
- The inference API does not depend on Redis, Valkey, a queue, or another
  persistence service.
- Secrets and article text do not appear in ordinary logs.
- Memory and latency have been measured on the actual host.
- The dashboard continues to read classifications without changing its public
  behavior.
