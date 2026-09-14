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
  for_each      = local.buckets
  bucket        = each.value
  force_destroy = var.force_destroy
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = aws_s3_bucket.this
  bucket                  = each.value.id
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
