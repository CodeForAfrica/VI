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
│   │   └── run_full_pipeline.py
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
├── lambda_function.py                # AWS Lambda handler
├── contextual_all_intents_v2.py      # Contextual signal computation
│
├── merged_dataset.csv                # Narrative dataset
├── Journalist.csv
├── MediaOutlet.csv
├── final_risk_by_actor_intent_country.csv
│
├── Dockerfile
├── Dockerfile.lambda
├── requirements.txt
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
