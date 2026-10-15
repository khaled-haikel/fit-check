# Offline checks of the planned app; no AWS credentials needed:
#   terraform init -backend=false && terraform test

provider "aws" {
  region                      = "us-east-1"
  access_key                  = "offline-test"
  secret_key                  = "offline-test"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
}

override_data {
  target = data.aws_caller_identity.current
  values = {
    account_id = "000000000000"
  }
}

variables {
  commit_sha         = "0123456789abcdef0123456789abcdef01234567"
  allowed_origin     = "https://example.com"
  embedding_model_id = "amazon.titan-embed-text-v2:0"
  # Plan only: any existing file satisfies filebase64sha256.
  lambda_package = "versions.tf"
}

run "prod" {
  command = plan

  assert {
    condition = alltrue([
      aws_lambda_function.api.function_name == "fitcheck-api",
      aws_cloudwatch_log_group.api.name == "fitcheck-api",
      aws_apigatewayv2_api.http.name == "fitcheck-api",
      aws_iam_role.lambda.name == "fitcheck-api-execution",
    ])
    error_message = "prod must use the fitcheck-api names; the CI roles only cover fitcheck-*."
  }

  assert {
    condition     = aws_iam_role.lambda.permissions_boundary == "arn:aws:iam::000000000000:policy/fitcheck-workload-boundary"
    error_message = "The execution role must carry the workload boundary, or the CI cannot create it."
  }

  assert {
    condition = alltrue([
      aws_lambda_function.api.runtime == "python3.12",
      one(aws_lambda_function.api.architectures) == "arm64",
      aws_lambda_function.api.tracing_config[0].mode == "Active",
      aws_lambda_function.api.publish,
      aws_lambda_function.api.environment[0].variables["GIT_COMMIT"] == var.commit_sha,
    ])
    error_message = "The function must be Python 3.12 on arm64, traced, published on every change, and report the commit."
  }

  assert {
    condition = alltrue([
      aws_lambda_function.api.logging_config[0].log_format == "JSON",
      aws_lambda_function.api.logging_config[0].log_group == aws_cloudwatch_log_group.api.name,
      aws_cloudwatch_log_group.api.retention_in_days == 14,
    ])
    error_message = "Logs must be JSON, in the fitcheck-api log group, kept for 14 days."
  }

  assert {
    condition = alltrue([
      aws_lambda_alias.live.name == "live",
      aws_lambda_permission.api_gateway.qualifier == "live",
    ])
    error_message = "API Gateway must invoke the live alias."
  }

  assert {
    condition = alltrue([
      aws_apigatewayv2_stage.default.default_route_settings[0].throttling_rate_limit == 2,
      aws_apigatewayv2_stage.default.default_route_settings[0].throttling_burst_limit == 5,
      one(aws_apigatewayv2_api.http.cors_configuration[0].allow_origins) == "https://example.com",
    ])
    error_message = "The stage must allow 2 requests per second (burst 5), and CORS only the configured origin."
  }

  assert {
    condition = alltrue([
      aws_apigatewayv2_route.health.route_key == "GET /health",
      aws_apigatewayv2_route.search.route_key == "POST /search",
      length(setsubtract(["GET", "POST"], aws_apigatewayv2_api.http.cors_configuration[0].allow_methods)) == 0,
    ])
    error_message = "The API must expose GET /health and POST /search, and CORS must allow both methods."
  }

  assert {
    condition = alltrue([
      for settings in aws_apigatewayv2_stage.default.route_settings : (
        settings.throttling_rate_limit < var.throttling_rate_limit &&
        settings.throttling_burst_limit < var.throttling_burst_limit
      ) if settings.route_key == "POST /search"
      ]) && length([
      for settings in aws_apigatewayv2_stage.default.route_settings : settings if settings.route_key == "POST /search"
    ]) == 1
    error_message = "POST /search must have its own throttling, tighter than the stage default."
  }

  assert {
    condition = alltrue([
      aws_lambda_function.api.environment[0].variables["EMBEDDING_MODEL_ID"] == "amazon.titan-embed-text-v2:0",
      length([
        for statement in jsondecode(data.aws_iam_policy_document.lambda.json).Statement : statement
        if statement.Sid == "InvokeEmbeddingModel" &&
        flatten([statement.Action]) == ["bedrock:InvokeModel"] &&
        flatten([statement.Resource]) == ["arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0"]
      ]) == 1,
    ])
    error_message = "The function must know its embedding model and may invoke only that model."
  }

  assert {
    condition = alltrue(flatten([
      for doc in [
        data.aws_iam_policy_document.lambda.json,
        data.aws_iam_policy_document.lambda_assume.json,
        ] : [
        for statement in jsondecode(doc).Statement : !(
          anytrue([for action in flatten([statement.Action]) : strcontains(action, "*")]) &&
          anytrue([for resource in flatten([try(statement.Resource, [])]) : strcontains(resource, "*")])
        )
      ]
    ]))
    error_message = "A policy statement has a wildcard in both Action and Resource."
  }
}

run "staging" {
  command = plan

  variables {
    env = "staging"
  }

  assert {
    condition = alltrue([
      aws_lambda_function.api.function_name == "fitcheck-api-staging",
      aws_iam_role.lambda.name == "fitcheck-api-staging-execution",
    ])
    error_message = "Other environments must get suffixed fitcheck-* names."
  }
}

run "rejects_a_malformed_embedding_model_id" {
  command = plan

  variables {
    embedding_model_id = "titan embeddings"
  }

  expect_failures = [var.embedding_model_id]
}
