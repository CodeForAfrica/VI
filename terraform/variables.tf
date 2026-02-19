variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-1"
}

variable "mediacloud_api_key" {
  description = "MediaCloud API key"
  type        = string
  sensitive   = true
}

variable "db_host" {
  description = "Database host"
  type        = string
}

variable "db_name" {
  description = "Database name"
  type        = string
}

variable "db_user" {
  description = "Database user"
  type        = string
}

variable "db_password" {
  description = "Database password"
  type        = string
  sensitive   = true
}

variable "db_port" {  # <-- ADD THIS VARIABLE DEFINITION
  description = "Database port"
  type        = string
  default     = "5432"
}

variable "subnet_ids" {
  description = "Subnet IDs for Lambda VPC configuration (optional, set to null if not needed)"
  type        = list(string)
  default     = null
}

variable "security_group_ids" {
  description = "Security group IDs for Lambda VPC configuration (optional, set to null if not needed)"
  type        = list(string)
  default     = null
}
