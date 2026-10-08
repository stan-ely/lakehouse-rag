output "raw_bucket" {
  value = module.storage.raw_bucket_name
}

output "lake_bucket" {
  value = module.storage.lake_bucket_name
}

output "ingest_queue_url" {
  value = module.ingest_queue.queue_url
}

output "ingest_dlq_url" {
  value = module.ingest_queue.dlq_url
}

output "register_function_name" {
  value = module.register_lambda.function_name
}

output "llm_secret_arn" {
  value = module.secrets.llm_secret_arn
}

output "db_host" {
  value = module.database.address
}

output "db_port" {
  value = module.database.port
}

output "db_name" {
  value = module.database.db_name
}

output "db_username" {
  value = module.database.username
}

output "db_master_secret_arn" {
  value = module.database.master_secret_arn
}

output "uc_lake_role_arn" {
  description = "Role ARN for the Databricks storage credential (lake read/write)"
  value       = module.lake_access.role_arn
}

output "uc_raw_role_arn" {
  description = "Role ARN for the Databricks service credential (raw read-only)"
  value       = module.raw_access.role_arn
}
