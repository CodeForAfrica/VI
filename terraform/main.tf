provider "aws" {
  region = var.aws_region
}

# IAM Role for Lambda
resource "aws_iam_role" "lambda_role" {
  name = "mediacloud_lambda_role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

# IAM Policy for Lambda
resource "aws_iam_role_policy" "lambda_policy" {
  name = "mediacloud_lambda_policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # 1. Logging Permissions
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      # 2. VPC / Network Permissions (Required for RDS access)
      {
        Effect = "Allow"
        Action = [
          "ec2:CreateNetworkInterface",
          "ec2:DescribeNetworkInterfaces",
          "ec2:DeleteNetworkInterface"
        ]
        Resource = "*"
      },
      # 3. RDS Permissions
      {
        Effect = "Allow"
        Action = [
          "rds:Describe*",
          "rds:List*"
        ]
        Resource = "*"
      },
      # 4. S3 Model Access (The part we just added)
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          "arn:aws:s3:::${var.s3_models_bucket}",
          "arn:aws:s3:::${var.s3_models_bucket}/*"
        ]
      }
    ]
  })
}

# Lambda Layer for Dependencies
resource "aws_lambda_layer_version" "dependencies" {
  filename   = "lambda_layer.zip" # Terraform will look for this file relative to where you run terraform
  layer_name = "mediacloud-dependencies"

  compatible_runtimes = ["python3.9", "python3.10", "python3.11"]
}

# Lambda Function for MediaCloud Ingestion
resource "aws_lambda_function" "mediacloud_ingestion" {
  filename = "deployment_package.zip"
  function_name    = "mediacloud-ingestion-function"
  role            = aws_iam_role.lambda_role.arn
  handler         = "manage.lambda_handler"
  runtime         = "python3.11"
  timeout         = 900  # 15 minutes (maximum for Lambda)
  memory_size     = 3008  # Maximum memory for better performance

  layers = [
    aws_lambda_layer_version.dependencies.arn
  ]

  environment {
    variables = {
      # The keys your Python logic expects
      API_KEY            = var.mediacloud_api_key
      MEDIACLOUD_API_KEY = var.mediacloud_api_key
      GROQ_API_KEY       = var.groq_api_key  # <--- Add this
      DB_HOST            = var.db_host
      DB_NAME            = var.db_name
      DB_USER            = var.db_user
      DB_PASSWORD        = var.db_password
      DB_PORT            = var.db_port
    }
  }

  # CONDITIONAL VPC CONFIGURATION: Only include if both subnet_ids and security_group_ids are provided and not null
  dynamic "vpc_config" {
    for_each = var.subnet_ids != null && var.security_group_ids != null && length(var.subnet_ids) > 0 && length(var.security_group_ids) > 0 ? [true] : []
    content {
      subnet_ids         = var.subnet_ids
      security_group_ids = var.security_group_ids
    }
  }
}
# EventBridge Rule (Triggers Daily)
resource "aws_cloudwatch_event_rule" "daily_ingestion" {
  name                = "daily-mediacloud-ingestion"
  description         = "Daily trigger for MediaCloud data ingestion"
  schedule_expression = "rate(1 day)"  # Run daily
}

# Connect EventBridge to Lambda
resource "aws_cloudwatch_event_target" "lambda_target" {
  rule      = aws_cloudwatch_event_rule.daily_ingestion.name
  target_id = "LambdaTarget"
  arn       = aws_lambda_function.mediacloud_ingestion.arn
}

# Give EventBridge permission to invoke Lambda
resource "aws_lambda_permission" "allow_cloudwatch" {
  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.mediacloud_ingestion.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_ingestion.arn
}
