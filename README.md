# lakehouse-rag

A production-grade enterprise RAG system for **Larkspur Logistics**, a fictional company. It answers questions across the kinds of sources a real company has:

| Source | Examples | How it's served |
|---|---|---|
| Documents in S3 | policy PDFs, SOP/product DOCX | hybrid vector + keyword search |
| Wiki | Markdown/HTML page tree, with stale and duplicate pages | hybrid vector + keyword search |
| Conversations | tickets, Slack threads, emails (JSON) | hybrid vector + keyword search |
| Operational DB | customers, shipments, invoices, employees (Postgres) | guarded text-to-SQL |

A router decides whether each question needs documents, SQL, or both.

> Status: **week 1, foundation** in progress. The build plan is below.

## Architecture

```
S3 raw/ ──event──► SQS ──► register Lambda ──► bronze manifest
Spark batch (Delta Lake): bronze → silver (parse, dedup, PII tag, ACLs) → gold (chunks, Change Data Feed)
Indexer: gold changes → bge-small embeddings (CPU) → Postgres + pgvector (+ tsvector, allowed_groups)
FastAPI /query: router → hybrid retrieval (RRF, ACL filter + RLS) | text-to-SQL (sqlglot-guarded, read-only role)
           → cited answer, refusal when evidence is missing → MLflow traces, Prometheus metrics, token cost
Streamlit: persona switcher to demonstrate access control
```

## Production-readiness goals
- **Evaluation gate:** a golden set scored on recall@k, MRR, router accuracy, SQL execution accuracy and LLM-judge faithfulness. CI fails on any regression, and on **any ACL leak**.
- **Access control:** retrieval is filtered by group, with Postgres row-level security as a second layer.
- **Observability and cost:** MLflow tracing, Prometheus metrics, structured logs, token cost per request.
- **IaC and CI/CD:** the same Terraform modules target [Floci](https://github.com/floci-io/floci) locally and real AWS for smoke tests. GitHub Actions runs lint, types, tests, integration tests against Floci, and the eval gate.
- **Databricks path:** the same Spark code ships as a Databricks Asset Bundle (serverless environment 6).

## Stack
Python 3.12 (matches Databricks serverless), FastAPI, PySpark + Delta Lake, Postgres 18 + pgvector, fastembed, Amazon Bedrock (pluggable; a fake provider for CI), MLflow 3, Terraform, Docker Compose, Floci, mise, uv.

## Quick start (local)
Prerequisites: Docker Desktop and [mise](https://mise.jdx.dev).

```sh
mise install        # pinned toolchain: python, uv, terraform, tflint, databricks cli, ...
mise run sync       # Python dependencies
mise run up         # Postgres+pgvector, Floci, MLflow
mise run tf-local   # buckets, queues, secrets on Floci
mise run migrate    # pgvector, ops/rag/analytics schemas, roles, row-level security
mise run seed       # generate Larkspur data: ops tables into Postgres, 157 documents into S3
mise run test       # unit tests
mise run test-integration
```

The generator is deterministic (`--seed`, default 7) and dated as of 2026-08-31. Re-running it uploads only objects whose content or ACL changed. Generated files and `manifest.json` land in `data/out/`.

Local runs target Floci by default. To use real AWS, set `MISE_ENV=aws`; this loads `mise.aws.toml` and removes the Floci endpoint overrides.

## Roadmap
1. Foundation: toolchain, Compose, Terraform on Floci, schema
2. Ingestion: Lambda register, Spark medallion jobs, incremental indexer
3. Retrieval and generation: hybrid search, citations, provider abstraction
4. Structured data: guarded text-to-SQL, router
5. Evaluation harness and CI gate
6. Hardening: guardrails, resilience, observability, UI
7. CI/CD and Databricks bundle
8. AWS smoke test and write-up
