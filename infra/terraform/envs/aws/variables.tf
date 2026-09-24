variable "region" {
  type    = string
  default = "us-east-1"
}

variable "name_prefix" {
  description = "Also the bucket prefix databricks.yml expects (s3://larkspur-smoke-lake)"
  type        = string
  default     = "larkspur-smoke"
}

variable "allowed_cidr" {
  description = "Your public IPv4 address as a /32; the only source allowed to reach Postgres"
  type        = string
}

variable "budget_email" {
  description = "Where budget alerts go"
  type        = string
}

variable "monthly_budget_usd" {
  type    = number
  default = 5
}

variable "storage_credential_external_id" {
  description = "External id of the Databricks storage credential; \"0000\" until it exists"
  type        = string
  default     = "0000"
}

variable "service_credential_external_id" {
  description = "External id of the Databricks service credential; \"0000\" until it exists"
  type        = string
  default     = "0000"
}
