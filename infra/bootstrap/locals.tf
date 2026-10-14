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

  # Stage 3 services. Their ARNs carry the resource name, so fitcheck-* scopes them.
  dynamodb_table_arns  = "arn:aws:dynamodb:${var.region}:${local.account_id}:table/fitcheck-*"
  dynamodb_index_arns  = "arn:aws:dynamodb:${var.region}:${local.account_id}:table/fitcheck-*/index/*"
  sqs_queue_arns       = "arn:aws:sqs:${var.region}:${local.account_id}:fitcheck-*"
  sns_topic_arns       = "arn:aws:sns:${var.region}:${local.account_id}:fitcheck-*"
  metric_alarm_arns    = "arn:aws:cloudwatch:${var.region}:${local.account_id}:alarm:fitcheck-*"
  event_source_mapping = "arn:aws:lambda:${var.region}:${local.account_id}:event-source-mapping:*"

  # Bedrock models are AWS-owned ARNs. Titan Text Embeddings V2 runs on demand
  # in the region. Claude Sonnet 5.5 runs through the us. cross-region
  # inference profile, which needs the profile ARN plus the foundation model in
  # each destination region of the profile (ADR 0004).
  titan_model_arn              = "arn:aws:bedrock:${var.region}::foundation-model/amazon.titan-embed-text-v2:0"
  sonnet_inference_profile_arn = "arn:aws:bedrock:${var.region}:${local.account_id}:inference-profile/us.anthropic.claude-sonnet-5-5"
  sonnet_profile_regions       = ["us-east-1", "us-east-2", "us-west-2"]
  sonnet_foundation_model_arns = [
    for region in local.sonnet_profile_regions : "arn:aws:bedrock:${region}::foundation-model/anthropic.claude-sonnet-5-5"
  ]

  # infra/app keeps its state (and its .tflock files) under this prefix.
  # The bootstrap state is only reachable by the administrator.
  app_state_object_arns = "${local.state_bucket_arn}/app/*"
}
