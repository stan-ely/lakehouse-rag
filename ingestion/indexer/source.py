"""Gold chunks from Delta, read with delta-rs: no JVM, so the indexer runs anywhere Python does."""

import logging
import os
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any, Protocol

import pyarrow as pa
import pyarrow.compute as pc
from deltalake import DeltaTable
from deltalake.exceptions import DeltaError

log = logging.getLogger(__name__)


class ChunkSource(Protocol):
    def version(self) -> int: ...

    def changed_doc_ids(self, after_version: int, up_to_version: int) -> set[str] | None:
        """Doc ids touched in (after_version, up_to_version], or None if history is gone."""
        ...

    def chunk_rows(self, doc_ids: Collection[str] | None, version: int) -> list[dict[str, Any]]:
        """Chunk rows at `version`, for the given documents or (None) for all of them."""
        ...


@dataclass(frozen=True)
class GoldSource:
    uri: str
    storage_options: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "GoldSource":
        options: dict[str, str] = {}
        if endpoint := os.environ.get("AWS_ENDPOINT_URL"):
            options["AWS_ENDPOINT_URL"] = endpoint
            options["AWS_ALLOW_HTTP"] = str(endpoint.startswith("http://")).lower()
        if region := os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"):
            options["AWS_REGION"] = region
        for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
            if value := os.environ.get(name):
                options[name] = value
        uri = os.environ.get("RAG_GOLD_URI", "s3://larkspur-local-lake/delta/gold/chunks")
        return cls(uri, options)

    def _table(self, version: int | None = None) -> DeltaTable:
        return DeltaTable(self.uri, version=version, storage_options=self.storage_options)

    def version(self) -> int:
        return int(self._table().version())

    def changed_doc_ids(self, after_version: int, up_to_version: int) -> set[str] | None:
        try:
            reader = self._table(up_to_version).load_cdf(
                starting_version=after_version + 1,
                ending_version=up_to_version,
                columns=["doc_id"],
            )
            return {str(doc_id) for doc_id in pa.table(reader).column("doc_id").to_pylist()}
        except DeltaError as exc:
            # Vacuumed or rewritten history: correctness beats incrementality.
            log.warning("change feed after version %d unavailable: %s", after_version, exc)
            return None

    def chunk_rows(self, doc_ids: Collection[str] | None, version: int) -> list[dict[str, Any]]:
        if doc_ids is not None and not doc_ids:
            return []
        filters = None if doc_ids is None else pc.field("doc_id").isin(sorted(doc_ids))
        rows: list[dict[str, Any]] = (
            self._table(version).to_pyarrow_table(filters=filters).to_pylist()
        )
        return rows
