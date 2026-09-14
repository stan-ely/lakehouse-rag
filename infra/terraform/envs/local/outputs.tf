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

output "llm_secret_arn" {
  value = module.secrets.llm_secret_arn
}
