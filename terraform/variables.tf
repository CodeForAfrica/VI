variable "aws_region" {
  type    = string
  default = "eu-west-1"
}

variable "mediacloud_api_key" {
  type      = string
  sensitive = true
}

variable "groq_api_key" {
  type      = string
  sensitive = true
}

variable "s3_models_bucket" {
  type        = string
  description = "Bucket name for ML models"
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
