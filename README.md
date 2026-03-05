# Vulnerability_index_tool

The **Vulnerability Index Tool** is a data analytics platform designed to measure how vulnerable countries are to **Foreign Information Manipulation and Interference (FIMI)** and other influence operations.

The system combines **media monitoring, machine learning classification, contextual geopolitical indicators, and narrative analysis** to generate a **Vulnerability Index score** representing exposure to influence operations by external actors.

The platform includes:

- Automated **media ingestion**
- **Narrative and tone classification**
- **Actor–intent analysis**
- **Vulnerability index computation**
- An interactive **web dashboard**
- Automated **report generation**

---

# Key Features

- Media monitoring and article ingestion
- Narrative classification using machine learning
- Tone detection using ensemble models
- Contextual geopolitical signal integration
- Actor–intent narrative mapping
- Automated vulnerability index computation
- Analytical dashboard for exploration
- Infrastructure deployment using Docker and Terraform

---

# Methodology

The Vulnerability Index combines two core signals.

## 1. Content Signal

Derived from media narratives targeting specific countries.

Examples include:

- narrative volume
- strategic intent distribution
- tone and sentiment
- actor–target relationships
- narrative amplification patterns

These signals measure **information pressure exerted by external actors**.

---

## 2. Contextual Signal

Structural characteristics of the target country that influence susceptibility.

Examples include:

- geopolitical dependencies
- economic exposure
- military relationships
- resource ties
- political or social fragility

---

## Vulnerability Index

The final index is computed as a function of the two signals: Vulnerability Index = f(Content Signal, Contextual Signal)

The score ranges from **0 to 1**.

| Score | Interpretation |
|------|------|
| 0.00 – 0.30 | Low vulnerability |
| 0.31 – 0.60 | Moderate vulnerability |
| 0.61 – 1.00 | High vulnerability |

---

# Repository Structure
Vulnerability_index_tool/

├── dashboard/ # Django application
│ ├── models.py # Database models
│ ├── views.py # Dashboard views
│ ├── urls.py # Application routes
│ │
│ ├── services/ # Core analytical services
│ │ ├── calibrated_ensemble.py
│ │ ├── tone_ensemble.py
│ │ ├── calibrators.py
│ │ ├── mediacloud_ingestion_service.py
│ │ ├── ml_inference_service.py
│ │ └── summarizer.py
│ │
│ ├── management/commands/ # Data pipeline commands
│ │ ├── ingest_mediacloud.py
│ │ ├── import_articles.py
│ │ ├── import_journalists.py
│ │ ├── import_media_outlets.py
│ │ ├── extract_authors.py
│ │ ├── link_journalists.py
│ │ ├── link_media_outlets.py
│ │ ├── fill_posting_time.py
│ │ ├── migrate_profiles.py
│ │ ├── calculate_vulnerability_index.py
│ │ └── run_full_pipeline.py
│ │
│ ├── templates/ # Dashboard HTML templates
│ ├── static/ # Static assets
│ └── migrations/ # Database migrations
│
├── config/ # Django configuration
│ ├── settings.py
│ ├── urls.py
│ └── wsgi.py
│
├── terraform/ # Infrastructure as Code
│ ├── main.tf
│ ├── variables.tf
│ └── outputs.tf
│
├── lambda_function.py # AWS Lambda handler
├── contextual_all_intents_v2.py # Contextual signal computation
│
├── merged_dataset.csv # Source narrative dataset
├── Journalist.csv
├── MediaOutlet.csv
├── final_risk_by_actor_intent_country.csv
│
├── Dockerfile
├── Dockerfile.lambda
├── requirements.txt
├── Makefile
└── manage.py

---

# Installation

Clone the repository:

```bash
git clone https://github.com/hanna-tes/Vulnerability_index_tool.git
cd Vulnerability_index_tool

Create a virtual environment:
python -m venv venv

Activate it:

Mac / Linux
source venv/bin/activate

Install dependencies:
pip install -r requirements.txt
