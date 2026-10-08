# 7. The AWS smoke test: managed services in AWS, compute on the laptop, destroyed the same day

Date: 2026-09-24

## Status

Accepted. Completes the week 8 goal from the project plan and the Databricks run left open by
week 7.

## Context

Everything so far runs against Floci. Two claims needed a real cloud to prove:

- **The same Terraform modules target Floci and AWS.** Until now only `envs/local` existed.
- **The Spark code is one implementation with two configurations.** The Databricks Asset Bundle
  had passed a schema check but never run on a workspace.

The budget constraint is the same as everywhere else in the project: close to free, and nothing
left running.

## Decision

`infra/terraform/envs/aws` reuses the `storage`, `queue`, `secrets` and `register_lambda` modules
unchanged. It adds only what the cloud needs:

| Resource | Choice | Why |
|---|---|---|
| Postgres | RDS db.t3.micro, PG 18, 20 GB gp3, single-AZ, no backups | Smallest class RDS offers for current Postgres (`db.t4g.micro` is not orderable for 17.x or 18.x). PG 18 matches the local stack. The data regenerates from a seed. |
| Network | Default VPC; public endpoint, security group allows one /32 | The laptop is the only client. A bastion or VPN would cost more than the hours the instance lives. |
| DB password | `manage_master_user_password` | Secrets Manager generates it. It is never in state, the repo or a variable. |
| Databricks | Two IAM roles: a storage credential (lake read/write) and a service credential (raw read-only) | See below. |
| Guard rail | $5 monthly budget with 80% actual and 100% forecast alerts | Catches a forgotten `destroy`. |

**No API deployment (ECS, App Runner).** The API, indexer and eval harness run on the laptop
against RDS, S3 and Bedrock. A container service would prove that a container starts, which CI
already proves by publishing the images. It would not change a single number in the eval.

**Everything is destroyed the same day.** `force_destroy` on the buckets, a 0-day recovery
window on secrets, no final snapshot and no deletion protection make `mise run tf-aws-destroy`
complete in one pass.

### Databricks needs two credentials, not one

A Unity Catalog **external location** lets Spark read and write `s3://larkspur-smoke-lake`. It
gives Python code on the cluster nothing. Bronze fetches raw objects **by version id** with
boto3 (`ingestion/spark/objects.py`), because a document edited twice before ingest must keep
both versions' bytes. Spark's file reader cannot address an S3 object version.

So bronze names a UC **service credential** (`--service-credential`). The job's own process
resolves it once, before the stream starts, and executors receive the resulting temporary keys,
which last about an hour. The role behind it can only read the raw bucket. The first smoke run
showed why it cannot be resolved per micro-batch: on serverless, `foreachBatch` runs in a
separate server-side Python process, and `dbutils` there has no credentials to authenticate with.

Reviewing the job against serverless's execution model found two more differences before any
run. Both are fixed in the shared code rather than in a Databricks branch:

- Serverless runs Spark Connect, so there is no `SparkContext`. `run.py` sets the log level only
  on a classic session.
- Executors import the `mapInPandas` functions by module name. Files synced beside the entry
  script reach only the driver, so `ingestion/` ships as a wheel (`databricks/pyproject.toml`)
  that the job environment installs everywhere.

The first real run (2026-10-08, [evidence](../smoke/2026-10-08/README.md)) found three more
differences. Each one failed a run, and each is fixed in the shared code:

- **`foreachBatch` runs in a separate server-side process** whose `dbutils` cannot
  authenticate. The service credential is now resolved once, before the stream starts.
- **Stopping the session after `dbutils` had used it never returned.** The task sat in
  RUNNING with its work done. `run.py` now stops only a local session; the platform owns the
  session on serverless.
- **Serverless turns deletion vectors on for new Delta tables**, and the indexer's delta-rs
  reader cannot read them. Lake tables now set `delta.enableDeletionVectors = false`.

A check against the RDS API before the first apply also found that `db.t4g.micro` is not
orderable for Postgres 17 or 18. The instance is `db.t3.micro` (same price) on Postgres 18,
which matches local.

### The external id takes two applies

Databricks issues an external id only when a credential is created, and the credential needs
the role to exist. The roles are created with Databricks' documented placeholder `0000`. After
the credentials are created, the real ids are applied. The role must also be able to assume
itself, or Unity Catalog refuses it. IAM rejects a trust principal that does not exist yet, so
the self-assume statement names the account root and pins `aws:PrincipalArn` to the role's own
ARN.

## Consequences

- The run costs cents. Estimated: RDS about $0.02/hour, plus Bedrock at about $0.015 per full
  golden-set run (ADR 0003). S3, SQS, Lambda and Secrets Manager are negligible at this volume.
  The Databricks side is Free Edition.
- The RDS master is not a superuser, so it is subject to the forced RLS on `rag.*`, unlike the
  local `rag` superuser. `db/cloud_logins.py` grants it `SET` (not `INHERIT`) on
  `rag_retriever` and `rag_indexer`. The code then switches roles exactly as it does locally,
  and the local tests' assumptions still hold.
- Service-login passwords are random and rotated on every smoke run. They live only in that
  process's environment.
- The public database endpoint is a deliberate, documented exception. It would not survive
  into a long-lived environment, and a production deployment would put the API next to the
  database in private subnets.

## Running it

```sh
aws login                                   # with AWS_ENDPOINT_URL cleared
MISE_ENV=aws mise run tf-aws -var allowed_cidr=<your-ip>/32 -var budget_email=<you>
# Databricks: the credentials, from the role ARNs in the Terraform outputs.
databricks storage-credentials create --json \
  '{"name":"larkspur-smoke-lake","aws_iam_role":{"role_arn":"<uc_lake_role_arn>"}}'
databricks credentials create-credential --json \
  '{"name":"larkspur-smoke-raw","purpose":"SERVICE","aws_iam_role":{"role_arn":"<uc_raw_role_arn>"}}'
# Re-apply with the external id each one returned (Free Edition issued the same id for both):
MISE_ENV=aws mise run tf-aws -var ... \
  -var storage_credential_external_id=<id> -var service_credential_external_id=<id>
databricks storage-credentials validate --json \
  '{"storage_credential_name":"larkspur-smoke-lake","url":"s3://larkspur-smoke-lake"}'
databricks credentials validate-credential --json \
  '{"credential_name":"larkspur-smoke-raw","purpose":"SERVICE"}'
databricks external-locations create larkspur-smoke-lake s3://larkspur-smoke-lake larkspur-smoke-lake
MISE_ENV=aws mise run smoke                 # resume with: -- --from <step>
MISE_ENV=aws mise run evidence              # API, UI and console evidence, before the destroy
MISE_ENV=aws mise run tf-aws-destroy        # the same day; then delete the three UC objects
```

With more than one CLI profile for the workspace host, set `DATABRICKS_CONFIG_PROFILE`.
