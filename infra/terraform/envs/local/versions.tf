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

provider "aws" {
  region     = var.region
  access_key = "test"
  secret_key = "test"

  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  s3_use_path_style           = true

  endpoints {
    s3             = var.floci_endpoint
    sqs            = var.floci_endpoint
    lambda         = var.floci_endpoint
    secretsmanager = var.floci_endpoint
    iam            = var.floci_endpoint
    sts            = var.floci_endpoint
    logs           = var.floci_endpoint
  }

  default_tags {
    tags = {
      project    = "lakehouse-rag"
      env        = "local"
      managed_by = "terraform"
    }
  }
}
