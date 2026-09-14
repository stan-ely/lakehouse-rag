variable "name" {
  type = string
}

variable "source_file" {
  description = "Path to the handler module; it depends only on the stdlib and the runtime's boto3"
  type        = string
}

variable "build_dir" {
  description = "Directory for the generated deployment zip"
  type        = string
}

variable "raw_bucket_arn" {
  type = string
}

variable "lake_bucket_name" {
  type = string
}

variable "lake_bucket_arn" {
  type = string
}

variable "manifest_prefix" {
  type    = string
  default = "manifests"
}

variable "queue_arn" {
  type = string
}

variable "batch_size" {
  type    = number
  default = 10
}

variable "maximum_concurrency" {
  description = "Caps concurrent invocations from the queue (minimum 2); protects laptop memory locally"
  type        = number
  default     = 2
}

variable "timeout_seconds" {
  description = "Keep at most 1/6 of the queue visibility timeout so in-flight batches are not redelivered"
  type        = number
  default     = 30
}

variable "memory_size" {
  type    = number
  default = 256
}

variable "log_retention_days" {
  type    = number
  default = 14
}

data "archive_file" "package" {
  type        = "zip"
  source_file = var.source_file
  output_path = "${var.build_dir}/${var.name}.zip"
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = var.name
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

# Least privilege: read raw objects, write only under the manifest prefix, consume one queue.
data "aws_iam_policy_document" "permissions" {
  statement {
    sid       = "ReadRawObjects"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${var.raw_bucket_arn}/*"]
  }

  statement {
    sid       = "WriteManifests"
    actions   = ["s3:PutObject"]
    resources = ["${var.lake_bucket_arn}/${var.manifest_prefix}/*"]
  }

  statement {
    sid       = "ConsumeIngestQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [var.queue_arn]
  }

  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.this.arn}:*"]
  }
}

resource "aws_iam_role_policy" "this" {
  name   = var.name
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.permissions.json
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${var.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "this" {
  function_name    = var.name
  role             = aws_iam_role.this.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.package.output_path
  source_code_hash = data.archive_file.package.output_base64sha256
  timeout          = var.timeout_seconds
  memory_size      = var.memory_size

  environment {
    variables = {
      LAKE_BUCKET     = var.lake_bucket_name
      MANIFEST_PREFIX = var.manifest_prefix
    }
  }

  depends_on = [aws_cloudwatch_log_group.this, aws_iam_role_policy.this]
}

resource "aws_lambda_event_source_mapping" "queue" {
  event_source_arn        = var.queue_arn
  function_name           = aws_lambda_function.this.arn
  batch_size              = var.batch_size
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = var.maximum_concurrency
  }
}

output "function_name" {
  value = aws_lambda_function.this.function_name
}

output "event_source_mapping_uuid" {
  value = aws_lambda_event_source_mapping.queue.uuid
}
