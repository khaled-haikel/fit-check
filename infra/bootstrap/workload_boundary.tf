# Permissions boundary for every role infra/app creates (today, the Lambda
# execution role). Later stages extend it here, through the bootstrap, before
# the app can use a new service.
data "aws_iam_policy_document" "workload_boundary" {
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
}

resource "aws_iam_policy" "workload_boundary" {
  name        = local.workload_boundary_name
  description = "Permissions boundary required on every role created by the fit-check CI."
  policy      = data.aws_iam_policy_document.workload_boundary.json
}
