variable "name_prefix" {
  type = string
}

variable "force_destroy" {
  type    = bool
  default = false
}

locals {
  buckets = {
    raw  = "${var.name_prefix}-raw"
    lake = "${var.name_prefix}-lake"
  }
}

resource "aws_s3_bucket" "this" {
  # checkov:skip=CKV_AWS_18: access logging needs a second bucket and a log pipeline nobody
  # reads; the audit trail this project needs is CloudTrail data events on the real account.
  # checkov:skip=CKV_AWS_144: a portfolio dataset regenerated from a seed does not need
  # cross-region replication, and it would double storage cost in the AWS smoke test.
  # checkov:skip=CKV_AWS_145: SSE-S3 is deliberate. A CMK adds a key policy to maintain and a
  # per-request KMS charge for data whose sensitivity is modelled in Postgres, not in S3.
  # checkov:skip=CKV2_AWS_62: the raw bucket's S3 -> SQS notification is wired in
  # envs/local/main.tf, outside this module, so the check cannot see it.
  for_each      = local.buckets
  bucket        = each.value
  force_destroy = var.force_destroy
}

# Versioning is on, so overwritten and deleted objects accumulate. Re-running the generator
# rewrites every changed document, so without expiry the lake grows on each run.
resource "aws_s3_bucket_lifecycle_configuration" "this" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.this[each.key].id

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 7
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.this[each.key].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.this[each.key].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = local.buckets
  bucket                  = aws_s3_bucket.this[each.key].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

output "raw_bucket_name" {
  value = aws_s3_bucket.this["raw"].id
}

output "raw_bucket_arn" {
  value = aws_s3_bucket.this["raw"].arn
}

output "lake_bucket_name" {
  value = aws_s3_bucket.this["lake"].id
}

output "lake_bucket_arn" {
  value = aws_s3_bucket.this["lake"].arn
}
