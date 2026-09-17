variable "aws_region" {
  type    = string
  default = "eu-west-1"
}

variable "mediacloud_api_key" {
  type      = string
  sensitive = true
}

variable "lambda_image_uri" {
  type        = string
  description = "Immutable Amazon ECR image URI for the ingestion-only Lambda image"
}

variable "inference_api_url" {
  type        = string
  description = "Public base URL for the VI model inference service"
  default     = "https://vi-model-inference.codeforafrica.org"
}

variable "inference_api_key" {
  type        = string
  description = "X-API-Key value accepted by the VI model inference service"
  sensitive   = true
}

variable "db_host" { type = string }
variable "db_name" { type = string }
variable "db_user" { type = string }
variable "db_password" {
  type      = string
  sensitive = true
}
variable "db_port" {
  type    = string
  default = "5432"
}

variable "subnet_ids" {
  type    = list(string)
  default = []
}

variable "security_group_ids" {
  type    = list(string)
  default = []
}
variable "groq_api_key" {
  description = "Existing Groq credential used by Lambda arbitration, never the model server."
  type        = string
  sensitive   = true
  default     = ""
}

variable "groq_model" {
  description = "Groq model used by the unchanged caller-side arbitration."
  type        = string
  default     = "qwen/qwen3.6-27b"
}
