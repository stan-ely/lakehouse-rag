variable "name_prefix" {
  type = string
}

variable "recovery_window_in_days" {
  type    = number
  default = 7
}

# Values are set out of band (aws secretsmanager put-secret-value), never in Terraform state.
resource "aws_secretsmanager_secret" "llm" {
  # checkov:skip=CKV_AWS_149: the AWS-managed key is the right default here. A CMK is worth its
  # key policy when the secret must be shared across accounts, which this one is not.
  # checkov:skip=CKV2_AWS_57: rotation needs a rotation Lambda per credential type. The
  # deployed provider is Bedrock, which uses the task role and has no key to rotate.
  name                    = "${var.name_prefix}/llm"
  description             = "LLM provider credentials/config for the query service"
  recovery_window_in_days = var.recovery_window_in_days
}

output "llm_secret_arn" {
  value = aws_secretsmanager_secret.llm.arn
}
