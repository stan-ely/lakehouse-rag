variable "identifier" {
  type = string
}

variable "allowed_cidr" {
  description = "The one address allowed to reach Postgres: the laptop that runs the smoke test"
  type        = string

  validation {
    condition     = can(cidrhost(var.allowed_cidr, 0)) && endswith(var.allowed_cidr, "/32")
    error_message = "allowed_cidr must be a single IPv4 address as a /32."
  }
}

variable "instance_class" {
  type    = string
  default = "db.t3.micro"
}

variable "engine_version" {
  description = "Major version only; RDS picks the current minor, which must offer pgvector"
  type        = string
  default     = "18"
}

variable "db_name" {
  type    = string
  default = "rag"
}

variable "username" {
  description = "Master login. Not `rag`: that name is the local superuser, and RDS masters are not superusers"
  type        = string
  default     = "rag_admin"
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_db_subnet_group" "this" {
  name       = var.identifier
  subnet_ids = data.aws_subnets.default.ids
}

resource "aws_security_group" "this" {
  # checkov:skip=CKV2_AWS_5: attached to the instance below through vpc_security_group_ids.
  name        = "${var.identifier}-postgres"
  description = "Postgres from the smoke-test laptop only"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_ingress_rule" "postgres" {
  security_group_id = aws_security_group.this.id
  description       = "Postgres from the smoke-test laptop"
  cidr_ipv4         = var.allowed_cidr
  ip_protocol       = "tcp"
  from_port         = 5432
  to_port           = 5432
}

# RDS for PostgreSQL 15+ already defaults rds.force_ssl to 1. Setting it here makes the
# requirement visible, and keeps it if the default ever changes.
resource "aws_db_parameter_group" "this" {
  name   = var.identifier
  family = "postgres${var.engine_version}"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "this" {
  # checkov:skip=CKV_AWS_17: public, but only to one /32 (see ADR 0007). The alternative, a
  # bastion or VPN, costs more than the hours this instance exists, and the laptop is the client.
  # checkov:skip=CKV_AWS_157: Multi-AZ doubles the cost of an instance that lives for one
  # afternoon and is destroyed on purpose.
  # checkov:skip=CKV_AWS_161: IAM database auth would need token refresh in every client; the
  # master password is generated and held by Secrets Manager, never in state or the repo.
  # checkov:skip=CKV_AWS_118: enhanced monitoring needs its own IAM role, for a smoke test that
  # reads its results from the eval report.
  # checkov:skip=CKV_AWS_353: Performance Insights is for tuning a long-lived database.
  # checkov:skip=CKV_AWS_129: log exports go to CloudWatch at a cost; failures surface in the
  # smoke script's own output.
  # checkov:skip=CKV_AWS_133: backups are off on purpose. The data is regenerated from a seed,
  # and a same-day destroy should leave no snapshot behind to keep paying for.
  # checkov:skip=CKV_AWS_293: deletion protection would block the same-day destroy.
  # checkov:skip=CKV2_AWS_60: copying tags to snapshots is moot; no snapshots are taken.
  # checkov:skip=CKV_AWS_354: the Performance Insights key check is moot; the feature is off.
  identifier     = var.identifier
  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class

  allocated_storage = 20
  storage_type      = "gp3"
  storage_encrypted = true

  db_name                     = var.db_name
  username                    = var.username
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.this.id]
  parameter_group_name   = aws_db_parameter_group.this.name
  publicly_accessible    = true
  multi_az               = false

  backup_retention_period    = 0
  skip_final_snapshot        = true
  deletion_protection        = false
  apply_immediately          = true
  auto_minor_version_upgrade = true
}

output "address" {
  value = aws_db_instance.this.address
}

output "port" {
  value = aws_db_instance.this.port
}

output "db_name" {
  value = aws_db_instance.this.db_name
}

output "username" {
  value = aws_db_instance.this.username
}

output "master_secret_arn" {
  description = "Secrets Manager secret RDS created for the master password"
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}
