terraform {
  # S3-native state locking (use_lockfile) shipped in 1.10 and became generally
  # available in 1.11. CloudShell and CI both pin 1.16.5.
  required_version = ">= 1.11.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.68"
    }
  }

  # Partial configuration: the bucket name is passed with
  # -backend-config=backend.hcl (git-ignored), so no account-specific value is
  # committed. The very first run uses a local backend through a git-ignored
  # backend_override.tf, because this configuration creates the bucket itself.
  backend "s3" {
    key          = "bootstrap/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
