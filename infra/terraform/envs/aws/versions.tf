terraform {
  required_version = ">= 1.16.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }
}

# Real AWS. Credentials come from `aws login` (~/.aws/login); `MISE_ENV=aws` clears the Floci
# endpoint and dummy keys that mise.toml sets for local work.
provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project    = "lakehouse-rag"
      env        = "smoke"
      managed_by = "terraform"
    }
  }
}
