# Default configuration
IMAGE_NAME ?= vulnerability-tool
REGION     ?= us-west-1
ACCOUNT_ID ?= 499665620971
ECR_REGISTRY = $(ACCOUNT_ID).dkr.ecr.$(REGION).amazonaws.com
ECR_URI      = $(ECR_REGISTRY)/$(IMAGE_NAME)

.PHONY: build push

# Build the image
build:
	docker build -t $(IMAGE_NAME):latest .

# Login to ECR and push the image
push:
	aws ecr get-login-password --region $(REGION) | docker login --username AWS --password-stdin $(ECR_REGISTRY)
	docker tag $(IMAGE_NAME):latest $(ECR_URI):latest
	docker push $(ECR_URI):latest
