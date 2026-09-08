# Inference API split - phased implementation plan

Target architecture is defined in [`solution-spec.md`](../solution-spec.md).
This doc is the build plan to get there. It maps every spec requirement to a
phase, marks what already exists in the repo, and calls out the one decision
that has to be made first.

## The seam decision (read this first)

We currently have two competing shapes for the same split:

| | Seam | Who writes the DB | ML runs as |
|---|---|---|---|
| **PR #22 (shipped)** | DB table `dashboard_medianarrative` | classifier container | batch command `fill_missing_intents` |
| **solution-spec** | synchronous HTTP `POST /api/v1/inference` | the Lambda | long-lived web process |

These are mutually exclusive. Implementing the spec means the classifier
container from PR #22 becomes an HTTP inference service, and `fill_missing_intents`
stops being the seam (it can stay as an ops/backfill tool, but the Lambda no
longer depends on it). The spec is the better target - DB-less inference
service, per-article durability, warm models, traceable requests - so this plan
assumes we go with the spec's HTTP seam.

Nothing from PR #22 is wasted: all the ML, the model-cache work, the Groq fix,
the local-test harness, and the `Neutral`-not-NULL fix carry straight over.

## What already exists (reuse, do not rebuild)

- `ml_inference_service.perform_inference(article_text)` - runs the full
  ensemble + tone + Groq arbitration and returns intent/tone/confidence/actor/
  country. This is the whole ML payload the API needs.
- `config/wsgi.py` - the app is already WSGI, so gunicorn drop-in works.
- Persistent model-cache env wiring (`MODEL_CACHE_DIR`, `HF_HOME`) from PR #22.
- `check_groq` preflight, `GROQ_MODEL` config, JSON parser adapted for the
  reasoning model.
- Local-test pattern (fixture in, isolated Postgres, no ECR) from PR #22 - reuse
  the same approach for the API.
- `Neutral`-not-NULL handling (PR #22 marks with `ml_processed_at`; spec wants
  `Neutral` stored explicitly - small adjustment, see Phase 1).

## Phases

Each phase is independently reviewable and, through Phase 3, runnable locally
with no prod/RDS/ECR/GPU access.

### Phase 1 - Inference API (code, local)

The keystone. A small dedicated Django app + urlconf; must NOT import
`dashboard.views` (spec 668).

Endpoints:
- `GET /healthz` - process alive, never loads models (spec 253).
- `GET /readyz` - 200 with `model_version` once loaded, else 503 (spec 267).
- `POST /api/v1/inference` - the contract in spec 296-357.

Cross-cutting for the inference route:
- `X-API-Key` auth: accepted-key list `[{caller,key}]` from env/Secrets,
  constant-time compare, generic 401, matched caller name carried for logging
  and rate limiting (spec 476-529).
- Input bounds: `Content-Type: application/json`, body <=256 KB, re-check
  article-text length before tokenization (spec 464-468).
- Per-caller in-process rate limit, RPM from env, 429 + `Retry-After`; counters
  in memory, no Redis/queue (spec 531-562).
- Concurrency semaphore around model calls, single worker (spec 642-644).
- Structured JSON logs, one `request_id` per attempt, the never-log list
  enforced (spec 164-247).
- Error envelope for 400/401/413/429/500/503; a 500 must never come back as
  `Neutral` (spec 362-450).

Response mapping: adapt `perform_inference`'s return dict to the spec response
(add `request_id`, `model_version`, `prediction_source`, split
`strategic_intent_confidence`/`tone_confidence`, `processing_time_ms`). Store
`Neutral` as an explicit value here rather than mapping it to NULL.

Local test: gunicorn + fixture article, curl the three endpoints, assert schema,
401 on bad key, 400 on missing text, 413 on oversized body, 429 past the limit.
Same one-command style as PR #22's `make test`.

### Phase 2 - API test suite (code, local) - DONE

The API-tests block from spec 772-788 as real tests (PR #22 shipped with none;
this closes improvements.md #9 for the new surface). Auth, validation, payload
limits, rate limit, enum membership, log-redaction, 500-not-Neutral.

Implemented in `inference_api/tests.py` (32 tests, all passing), runnable with
`make test-api` (in the classifier container) or, since `inference_api/
tests_settings.py` is deliberately light, directly in any venv with Django +
requests + beautifulsoup4:
`python manage.py test inference_api --settings=inference_api.tests_settings`.
The suite is contract-level - the ML service is mocked and readiness toggled -
so it needs no torch, no model weights, and no DB server.

## Phase 1 testing status

Phase 1 ships unit/contract-tested. The integration boot is deferred to Phase 5
because it can only pass on a real host with the models and the GPU decision
made - stubbing it here would just duplicate Phase 5.

### Covered (verified with runnable checks, real code where noted)

- Auth: valid / bad / empty / multi-key, malformed accepted-keys env, matched
  caller returned (real `security.authenticate`).
- Rate limit: N allowed then `429` + `Retry-After` (real `RateLimiter`).
- Body-size, article-text-length, content-type, method, JSON-parse, and
  required-field validation, each with the right status + error envelope.
- Readiness gating: `/readyz` 503 -> 200; inference `503 models_not_ready` with
  `request_id` while loading.
- Response mapping (real `map_to_canonical_intent`): raw -> canonical,
  Neutral kept explicit (never NULL), 4dp rounding, missing-key defaults.
- No-service -> `RuntimeError` -> view returns `500` (never a bogus Neutral).

### Remaining to test (Phase 5 - needs container / real host)

- gunicorn boot of `config.inference_wsgi:application` under the real
  `config.settings` (whitenoise, csrf, dashboard app, DB env present).
- Background warmup against real weights; `/readyz` flips 503 -> 200 only when
  the ensemble is genuinely usable; `model_load_failed` keeps it out of ready.
- Model cache persists across container restart (no re-download).
- Only one ensemble copy loaded; system + GPU memory within limits under load.
- Concurrency semaphore serializes real inference; timeout aligned with the LB.
- `/healthz` reachable through the public domain; unauthenticated calls fail
  end to end.

### Full Phase-1 suite to implement in Phase 2 (`inference_api/tests.py`)

Health/readiness:
- `healthz` returns 200 `{"status":"ok"}` and does not touch the model.
- `readyz` returns 503 `{models_loaded:false}` before load, 200 with
  `model_version` after.

Auth:
- missing `X-API-Key` -> 401 generic envelope.
- unknown key -> 401; empty key -> 401; body never reveals which key/why.
- accepted key -> authenticates and carries caller through.
- malformed `VI_INFERENCE_ACCEPTED_KEYS` -> zero keys, everyone rejected.

Validation / limits:
- wrong content-type -> 400; non-JSON body -> 400; non-object JSON -> 400.
- missing `request_id` -> 400; missing/blank `article_text` -> 400.
- `Content-Length` over limit -> 413 (before body read); actual body over
  limit -> 413; `article_text` over char limit -> 413.
- GET/PUT on the inference path -> 405.

Rate limit:
- first N within a window pass; N+1 -> 429 with `Retry-After: 60`.
- limit is per-caller (one caller's usage does not throttle another).

Inference happy path (mocked service):
- 200 with `request_id` and `article_id` echoed, and the full response schema
  (`strategic_intent`, split `*_confidence`, `tone`, `prediction_source`,
  `model_version`, `processing_time_ms`).
- every returned `strategic_intent` is in the allowed enum (incl. `Neutral`).

Failure / safety:
- not ready -> 503 `models_not_ready` + `Retry-After` + `request_id`.
- service raises -> 500 `inference_failed`, never a `Neutral` result.

Logging discipline (spec 238-247):
- the `X-API-Key` value and full `article_text` never appear in emitted logs.

### Phase 3 - Lambda rewrite (code, local-testable) - DONE

Implemented: `inference_client.py` (pure, requests-only HTTP client with the
retry/no-retry classification, backoff+jitter, response validation) and a
rewritten `lambda_function.py` that ingests, then drains pending rows
(`ml_processed_at IS NULL`, bounded by `VI_INFERENCE_MAX_PER_RUN`) through the
client and writes back predictions. Inference runs AFTER ingestion so an
outage never blocks ingestion; failures stay pending; Neutral is stored
explicitly; structured JSON logs per step with a reused `request_id`; final
summary counts. New env vars documented in `.env.example`. Client logic verified
(19 checks); the drain loop against a mocked DB is Phase 4.

Replace the removed ML step (PR #22 already stripped it) with an HTTP client:
insert-pending -> call API -> validate -> write back; leave pending on failure
(spec 105-118, 564-635).
- `VI_INFERENCE_API_URL` from env, never hardcoded (spec 566-572).
- One `request_id` per logical request, reused across retries.
- Retry 429/502/503/504/timeouts; do not retry 400/401/403/413; 2-3 attempts,
  exponential backoff + jitter (spec 613-631).
- Bounded pending-retry at the start of each invocation (spec 633-635).
- Structured logs + final count summary (spec 194-208).
- Decide pending marker: reuse `ml_processed_at IS NULL`, or add an explicit
  `inference_status` column (spec 600-610 - explicit column preferred but
  optional).

Local test: run the API locally, point a local Lambda-handler invocation at it,
assert pending->completed, timeout leaves pending, retryable vs permanent codes.

### Phase 4 - Lambda test suite (code, local)

The Lambda-tests block from spec 790-802.

### Phase 5 - Infra + deploy (NOT code, needs cloud access)

Deferred until Phases 1-4 are green. Owned at deploy time, tracked here so it
isn't forgotten:
- Dokku app `vi-model-inference` on the Ollama/Open WebUI `g4dn.xlarge` host;
  public ALB, DNS, TLS for `vi-model-inference.codeforafrica.org`.
- Dedicated ECR repo; GitHub OIDC for `CodeForAfrica/VI`; **manual** first build
  (large/expensive image, spec 700-704).
- Persistent `/models` volume; read-only S3 model-bucket perms.
- Secrets Manager: API accepted-key list + the Lambda's key; rotation support.
- **GPU decision:** current Dockerfile is CPU-only torch, so the T4 is unused
  (spec 754-765). Measure memory + latency on the real host with real weights,
  alongside Ollama, before deciding CUDA torch / host resize / serialization.
- **MediaCloud key rotation + git-history scrub** (still owed from PR #22, see
  [[vi-mediacloud-key-leak]]).

### Phase 6 - Ollama arbitration (optional, later)

Spec Phase 2 (spec 707-745): `StrategicIntentArbitrator` interface with Groq and
Ollama implementations, `LLM_PROVIDER` switch. Ship only after Phase 1-5 are
live and after comparing both providers on a fixed labelled set. Not on the
critical path.

## Deployment / testability guardrails

- Phases 1-4 run fully locally: isolated Postgres, fixtures, no RDS/ECR/GPU.
- Inference service gets no VI Postgres credentials (spec 136-138, 699).
- No new persistence dependency (no Redis/Valkey/queue) - hard constraint
  (spec 828-830).
- Secrets and full article text never logged (spec 238-247).

## Suggested PR breakdown

1. Phase 1 + 2 - inference API + its tests (self-contained, local).
2. Phase 3 + 4 - Lambda HTTP client + its tests.
3. Phase 5 - infra (separate, deploy-time, likely IaC repo not this one).
4. Phase 6 - Ollama arbitration (separate, later).
