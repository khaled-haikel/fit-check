data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  # ARNs built from known values rather than resource attributes, so that
  # terraform plan shows every trust and permission policy in full before apply.
  state_bucket_arn         = "arn:aws:s3:::${var.state_bucket_name}"
  github_oidc_provider_arn = "arn:aws:iam::${local.account_id}:oidc-provider/token.actions.githubusercontent.com"
  workload_boundary_name   = "fitcheck-workload-boundary"
  workload_boundary_arn    = "arn:aws:iam::${local.account_id}:policy/${local.workload_boundary_name}"

  # Scopes the CI roles may touch. Every resource the app creates is named fitcheck-*.
  lambda_function_arns = "arn:aws:lambda:${var.region}:${local.account_id}:function:fitcheck-*"
  log_group_arns       = "arn:aws:logs:${var.region}:${local.account_id}:log-group:fitcheck-*"
  workload_role_arns   = "arn:aws:iam::${local.account_id}:role/fitcheck-*"
  ci_role_arns         = "arn:aws:iam::${local.account_id}:role/fitcheck-ci-*"

  # API Gateway ARNs carry generated IDs, not names, so these cannot be
  # name-scoped. See docs/adr/0001-keyless-oidc.md.
  http_api_collection_arn = "arn:aws:apigateway:${var.region}::/apis"
  http_api_arns           = "arn:aws:apigateway:${var.region}::/apis/*"
  apigateway_tag_arns     = "arn:aws:apigateway:${var.region}::/tags/*"

  # infra/app keeps its state (and its .tflock files) under this prefix.
  # The bootstrap state is only reachable by the administrator.
  app_state_object_arns = "${local.state_bucket_arn}/app/*"
}
