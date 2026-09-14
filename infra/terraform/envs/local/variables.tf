variable "region" {
  type    = string
  default = "us-east-1"
}

variable "floci_endpoint" {
  description = "Floci emulator endpoint"
  type        = string
  default     = "http://localhost:4566"
}

variable "name_prefix" {
  type    = string
  default = "larkspur-local"
}
