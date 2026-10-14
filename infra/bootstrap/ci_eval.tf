# fitcheck-ci-eval: model invocation only. Pull requests use it for the
# retrieval evaluation; the deploy job on main uses it to build the vector
# index before it assumes fitcheck-ci-apply for Terraform. It holds no state
# access and no infrastructure permissions.
data "aws_iam_policy_document" "ci_eval_trust" {
  statement {
    sid     = "GitHubPullRequestsAndMain"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "${var.github_subject_prefix}:pull_request",
        "${var.github_subject_prefix}:ref:refs/heads/main",
      ]
    }
  }
}

resource "aws_iam_role" "ci_eval" {
  name               = "fitcheck-ci-eval"
  description        = "GitHub Actions on pull requests and main: Bedrock model invocation only."
  assume_role_policy = data.aws_iam_policy_document.ci_eval_trust.json

  # The trust policy names the provider by its computed ARN.
  depends_on = [aws_iam_openid_connect_provider.github]
}

resource "aws_iam_role_policy" "ci_eval" {
  name   = "fitcheck-ci-eval"
  role   = aws_iam_role.ci_eval.id
  policy = data.aws_iam_policy_document.bedrock_invoke.json
}
