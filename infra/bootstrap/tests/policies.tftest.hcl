# Offline checks of the rendered IAM documents; no AWS credentials needed:
#   terraform init -backend=false && terraform test

provider "aws" {
  region                      = "us-east-1"
  access_key                  = "offline-test"
  secret_key                  = "offline-test"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
}

override_data {
  target = data.aws_caller_identity.current
  values = {
    account_id = "000000000000"
  }
}

variables {
  state_bucket_name = "fitcheck-tfstate-0123abcd"
  alert_email       = "alerts@example.com"
}

run "iam_policies" {
  command = plan

  assert {
    condition = alltrue(flatten([
      for doc in [
        data.aws_iam_policy_document.ci_plan.json,
        data.aws_iam_policy_document.ci_apply.json,
        data.aws_iam_policy_document.workload_boundary.json,
        data.aws_iam_policy_document.ci_plan_trust.json,
        data.aws_iam_policy_document.ci_apply_trust.json,
        aws_iam_role_policy.ci_eval.policy,
        data.aws_iam_policy_document.ci_eval_trust.json,
        ] : [
        for statement in jsondecode(doc).Statement : !(
          anytrue([for action in flatten([statement.Action]) : strcontains(action, "*")]) &&
          anytrue([for resource in flatten([try(statement.Resource, [])]) : strcontains(resource, "*")])
        )
      ]
    ]))
    error_message = "A policy statement has a wildcard in both Action and Resource."
  }

  assert {
    condition     = jsondecode(data.aws_iam_policy_document.ci_plan_trust.json).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:khaled-haikel@72475193/fit-check@1417405917:pull_request"
    error_message = "The plan role must trust pull requests of this repository only."
  }

  assert {
    condition     = jsondecode(data.aws_iam_policy_document.ci_apply_trust.json).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:khaled-haikel@72475193/fit-check@1417405917:ref:refs/heads/main"
    error_message = "The apply role must trust the main branch of this repository only."
  }

  assert {
    condition = toset(flatten([jsondecode(data.aws_iam_policy_document.ci_eval_trust.json).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"]])) == toset([
      "repo:khaled-haikel@72475193/fit-check@1417405917:pull_request",
      "repo:khaled-haikel@72475193/fit-check@1417405917:ref:refs/heads/main",
    ])
    error_message = "The eval role must trust exactly the pull requests and the main branch of this repository."
  }

  assert {
    condition = alltrue(flatten([
      for statement in jsondecode(aws_iam_role_policy.ci_eval.policy).Statement : [
        for action in flatten([statement.Action]) :
        startswith(action, "bedrock:") || startswith(action, "bedrock-mantle:")
      ]
    ]))
    error_message = "The eval role may only invoke Bedrock models."
  }
}
