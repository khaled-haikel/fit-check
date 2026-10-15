# Permissions boundary for every role infra/app creates (today, the Lambda
# execution role). It covers what stages 2 to 5 need; a new service is added
# here, through the bootstrap, before the app can use it.
data "aws_iam_policy_document" "workload_boundary" {
  source_policy_documents = [data.aws_iam_policy_document.bedrock_invoke.json]

  # log-group:fitcheck-* also matches the log streams inside those groups.
  statement {
    sid = "WriteOwnLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [local.log_group_arns]
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

  # Data plane only: no table management.
  statement {
    sid = "UseOwnTables"
    actions = [
      "dynamodb:DeleteItem",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:UpdateItem",
    ]
    resources = [
      local.dynamodb_table_arns,
      local.dynamodb_index_arns,
    ]
  }

  # Messages only: no queue management.
  statement {
    sid = "UseOwnQueues"
    actions = [
      "sqs:ChangeMessageVisibility",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:ReceiveMessage",
      "sqs:SendMessage",
    ]
    resources = [local.sqs_queue_arns]
  }
}

resource "aws_iam_policy" "workload_boundary" {
  name        = local.workload_boundary_name
  description = "Permissions boundary required on every role created by the fit-check CI."
  policy      = data.aws_iam_policy_document.workload_boundary.json
}
