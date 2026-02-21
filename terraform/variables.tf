# 1. THE LAMBDA FUNCTION
resource "aws_lambda_function" "ingestion_lambda" {
  filename      = "deployment_package.zip" # The name of your zip file
  function_name = "mediacloud_daily_ingestor"
  role          = aws_iam_role.lambda_exec_role.arn # Ensure this role exists in your iam.tf
  handler       = "lambda_function.lambda_handler" # filename.function_name
  runtime       = "python3.9"
  timeout       = 900  # 15 minutes
  memory_size   = 1024 # 1GB RAM

  # VPC Configuration (Connects Lambda to your RDS)
  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = var.security_group_ids
  }

  # Environment Variables (Mapping your variables to the OS)
  environment {
    variables = {
      API_KEY            = var.mediacloud_api_key
      MEDIACLOUD_API_KEY = var.mediacloud_api_key
      DB_HOST            = var.db_host
      DB_NAME            = var.db_name
      DB_USER            = var.db_user
      DB_PASSWORD        = var.db_password
      DB_PORT            = var.db_port
    }
  }
}

# 2. THE DAILY SCHEDULE (CRON)
resource "aws_cloudwatch_event_rule" "daily_trigger" {
  name                = "mediacloud-daily-ingestion-trigger"
  description         = "Triggers MediaCloud ingestion every day at 2 AM"
  schedule_expression = "cron(0 2 * * ? *)" 
}

# 3. LINKING THE SCHEDULE TO THE LAMBDA
resource "aws_cloudwatch_event_target" "run_ingestion_daily" {
  rule      = aws_cloudwatch_event_rule.daily_trigger.name
  target_id = "IngestionLambda"
  arn       = aws_lambda_function.ingestion_lambda.arn
}

# 4. PERMISSION FOR CLOUDWATCH TO CALL LAMBDA
resource "aws_lambda_permission" "allow_cloudwatch" {
  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingestion_lambda.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_trigger.arn
}
