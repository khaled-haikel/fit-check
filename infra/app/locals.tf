data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  # prod keeps the bare names (fitcheck-api); other environments get a suffix
  # (fitcheck-api-staging). Every name must start with fitcheck-: the CI roles
  # created by infra/bootstrap cover nothing else.
  name = var.env == "prod" ? "fitcheck-api" : "fitcheck-api-${var.env}"

  # Built from known values, so terraform plan shows the execution role policy in full.
  log_group_arn = "arn:aws:logs:${var.region}:${local.account_id}:log-group:${local.name}"

  # Foundation models are AWS-owned, so the ARN has no account ID. The workload
  # boundary (infra/bootstrap) allows this model too.
  embedding_model_arn = "arn:aws:bedrock:${var.region}::foundation-model/${var.embedding_model_id}"

  # Stage 3 resources follow the same pattern: bare names in prod, a suffix elsewhere.
  suffix          = var.env == "prod" ? "" : "-${var.env}"
  checks_name     = "fitcheck-checks${local.suffix}"
  checks_dlq_name = "fitcheck-checks-dlq${local.suffix}"
  alerts_name     = "fitcheck-alerts${local.suffix}"
  # Known values, so the plan shows the execution role policy in full.
  checks_table_arn = "arn:aws:dynamodb:${var.region}:${local.account_id}:table/${local.checks_name}"
  checks_queue_arn = "arn:aws:sqs:${var.region}:${local.account_id}:${local.checks_name}"

  # The worker runs an analysis within one invocation (docs/adr/0006-async-analysis.md).
  lambda_timeout = 300

  # The generation model is invoked through its us. inference profile, which
  # routes to the base model in the destination regions (same pattern as
  # infra/bootstrap/bedrock_invoke.tf).
  generation_profile_arn = "arn:aws:bedrock:${var.region}:${local.account_id}:inference-profile/${var.generation_model_id}"
  generation_model_arns = [
    for region in var.generation_destination_regions :
    "arn:aws:bedrock:${region}::foundation-model/${trimprefix(var.generation_model_id, "us.")}"
  ]

  # Created by infra/bootstrap. The CI may only create roles that carry it.
  workload_boundary_arn = "arn:aws:iam::${local.account_id}:policy/fitcheck-workload-boundary"
}
