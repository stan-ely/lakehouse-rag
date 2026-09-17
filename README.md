# lakehouse-rag

A production-grade enterprise RAG system for **Larkspur Logistics**, a fictional company. It answers questions across the kinds of sources a real company has:

| Source | Examples | How it's served |
|---|---|---|
| Documents in S3 | policy PDFs, SOP/product DOCX | hybrid vector + keyword search |
| Wiki | Markdown/HTML page tree, with stale and duplicate pages | hybrid vector + keyword search |
| Conversations | tickets, Slack threads, emails (JSON) | hybrid vector + keyword search |
| Operational DB | customers, shipments, invoices, employees (Postgres) | guarded text-to-SQL |

A router decides whether each question needs documents, SQL, or both.

> Status: weeks 1–6 are done (foundation, ingestion, retrieval and generation, text-to-SQL and routing, evaluation, production hardening). **Week 7, CI/CD and the Databricks bundle**, is next. The roadmap is below.

## Architecture

```
S3 raw/ ──event──► SQS ──► register Lambda ──► bronze manifest
Spark batch (Delta Lake): bronze → silver (parse, dedup, PII tag, ACLs) → gold (chunks, Change Data Feed)
Indexer: gold changes → bge-small embeddings (CPU) → Postgres + pgvector (+ tsvector, allowed_groups)
FastAPI /query (JWT groups): router → docs  | hybrid retrieval (RRF, ACL filter + RLS)
                                   → sql   | model-written SQL → sqlglot allowlist → rag_sql login, read-only txn
                                   → hybrid| SQL result + retrieved chunks as numbered sources
           → cited answer, refusal when evidence is missing, PII masked → token usage and cost per request
Streamlit: persona switcher to demonstrate access control
```

## Production-readiness goals
- **Evaluation gate:** a golden set scored on recall@k, MRR, router accuracy, SQL execution accuracy and LLM-judge faithfulness. CI fails on any regression, and on **any ACL leak**.
- **Access control:**
  - Retrieval is filtered by group, with Postgres row-level security as a second layer.
  - Text-to-SQL is enforced twice, independently: a sqlglot allowlist for views and functions, and a least-privilege login whose group context the query cannot change.
  - See [ADR 0002](docs/adr/0002-text-to-sql-isolation.md).
- **Grounding:** answers must cite their sources. Uncited or low-evidence answers become one uniform refusal, and retrieved text is wrapped as untrusted data.
- **Guardrails and resilience:** prompt-injection attempts in retrieved text are detected, counted and marked for the model rather than silently dropped; PII is masked in answers and in traces; `/query` is rate limited per caller; a circuit breaker stops a dead provider from costing every request its timeout.
- **Observability and cost:** structured JSON logs carrying identifiers but never content, Prometheus metrics with closed label sets, and USD cost per request. MLflow tracing is available but off by default, because a span records the question, the retrieved text and the answer. See [ADR 0004](docs/adr/0004-hardening-defaults.md).
- **IaC and CI/CD:** the same Terraform modules target [Floci](https://github.com/floci-io/floci) locally and real AWS for smoke tests. GitHub Actions runs lint, types, unit tests, `tflint`, `actionlint`, a checkov scan of the Terraform, a schema check of the Databricks bundle, then integration tests against a real Compose stack and the retrieval eval gate. `main` is protected: both jobs must pass.
- **Databricks path:** the same Spark code ships as a Databricks Asset Bundle (serverless environment 6, Python 3.12 — the version mise pins locally). The job runs `ingestion/spark/run.py`, the module the ingest container runs, against the lake through a Unity Catalog external location. One implementation, two configurations.

## Stack
Python 3.12 (matches Databricks serverless), FastAPI, PySpark + Delta Lake, Postgres 18 + pgvector, sqlglot, fastembed, Claude on Amazon Bedrock or the Anthropic API (pluggable; a fake provider for CI), MLflow 3, Terraform, Docker Compose, Floci, mise, uv.

## Quick start (local)
Prerequisites: Docker Desktop and [mise](https://mise.jdx.dev).

```sh
mise install        # pinned toolchain: python, uv, terraform, tflint, databricks cli, ...
mise run sync       # Python dependencies
mise run up         # Postgres+pgvector, Floci, MLflow
mise run tf-local   # buckets, queues, secrets and the register Lambda on Floci
mise run migrate    # schemas, roles, row-level security; enables local service logins
mise run seed       # generate Larkspur data: ops tables into Postgres, 157 documents into S3
mise run backfill   # register manifests for objects whose S3 events were missed (idempotent)
mise run ingest     # Spark container: bronze objects -> silver documents -> gold chunks (Delta)
mise run index      # embed changed gold chunks into pgvector (incremental via change feed)
mise run serve      # query API container on :8000 (768 MiB; `mise run ingest` stops it first)
mise run api        # or run it on the host with reload (RAG_LLM_PROVIDER=fake|bedrock|anthropic)
mise run ui         # Streamlit demo on :8501 (persona switcher; needs the API running)
mise run token sales  # JWT for a demo persona; then POST /query with `Authorization: Bearer ...`
mise run test       # unit tests
mise run test-integration
mise run eval       # score the golden set (retrieval only: deterministic, no model, no cost)
mise run scan       # checkov over the Terraform
mise run bundle-check  # databricks.yml against the bundle schema (no workspace needed)
```

A `/query` response carries:
- the answer and its citations
- `route` and `route_method` (`llm`, `heuristic` or `fallback`)
- any generated `sql` and `sql_error`, **only when `RAG_EXPOSE_SQL` is set** — the SQL names tables, columns and filters, so it is schema disclosure by default
- token usage, `cost_usd` and per-stage timings

The fake provider is deterministic and free. With it, routing always falls back to the heuristic and SQL generation does not succeed, so use `bedrock` or `anthropic` to see SQL answers.

The generator is deterministic (`--seed`, default 7) and dated as of 2026-08-31, which is also the API's reference date for relative periods (`RAG_AS_OF_DATE`). Re-running it uploads only objects whose content or ACL changed. Generated files and `manifest.json` land in `data/out/`.

Local runs target Floci by default. To use real AWS, set `MISE_ENV=aws`; this loads `mise.aws.toml` and removes the Floci endpoint overrides.

## Evaluation

`mise run eval` scores a golden set of 121 questions and fails on a regression or any ACL leak. Every expected answer is computed from the same modules that generate the corpus and the ops database (`data_gen/facts.py`, `data_gen/world.py`), so changing a business rule moves the documents, the rows and the expectations together instead of leaving a stale answer key.

| Case kind | Count | What it checks |
|---|---|---|
| docs | 62 | the document that answers the question is retrieved, and the answer states the fact |
| sql | 37 | the router picks `sql`, the generated query runs, and the number is right |
| hybrid | 10 | policy and live records are combined in one answer |
| acl | 12 | a caller without the group is refused, and the restricted text never reaches them |

Two profiles, gated by `eval/thresholds.yaml`:

```sh
mise run eval        # retrieval: index and ACLs only, no model, free -- this is the CI gate
mise run eval-full   # whole pipeline against real Bedrock; about a cent per run
```

Runs are logged to MLflow (`lakehouse-rag-eval` experiment on :5000) with the full per-case report as an artifact; `--no-mlflow` skips it, as CI does.

**Retrieval profile** (the index on its own): recall@k 0.93, MRR 0.88, **0 ACL leaks**, ~26 ms per query. Every miss is a hybrid question naming a customer: tickets and emails about that customer outrank the policy page the question also needs. Reranking is a week 6 candidate.

**Full profile** on Amazon Nova Lite ([ADR 0003](docs/adr/0003-evaluation-model.md) compares three models):

| metric | result |
|---|---|
| router accuracy | 0.927 |
| SQL execution success | 0.936 |
| answer correctness | 0.872 |
| recall@k (end to end) | 0.903 |
| refusal on restricted questions | 1.000 |
| **ACL leaks** | **0** |
| cost / p50 latency | $0.013 per run / 1.6 s |

No model tried leaked restricted content or answered a question the caller had no right to, which is the point: access control lives in Postgres and the retrieval filter, not in the model's judgement.

## Design decisions
- [ADR 0001: Local memory budget](docs/adr/0001-local-memory-budget.md)
- [ADR 0002: Text-to-SQL isolation](docs/adr/0002-text-to-sql-isolation.md)
- [ADR 0003: Evaluation model choice](docs/adr/0003-evaluation-model.md)
- [ADR 0004: Hardening defaults](docs/adr/0004-hardening-defaults.md)

## Roadmap
1. ✅ Foundation: toolchain, Compose, Terraform on Floci, schema
2. ✅ Ingestion: Lambda register, Spark medallion jobs, incremental indexer
3. ✅ Retrieval and generation: hybrid search, citations, provider abstraction
4. ✅ Structured data: guarded text-to-SQL, router
5. ✅ Evaluation harness and CI gate
6. ✅ Hardening: guardrails, resilience, observability, Streamlit UI
7. 🚧 CI/CD and Databricks bundle: security scanning, bundle schema check (Free Edition run still to do)
8. AWS smoke test and write-up
