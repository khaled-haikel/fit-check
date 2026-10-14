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
  # 20 while stages 3 to 5 call Claude Sonnet 5.5; back to 5 after stage 5.
  default = 20
}

variable "embedding_model_id" {
  description = "Bedrock model for embeddings, invoked on demand in the region (ADR 0005)."
  type        = string
  default     = "amazon.titan-embed-text-v2:0"
}

variable "generation_models" {
  description = <<-EOT
    Claude models the workload and fitcheck-ci-eval may invoke, each through its
    us. cross-region inference profile: the profile ID, the base model ID, and
    the profile's destination regions (from scripts/bedrock_probe.py). Holds the
    current generation model and the approved upgrade (ADR 0004).
  EOT
  type = list(object({
    inference_profile_id = string
    model_id             = string
    destination_regions  = list(string)
  }))
  default = [
    {
      # Current generation model.
      inference_profile_id = "us.anthropic.claude-sonnet-5"
      model_id             = "anthropic.claude-sonnet-5"
      destination_regions  = ["us-east-1", "us-east-2", "us-west-2"]
    },
    {
      # Planned upgrade. The account is not entitled to it yet, so this grant
      # opens nothing until AWS enables the model (ADR 0001, ADR 0004).
      inference_profile_id = "us.anthropic.claude-sonnet-5-5"
      model_id             = "anthropic.claude-sonnet-5-5"
      destination_regions  = ["us-east-1", "us-east-2", "us-west-2"]
    },
  ]
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
