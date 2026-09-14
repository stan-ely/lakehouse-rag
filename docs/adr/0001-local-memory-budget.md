# ADR 0001: Local memory budget

- **Status:** accepted
- **Date:** 2026-09-14

## Context
The development machine is a Windows laptop with 7.8 GB of RAM. Docker Desktop's VM has 3.88 GiB and 8 CPUs. On top of that sit the IDE, a browser and Windows itself, so the Docker VM cannot grow.

The full system has more moving parts than fit in that VM at once:
- **Always on:** Postgres with pgvector, Floci (the AWS emulator) and MLflow.
- **Later:** a FastAPI service with an in-process embedding model, and a Streamlit UI.
- **Occasionally:** a PySpark job that builds the Delta Lake medallion tables.

If we cap nothing, one noisy container (typically the JVM or MLflow) pushes the VM into swap or triggers the OOM killer on something unrelated.

## Decision
1. **Every service gets a hard `mem_limit`** in `docker-compose.yml`. A container that exceeds its limit is killed and restarted on its own, instead of starving its neighbours.
2. **Workloads are split into Compose profiles.**
   - `core` (Postgres, Floci, MLflow) is always on.
   - `ingest` (Spark) is a one-shot `run --rm` job.
   - `mise run ingest` stops the API and UI while Spark runs.
3. **Limits:**

   | Service | Limit | Notes |
   |---|---|---|
   | postgres (pgvector) | 512 MiB | `shared_buffers=128MB`, `work_mem=8MB`, `maintenance_work_mem=128MB` |
   | floci | 768 MiB | Quarkus native image. Lambda functions run as sibling containers. |
   | mlflow | 512 MiB | One worker; server-side job execution off (see below). |
   | api (week 3) | 768 MiB | Includes the fastembed ONNX model (bge-small, 384-d) |
   | streamlit (week 6) | 256 MiB | |
   | spark ingest (week 2) | ~1.5 GiB | `local[2]`, 1 GiB driver; run on its own schedule |

   - **During ingest:** core plus Spark comes to 3.3 GiB of limits, which fits the 3.88 GiB VM.
   - **Serving:** core plus API plus UI comes to 2.8 GiB of limits.
   - **Never together:** Spark alongside the API and UI would total 4.3 GiB.
4. **MLflow runs with `MLFLOW_SERVER_ENABLE_JOB_EXECUTION=false`.** With it on, the server started extra Huey consumer processes and was OOM-killed repeatedly: exit code 137 right after turning healthy, at both 384 MiB and 512 MiB. Evaluations run client-side (`mise run eval`), so the server needs no job queue.

## Measurements
Measured with `docker stats --no-stream`, core profile, after `mise run migrate` and `mise run seed`:

| Container | Usage | Limit | % of limit |
|---|---|---|---|
| mlflow | 372 MiB | 512 MiB | 73% |
| floci | 193 MiB | 768 MiB | 25% |
| postgres | 35 MiB | 512 MiB | 7% |
| **total** | **~600 MiB** | 1,792 MiB | |

Earlier startup measurements:
- **MLflow:** peaked at 445 MiB during its first 120 s, with 0 restarts and `oom_kill 0`.
- **Postgres:** 52 MiB right after start.

Ingest (week 2): peaks sampled every ~2 s with `docker stats` across a bronze run (twice) and a
silver + gold run over the seeded corpus (163 object events, 330 chunks):

| Container | Peak | Limit | % of limit |
|---|---|---|---|
| spark (one-shot) | 1,321 MiB | 1,536 MiB | 86% |
| mlflow | 362 MiB | 512 MiB | 71% |
| floci | 216 MiB | 768 MiB | 28% |
| postgres | 67 MiB | 512 MiB | 13% |
| **total** | **~1,970 MiB** | 3,328 MiB | |

- **Spark:** the 1 GiB driver heap plus the Python workers running `mapInPandas` (parsing,
  boto3 downloads) account for the peak. With 14% headroom, a much larger corpus should lower
  `spark.sql.shuffle.partitions`/`maxFilesPerTrigger` before raising the limit.
- **Indexer:** runs on the host, not in the VM (fastembed ONNX, bge-small), so it does not count
  against this budget.
- **Image size is disk, not memory:** the ingest image is 3.3 GB (PySpark jars, the 690 MB AWS
  SDK bundle that S3A requires). Leaving uv's cache in the image doubled it to 7.4 GB.

## Consequences
- **Headroom:** ingest fits with ~1.9 GiB of the VM unused. Re-measure when the API exists (week 3) and update this ADR.
- **Risk:** MLflow sits closest to its limit and its memory has been reported to creep upward over time ([mlflow#22792](https://github.com/mlflow/mlflow/issues/22792)). If steady-state use stays above ~460 MiB, raise it to 640 MiB and lower Floci to 640 MiB. Floci uses a quarter of its limit, so the total stays the same.
- **CI:** GitHub-hosted runners have 16 GB, so the limits never bind there. They exist for local development and document the expected footprint of each service.
- **Postgres tuning:** these settings are for a laptop. The AWS smoke environment (RDS) uses the parameter group defaults for its instance class.
