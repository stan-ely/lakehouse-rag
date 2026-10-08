"""Evidence of the AWS smoke run: what existed, what ran, and what a user saw.

    MISE_ENV=aws uv run python -m eval.evidence            # infra, databricks, api, ui
    MISE_ENV=aws uv run python -m eval.evidence --only ui

Run it after `eval.smoke` and before `tf-aws-destroy`, while the resources still exist. Writes
docs/smoke/<date>/:

    infra.json       RDS, S3, Lambda, SQS and Bedrock facts read back from the AWS APIs
    databricks.json  the last run of the ingest job: tasks, states, durations
    api.json         /query responses per persona against RDS + Bedrock, and /metrics
    ui/*.png         the Streamlit demo, one screenshot per scenario
    ui/demo.webm     the same scenarios as one recording

The account id and the database host are redacted from everything written.
"""

import argparse
import json
import logging
import re
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
import httpx

from app.auth import mint_token
from app.settings import Settings
from app.tokens import PERSONAS
from eval.smoke import BASELINE, ROOT, TF_DIR, Smoke, count_objects

log = logging.getLogger("evidence")

API_PORT = 8000
UI_PORT = 8501

# (persona, question, what it demonstrates)
SCENARIOS = [
    ("exec", "What is the nightly hotel cap for domestic travel?", "docs route, cited answer"),
    ("ops", "How many shipments were late last month?", "sql route on RDS"),
    (
        "sales",
        "Hardy Outfitters had late deliveries in August 2026. How many were there, and what "
        "late delivery credit does the policy give their tier?",
        "hybrid route: policy plus live rows",
    ),
    ("hr", "What is the base salary range for pay band L7?", "restricted doc, allowed group"),
    ("ops", "What is the base salary range for pay band L7?", "same question, refused"),
    ("contractor", "What is the nightly hotel cap for domestic travel?", "no groups, fails closed"),
]


class Evidence:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.out.mkdir(parents=True, exist_ok=True)
        self.smoke = Smoke(argparse.Namespace(tf_dir=TF_DIR, baseline=BASELINE, register_timeout=0))
        self.smoke._sql_login()
        self.env = self.smoke.env
        self.env["RAG_EXPOSE_SQL"] = "true"  # the demo shows the generated SQL
        self.outputs = self.smoke.outputs
        self.account = boto3.client("sts").get_caller_identity()["Account"]

    def redact(self, text: str) -> str:
        text = text.replace(self.account, "<account>")
        return text.replace(str(self.outputs["db_host"]), "<rds-host>")

    def write(self, name: str, data: Any) -> None:
        path = self.out / name
        path.write_text(self.redact(json.dumps(data, indent=2, default=str)) + "\n", "utf-8")
        log.info("wrote %s", path)

    # -- AWS -----------------------------------------------------------------------------------

    def infra(self) -> None:
        out = self.outputs
        rds = boto3.client("rds").describe_db_instances()["DBInstances"]
        s3 = boto3.client("s3")
        raw, lake = out["raw_bucket"], out["lake_bucket"]
        lam = boto3.client("lambda")
        functions = [
            f for f in lam.list_functions()["Functions"] if f["FunctionName"].startswith("larkspur")
        ]
        sqs = boto3.client("sqs")
        queues = sqs.list_queues(QueueNamePrefix="larkspur").get("QueueUrls", [])
        self.write(
            "infra.json",
            {
                "captured_at": datetime.now(UTC).isoformat(),
                "terraform_outputs": out,
                "rds": [
                    {
                        k: db.get(k)
                        for k in (
                            "DBInstanceIdentifier",
                            "DBInstanceClass",
                            "Engine",
                            "EngineVersion",
                            "DBInstanceStatus",
                            "AllocatedStorage",
                            "StorageType",
                            "StorageEncrypted",
                            "PubliclyAccessible",
                            "MultiAZ",
                            "InstanceCreateTime",
                        )
                    }
                    for db in rds
                ],
                "s3_objects": {
                    f"{raw}/": count_objects(s3, raw, ""),
                    **{
                        f"{lake}/{p}": count_objects(s3, lake, p)
                        for p in ("manifests/", "delta/bronze/", "delta/silver/", "delta/gold/")
                    },
                },
                "lambda": [
                    {
                        "name": f["FunctionName"],
                        "runtime": f.get("Runtime"),
                        "memory_mb": f.get("MemorySize"),
                        "invocations_today": self._metric(
                            "AWS/Lambda", "Invocations", "FunctionName", f["FunctionName"]
                        ),
                        "errors_today": self._metric(
                            "AWS/Lambda", "Errors", "FunctionName", f["FunctionName"]
                        ),
                    }
                    for f in functions
                ],
                "sqs": {
                    url.rsplit("/", 1)[-1]: sqs.get_queue_attributes(
                        QueueUrl=url,
                        AttributeNames=[
                            "ApproximateNumberOfMessages",
                            "ApproximateNumberOfMessagesNotVisible",
                        ],
                    )["Attributes"]
                    for url in queues
                },
                "bedrock": {
                    "region": self.env["RAG_BEDROCK_REGION"],
                    "model": self.env["RAG_LLM_MODEL"],
                    "invocations_today": self._metric(
                        "AWS/Bedrock",
                        "Invocations",
                        "ModelId",
                        self.env["RAG_LLM_MODEL"],
                        region=self.env["RAG_BEDROCK_REGION"],
                    ),
                },
            },
        )

    @staticmethod
    def _metric(
        namespace: str, name: str, dim: str, value: str, region: str | None = None
    ) -> float:
        now = datetime.now(UTC)
        cw = boto3.client("cloudwatch", region_name=region)
        points = cw.get_metric_statistics(
            Namespace=namespace,
            MetricName=name,
            Dimensions=[{"Name": dim, "Value": value}],
            StartTime=now - timedelta(hours=24),
            EndTime=now,
            Period=86400,
            Statistics=["Sum"],
        )["Datapoints"]
        return float(sum(p["Sum"] for p in points))

    # -- Databricks ----------------------------------------------------------------------------

    def databricks(self) -> None:
        summary = json.loads(
            self._cli("databricks", "bundle", "summary", "-t", "smoke", "-o", "json")
        )
        job_id = summary["resources"]["jobs"]["ingest"]["id"]
        runs = json.loads(
            self._cli("databricks", "jobs", "list-runs", "--job-id", str(job_id), "-o", "json")
        )
        latest = runs[0] if isinstance(runs, list) else runs["runs"][0]
        run = json.loads(
            self._cli("databricks", "jobs", "get-run", str(latest["run_id"]), "-o", "json")
        )
        self.write(
            "databricks.json",
            {
                "job_name": run.get("run_name"),
                "run_id": run["run_id"],
                "state": run.get("state"),
                "status": run.get("status"),
                "started": _ms(run.get("start_time")),
                "ended": _ms(run.get("end_time")),
                "run_duration_s": (run.get("run_duration") or 0) / 1000,
                "tasks": [
                    {
                        "task_key": t.get("task_key"),
                        "state": t.get("state"),
                        "status": t.get("status"),
                        # Tasks of a multi-task run report start and end, not run_duration.
                        "run_duration_s": ((t.get("end_time") or 0) - (t.get("start_time") or 0))
                        / 1000,
                        "environment_key": t.get("environment_key"),
                    }
                    for t in run.get("tasks", [])
                ],
            },
        )

    def _cli(self, *argv: str) -> str:
        return subprocess.run(  # noqa: S603 - fixed argv
            argv, cwd=ROOT, env=self.env, capture_output=True, text=True, check=True
        ).stdout

    # -- API and UI ----------------------------------------------------------------------------

    def _serve(self, *argv: str) -> subprocess.Popen[bytes]:
        log.info("$ %s", " ".join(argv))
        return subprocess.Popen(argv, cwd=ROOT, env=self.env)  # noqa: S603

    def _api(self) -> subprocess.Popen[bytes]:
        proc = self._serve(
            sys.executable, "-m", "uvicorn", "app.api.main:create_app", "--factory",
            "--port", str(API_PORT),
        )  # fmt: skip
        _wait(f"http://localhost:{API_PORT}/ready", proc, 180)
        return proc

    def api(self) -> None:
        settings = Settings()
        proc = self._api()
        try:
            results = []
            for persona, question, shows in SCENARIOS:
                token = mint_token(settings, f"demo-{persona}", PERSONAS[persona])
                started = time.monotonic()
                response = httpx.post(
                    f"http://localhost:{API_PORT}/query",
                    json={"question": question},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=120,
                )
                results.append(
                    {
                        "persona": persona,
                        "groups": PERSONAS[persona],
                        "question": question,
                        "demonstrates": shows,
                        "status": response.status_code,
                        "client_ms": round((time.monotonic() - started) * 1000),
                        "response": response.json(),
                    }
                )
            metrics = httpx.get(f"http://localhost:{API_PORT}/metrics", timeout=10).text
            self.write(
                "api.json",
                {
                    "captured_at": datetime.now(UTC).isoformat(),
                    "queries": results,
                    "metrics": [line for line in metrics.splitlines() if line.startswith("rag_")],
                },
            )
        finally:
            proc.terminate()
            proc.wait()

    def ui(self) -> None:
        from playwright.sync_api import sync_playwright

        shots = self.out / "ui"
        shots.mkdir(exist_ok=True)
        api = self._api()
        self.env["RAG_API_URL"] = f"http://localhost:{API_PORT}"
        ui = self._serve(
            sys.executable, "-m", "streamlit", "run", "ui/streamlit_app.py",
            "--server.headless", "true", "--server.port", str(UI_PORT),
            "--browser.gatherUsageStats", "false",
        )  # fmt: skip
        try:
            _wait(f"http://localhost:{UI_PORT}/_stcore/health", ui, 60)
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                context = browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    record_video_dir=str(shots),
                    record_video_size={"width": 1280, "height": 900},
                )
                page = context.new_page()
                page.goto(f"http://localhost:{UI_PORT}")
                page.get_by_role("button", name="Ask").wait_for(timeout=60_000)
                for n, (persona, question, _) in enumerate(SCENARIOS, start=1):
                    self._ask(page, persona, question)
                    page.screenshot(path=str(shots / f"{n:02d}-{persona}.png"), full_page=True)
                    page.wait_for_timeout(2500)  # let the recording linger on the answer
                video = page.video
                context.close()
                browser.close()
                if video:
                    Path(video.path()).replace(shots / "demo.webm")
            log.info("screenshots and demo.webm in %s", shots)
        finally:
            for proc in (ui, api):
                proc.terminate()
                proc.wait()

    @staticmethod
    def _ask(page: Any, persona: str, question: str) -> None:
        sidebar = page.get_by_test_id("stSidebar")
        sidebar.get_by_role("combobox").click()
        page.get_by_role("option", name=persona, exact=True).click()
        box = page.get_by_label("Question")
        box.fill(question)
        box.press("Enter")
        page.wait_for_timeout(500)
        page.get_by_role("button", name="Ask").click()
        page.get_by_text(re.compile(r"^request ")).wait_for(timeout=120_000)
        page.wait_for_timeout(500)


def _ms(value: int | None) -> str | None:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat() if value else None


def _wait(url: str, proc: subprocess.Popen[bytes], seconds: int) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            sys.exit(f"process for {url} exited with {proc.returncode}")
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    sys.exit(f"{url} not ready after {seconds}s")


STEPS = ("infra", "databricks", "api", "ui")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Capture evidence of the AWS smoke run")
    parser.add_argument("--only", choices=STEPS)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "docs" / "smoke" / datetime.now(UTC).date().isoformat(),
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    evidence = Evidence(args.out)
    for step in [args.only] if args.only else STEPS:
        log.info("== %s", step)
        getattr(evidence, step)()


if __name__ == "__main__":
    main()
