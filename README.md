# lakehouse-rag

A production-grade enterprise RAG system for **Larkspur Logistics**, a fictional company. It answers questions across the kinds of sources a real company has:

| Source | Examples | How it's served |
|---|---|---|
| Documents in S3 | policy PDFs, SOP/product DOCX | hybrid vector + keyword search |
| Wiki | Markdown/HTML page tree, with stale and duplicate pages | hybrid vector + keyword search |
| Conversations | tickets, Slack threads, emails (JSON) | hybrid vector + keyword search |
| Operational DB | customers, shipments, invoices, employees (Postgres) | guarded text-to-SQL |

A router decides whether each question needs documents, SQL, or both.

> Status: weeks 1–4 are done (foundation, ingestion, retrieval and generation, text-to-SQL and routing). **Week 5, the evaluation harness**, is next. The roadmap is below.

## Architecture

```
S3 raw/ ──event──► SQS ──► register Lambda ──► bronze manifest
Spark batch (Delta Lake): bronze → silver (parse, dedup, PII tag, ACLs) → gold (chunks, Change Data Feed)
Indexer: gold changes → bge-small embeddings (CPU) → Postgres + pgvector (+ tsvector, allowed_groups)
FastAPI /query (JWT groups): router → docs  | hybrid retrieval (RRF, ACL filter + RLS)
                                   → sql   | model-written SQL → sqlglot allowlist → rag_sql login, read-only txn
                                   → hybrid| SQL result + retrieved chunks as numbered sources
           → cited answer, refusal when evidence is missing, PII masked → token usage and cost per request
Streamlit (week 6): persona switcher to demonstrate access control
```

## Production-readiness goals
- **Evaluation gate:** a golden set scored on recall@k, MRR, router accuracy, SQL execution accuracy and LLM-judge faithfulness. CI fails on any regression, and on **any ACL leak**.
- **Access control:**
  - Retrieval is filtered by group, with Postgres row-level security as a second layer.
  - Text-to-SQL is enforced twice, independently: a sqlglot allowlist for views and functions, and a least-privilege login whose group context the query cannot change.
  - See [ADR 0002](docs/adr/0002-text-to-sql-isolation.md).
- **Grounding:** answers must cite their sources. Uncited or low-evidence answers become one uniform refusal, and retrieved text is wrapped as untrusted data.
- **Observability and cost:** token usage and USD cost per request today; MLflow tracing, Prometheus metrics and structured logs in week 6.
- **IaC and CI/CD:** the same Terraform modules target [Floci](https://github.com/floci-io/floci) locally and real AWS for smoke tests. GitHub Actions runs lint, types, tests, integration tests against Floci, and the eval gate.
- **Databricks path:** the same Spark code ships as a Databricks Asset Bundle (serverless environment 6).

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
mise run token sales  # JWT for a demo persona; then POST /query with `Authorization: Bearer ...`
mise run test       # unit tests
mise run test-integration
```

A `/query` response carries:
- the answer and its citations
- `route` and `route_method` (`llm`, `heuristic` or `fallback`)
- any generated `sql` and `sql_error`
- token usage, `cost_usd` and per-stage timings

The fake provider is deterministic and free. With it, routing always falls back to the heuristic and SQL generation does not succeed, so use `bedrock` or `anthropic` to see SQL answers.

The generator is deterministic (`--seed`, default 7) and dated as of 2026-08-31, which is also the API's reference date for relative periods (`RAG_AS_OF_DATE`). Re-running it uploads only objects whose content or ACL changed. Generated files and `manifest.json` land in `data/out/`.

Local runs target Floci by default. To use real AWS, set `MISE_ENV=aws`; this loads `mise.aws.toml` and removes the Floci endpoint overrides.

## Design decisions
- [ADR 0001: Local memory budget](docs/adr/0001-local-memory-budget.md)
- [ADR 0002: Text-to-SQL isolation](docs/adr/0002-text-to-sql-isolation.md)

## Roadmap
1. ✅ Foundation: toolchain, Compose, Terraform on Floci, schema
2. ✅ Ingestion: Lambda register, Spark medallion jobs, incremental indexer
3. ✅ Retrieval and generation: hybrid search, citations, provider abstraction
4. ✅ Structured data: guarded text-to-SQL, router
5. Evaluation harness and CI gate
6. Hardening: guardrails, resilience, observability, UI
7. CI/CD and Databricks bundle
8. AWS smoke test and write-up
