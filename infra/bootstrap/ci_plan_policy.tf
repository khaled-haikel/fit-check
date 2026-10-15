# Read-only access for terraform plan on pull requests. Pull request plans run
# with -lock=false, so this role never writes the state lock file.
# The actions match the reads the AWS provider performs for the resources in
# infra/app.
data "aws_iam_policy_document" "ci_plan" {
  statement {
    sid       = "StateList"
    actions   = ["s3:ListBucket"]
    resources = [local.state_bucket_arn]
  }

  statement {
    sid       = "StateRead"
    actions   = ["s3:GetObject"]
    resources = [local.app_state_object_arns]
  }

  statement {
    sid = "LambdaRead"
    actions = [
      "lambda:GetAlias",
      "lambda:GetFunction",
      "lambda:GetFunctionCodeSigningConfig",
      "lambda:GetFunctionConcurrency",
      "lambda:GetFunctionConfiguration",
      "lambda:GetPolicy",
      "lambda:ListAliases",
      "lambda:ListTags",
      "lambda:ListVersionsByFunction",
    ]
    resources = [local.lambda_function_arns]
  }

  statement {
    sid       = "ApiGatewayRead"
    actions   = ["apigateway:GET"]
    resources = [local.http_api_arns]

    # API Gateway exposes the API name only on the API itself, and tags only
    # where they apply; IfExists lets child resources (routes, integrations)
    # through while still refusing APIs that are not fit-check's.
    condition {
      test     = "StringLikeIfExists"
      variable = "apigateway:Resource/ApiName"
      values   = ["fitcheck-*"]
    }

    condition {
      test     = "StringEqualsIfExists"
      variable = "aws:ResourceTag/project"
      values   = ["fit-check"]
    }
  }

  statement {
    sid       = "ApiGatewayTagsRead"
    actions   = ["apigateway:GET"]
    resources = [local.apigateway_tag_arns]
  }

  statement {
    sid = "IamReadWorkloadRoles"
    actions = [
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:ListRolePolicies",
      "iam:ListRoleTags",
    ]
    resources = [local.workload_role_arns]
  }

  statement {
    sid       = "LogsReadTags"
    actions   = ["logs:ListTagsForResource"]
    resources = [local.log_group_arns]
  }

  # DescribeLogGroups has no resource-level permissions.
  statement {
    sid       = "LogsDescribe"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["*"]
  }

  # Stage 3 services: the reads behind a plan of the resources the apply
  # role manages. No writes, no model invocation.
  statement {
    sid = "DynamoDbReadTables"
    actions = [
      "dynamodb:DescribeContinuousBackups",
      "dynamodb:DescribeTable",
      "dynamodb:DescribeTimeToLive",
      "dynamodb:ListTagsOfResource",
    ]
    resources = [local.dynamodb_table_arns]
  }

  statement {
    sid = "SqsReadQueues"
    actions = [
      "sqs:GetQueueAttributes",
      "sqs:ListQueueTags",
    ]
    resources = [local.sqs_queue_arns]
  }

  statement {
    sid = "SnsReadTopics"
    actions = [
      "sns:GetSubscriptionAttributes",
      "sns:GetTopicAttributes",
      "sns:ListSubscriptionsByTopic",
      "sns:ListTagsForResource",
    ]
    resources = [local.sns_topic_arns]
  }

  statement {
    sid = "CloudWatchReadAlarms"
    actions = [
      "cloudwatch:DescribeAlarms",
      "cloudwatch:ListTagsForResource",
    ]
    resources = [local.metric_alarm_arns]
  }

  statement {
    sid       = "LambdaReadEventSourceMappings"
    actions   = ["lambda:GetEventSourceMapping"]
    resources = [local.event_source_mapping]

    condition {
      test     = "ArnLike"
      variable = "lambda:FunctionArn"
      values   = [local.lambda_function_arns]
    }
  }

  statement {
    sid       = "LambdaReadEventSourceMappingTags"
    actions   = ["lambda:ListTags"]
    resources = [local.event_source_mapping]
  }
}
