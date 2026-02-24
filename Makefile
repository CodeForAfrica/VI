# Define variables
IMAGE_NAME = vulnerability-tool
REGION = us-east-1 # Change to your region
ACCOUNT_ID = 123456789012 # Change to your AWS Account ID

.PHONY: build push

# Now, 'make build' just runs the Docker command
build:
	docker build -t $(IMAGE_NAME) .

# 'make push' handles authentication and deployment
push:
	aws ecr get-login-password --region $(REGION) | docker login --username AWS --password-stdin $(ACCOUNT_ID).dkr.ecr.$(REGION).amazonaws.com
	docker tag $(IMAGE_NAME):latest $(ACCOUNT_ID).dkr.ecr.$(REGION).amazonaws.com/$(IMAGE_NAME):latest
	docker push $(ACCOUNT_ID).dkr.ecr.$(REGION).amazonaws.com/$(IMAGE_NAME):latest
