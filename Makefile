# Default configuration
IMAGE_NAME ?= vulnerability-tool
REGION     ?= us-west-1
ACCOUNT_ID ?= 499665620971
ECR_REGISTRY = $(ACCOUNT_ID).dkr.ecr.$(REGION).amazonaws.com
ECR_URI      = $(ECR_REGISTRY)/$(IMAGE_NAME)

CLF_COMPOSE = docker compose -f docker-compose.classifier.yml
INF_COMPOSE = docker compose -f docker-compose.inference.yml

.PHONY: build push build-test test test-api test-lambda smoke-api results reset verify db down clean-test

# Build the web image
build:
	docker build -t $(IMAGE_NAME):latest .

# Login to ECR and push the image (deploy path - needs ECR access)
push:
	aws ecr get-login-password --region $(REGION) | docker login --username AWS --password-stdin $(ECR_REGISTRY)
	docker tag $(IMAGE_NAME):latest $(ECR_URI):latest
	docker push $(ECR_URI):latest

# --- Local classification-split test (docker-compose.classifier.yml) ---
# All local: the classifier image is built from Dockerfile.classifier on the
# machine running the test. No ECR access required.

# Build the classifier image locally.
build-test:
	$(CLF_COMPOSE) build

# Full local end-to-end in one command: build locally, migrate, seed the
# 10-article fixture (fixtures/test_articles.json) as unclassified rows, check
# Groq, classify, print results. Exits when the classifier finishes.
test: build-test
	$(CLF_COMPOSE) up --abort-on-container-exit

# Run the inference-API contract test suite (inference_api/tests.py). Runs in
# the classifier container so no local Python setup is needed. Contract-level:
# no models are loaded, so it is fast. The tests_settings module is deliberately
# light (Django + requests + bs4 only), so it also runs in any local venv via:
#   python manage.py test inference_api --settings=inference_api.tests_settings
test-api:
	$(CLF_COMPOSE) run --rm classifier python manage.py test inference_api --settings=inference_api.tests_settings

# Run the ingestion Lambda test suite (inference client + drain loop + handler).
# Plain unittest with a fake DB and fake client - no Postgres/AWS/MediaCloud.
# Also runs in any local venv with requests installed:
#   python -m unittest test_inference_client test_lambda_function
test-lambda:
	$(CLF_COMPOSE) run --rm classifier python -m unittest test_inference_client test_lambda_function

# Boot smoke: gunicorn-boot config.inference_wsgi under the real settings and
# assert the HTTP contract (/healthz, /readyz, auth, readiness gating). Warmup
# is skipped, so no models/GPU/DB are needed. Exits non-zero if the smoke fails.
smoke-api:
	$(INF_COMPOSE) up --build --abort-on-container-exit --exit-code-from smoke; \
	code=$$?; $(INF_COMPOSE) down; exit $$code

# Print the current classification state of every row.
results:
	$(CLF_COMPOSE) run --rm classifier python manage.py show_results

# Reset the sample rows back to unclassified (reloads the fixture by pk).
reset:
	$(CLF_COMPOSE) run --rm classifier python manage.py loaddata fixtures/test_articles.json

# Prove the blank-intent fix: count rows the classifier would still reprocess.
# Expect 0 after a run - genuine-Neutral rows are marked processed and settle.
verify:
	$(CLF_COMPOSE) up -d db
	$(CLF_COMPOSE) exec -T db psql -U vi -d vulnerability -c "SELECT count(*) AS would_reprocess FROM dashboard_medianarrative WHERE (strategic_intent IS NULL OR strategic_intent='') AND article_text IS NOT NULL AND article_text <> '' AND lower(article_text) <> 'no content available' AND ml_processed_at IS NULL;"

# Bring up just the local db.
db:
	$(CLF_COMPOSE) up -d db

# Stop the test containers (keep the db data).
down:
	$(CLF_COMPOSE) down

# Stop and wipe the test db.
clean-test:
	$(CLF_COMPOSE) down -v
