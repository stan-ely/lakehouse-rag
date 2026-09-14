"""CLI: generate the Larkspur Logistics dataset and load it into the local (or AWS) stack.

uv run python -m data_gen.generate                 # write data/out, load Postgres, sync S3
uv run python -m data_gen.generate --skip-s3 --skip-db
"""

import argparse
import logging
import os
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import boto3
from botocore.config import Config

from data_gen.corpus import build_corpus
from data_gen.load import RenderedDocument, load_ops, sync_to_s3, write_local
from data_gen.render import render
from data_gen.world import build_world

log = logging.getLogger("data_gen")

DEFAULT_DATABASE_URL = "postgresql+psycopg://rag:rag@localhost:5432/rag"


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path, default=Path("data/out"))
    parser.add_argument("--bucket", default=os.environ.get("RAG_RAW_BUCKET", "larkspur-local-raw"))
    parser.add_argument("--skip-s3", action="store_true", help="do not sync documents to S3")
    parser.add_argument("--skip-db", action="store_true", help="do not load the ops database")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args(argv)

    world = build_world(args.seed)
    documents = build_corpus(world, args.seed)
    rendered = [RenderedDocument(doc, render(doc)) for doc in documents]
    manifest = write_local(rendered, args.out)
    by_type = Counter(doc.source_type for doc in documents)
    log.info("wrote %d documents %s, manifest at %s", len(documents), dict(by_type), manifest)

    if not args.skip_db:
        dsn = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL).replace("+psycopg", "")
        log.info("loaded ops tables %s", load_ops(world, dsn))

    if not args.skip_s3:
        s3 = boto3.client("s3", config=Config(s3={"addressing_style": "path"}))
        stats = sync_to_s3(s3, args.bucket, rendered)
        log.info(
            "s3://%s: %d uploaded, %d unchanged, %d deleted",
            args.bucket,
            stats.uploaded,
            stats.unchanged,
            stats.deleted,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
