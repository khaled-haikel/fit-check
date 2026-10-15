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

variable "embedding_model_id" {
  description = "Bedrock model that embeds /search queries. It must be the model the packaged index was built with; CI reads it from config/models.json."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]+[.][a-z0-9.-]+:[0-9]+$", var.embedding_model_id))
    error_message = "The embedding_model_id value must be a Bedrock foundation model ID, such as amazon.titan-embed-text-v2:0."
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

# Each /search call invokes Bedrock, so it gets a tighter limit than the stage default.
variable "search_throttling_rate_limit" {
  description = "Steady-state requests per second allowed on POST /search."
  type        = number
  default     = 1
}

variable "search_throttling_burst_limit" {
  description = "Request burst allowed on POST /search."
  type        = number
  default     = 2
}

variable "generation_model_id" {
  description = "Inference profile of the generation model (config/models.json), such as us.anthropic.claude-sonnet-4-6."
  type        = string

  validation {
    condition     = can(regex("^us[.]anthropic[.][a-z0-9.-]+(:[0-9]+)?$", var.generation_model_id))
    error_message = "The generation_model_id value must be a us. Anthropic inference profile ID."
  }
}

variable "generation_destination_regions" {
  description = "Regions the us. inference profile routes to from us-east-1 (docs/adr/0004-bedrock-invocation-path.md)."
  type        = list(string)
  default     = ["us-east-1", "us-east-2", "us-west-2"]
}

variable "alert_email" {
  description = "Address subscribed to the alerts topic. CI passes it from the ALERT_EMAIL secret; it is never in the repository."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[^@ ]+@[^@ ]+[.][^@ ]+$", var.alert_email))
    error_message = "The alert_email value must be an email address."
  }
}

variable "public_monthly_cap_usd" {
  description = "What public analyses may spend on Bedrock per month, in USD."
  type        = number
  default     = 2
}

variable "daily_checks_per_ip" {
  description = "New analyses one client IP may start per UTC day (cached results do not count)."
  type        = number
  default     = 5
}

variable "checks_post_throttling_rate_limit" {
  description = "Steady-state requests per second allowed on POST /checks."
  type        = number
  default     = 1
}

variable "checks_post_throttling_burst_limit" {
  description = "Request burst allowed on POST /checks."
  type        = number
  default     = 2
}

variable "checks_get_throttling_rate_limit" {
  description = "Steady-state requests per second allowed on GET /checks/{id}, which pages poll."
  type        = number
  default     = 5
}

variable "checks_get_throttling_burst_limit" {
  description = "Request burst allowed on GET /checks/{id}."
  type        = number
  default     = 10
}
