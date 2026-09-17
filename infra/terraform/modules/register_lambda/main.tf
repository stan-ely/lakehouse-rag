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
  # checkov:skip=CKV_AWS_158: CloudWatch encrypts log groups with a service key already. A CMK
  # here would add a key policy to maintain for logs that carry S3 keys, not document content.
  # checkov:skip=CKV_AWS_338: a year of retention is for an audit obligation this project does
  # not have; 14 days is what a registration failure is actually investigated within.
  name              = "/aws/lambda/${var.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "this" {
  # checkov:skip=CKV_AWS_116: failed batches are redriven to the queue's own DLQ (see the queue
  # module), which is the right place for a function whose only trigger is SQS. A function-level
  # DLQ would catch nothing extra and would split failures across two places.
  # checkov:skip=CKV_AWS_117: the function talks to S3 and SQS over their public endpoints. A VPC
  # would mean NAT or endpoints to pay for, and would not reduce what the function can reach.
  # checkov:skip=CKV_AWS_173: the environment holds a bucket name and a prefix, not a secret.
  # checkov:skip=CKV_AWS_272: code signing needs a signing profile and a release process; the
  # package is built from this repo by `terraform apply` and has no separate supply chain.
  # checkov:skip=CKV_AWS_115: concurrency is capped where it is actually triggered — the event
  # source mapping sets maximum_concurrency (default 2), which is what protects a 7.8 GB laptop.
  # Reserved concurrency would carve the same cap out of the account limit as well.
  # checkov:skip=CKV_AWS_50: X-Ray on a single-step handler adds a trace nobody reads. The
  # tracing that matters in this system is on the query path, in MLflow.
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
