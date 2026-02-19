output "lambda_function_name" {
  description = "Name of the Lambda function"
  value       = aws_lambda_function.mediacloud_ingestion.function_name
}

output "eventbridge_rule" {
  description = "EventBridge rule name"
  value       = aws_cloudwatch_event_rule.daily_ingestion.name
}

output "lambda_role_arn" {
  description = "ARN of the Lambda IAM role"
  value       = aws_iam_role.lambda_role.arn
}
