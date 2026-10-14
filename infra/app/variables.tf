variable "env" {
  description = "Environment name. prod keeps the bare resource names; other environments get a suffix."
  type        = string
  default     = "prod"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,15}$", var.env))
    error_message = "The env value must be a short lowercase name, such as prod or staging."
  }
}

variable "region" {
  description = "AWS region for every fit-check resource."
  type        = string
  default     = "us-east-1"
}

variable "commit_sha" {
  description = "Git commit being deployed. GET /health reports it, and each published version keeps it."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.commit_sha))
    error_message = "The commit_sha value must be a full 40-character git SHA."
  }
}

variable "allowed_origin" {
  description = "The only browser origin allowed by CORS, such as https://example.com (no path, no trailing slash)."
  type        = string

  validation {
    condition     = can(regex("^https://[a-z0-9.-]+(:[0-9]+)?$", var.allowed_origin))
    error_message = "The allowed_origin value must be an https origin without a path or trailing slash."
  }
}

variable "lambda_package" {
  description = "Zip built by scripts/build_lambda.py, relative to infra/app."
  type        = string
  default     = "../../dist/fitcheck-api.zip"
}

variable "log_retention_days" {
  description = "Retention of the function's log group."
  type        = number
  default     = 14
}

variable "throttling_rate_limit" {
  description = "Steady-state requests per second allowed on the API stage."
  type        = number
  default     = 2
}

variable "throttling_burst_limit" {
  description = "Request burst allowed on the API stage."
  type        = number
  default     = 5
}
