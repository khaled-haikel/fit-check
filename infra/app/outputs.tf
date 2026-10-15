output "api_url" {
  description = "Base URL of the HTTP API ($default stage)."
  value       = aws_apigatewayv2_stage.default.invoke_url
}

output "function_name" {
  description = "Name of the Lambda function."
  value       = aws_lambda_function.api.function_name
}

output "live_alias" {
  description = "Alias that API Gateway invokes."
  value       = aws_lambda_alias.live.name
}

output "live_version" {
  description = "Version Terraform points the alias at: the one this deploy published."
  value       = aws_lambda_alias.live.function_version
}

output "checks_table" {
  description = "DynamoDB table with the analyses and the spending counters (docs/runbook.md)."
  value       = aws_dynamodb_table.checks.name
}

output "checks_queue_url" {
  description = "Queue of analyses to run."
  value       = aws_sqs_queue.checks.url
}

output "checks_dlq_arn" {
  description = "Dead-letter queue of analyses that failed twice (redrive: docs/runbook.md)."
  value       = aws_sqs_queue.checks_dlq.arn
}
