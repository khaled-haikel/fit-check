# Model invocation shared by the workload boundary (the Lambda) and by
# fitcheck-ci-eval (retrieval evaluation and index builds in CI).
data "aws_iam_policy_document" "bedrock_invoke" {
  statement {
    sid       = "InvokeTitanEmbeddings"
    actions   = ["bedrock:InvokeModel"]
    resources = [local.titan_model_arn]
  }

  statement {
    sid       = "InvokeSonnetProfile"
    actions   = ["bedrock:InvokeModel"]
    resources = [local.sonnet_inference_profile_arn]
  }

  # The destination models are reachable only through the profile.
  statement {
    sid       = "InvokeSonnetThroughProfile"
    actions   = ["bedrock:InvokeModel"]
    resources = local.sonnet_foundation_model_arns

    condition {
      test     = "StringEquals"
      variable = "bedrock:InferenceProfileArn"
      values   = [local.sonnet_inference_profile_arn]
    }
  }
}
