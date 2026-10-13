# These values identify the account. They go into backend.hcl and into the
# GitHub repository variables, never into a committed file.

output "state_bucket" {
  description = "State bucket name: backend.hcl and the TF_STATE_BUCKET repository variable."
  value       = aws_s3_bucket.state.bucket
}

output "ci_plan_role_arn" {
  description = "Role assumed by pull request workflows: the AWS_PLAN_ROLE_ARN repository variable."
  value       = aws_iam_role.ci_plan.arn
}

output "ci_apply_role_arn" {
  description = "Role assumed by main-branch and rollback workflows: the AWS_APPLY_ROLE_ARN repository variable."
  value       = aws_iam_role.ci_apply.arn
}

output "workload_boundary_policy_name" {
  description = "Permissions boundary that infra/app must attach to every role it creates."
  value       = aws_iam_policy.workload_boundary.name
}
