provider "aws" {
  region = var.aws_region
}

# --- REFERENCE EXISTING ROLE ---
# use a data source to fetch the role created manually.
data "aws_iam_role" "existing_lambda_role" {
  name = "VulnerabilityIndex-MediaCloud-Lambda-Role" 
}

/* #IAM Role creation
resource "aws_iam_role" "lambda_role" {
  name = "mediacloud_lambda_role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

# IAM Policy attachment
resource "aws_iam_role_policy" "lambda_policy" {
  name = "mediacloud_lambda_policy"
  role = aws_iam_role.lambda_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [...] # (Your policy statements)
  })
}
*/

# --- LAMBDA LAYER ---
resource "aws_lambda_layer_version" "dependencies" {
  filename   = "lambda_layer.zip"
  layer_name = "mediacloud-dependencies"

  compatible_runtimes = ["python3.9", "python3.10", "python3.11", "python3.12"]
}

# --- LAMBDA FUNCTION ---
resource "aws_lambda_function" "mediacloud_ingestion" {
  filename      = "deployment_package.zip"
  function_name = "mediacloud-ingestion-function"
  
  # Point to the DATA source ARN instead of a RESOURCE ARN
  role          = data.aws_iam_role.existing_lambda_role.arn
  
  handler       = "manage.lambda_handler"
  runtime       = "python3.12"
  timeout       = 900
  memory_size   = 3008

  layers = [
    aws_lambda_layer_version.dependencies.arn
  ]

  environment {
    variables = {
      API_KEY            = var.mediacloud_api_key
      MEDIACLOUD_API_KEY = var.mediacloud_api_key
      GROQ_API_KEY       = var.groq_api_key
      DB_HOST            = var.db_host
      DB_NAME            = var.db_name
      DB_USER            = var.db_user
      DB_PASSWORD        = var.db_password
      DB_PORT            = var.db_port
    }
  }

  dynamic "vpc_config" {
    for_each = var.subnet_ids != null && var.security_group_ids != null && length(var.subnet_ids) > 0 && length(var.security_group_ids) > 0 ? [true] : []
    content {
      subnet_ids         = var.subnet_ids
      security_group_ids = var.security_group_ids
    }
  }
}

# --- EVENTBRIDGE TRIGGER ---
resource "aws_cloudwatch_event_rule" "daily_ingestion" {
  name                = "daily-mediacloud-ingestion"
  description         = "Daily trigger for MediaCloud data ingestion"
  schedule_expression = "rate(1 day)"
}

resource "aws_cloudwatch_event_target" "lambda_target" {
  rule      = aws_cloudwatch_event_rule.daily_ingestion.name
  target_id = "LambdaTarget"
  arn       = aws_lambda_function.mediacloud_ingestion.arn
}

resource "aws_lambda_permission" "allow_cloudwatch" {
  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.mediacloud_ingestion.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_ingestion.arn
}
