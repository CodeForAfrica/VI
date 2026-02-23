provider "aws" {
  region = var.aws_region
}

# --- LAMBDA LAYER ---
# We comment this out so Terraform doesn't try to upload the 720MB zip
/*
resource "aws_lambda_layer_version" "dependencies" {
  filename   = "lambda_layer.zip"
  layer_name = "mediacloud-dependencies"
  compatible_runtimes = ["python3.9", "python3.10", "python3.11", "python3.12"]
}
*/

# --- LAMBDA FUNCTION ---
resource "aws_lambda_function" "mediacloud_ingestion" {
  filename         = "deployment_package.zip"
  source_code_hash = filebase64sha256("deployment_package.zip")
  function_name    = "mediacloud-ingestion-function"
  
  role             = var.lambda_role_arn
  
  # UPDATE: Changed from manage.lambda_handler to your actual script name
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.12"
  timeout          = 900
  memory_size      = 3008

  # UPDATE: Reference the ARN 
  layers = [
    "arn:aws:iam::499665620971:role/VulnerabilityIndex-MediaCloud-Lambda-Role"
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
