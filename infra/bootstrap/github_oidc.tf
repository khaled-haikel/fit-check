# GitHub Actions authenticates with short-lived OIDC tokens; no AWS access key
# exists anywhere. See docs/adr/0001-keyless-oidc.md.
#
# AWS does not allow a name prefix on this resource. Only one provider per URL
# can exist in an account: if one already exists, creation fails instead of
# importing it.
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # thumbprint_list is omitted: IAM fetches it and validates GitHub's
  # certificate against its own trusted root CAs.
}

# Pull requests only. GitHub does not issue OIDC tokens to workflows triggered
# from forks, so only branches of this repository can assume the role.
data "aws_iam_policy_document" "ci_plan_trust" {
  statement {
    sid     = "GitHubPullRequests"
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
      values   = ["${var.github_subject_prefix}:pull_request"]
    }
  }
}

# The main branch only. Jobs that assume this role must not declare a GitHub
# environment: that changes the sub claim to ...:environment:<name>.
data "aws_iam_policy_document" "ci_apply_trust" {
  statement {
    sid     = "GitHubMainBranch"
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
      values   = ["${var.github_subject_prefix}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "ci_plan" {
  name               = "fitcheck-ci-plan"
  description        = "GitHub Actions on pull requests: read-only terraform plan of infra/app."
  assume_role_policy = data.aws_iam_policy_document.ci_plan_trust.json

  # The trust policy names the provider by its computed ARN.
  depends_on = [aws_iam_openid_connect_provider.github]
}

resource "aws_iam_role_policy" "ci_plan" {
  name   = "fitcheck-ci-plan"
  role   = aws_iam_role.ci_plan.id
  policy = data.aws_iam_policy_document.ci_plan.json
}

resource "aws_iam_role" "ci_apply" {
  name               = "fitcheck-ci-apply"
  description        = "GitHub Actions on main: terraform apply of infra/app and alias rollback."
  assume_role_policy = data.aws_iam_policy_document.ci_apply_trust.json

  # The trust policy names the provider by its computed ARN.
  depends_on = [aws_iam_openid_connect_provider.github]
}

resource "aws_iam_role_policy" "ci_apply" {
  name   = "fitcheck-ci-apply"
  role   = aws_iam_role.ci_apply.id
  policy = data.aws_iam_policy_document.ci_apply.json

  # The policy names the boundary by its computed ARN.
  depends_on = [aws_iam_policy.workload_boundary]
}
