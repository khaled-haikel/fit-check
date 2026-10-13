variable "region" {
  description = "AWS region for every fit-check resource."
  type        = string
  default     = "us-east-1"
}

variable "state_bucket_name" {
  description = "State bucket name: fitcheck-tfstate- plus 8 random hex characters, never the account ID. The same value goes into backend.hcl."
  type        = string

  validation {
    condition     = can(regex("^fitcheck-tfstate-[0-9a-f]{8}$", var.state_bucket_name))
    error_message = "The state_bucket_name value must look like fitcheck-tfstate-0123abcd."
  }
}

variable "alert_email" {
  description = "Address that receives the AWS Budgets alerts. Set it in terraform.tfvars, which is git-ignored."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "The alert_email value must be an email address."
  }
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget, in USD, for the whole AWS account."
  type        = number
  default     = 3
}

variable "github_subject_prefix" {
  description = <<-EOT
    Prefix of the sub claim in this repository's GitHub Actions OIDC tokens.
    Repositories created after 2026-07-15 use the immutable format
    repo:OWNER@OWNER_ID/REPO@REPO_ID. Check the current value (sub_claim_prefix) with:
    curl -s https://api.github.com/repos/khaled-haikel/fit-check/actions/oidc/customization/sub
  EOT
  type        = string
  default     = "repo:khaled-haikel@72475193/fit-check@1417405917"
}
