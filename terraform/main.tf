provider "aws" {
  region = var.aws_region
}

resource "aws_lambda_function" "my_lambda" {
  function_name = "vulnerability-tool"
  
  # MANDATORY: This tells Lambda to treat this as a container
  package_type  = "Image"
  
  role          = "arn:aws:iam::499665620971:role/VulnerabilityIndex-MediaCloud-Lambda-Role"
  
  # FIXED: Use the correct image name
  image_uri     = "hannateshager/django-vi:latest"

  image_config {
    command = ["lambda_function.lambda_handler"]
  }
  
  memory_size   = 3008
  timeout       = 900
}

environment {
    variables = {
      API_KEY      = var.mediacloud_api_key
      GROQ_API_KEY = var.groq_api_key
      DB_HOST      = var.db_host
      DB_NAME      = var.db_name
      DB_USER      = var.db_user
      DB_PASSWORD  = var.db_password
      DB_PORT      = var.db_port
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
  name                = "daily-vulnerability-ingestion"
  description         = "Daily trigger for vulnerability tool"
  schedule_expression = "rate(1 day)"
}

resource "aws_cloudwatch_event_target" "lambda_target" {
  rule      = aws_cloudwatch_event_rule.daily_ingestion.name
  target_id = "LambdaTarget"
  arn       = aws_lambda_function.my_lambda.arn
}

resource "aws_lambda_permission" "allow_cloudwatch" {
  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.my_lambda.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_ingestion.arn
}
