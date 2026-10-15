# Model invocation shared by the workload boundary (the Lambda) and by
# fitcheck-ci-eval (retrieval evaluation and index builds in CI). The models
# are configuration: var.embedding_model_id and var.generation_models.
data "aws_iam_policy_document" "bedrock_invoke" {
  statement {
    sid       = "InvokeEmbeddingModel"
    actions   = ["bedrock:InvokeModel"]
    resources = [local.embedding_model_arn]
  }

  statement {
    sid       = "InvokeGenerationProfiles"
    actions   = ["bedrock:InvokeModel"]
    resources = [for model in local.generation_models : model.inference_profile_arn]
  }

  # Each profile's destination models are reachable only through that profile.
  dynamic "statement" {
    for_each = local.generation_models

    content {
      sid       = "Invoke${statement.value.sid_suffix}ThroughProfile"
      actions   = ["bedrock:InvokeModel"]
      resources = statement.value.foundation_model_arns

      condition {
        test     = "StringEquals"
        variable = "bedrock:InferenceProfileArn"
        values   = [statement.value.inference_profile_arn]
      }
    }
  }
}
