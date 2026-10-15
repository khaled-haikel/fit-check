# Named fitcheck-* instead of Lambda's default /aws/lambda/<function>: the CI
# roles and the workload boundary only cover log groups with that prefix.
resource "aws_cloudwatch_log_group" "api" {
  name              = local.name
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name                 = "${local.name}-execution"
  description          = "Execution role of the ${local.name} Lambda function."
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
  permissions_boundary = local.workload_boundary_arn
}

data "aws_iam_policy_document" "lambda" {
  statement {
    sid = "WriteLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${local.log_group_arn}:*"]
  }

  # X-Ray has no resource-level permissions.
  statement {
    sid = "WriteTraces"
    actions = [
      "xray:PutTelemetryRecords",
      "xray:PutTraceSegments",
    ]
    resources = ["*"]
  }

  # POST /search embeds the query with the model the index was built with.
  statement {
    sid       = "InvokeEmbeddingModel"
    actions   = ["bedrock:InvokeModel"]
    resources = [local.embedding_model_arn]
  }

  # The analysis calls the generation model through its inference profile only.
  statement {
    sid       = "InvokeGenerationProfile"
    actions   = ["bedrock:InvokeModel"]
    resources = [local.generation_profile_arn]
  }

  statement {
    sid       = "InvokeGenerationModelThroughProfile"
    actions   = ["bedrock:InvokeModel"]
    resources = local.generation_model_arns

    condition {
      test     = "StringEquals"
      variable = "bedrock:InferenceProfileArn"
      values   = [local.generation_profile_arn]
    }
  }

  # Items only. TransactWriteItems is authorized by PutItem and UpdateItem.
  statement {
    sid = "UseChecksTable"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
    ]
    resources = [local.checks_table_arn]
  }

  # Sending from the API, receiving through the event source mapping.
  statement {
    sid = "UseChecksQueue"
    actions = [
      "sqs:ChangeMessageVisibility",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:ReceiveMessage",
      "sqs:SendMessage",
    ]
    resources = [local.checks_queue_arn]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "${local.name}-execution"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

resource "aws_lambda_function" "api" {
  function_name = local.name
  description   = "fit-check HTTP API"
  role          = aws_iam_role.lambda.arn
  runtime       = "python3.12"
  architectures = ["arm64"]
  handler       = "fitcheck_api.handler.handler"
  memory_size   = 256
  # The worker runs a whole analysis; the HTTP routes still answer in seconds.
  timeout = local.lambda_timeout

  filename         = var.lambda_package
  source_code_hash = filebase64sha256(var.lambda_package)

  # Any change to code or configuration publishes a new immutable version.
  # GIT_COMMIT changes on every deploy, so every deploy publishes one.
  publish = true

  environment {
    variables = {
      GIT_COMMIT              = var.commit_sha
      POWERTOOLS_SERVICE_NAME = local.name
      # The handler refuses to search if the packaged index used another model.
      EMBEDDING_MODEL_ID = var.embedding_model_id
      # The analysis refuses to run if the packaged prompts name another model.
      GENERATION_MODEL_ID    = var.generation_model_id
      CHECKS_TABLE           = aws_dynamodb_table.checks.name
      CHECKS_QUEUE_URL       = aws_sqs_queue.checks.url
      PUBLIC_MONTHLY_CAP_USD = tostring(var.public_monthly_cap_usd)
      DAILY_CHECKS_PER_IP    = tostring(var.daily_checks_per_ip)
    }
  }

  logging_config {
    log_format            = "JSON"
    log_group             = aws_cloudwatch_log_group.api.name
    application_log_level = "INFO"
    system_log_level      = "INFO"
  }

  tracing_config {
    mode = "Active"
  }

  # The role needs its log and trace permissions before the first invocation.
  depends_on = [aws_iam_role_policy.lambda]
}

# API Gateway calls this alias, never $LATEST. The rollback workflow can point
# it at an older version until the next deploy (docs/adr/0003-alias-rollback.md).
resource "aws_lambda_alias" "live" {
  name             = "live"
  description      = "Version served by the HTTP API."
  function_name    = aws_lambda_function.api.function_name
  function_version = aws_lambda_function.api.version
}
