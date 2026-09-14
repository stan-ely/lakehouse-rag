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

# Every create/delete in the raw bucket lands on the ingest queue; the register Lambda consumes it.
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
