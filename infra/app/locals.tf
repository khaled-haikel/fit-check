data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  # prod keeps the bare names (fitcheck-api); other environments get a suffix
  # (fitcheck-api-staging). Every name must start with fitcheck-: the CI roles
  # created by infra/bootstrap cover nothing else.
  name = var.env == "prod" ? "fitcheck-api" : "fitcheck-api-${var.env}"

  # Built from known values, so terraform plan shows the execution role policy in full.
  log_group_arn = "arn:aws:logs:${var.region}:${local.account_id}:log-group:${local.name}"

  # Created by infra/bootstrap. The CI may only create roles that carry it.
  workload_boundary_arn = "arn:aws:iam::${local.account_id}:policy/fitcheck-workload-boundary"
}
