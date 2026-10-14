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
