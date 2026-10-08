# The same modules as envs/local, pointed at real AWS, plus what only the cloud needs: a managed
# Postgres, the roles Databricks assumes, and a budget. Everything here is destroyed the same day
# (ADR 0007), which is why nothing below keeps data past `terraform destroy`.

module "storage" {
  source        = "../../modules/storage"
  name_prefix   = var.name_prefix
  force_destroy = true
}

module "ingest_queue" {
  source            = "../../modules/queue"
  name              = "${var.name_prefix}-ingest"
  source_bucket_arn = module.storage.raw_bucket_arn
}

module "secrets" {
  source                  = "../../modules/secrets"
  name_prefix             = var.name_prefix
  recovery_window_in_days = 0
}

resource "aws_s3_bucket_notification" "raw_to_ingest" {
  bucket = module.storage.raw_bucket_name

  queue {
    queue_arn = module.ingest_queue.queue_arn
    events    = ["s3:ObjectCreated:*", "s3:ObjectRemoved:*"]
  }

  depends_on = [module.ingest_queue]
}

module "register_lambda" {
  source           = "../../modules/register_lambda"
  name             = "${var.name_prefix}-register"
  source_file      = "${path.module}/../../../../ingestion/lambda_register/handler.py"
  build_dir        = "${path.module}/.build"
  raw_bucket_arn   = module.storage.raw_bucket_arn
  lake_bucket_name = module.storage.lake_bucket_name
  lake_bucket_arn  = module.storage.lake_bucket_arn
  queue_arn        = module.ingest_queue.queue_arn
}

module "database" {
  source       = "../../modules/database"
  identifier   = var.name_prefix
  allowed_cidr = var.allowed_cidr
}

# Spark on Databricks reaches the lake through a UC external location backed by this role.
module "lake_access" {
  source            = "../../modules/databricks_access"
  name              = "${var.name_prefix}-uc-lake"
  external_id       = var.storage_credential_external_id
  write_bucket_arns = [module.storage.lake_bucket_arn]
}

# Bronze reads raw objects by version with boto3, which an external location does not cover,
# so it uses a UC service credential. Read-only, and only the raw bucket.
module "raw_access" {
  source           = "../../modules/databricks_access"
  name             = "${var.name_prefix}-uc-raw"
  external_id      = var.service_credential_external_id
  read_bucket_arns = [module.storage.raw_bucket_arn]
}

resource "aws_budgets_budget" "smoke" {
  name         = "${var.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_email]
  }
}
