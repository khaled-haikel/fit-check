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

# Computed ARNs the plan cannot know; the assertions below compare against them.
override_resource {
  target          = aws_lambda_alias.live
  override_during = plan
  values = {
    arn = "arn:aws:lambda:us-east-1:000000000000:function:fitcheck-api:live"
  }
}

override_resource {
  target          = aws_sqs_queue.checks_dlq
  override_during = plan
  values = {
    arn = "arn:aws:sqs:us-east-1:000000000000:fitcheck-checks-dlq"
  }
}

override_resource {
  target          = aws_sqs_queue.checks
  override_during = plan
  values = {
    arn = "arn:aws:sqs:us-east-1:000000000000:fitcheck-checks"
    url = "https://sqs.us-east-1.amazonaws.com/000000000000/fitcheck-checks"
  }
}

override_resource {
  target          = aws_sns_topic.alerts
  override_during = plan
  values = {
    arn = "arn:aws:sns:us-east-1:000000000000:fitcheck-alerts"
  }
}

variables {
  commit_sha          = "0123456789abcdef0123456789abcdef01234567"
  allowed_origin      = "https://example.com"
  embedding_model_id  = "amazon.titan-embed-text-v2:0"
  generation_model_id = "us.anthropic.claude-sonnet-4-6"
  alert_email         = "alerts@example.com"
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

run "checks" {
  command = plan

  assert {
    condition = alltrue([
      aws_dynamodb_table.checks.name == "fitcheck-checks",
      aws_dynamodb_table.checks.billing_mode == "PAY_PER_REQUEST",
      aws_dynamodb_table.checks.hash_key == "pk",
      aws_dynamodb_table.checks.ttl[0].enabled,
      aws_dynamodb_table.checks.ttl[0].attribute_name == "expires_at",
    ])
    error_message = "The checks table must be on demand, keyed by pk, with TTL on expires_at."
  }

  assert {
    condition = alltrue([
      aws_sqs_queue.checks.visibility_timeout_seconds >= 6 * aws_lambda_function.api.timeout,
      jsondecode(aws_sqs_queue.checks.redrive_policy).maxReceiveCount == 2,
      jsondecode(aws_sqs_queue.checks.redrive_policy).deadLetterTargetArn == "arn:aws:sqs:us-east-1:000000000000:fitcheck-checks-dlq",
      aws_sqs_queue.checks_dlq.message_retention_seconds == 1209600,
      aws_lambda_function.api.timeout == 300,
    ])
    error_message = "The queue must hide a message for at least 6 times the function timeout and send it to the DLQ after 2 receives."
  }

  assert {
    condition = alltrue([
      aws_lambda_event_source_mapping.checks.function_name == "arn:aws:lambda:us-east-1:000000000000:function:fitcheck-api:live",
      aws_lambda_event_source_mapping.checks.batch_size == 1,
      aws_lambda_event_source_mapping.checks.scaling_config[0].maximum_concurrency == 2,
    ])
    error_message = "The live alias must consume the queue one message at a time, at most 2 at once."
  }

  assert {
    condition = alltrue([
      one(aws_cloudwatch_metric_alarm.checks_dlq.alarm_actions) == "arn:aws:sns:us-east-1:000000000000:fitcheck-alerts",
      aws_cloudwatch_metric_alarm.checks_dlq.metric_name == "ApproximateNumberOfMessagesVisible",
      aws_cloudwatch_metric_alarm.checks_dlq.dimensions["QueueName"] == "fitcheck-checks-dlq",
      aws_cloudwatch_metric_alarm.checks_dlq.statistic == "Maximum",
      aws_cloudwatch_metric_alarm.checks_dlq.threshold == 0,
      aws_cloudwatch_metric_alarm.checks_dlq.comparison_operator == "GreaterThanThreshold",
      aws_cloudwatch_metric_alarm.checks_dlq.treat_missing_data == "notBreaching",
      aws_sns_topic_subscription.alert_email.protocol == "email",
    ])
    error_message = "The alarm must watch the DLQ and notify the alerts topic, which mails the alert address."
  }

  assert {
    condition = alltrue([
      aws_apigatewayv2_route.post_checks.route_key == "POST /checks",
      aws_apigatewayv2_route.get_check.route_key == "GET /checks/{id}",
      length([
        for settings in aws_apigatewayv2_stage.default.route_settings : settings
        if settings.route_key == "POST /checks" && settings.throttling_rate_limit == 1 && settings.throttling_burst_limit == 2
      ]) == 1,
      length([
        for settings in aws_apigatewayv2_stage.default.route_settings : settings
        if settings.route_key == "GET /checks/{id}" && settings.throttling_rate_limit == 5 && settings.throttling_burst_limit == 10
      ]) == 1,
    ])
    error_message = "POST /checks must allow 1 request per second (burst 2) and GET /checks/{id} 5 (burst 10)."
  }

  assert {
    condition = alltrue([
      aws_lambda_function.api.environment[0].variables["GENERATION_MODEL_ID"] == "us.anthropic.claude-sonnet-4-6",
      aws_lambda_function.api.environment[0].variables["PUBLIC_MONTHLY_CAP_USD"] == "2",
      aws_lambda_function.api.environment[0].variables["DAILY_CHECKS_PER_IP"] == "5",
      aws_lambda_function.api.environment[0].variables["CHECKS_TABLE"] == "fitcheck-checks",
    ])
    error_message = "The function must know the generation model, the table and the public limits."
  }

  # Exactly these actions per statement; anything else fails the test.
  assert {
    condition = alltrue([
      for statement in jsondecode(data.aws_iam_policy_document.lambda.json).Statement :
      toset(flatten([statement.Action])) == lookup({
        WriteLogs                           = toset(["logs:CreateLogStream", "logs:PutLogEvents"])
        WriteTraces                         = toset(["xray:PutTelemetryRecords", "xray:PutTraceSegments"])
        InvokeEmbeddingModel                = toset(["bedrock:InvokeModel"])
        InvokeGenerationProfile             = toset(["bedrock:InvokeModel"])
        InvokeGenerationModelThroughProfile = toset(["bedrock:InvokeModel"])
        UseChecksTable                      = toset(["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"])
        UseChecksQueue = toset([
          "sqs:ChangeMessageVisibility", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ReceiveMessage", "sqs:SendMessage",
        ])
      }, statement.Sid, toset(["unexpected statement"]))
    ])
    error_message = "The execution role policy must hold exactly the listed actions."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(data.aws_iam_policy_document.lambda.json).Statement :
      statement.Sid != "InvokeGenerationModelThroughProfile" || (
        toset(flatten([statement.Resource])) == toset([
          "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-4-6",
          "arn:aws:bedrock:us-east-2::foundation-model/anthropic.claude-sonnet-4-6",
          "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-sonnet-4-6",
        ])
        && statement.Condition.StringEquals["bedrock:InferenceProfileArn"] == "arn:aws:bedrock:us-east-1:000000000000:inference-profile/us.anthropic.claude-sonnet-4-6"
      )
    ])
    error_message = "The base model must be reachable only through the us. inference profile, in its destination regions."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(data.aws_iam_policy_document.lambda.json).Statement :
      !contains(["UseChecksTable", "UseChecksQueue"], statement.Sid) || contains([
        "arn:aws:dynamodb:us-east-1:000000000000:table/fitcheck-checks",
        "arn:aws:sqs:us-east-1:000000000000:fitcheck-checks",
      ], one(flatten([statement.Resource])))
    ])
    error_message = "The table and queue statements must name only the checks table and the main queue."
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

run "rejects_a_malformed_generation_model_id" {
  command = plan

  variables {
    generation_model_id = "anthropic.claude-sonnet-4-6"
  }

  expect_failures = [var.generation_model_id]
}

run "rejects_a_malformed_embedding_model_id" {
  command = plan

  variables {
    embedding_model_id = "titan embeddings"
  }

  expect_failures = [var.embedding_model_id]
}
