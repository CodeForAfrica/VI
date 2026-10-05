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
make test-split-e2e
```

To exercise the real Lambda path against the deployed API while keeping the
database isolated and local, supply its API key:

```bash
make test-split-e2e VI_INFERENCE_API_KEY='<deployed-api-key>'
```

Supplying the key automatically selects
`https://vi-model-inference.codeforafrica.org`. Set `VI_INFERENCE_API_URL` as
well only when intentionally testing another deployment. The key is never
printed. Remote mode skips Git LFS and starts only Postgres plus the Lambda
verifier, so it does not need the 20GB local-model allocation.

Without a key, this builds the real Lambda and inference images, starts an
isolated Postgres, and serves the local inference API through trusted HTTPS.
With a key, it builds only the Lambda image and calls the deployed HTTPS API.
Both modes load ten deterministic articles and invoke the Lambda's real
classification path. The output shows
six numbered stages and one `ARTICLE nn/10 PASS` line per fixture, including its
saved intent, tone, and confidence. It also retains the earlier ten-row results
table with `id`, intent, confidence, tone, and processed time. The verifier checks
the fixture/pending order, all ten HTTPS responses, API-to-Lambda prediction
agreement, database persistence, and the empty pending queue. It does not contact
production databases or APIs. The local API key and database password are
intentionally non-secret and only exist inside the Compose network.

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
docker compose -f docker-compose.e2e.yml up --build \
  --abort-on-container-exit --exit-code-from e2e
```

### Commands

| Command | Description |
|---------|-------------|
| `make test-split-e2e` | Run Git LFS model setup, build the real split images, and verify Lambda → trusted HTTPS inference API → local Postgres with ten articles |
| `make test` | Build locally, then run the full pipeline in one shot: migrate, seed the sample fixture as unclassified rows, verify Groq, classify, print results |
| `make results` | Print the current classification (`strategic_intent` / confidence / tone / processed-at) of every row |
| `make reset` | Reload the fixture, resetting the rows back to unclassified |
| `make verify` | Assert 0 rows would be reprocessed — proves the blank-intent guard |
| `make down` | Stop the test containers (keeps the db data) |
| `make clean-test` | Stop and wipe the local test database |

The sample articles live in `fixtures/test_articles.json` and stand in for the
Lambda ingestion output, so the run is deterministic and offline. The Git LFS
models in `model_cache/` are mounted read-only and reused on subsequent runs.
