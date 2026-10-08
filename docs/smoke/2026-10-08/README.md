# Evidence: AWS + Databricks smoke run, 2026-10-08

The whole pipeline ran once on real services, and the stack was then destroyed the same day.

- **AWS:** S3, SQS, Lambda, RDS Postgres 18 and Bedrock (Nova Lite).
- **Databricks:** the Free Edition serverless job built the Delta lake.
- **The laptop:** ran the indexer, the eval harness, the API and the UI against those services.

The topology is in [ADR 0007](../../adr/0007-aws-smoke-topology.md). The numbers are in the
[run report](../2026-10-08.md).

The account id, database host, client IP, e-mail address and workspace host are redacted
everywhere below.

## Result

| | |
|---|---|
| Raw objects → manifests written by the Lambda | 157 → 157 (150 invocations, 0 errors, empty DLQ) |
| Databricks job bronze → silver → gold | succeeded in 2 min 49 s |
| Indexed into RDS | 156 documents, 330 chunks (one near-duplicate wiki page collapsed in silver) |
| Golden set, full profile on Bedrock | 121 cases, **gate passed**, **0 ACL leaks**, 0 errors |
| Router / SQL / answer correctness | 0.936 / 0.957 / 0.917 (local baseline 0.927 / 0.894 / 0.899) |
| Cost of the eval run | $0.014 |

## What a user saw

The Streamlit demo ran on the laptop and called the API, which used RDS and Bedrock. The
screenshots and the recording come from `python -m eval.evidence --only ui`.

| | Persona | Shows |
|---|---|---|
| [01](ui/01-exec.png) | exec | docs route: cited answer from the travel policy |
| [02](ui/02-ops.png) | ops | sql route: model-written SQL on RDS, cited as a live query |
| [03](ui/03-sales.png) | sales | hybrid route: live late-delivery count plus the SLA policy's credit |
| [04](ui/04-hr.png) | hr | restricted pay-band document, answered for the group that may read it |
| [05](ui/05-ops.png) | ops | the same question, refused with the uniform refusal |
| [06](ui/06-contractor.png) | contractor | no groups at all: everything fails closed |

The [recording](ui/demo.webm) is all six in one take.

![Hybrid answer](ui/03-sales.png)

## Files

| File | What it is |
|---|---|
| [`infra.json`](infra.json) | RDS, S3, Lambda, SQS and Bedrock state, read back from the AWS APIs |
| [`uc-credentials.json`](uc-credentials.json) | Unity Catalog validation of both credentials (11/11 PASS) |
| [`databricks.json`](databricks.json) | the successful ingest run and its three tasks |
| [`api.json`](api.json) | the six `/query` responses in full, plus the Prometheus counters afterwards |
| [`console/`](console/) | AWS and Databricks console screenshots, cropped to remove the account id |
| [`logs/`](logs/) | Terraform applies, every smoke attempt including the failures, and the final pass |

## What the run caught

The run failed three times before it passed. Each failure was a real difference between
Databricks serverless and the local stack, and each is now fixed in the shared code:

1. **`dbutils` cannot authenticate inside `foreachBatch`** ([log](logs/3-smoke-attempt1-bronze-dbutils-auth.log)).
   - On serverless, the batch function runs in a separate server-side Python process.
   - The fix: bronze now resolves the UC service credential once, in the job's own process,
     and hands the temporary keys to the executors.
2. **The task never finished after its work was done** ([log](logs/4-smoke-attempt2-bronze-hang.log)).
   - Bronze logged "up to date" within a minute, then stayed RUNNING.
   - Two probe jobs ruled out the obvious causes:
     - `dbutils` starts no extra threads.
     - `spark.stop()` returns normally after a plain stream.
   - The remaining difference was stopping the platform's session after `dbutils` had used it.
   - The fix: the job now leaves the session to the platform on serverless.
3. **Deletion vectors** ([log](logs/5-smoke-attempt3-deletion-vectors.log)).
   - Serverless enables them on new Delta tables, and the delta-rs reader in the indexer cannot
     read them.
   - The fix: lake tables are created with `delta.enableDeletionVectors = false`.

Before anything was applied, a check against the RDS API found one more problem: `db.t4g.micro`
is not orderable for Postgres 17 or 18. The module now uses `db.t3.micro`, at the same price,
and Postgres 18, which matches local.
