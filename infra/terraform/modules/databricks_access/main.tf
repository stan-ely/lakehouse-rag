variable "name" {
  type = string
}

variable "external_id" {
  description = <<-EOT
    External id Databricks shows when the credential is created. Apply first with the documented
    placeholder "0000" so the role exists, create the credential from the role ARN, then re-apply
    with the real id.
  EOT
  type        = string
  default     = "0000"
}

variable "read_bucket_arns" {
  description = "Buckets the credential may read"
  type        = list(string)
  default     = []
}

variable "write_bucket_arns" {
  description = "Buckets the credential may read and write"
  type        = list(string)
  default     = []
}

variable "uc_master_role_arn" {
  description = "The Databricks-owned role Unity Catalog assumes from, in AWS commercial regions"
  type        = string
  default     = "arn:aws:iam::414351767826:role/unity-catalog-prod-UCMasterRole-14S5ZJVKOTYTL"
}

data "aws_caller_identity" "current" {}

locals {
  # Built from the name rather than read from the resource, which the trust policy cannot
  # reference while the role is being created.
  role_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.name}"
}

data "aws_iam_policy_document" "trust" {
  statement {
    sid     = "UnityCatalog"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [var.uc_master_role_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [var.external_id]
    }
  }

  # Unity Catalog refuses credentials whose role cannot assume itself. IAM rejects a principal
  # that does not exist yet, so name the account and pin the caller to this role's ARN.
  statement {
    sid     = "SelfAssume"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:PrincipalArn"
      values   = [local.role_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [var.external_id]
    }
  }
}

resource "aws_iam_role" "this" {
  name                 = var.name
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "permissions" {
  dynamic "statement" {
    for_each = length(var.read_bucket_arns) > 0 ? [1] : []

    content {
      sid       = "ReadObjects"
      actions   = ["s3:GetObject", "s3:GetObjectVersion"]
      resources = [for arn in var.read_bucket_arns : "${arn}/*"]
    }
  }

  dynamic "statement" {
    for_each = length(var.write_bucket_arns) > 0 ? [1] : []

    content {
      sid       = "ReadWriteObjects"
      actions   = ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:DeleteObject"]
      resources = [for arn in var.write_bucket_arns : "${arn}/*"]
    }
  }

  statement {
    sid       = "ListBuckets"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = concat(var.read_bucket_arns, var.write_bucket_arns)
  }

  statement {
    sid       = "AssumeSelf"
    actions   = ["sts:AssumeRole"]
    resources = [local.role_arn]
  }
}

resource "aws_iam_role_policy" "this" {
  name   = var.name
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.permissions.json
}

output "role_arn" {
  value = aws_iam_role.this.arn
}
