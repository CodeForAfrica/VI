# Vulnerability_index_tool
![Python](https://img.shields.io/badge/Python-3.10+-blue)
![Django](https://img.shields.io/badge/Django-Framework-green)
![License](https://img.shields.io/badge/License-MIT-yellow)

The **Vulnerability Index Tool** is a data analytics platform designed to measure how vulnerable countries are to **Foreign Information Manipulation and Interference (FIMI)** and other influence operations conducted by external actors.

The system integrates **media monitoring, machine learning classification, narrative analysis, and geopolitical context indicators** to compute a **Vulnerability Index score** that reflects the exposure of a target country to external influence campaigns.

The platform provides:

- automated **media ingestion**
- **machine learning inference** for tone and narratives
- **actor–intent analysis**
- **vulnerability index computation**
- an interactive **analytical dashboard**
- automated **report generation**

---

## Removed unused dashboard routes

`/intents/` and `/all-articles/` are intentionally not exposed. Neither was linked
from the dashboard navigation or templates, and both referenced missing templates,
causing HTTP 500 on direct access. Their unused handlers were removed rather than
introducing new pages outside the current dashboard scope. Direct requests now
return HTTP 404; the existing overview filters and `/articles/` search remain.

## Dashboard translation

### Local dashboard verification — one command

```bash
make run-local-dashboard
```

Prerequisites: Git, Make, Python 3 (standard library only), gzip and a running Docker installation with Compose v2
(`up --wait` support). No Python packages/virtualenv, Node.js, gettext, AWS profile, API key,
Git LFS pull or ML model download is needed. On Windows use WSL2 with Docker
integration. First use downloads the application dependencies and a pinned
Playwright/Chromium image; later runs reuse the Docker cache.

This is a **verification run**, not a persistent development server. It:

1. Builds the production dashboard image and compiles EN/FR catalogues.
   All subsequent runtime checks use its immutable image ID, without rebuilding.
   Compares every installed runtime version with `requirements.txt`, runs
   `pip check` and a pinned Trivy scan of that image's OS and installed
   Python packages. Database/scanner/network failures stop the run. Any critical
   vulnerability blocks, including unfixed ones; high/medium/low/unknown findings
   remain visible in `.dashboard-check/security.json` (not silently ignored).
2. Extracts current marked UI strings and fails on missing, empty, fuzzy or
   placeholder-invalid French translations. Catalogue checks cover marked
   messages, not arbitrary article text or unmarked future strings.
3. Starts the isolated database/cache and runs the full automated suite,
   including the PostgreSQL locking tests that otherwise skip without a DB.
4. Migrates an isolated local Postgres and loads 25 synthetic saved article
   results; starts Redis and the actual Gunicorn dashboard. First boots the
   uninstrumented production `config.wsgi:application` and requests `/chat/`
   to exercise production imports, URL loading and template rendering. A dummy
   non-secret key permits Groq SDK construction; no provider calls are made by
   that startup smoke. Then starts the instrumented browser-test server.
5. Uses Chromium to exercise ten routes in both languages, language switching,
   filters, real Plotly charts, article rendering, CSRF-protected chat and PDF
   responses. Third-party CDN styling is excluded from these functional checks;
   the installed Plotly bundle is served locally.

By default the stack has no published ports, production data, mounted
credentials or external runtime network access. Test-only WSGI wiring supplies
controlled Amazon Translate/Groq responses. Provider contract/failure tests are
included. Ordinary production WSGI is unchanged.

#### Same release gate locally and on GitHub

```bash
make run-local-dashboard
```

No live AWS/Groq calls or credentials are required for release checks. Both
locally and in GitHub, the required gate checks installed dependency pins,
dependency consistency, vulnerabilities, catalogue validation, the full suite,
production WSGI startup and EN/FR browser/PDF functionality using controlled
external-provider responses. Optional live checks remain available below; they
are never a publishing requirement. These checks do not certify live provider
availability/credentials, ML inference accuracy, production data, host capacity
or third-party CDN styling.

A passing run writes `.dashboard-check/passed.json`, binding checks to the
image configuration/layers, commit, provider modes, security report and workflow
run. A new run removes old receipts before starting. Failed checks/cleanup cannot
leave a passing receipt. Local uncommitted changes can be tested, but **cannot be
published**; checks must run again from the final clean commit on GitHub.

`deploy_dashboard_to_prod` invokes `scripts/test-local.sh`, the same script that
`make run-local-dashboard` invokes, with `AWS_PROFILE=` and `GROQ_API_KEY=` before
registry login. Invoking the script directly avoids requiring Make on CFA runners
while running identical checks. Empty provider variables prevent inherited runner credentials
from opting into live tests. It tags/pushes the checked image ID, never rebuilds it, then pulls the
registry digest and verifies identical runtime configuration/filesystem layers
before passing that digest to Dokku. The compatibility `main` image tag is updated
to the same checked image, but deployments never select it. Invalid, stale (over two hours), altered or
other-run receipts block promotion. Receipt/scan evidence is retained for three
days; it contains package metadata, not provider credentials.

Existing `CFA_RUNNER_LABELS` and Dokku/Docker Hub deployment secrets remain
required. No extra AWS role, OIDC permission, Groq secret variable or AWS CLI is
required by the workflow. Runner requires Bash, Git, Python 3, gzip and Docker
Compose v2. The workflow explicitly sets up Compose v5.1.4 (the locally tested
version) inside the job's isolated Docker configuration, with binary caching
disabled; it does not depend on a Compose plugin preinstalled on the shared
runner. Runtime provider credentials on production remain unchanged; this
gate does not create, read or alter them.

**High-cost workflow risk:** existing automatic main-push and manual triggers are
retained: one ARM64 build (QEMU on x64 CFA runners), no matrix, bounded 60-minute
build/check job. Scan database and Docker layer caches consume runner disk; the
temporary compressed image archive is removed after a successful scan. GitHub
cache exports are no longer used; cache reuse depends on the selected runner and
builder (a new ephemeral builder may need a cold build).
The required gate incurs no AWS/Groq call charges or rate-limit cooldown. Concurrency stays
serialized with `cancel-in-progress: false` to avoid interrupting a Dokku release.
Disable the main `push` trigger to make releases manual-only. No production change
is made by the local command; deployment/rollback remain the existing Dokku path.

#### Optional live Amazon Translate and Groq checks

Supplying `AWS_PROFILE` and/or `GROQ_API_KEY` automatically opts the selected
provider into **live calls using synthetic local articles only**. These calls
can incur charges. Providers without credentials remain controlled and are
explicitly labelled; a controlled provider is never reported as verified live.

```bash
# Use your own configured AWS profile (AWS CLI v2 required on the host).
make run-local-dashboard AWS_PROFILE=hannah AWS_REGION=eu-west-1

# zsh/bash: enter your Groq key without putting its value in shell history.
read -r -s GROQ_API_KEY
export GROQ_API_KEY
make run-local-dashboard

# Both live together, using the exported Groq key and selected AWS profile.
make run-local-dashboard AWS_PROFILE=hannah AWS_REGION=eu-west-1
```

AWS profiles are resolved on the host using AWS CLI v2's `configure
export-credentials`. SSO users should first run `aws sso login --profile hannah`.
Only the resolved credentials are temporarily passed as a read-only Compose
secret; the whole `~/.aws` directory is never mounted. The selected identity
needs `translate:TranslateText` and `comprehend:DetectDominantLanguage` in the
chosen region. The region defaults explicitly to `eu-west-1` (set `AWS_REGION`
to change it; the profile's implicit region is not used).

The Groq key is also passed as a temporary file, not a Docker build argument or
container environment entry. Only the running dashboard receives the secret
mounts and runtime egress; the automated suite still runs offline without keys.
The host secret files live in a private temporary directory and are removed on
success/failure. Docker administrators can access running container secrets;
use a trusted PC/Docker daemon and least-privilege development credentials.

The live run exercises actual display translation, AI-backed chat, four article
summaries and the overall analysis for each language's PDF. It checks successful
provider call counts and fails on any provider error or empty answer, even if
the application returns HTTP 200 with fallback content. Logs show each provider's
live/controlled mode and success/failure counts, never credentials. Groq calls
use the application's existing models, with bounded timeouts and no SDK retries.
Model accuracy, semantic translation quality and ML inference are not assessed
by this dashboard-only command.

With live Groq enabled, the browser run waits 75 seconds between the English
and French flows, logging progress every 25 seconds. This avoids bursting both
languages' AI requests into one minute's quota. It does not retry failed calls:
shared-account traffic or exhausted quotas can still return HTTP 429 and fail
the run. Controlled-provider runs do not wait.

To force the credential-free run when your terminal already exports provider
credentials:

```bash
make run-local-dashboard AWS_PROFILE= GROQ_API_KEY=
```

Each stage logs progress. Failure exits non-zero with the stage and service
logs; success prints an explicit coverage summary. Its own containers/fixture
data are removed on either outcome, while Docker images are retained for reuse.
Do not run two copies simultaneously. Fix any failure and rerun before pushing;
this command does not enforce Git push/merge permissions or modify workflows.

Use `make help` to choose a run. For Lambda/inference changes use the explicit
commands below, not the dashboard-only command.

The dashboard interface supports English and French through Django's language
selector. Labels are translated; submitted country, actor and intent values stay
as canonical database keys. The language cookie preserves the selection between
requests without rewriting article data. Report headings/form labels are also
translated. Article-body translation and AI-generated output retain their
existing behavior; this is not automatic French translation of all content.

The Docker build installs GNU gettext in a build-only stage and compiles both
catalogues from `locale/`, so no manual EC2 package installation is required.
When editing translations locally, install gettext for your operating system,
then run `python manage.py compilemessages --locale en --locale fr --ignore=venv`.
Run bilingual regression tests with
`python manage.py test test_bilingual_dashboard --settings=inference_api.tests_settings`.

Dashboard display translation uses Amazon Translate in `AWS_REGION_NAME`
(default `eu-west-1`). Production uses the EC2 instance role, not human access
keys in the container. The role needs `translate:TranslateText` and
`comprehend:DetectDominantLanguage` for automatic language detection. Successful
translations are cached for 24 hours; failures keep the original text and are
cached for 60 seconds to avoid retry storms. Requests have a 2-second connection
timeout, a 3-second read timeout, and no SDK retries. Translate and automatic
language detection are billable; caching avoids paying again on every page load.

## Targeted correction of saved LGBTQ classifications

Deploy the updated Lambda mapping before reprocessing: `Human Rights Advocacy`
now maps to `SocialFragility`, not `LGBTQ`. This corrects the mapping; it does not
guarantee that the underlying model prediction is correct or add calibration.

Use the additive command below, **not** `fix_mappings` (which also performs the
General News keyword cleanup) or `reset_other_intents_to_null` (which targets Other).
Run it in the dashboard environment connected to the intended database:

```sh
# Preview only; neither resets records nor calls inference.
python manage.py reset_lgbtq_intents_to_null

# After reviewing the IDs and approving the database change:
python manage.py reset_lgbtq_intents_to_null --apply --backup /tmp/vi.lgbtq-reset-backup.json
```

Anchor records are excluded unless explicitly approved with `--include-anchors`.
Empty/unavailable article content is excluded. The reset stores the previous
intent, confidence, tone and processed timestamp in a new mode-0600 JSON file
before clearing **only** `strategic_intent` and `ml_processed_at` for the recorded
IDs. Keep that backup outside Git and copy it to durable, access-controlled storage.
An existing backup file is never overwritten; backup failure aborts the reset.

The command prints a scoped Lambda event, for example:

```json
{"operation": "processing-only", "article_ids": [82651]}
```

Invoke the updated Lambda with the **actual IDs from the backup**, not the example.
Each event accepts at most `VI_INFERENCE_MAX_PER_RUN` IDs (default 200); split
larger ID lists into batches. Repeat the same event after the previous invocation
finishes until `left_pending` and `failed` are zero. Completed IDs are skipped;
unrelated pending rows are not selected by this event. A failed HTTPS inference
stays pending rather than saving fallback values during this targeted rerun.
Existing daily and four-hour scheduled events retain their normal behavior and
may also process these newly pending records; do not overlap manual invocations.

For rollback, stop/wait for processing first, then restore the four saved result
fields by each backup record's `id` in a database transaction, parsing the saved
`ml_processed_at` as a datetime (or keeping null). Do not reset other categories,
modify article text, or run the General News cleanup as part of this correction.

# Concept

Influence operations are not only driven by messaging strength. Their impact depends heavily on **pre-existing vulnerabilities within the target environment**, including:

- economic dependencies
- geopolitical alignments
- military partnerships
- political instability
- social polarization
- media ecosystem fragility

The **Vulnerability Index** captures these dynamics by combining:

1. **Content Signals** derived from narrative monitoring
2. **Contextual Signals** describing geopolitical exposure

Together, these signals produce a **single interpretable score summarizing vulnerability to influence campaigns**.

---

# Methodology

## Content Signal

The **Content Signal** measures information pressure targeting a country.

Indicators include:

- narrative volume
- strategic intent distribution
- tone and sentiment
- actor–target narrative relationships
- narrative amplification

These signals capture **how actively external actors attempt to shape the information environment**.

## Contextual Signal

The **Contextual Signal** measures structural vulnerabilities within the target country.

Examples include:

- geopolitical dependencies
- economic exposure
- natural resource ties
- military relationships
- political fragility
- social tensions

These factors determine **how receptive the environment may be to influence narratives**.

## Vulnerability Index

The final score is computed as a function of both signals: Vulnerability Index = f(Content Signal, Contextual Signal)

The score ranges between **0 and 1**.

| Score Range | Interpretation |
|-------------|---------------|
| 0.00 – 0.30 | Low vulnerability |
| 0.31 – 0.60 | Moderate vulnerability |
| 0.61 – 1.00 | High vulnerability |

---

# System Architecture
            ┌─────────────────────────┐
            │   Media Sources         │
            │  (MediaCloud, datasets) │
            └─────────────┬───────────┘
                          │
                          ▼
              ┌─────────────────────┐
              │ Data Ingestion      │
              │ MediaCloud Service  │
              └─────────────┬───────┘
                            │
                            ▼
                 ┌──────────────────────┐
                 │ ML Inference Layer   │
                 │                      │
                 │ - Tone Ensemble      │
                 │ - Calibrated Models  │
                 │ - Narrative Analysis │
                 └─────────────┬────────┘
                               │
                               ▼
                 ┌────────────────────────┐
                 │ Feature Aggregation    │
                 │                        │
                 │ - Content Signals      │
                 │ - Contextual Signals   │
                 └─────────────┬──────────┘
                               │
                               ▼
                 ┌────────────────────────┐
                 │ Vulnerability Index    │
                 │ Calculation Engine     │
                 └─────────────┬──────────┘
                               │
                               ▼
                 ┌────────────────────────┐
                 │ Dashboard + Reports    │
                 └────────────────────────┘
                 
---

# Deployment Topology (ingestion / inference split)

The ML ensemble (~13GB) exceeds AWS Lambda's 10GB image limit, so ingestion and
model inference run as separate deployments:

- **Ingestion (AWS Lambda)** — `lambda_function.py` pulls articles from
  MediaCloud, stores them in PostgreSQL, and calls the inference service over
  HTTPS. Its `Dockerfile.lambda` contains no PyTorch, Transformers, or model
  weights.
- **Inference (Dokku)** — `Dockerfile.classifier` runs a long-lived HTTP service
  at `https://vi-model-inference.codeforafrica.org`. It authenticates requests
  with one `X-API-Key`, keeps models warm, and persists its model cache at
  `/models`. It has no PostgreSQL, Redis, or Valkey dependency.

The Lambda inserts an article before requesting inference. If the API is down,
the article stays pending and a later invocation retries it. Because the API is
public HTTPS, Lambda needs only its URL and API key; it does not need an ingress
security-group rule for the inference host. Any existing Lambda VPC settings
may still be required for private PostgreSQL access.

See [solution-spec.md](solution-spec.md) for the full contract, security model,
traffic flow, failure handling, and logging requirements.

## Lambda operation modes

The normal daily event still ingests articles, validates them, then classifies
pending rows. The additional event below skips MediaCloud ingestion and quality
validation and spends the remaining Lambda time budget on existing pending rows:

```json
{"operation": "processing-only"}
```

For ingestion into the database plus the existing quality validation, without
classification or inference API calls, use:

```json
{"operation": "ingestion-only"}
```

Omitting `operation` (for example `{}` or the existing daily EventBridge event)
keeps ingestion followed by processing. The operation is included in logs;
ingestion-only responses explicitly report processing skipped and unknown
pending counts, rather than suggesting that the backlog is empty.

All Lambda classification paths (daily, pending-only, and targeted verification) acquire
the same non-blocking PostgreSQL session advisory lock before selecting rows.
If another worker holds it, classification exits with
`reason: classification_lock_held`; the pending counts are `null` because no
queue query ran. Daily ingestion is not blocked. The lock survives per-article
commits/rollbacks and is released on completion, failure, or session termination;
no Redis, new table, or database migration is needed. Unknown operation names
fail rather than accidentally starting ingestion.

Pending-only runs retain the oldest-first selection, the existing 200-row cap,
the safety margin before timeout, and all existing prediction/fallback rules.
An empty queue exits without creating an inference client. Logs identify mode,
ingestion skipped, lock acquisition/busy/release, time-budget stops, per-article
results, and completion counts. `left_pending` describes the selected batch,
not the total database backlog. Transport fallbacks already saved as processed
are not made retryable by this change.

**Rollout:** deploy this Lambda code first, then add a separate four-hour
EventBridge trigger with `rate(4 hours)` and input
`{"operation":"processing-only"}`.
Keep the daily rule unchanged. This adds up to six pending-only invocations per
day, each bounded by the existing 15-minute runtime and article cap. This is a
legacy app: manage this schedule directly with AWS CLI until the Lambda is
migrated to Pulumi. Do not apply the app repo's legacy Terraform just to enable
this rule or add it through the deployment workflow.

**Verify after rollout:** confirm pending-only logs never show ingestion, check
saved classifications across successive runs, and check a competing classifier
logs a lock-busy skip. **Rollback:** disable the new four-hour rule before
restoring older Lambda code; an older handler would treat this event as normal
daily ingestion. No database rollback is required.

## Deploying the inference service

The `Deploy model inference` GitHub Actions workflow is deliberately
manual-only because the model image is large. It builds one `linux/amd64` image,
reuses an immutable ECR image for the same source/config when possible, and
serializes releases so two model deployments cannot compete for host capacity.

After the corresponding IaC stack has been applied, configure:

- Repository variable `VI_MODEL_INFERENCE_PULUMI_STACK`, set to the fully
  qualified `cfa-platform-infra-vi-model-inference/prod` stack reference.
- Repository secret `PULUMI_ACCESS_TOKEN`, scoped to read that stack.

The workflow reads the live role ARN, ECR repository, Dokku host, app name, and
URL from Pulumi. Do not copy those replaceable values into GitHub variables.
The Lambda's sensitive Terraform variable `inference_api_key` must equal the
`key` in one entry of the inference stack's `inferenceApiKeys` JSON secret; the
API uses that entry's `caller` only for identification, rate limiting, and logs.

For an operator-initiated deployment from a trusted workstation, the equivalent
local path also reads those live Pulumi outputs and deploys through AWS Systems
Manager rather than SSH:

```bash
AWS_PROFILE=cfa-bootstrap \
  scripts/deploy-model-inference-locally.sh \
  tech-codeforafrica-org/cfa-platform-infra-vi-model-inference/prod
```

The script builds and pushes a `linux/amd64` image only when the current source
fingerprint is absent from ECR, deploys its immutable digest to Dokku, then waits
for `/healthz` and `/readyz`. It never reads or prints the inference API key.

---

# Data Pipeline

The platform includes a **data pipeline implemented through Django management commands**.

Pipeline steps include:

1. Import media outlets
2. Import journalists
3. Import articles
4. Ingest MediaCloud datasets
5. Extract and link authors
6. Perform machine learning inference
7. Aggregate narrative signals
8. Compute vulnerability scores

The pipeline can be executed step-by-step or using a **full automated pipeline command**.

---

# Repository Structure

```
Vulnerability_index_tool/
│
├── dashboard/                        # Django application
│
│   ├── models.py                     # Database models
│   ├── views.py                      # Dashboard views
│   ├── urls.py                       # Application routes
│
│   ├── services/                     # Core analytical services
│   │   ├── calibrated_ensemble.py
│   │   ├── tone_ensemble.py
│   │   ├── calibrators.py
│   │   ├── mediacloud_ingestion_service.py
│   │   ├── ml_inference_service.py
│   │   └── summarizer.py
│
│   ├── management/commands/          # Data pipeline commands
│   │   ├── ingest_mediacloud.py
│   │   ├── import_articles.py
│   │   ├── import_journalists.py
│   │   ├── import_media_outlets.py
│   │   ├── extract_authors.py
│   │   ├── link_journalists.py
│   │   ├── link_media_outlets.py
│   │   ├── fill_posting_time.py
│   │   ├── migrate_profiles.py
│   │   ├── calculate_vulnerability_index.py
│   │   ├── run_full_pipeline.py
│   │   ├── fill_missing_intents.py     # Classifier half: fills null intents
│   │   ├── check_groq.py               # Fail-loud Groq preflight
│   │   └── show_results.py             # Print classification results (local test)
│
│   ├── templates/                    # Dashboard HTML templates
│   ├── static/                       # Static assets
│   └── migrations/                   # Database migrations
│
├── config/                           # Django configuration
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
│
├── terraform/                        # Infrastructure as Code
│   ├── main.tf
│   ├── variables.tf
│   └── outputs.tf
│
├── lambda_function.py                # AWS Lambda handler (ingestion only)
├── contextual_all_intents_v2.py      # Contextual signal computation
│
├── fixtures/
│   └── test_articles.json            # Sample articles for the local test
├── docs/
│   └── improvements.md               # Codebase improvement backlog
│
├── Journalist.csv
├── MediaOutlet.csv
├── final_risk_by_actor_intent_country.csv
│
├── Dockerfile                        # Web app image
├── Dockerfile.lambda                 # Ingestion Lambda image
├── Dockerfile.classifier             # Classification container image
├── docker-compose.classifier.yml     # Local end-to-end classification test
├── requirements.txt                  # Full deps (web + ML)
├── requirements-lambda.txt           # Ingestion-only deps (Lambda)
├── Makefile
└── manage.py
```
# Installation

Clone the repository:

```bash
git clone https://github.com/hanna-tes/Vulnerability_index_tool.git
cd Vulnerability_index_tool
```

Create a virtual environment:

```bash
python -m venv venv
```

Activate the virtual environment.

**Mac / Linux**

```bash
source venv/bin/activate
```

**Windows**

```bash
venv\Scripts\activate
```

Install the required dependencies:

```bash
pip install -r requirements.txt
```

---

# Running the Application

Apply database migrations:

```bash
python manage.py migrate
```

Start the Django development server:

```bash
python manage.py runserver
```

---

# Local Testing (classification split)

The classification half runs end-to-end locally against an **isolated Postgres**,
with no access to production and no RDS or ECR required. The classifier image is
built on your machine from `Dockerfile.classifier`; `docker-compose.classifier.yml`
pins `DB_HOST` to the local db so a run physically cannot reach prod.

The dedicated Dokku inference API (`config.inference_wsgi`) runs only the local
strategic-intent and tone models. Lambda keeps its existing orchestration and
replaces only those in-process classifiers with an HTTP adapter. The legacy
classifier/dashboard path below continues to run in-process.

### Debugging Lambda inference

Lambda emits structured JSON events with invocation and article IDs. Follow
`local_inference_request_started` → `local_inference_retry` (if any) →
`local_inference_request_completed` or `local_inference_request_failed`.
The same `request_id` is sent to the API and reused across retries, so it can
also be searched in the server logs. Requests log the host/path, input length,
timeout and retry limit; responses log HTTP status, predictions/confidences,
model version, attempts and timings. Failures log error codes and fallback use.
`arbitration_started`/`arbitration_completed` cover the caller's separate prediction;
`article_inference_completed` shows the final combined result and
`article_classification_saved` confirms the database write. A completed HTTP
request alone does not mean the result was saved. Article bodies, authentication
headers and raw response bodies are deliberately excluded from these events.

### Prerequisites

- Docker. The default fully local mode requires at least 20GB memory; a 16GB
  allocation can be OOM-killed while loading the complete ensembles.
- Git LFS only for fully local mode. Remote mode does not download or load models.

### Production-shaped Lambda/API test

Run the changed architecture end to end with one command:

```bash
make run-local-dashboard-with-lambda-local-inference
```

To exercise the real Lambda path against the deployed API while keeping the
database isolated and local, supply its API key:

```bash
make run-local-dashboard-with-lambda-remote-inference
```

Export `VI_INFERENCE_API_KEY` in your terminal before the remote command;
do not commit it or put it in a shared terminal transcript. Remote inference uses
`https://vi-model-inference.codeforafrica.org`. Set `VI_INFERENCE_API_URL` as
well only when intentionally testing another deployment. The key is never
printed. Remote mode skips Git LFS and starts Postgres, Redis, the dashboard and
the Lambda verifier, so it does not need the 20GB local-model allocation.

The local-inference command rejects a set API key (unset it first), builds the real Lambda and inference images, starts an
isolated Postgres, and serves the local inference API through trusted HTTPS.
Both modes also build the real dashboard image with an isolated Redis cache.
The remote-inference command requires the key and never silently switches to local models.
Both modes load ten deterministic articles and invoke the Lambda's real
classification path. The output shows
seven numbered stages and one `ARTICLE nn/10 PASS` line per fixture, including its
saved intent, tone, and confidence. It also retains the earlier ten-row results
table with `id`, intent, confidence, tone, and processed time. The verifier checks
the fixture/pending order, all ten HTTPS responses, API-to-Lambda prediction
agreement, database persistence, and the empty pending queue. The final stage
checks dashboard pages, all ten articles' rendered titles/summaries/links and
saved intent/tone, and a CSRF-protected database-backed chat request. It never
contacts a production database; remote mode contacts only the selected inference
API. The local API key and database password are
intentionally non-secret and only exist inside the Compose network.

During the test the dashboard is available at `http://127.0.0.1:8036` (override
with `VI_E2E_DASHBOARD_PORT`). It is removed with the test stack when the command
exits. A loopback-only proxy exposes that port while the dashboard stays on an
internal-only network that blocks external provider calls; AWS translation,
provider-backed AI chat, PDF report generation, and browser JavaScript execution
are **not** covered by these dashboard checks. Original text is displayed when
translation credentials are absent. No AWS profile or Groq key is required.

The original machine-readable `e2e_https_ready` and `split_e2e_passed` events,
per-result `id`/`strategic_intent`/`confidence`/`tone`/`processed` fields, and
final success message remain unchanged for existing readers. The staged and
per-article output is additional information.

This is an architecture/regression test: it proves that the frozen model output
travels through every split component unchanged and in the correct order. It is
not a model-accuracy benchmark against human judgement because the sample fixture
does not currently contain reviewed expected intent/tone labels. Add a separately
reviewed labelled fixture before describing these ten predictions as semantically
correct.

All required models live in `model_cache/` and are managed by Git LFS. The test
always runs `git lfs pull` for that directory, verifies every tracked artifact,
and then starts Docker. Git LFS reuses objects already present locally, while a
fresh checkout downloads the full model set. Clear progress messages distinguish
model setup, image build, stack startup, and test completion. Inside Docker,
Transformers and Hugging Face Hub remain in offline mode, so they cannot silently
fetch a different model. No AWS profile, cloud credentials, Hugging Face download,
or model-bucket access is required.

The equivalent raw command is:

```bash
docker compose -f docker-compose.e2e.yml -f docker-compose.e2e.dashboard.yml up --build \
  --abort-on-container-exit --exit-code-from e2e
```

### Commands

| Command | Description |
|---------|-------------|
| `make run-local-dashboard` | Credential-free dashboard build, full suite, French catalogue and EN/FR browser/PDF checks; no Lambda/models |
| `make run-local-dashboard-with-lambda-remote-inference` | Local dashboard/Lambda/Postgres, deployed HTTPS inference; requires API key, no model downloads |
| `make run-local-dashboard-with-lambda-local-inference` | Whole stack including local inference/models; Git LFS and >=20 GB Docker memory |
| `make test-split-e2e` | Deprecated compatibility alias; retains old key-based local/remote selection |
| `make test` | Build locally, then run the full pipeline in one shot: migrate, seed the sample fixture as unclassified rows, verify Groq, classify, print results |
| `make results` | Print the current classification (`strategic_intent` / confidence / tone / processed-at) of every row |
| `make reset` | Reload the fixture, resetting the rows back to unclassified |
| `make verify` | Assert 0 rows would be reprocessed — proves the blank-intent guard |
| `make down` | Stop the test containers (keeps the db data) |
| `make clean-test` | Stop and wipe the local test database |

The sample articles live in `fixtures/test_articles.json` and stand in for the
Lambda ingestion output, so the run is deterministic and offline. The Git LFS
models in `model_cache/` are mounted read-only and reused on subsequent runs.
