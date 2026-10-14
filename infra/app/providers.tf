provider "aws" {
  region = var.region

  # The CI roles' API Gateway permissions check the project tag.
  default_tags {
    tags = {
      project = "fit-check"
      env     = var.env
    }
  }
}
