"""Same-day AWS smoke test: the whole pipeline on real services, then a written report.

    MISE_ENV=aws mise run smoke                 # every step
    MISE_ENV=aws mise run smoke -- --from index # resume after a failure

Steps, in order, each a module the local stack already runs:

    migrate   alembic on RDS, fresh service-login passwords, SET on the switched roles
    seed      data_gen: ops tables into RDS, documents into the raw bucket
    register  wait for the real Lambda to write a manifest per raw object (backfill on timeout)
    ingest    databricks bundle deploy + run: bronze, silver, gold on serverless
    index     embed gold chunks from the S3 lake into RDS
    eval      the full golden set against RDS and Bedrock (Nova Lite), gated by thresholds
    report    docs/smoke/<date>.md

The laptop is the compute; AWS provides S3, SQS, Lambda, RDS and Bedrock (docs/adr/0007).
Children run with this interpreter rather than through `uv run`, because a mise shim would put
the local `[env]` back over the values set here. Step timings persist in data/eval/smoke-state.json
so a resumed run still reports the whole day.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import boto3
import psycopg

from db.cloud_logins import enable_logins, grant_role_switching

log = logging.getLogger("smoke")

ROOT = Path(__file__).resolve().parent.parent
TF_DIR = ROOT / "infra" / "terraform" / "envs" / "aws"
STATE = ROOT / "data" / "eval" / "smoke-state.json"
REPORT = ROOT / "data" / "eval" / "smoke.json"
# The last local full run on Nova Lite, the numbers the cloud run should reproduce.
BASELINE = ROOT / "data" / "eval" / "full-novalite-prompt-v2.json"
MODEL = "us.amazon.nova-lite-v1:0"
COMPARED = (
    "router_accuracy",
    "sql_success",
    "answer_correctness",
    "recall_at_k",
    "mrr",
    "acl_leaks",
    "p50_latency_ms",
    "p95_latency_ms",
    "total_cost_usd",
)


class Smoke:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.outputs = terraform_outputs(args.tf_dir)
        self.state: dict[str, Any] = (
            json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
        )
        self.env = self._environment()

    # -- environment ---------------------------------------------------------------------------

    def _environment(self) -> dict[str, str]:
        out = self.outputs
        secret = boto3.client("secretsmanager").get_secret_value(
            SecretId=out["db_master_secret_arn"]
        )
        master = json.loads(secret["SecretString"])
        host, port, db = out["db_host"], out["db_port"], out["db_name"]
        dsn = (
            f"postgresql+psycopg://{quote(master['username'], safe='')}:"
            f"{quote(master['password'], safe='')}@{host}:{port}/{db}?sslmode=require"
        )
        env = dict(os.environ)
        env.update(
            {
                "RAG_ENV": "aws",
                "DATABASE_URL": dsn,
                "RAG_DATABASE_URL": dsn,
                "RAG_RAW_BUCKET": out["raw_bucket"],
                "RAG_LAKE_BUCKET": out["lake_bucket"],
                "RAG_GOLD_URI": f"s3://{out['lake_bucket']}/delta/gold/chunks",
                "RAG_LLM_PROVIDER": "bedrock",
                "RAG_LLM_MODEL": MODEL,
                "RAG_BEDROCK_REGION": "us-west-2",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        env.pop("RAG_BEDROCK_ENDPOINT_URL", None)
        # The indexer reads Delta through Rust's object_store, which cannot read `aws login`'s
        # credential cache. Resolve the session once here and pass plain temporary keys down.
        credentials = boto3.Session().get_credentials()
        if credentials is None:
            sys.exit("no AWS credentials; run `aws login` first")
        frozen = credentials.get_frozen_credentials()
        env["AWS_ACCESS_KEY_ID"] = str(frozen.access_key)
        env["AWS_SECRET_ACCESS_KEY"] = str(frozen.secret_key)
        if frozen.token:
            env["AWS_SESSION_TOKEN"] = frozen.token
        return env

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.env["DATABASE_URL"].replace("+psycopg", ""), autocommit=True)

    def _sql_login(self) -> None:
        """Rotates the text-to-SQL login's password and points the API settings at it."""
        with self._connect() as conn:
            grant_role_switching(conn)
            password = enable_logins(conn)["rag_sql"]
        out = self.outputs
        self.env["RAG_SQL_DATABASE_URL"] = (
            f"postgresql://rag_sql:{quote(password, safe='')}@{out['db_host']}:{out['db_port']}"
            f"/{out['db_name']}?sslmode=require"
        )

    def run(self, *argv: str, check: bool = True) -> int:
        log.info("$ %s", " ".join(argv))
        done = subprocess.run(argv, cwd=ROOT, env=self.env, check=check)  # noqa: S603
        return done.returncode

    def python(self, module: str, *argv: str, check: bool = True) -> int:
        return self.run(sys.executable, "-m", module, *argv, check=check)

    # -- steps ---------------------------------------------------------------------------------

    def migrate(self) -> None:
        self.python("alembic", "upgrade", "head")
        self._sql_login()

    def seed(self) -> None:
        self.python("data_gen.generate")

    def register(self) -> None:
        s3 = boto3.client("s3")
        raw, lake = self.outputs["raw_bucket"], self.outputs["lake_bucket"]
        expected = count_objects(s3, raw, "")
        deadline = time.monotonic() + self.args.register_timeout
        while (found := count_objects(s3, lake, "manifests/")) < expected:
            if time.monotonic() > deadline:
                log.warning("%d of %d manifests after timeout; backfilling", found, expected)
                self.python("ingestion.lambda_register.backfill")
                break
            log.info("manifests %d/%d", found, expected)
            time.sleep(10)
        self.state["raw_objects"] = expected
        self.state["manifests"] = count_objects(s3, lake, "manifests/")

    def ingest(self) -> None:
        self.run("databricks", "bundle", "deploy", "-t", "smoke")
        self.run("databricks", "bundle", "run", "ingest", "-t", "smoke")

    def index(self) -> None:
        self.python("ingestion.indexer.run")
        # RLS is forced on rag.* and the RDS master is not a superuser, so count as the indexer.
        with self._connect() as conn, conn.transaction():
            conn.execute("SET LOCAL ROLE rag_indexer")
            row = conn.execute("SELECT count(*), count(DISTINCT doc_id) FROM rag.chunks").fetchone()
        self.state["chunks"], self.state["documents"] = row if row else (0, 0)

    def eval(self) -> None:
        if "RAG_SQL_DATABASE_URL" not in self.env:
            self._sql_login()
        # The thresholds gate is the pass criterion, but a miss must still produce the report,
        # so the exit code is recorded here and returned by the run as a whole.
        code = self.python("eval.run_eval", "--mode", "full", "--report", str(REPORT), check=False)
        self.state["gate_passed"] = code == 0

    def report(self) -> None:
        path = ROOT / "docs" / "smoke" / f"{datetime.now(UTC).date().isoformat()}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        smoke = json.loads(REPORT.read_text(encoding="utf-8"))
        baseline = (
            json.loads(self.args.baseline.read_text(encoding="utf-8"))
            if self.args.baseline.exists()
            else None
        )
        path.write_text(render(smoke, baseline, self.state), encoding="utf-8", newline="\n")
        log.info("report written to %s", path)

    # -- driver --------------------------------------------------------------------------------

    def steps(self) -> dict[str, Callable[[], None]]:
        return {
            "migrate": self.migrate,
            "seed": self.seed,
            "register": self.register,
            "ingest": self.ingest,
            "index": self.index,
            "eval": self.eval,
            "report": self.report,
        }

    def execute(self, names: list[str]) -> None:
        timings: dict[str, float] = self.state.setdefault("seconds", {})
        for name in names:
            log.info("== %s", name)
            started = time.monotonic()
            self.steps()[name]()
            timings[name] = round(time.monotonic() - started, 1)
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_text(json.dumps(self.state, indent=2), encoding="utf-8")


STEP_NAMES = ("migrate", "seed", "register", "ingest", "index", "eval", "report")


def terraform_outputs(tf_dir: Path) -> dict[str, Any]:
    result = subprocess.run(  # noqa: S603 - fixed argv
        ["terraform", f"-chdir={tf_dir}", "output", "-json"],  # noqa: S607 - terraform from mise
        capture_output=True,
        text=True,
        check=True,
    )
    outputs = {name: item["value"] for name, item in json.loads(result.stdout).items()}
    if not outputs:
        sys.exit(f"no terraform outputs in {tf_dir}; run `mise run tf-aws` first")
    return outputs


def count_objects(s3: Any, bucket: str, prefix: str) -> int:
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
    return sum(page.get("KeyCount", 0) for page in pages)


def render(smoke: dict[str, Any], baseline: dict[str, Any] | None, state: dict[str, Any]) -> str:
    metrics = smoke["metrics"]
    lines = [
        f"# AWS smoke run, {smoke['generated_at'][:10]}",
        "",
        f"Model `{smoke['model']}` on Bedrock; Postgres on RDS; lake on S3 built by the",
        "Databricks Free Edition serverless job. See docs/adr/0007-aws-smoke-topology.md.",
        "",
        "## Pipeline",
        "",
        "| Check | Value |",
        "|---|---|",
        f"| Raw objects | {state.get('raw_objects', '?')} |",
        f"| Manifests written by the Lambda | {state.get('manifests', '?')} |",
        f"| Documents indexed | {state.get('documents', '?')} |",
        f"| Chunks indexed | {state.get('chunks', '?')} |",
        f"| Golden cases | {smoke['cases']} |",
        f"| ACL leaks | {smoke['leaks']} |",
        f"| Errors | {smoke['errors']} |",
        f"| Threshold gate | {_gate(state.get('gate_passed'))} |",
        "",
        "## Metrics",
        "",
        "| Metric | Smoke (AWS) | Local baseline |" if baseline else "| Metric | Smoke (AWS) |",
        "|---|---|---|" if baseline else "|---|---|",
    ]
    for name in COMPARED:
        if name not in metrics:
            continue
        row = f"| {name} | {_fmt(metrics[name])} |"
        if baseline:
            row += f" {_fmt(baseline['metrics'].get(name))} |"
        lines.append(row)
    lines += ["", "## Step durations", "", "| Step | Seconds |", "|---|---|"]
    lines += [f"| {name} | {secs} |" for name, secs in state.get("seconds", {}).items()]
    return "\n".join(lines) + "\n"


def _gate(passed: bool | None) -> str:
    if passed is None:
        return "not run"
    return "passed" if passed else "FAILED"


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}" if value < 1 else f"{value:,.0f}"
    return str(value)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Same-day AWS smoke test")
    parser.add_argument("--from", dest="start", choices=STEP_NAMES, default=STEP_NAMES[0])
    parser.add_argument("--only", choices=STEP_NAMES, help="run one step")
    parser.add_argument("--tf-dir", type=Path, default=TF_DIR)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--register-timeout", type=int, default=300, help="seconds")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    # A leftover Floci endpoint would seed the emulator and report success against it.
    if os.environ.get("AWS_ENDPOINT_URL"):
        sys.exit("AWS_ENDPOINT_URL is set; run with MISE_ENV=aws so the Floci endpoint is cleared")

    names = [args.only] if args.only else list(STEP_NAMES[STEP_NAMES.index(args.start) :])
    smoke = Smoke(args)
    smoke.execute(names)
    if smoke.state.get("gate_passed") is False:
        sys.exit("the eval gate failed; see the report")


if __name__ == "__main__":
    main()
