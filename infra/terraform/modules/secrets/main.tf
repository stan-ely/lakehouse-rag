variable "name_prefix" {
  type = string
}

variable "recovery_window_in_days" {
  type    = number
  default = 7
}

# Values are set out of band (aws secretsmanager put-secret-value), never in Terraform state.
resource "aws_secretsmanager_secret" "llm" {
  name                    = "${var.name_prefix}/llm"
  description             = "LLM provider credentials/config for the query service"
  recovery_window_in_days = var.recovery_window_in_days
}

output "llm_secret_arn" {
  value = aws_secretsmanager_secret.llm.arn
}
