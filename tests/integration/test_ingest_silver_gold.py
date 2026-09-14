"""Runs silver and gold in the ingest container and checks the documents and chunks they hold.

Runs after the bronze test (alphabetical order), which leaves bronze current.
"""

from collections import Counter
from typing import Any

import pytest

from tests.integration.lake import delta_table, run_ingest, table_rows

pytestmark = pytest.mark.integration

ORIGINAL = "wiki-support-escalation-matrix"
COPY = "wiki-support-escalation-matrix-copy"


@pytest.fixture(scope="module")
def layers() -> dict[str, list[dict[str, Any]]]:
    run_ingest("bronze", "silver", "gold")
    return {
        "bronze": table_rows("bronze", "objects"),
        "silver": table_rows("silver", "documents"),
        "gold": table_rows("gold", "chunks"),
    }


def _live_canonical(doc: dict[str, Any]) -> bool:
    return not doc["is_deleted"] and doc["parse_error"] is None and not doc["is_duplicate"]


def test_silver_keeps_the_latest_event_per_key(layers: dict[str, list[dict[str, Any]]]) -> None:
    latest: dict[str, str] = {}
    for row in layers["bronze"]:
        latest[row["key"]] = max(latest.get(row["key"], ""), row["sort_key"])

    silver = {doc["key"]: doc for doc in layers["silver"]}
    assert set(silver) == set(latest)
    assert all(silver[key]["sort_key"] == sort_key for key, sort_key in latest.items())


def test_copied_wiki_page_collapses_onto_one_canonical_document(
    layers: dict[str, list[dict[str, Any]]],
) -> None:
    by_doc = {doc["doc_id"]: doc for doc in layers["silver"]}
    original, copy = by_doc[ORIGINAL], by_doc[COPY]

    assert original["fingerprint"] == copy["fingerprint"]
    assert original["allowed_groups"] == copy["allowed_groups"]
    assert sorted([original["is_duplicate"], copy["is_duplicate"]]) == [False, True]
    canonical = copy if original["is_duplicate"] else original
    assert {original["canonical_key"], copy["canonical_key"]} == {canonical["key"]}


def test_unparseable_objects_are_recorded_not_chunked(
    layers: dict[str, list[dict[str, Any]]],
) -> None:
    smoke = [doc for doc in layers["silver"] if doc["key"].startswith("smoke/")]
    assert smoke
    assert all(doc["is_deleted"] or doc["parse_error"] for doc in smoke)
    assert not [chunk for chunk in layers["gold"] if chunk["key"].startswith("smoke/")]


def test_gold_holds_chunks_for_exactly_the_live_canonical_documents(
    layers: dict[str, list[dict[str, Any]]],
) -> None:
    expected = {doc["key"] for doc in layers["silver"] if _live_canonical(doc)}
    chunk_ids = Counter(chunk["chunk_id"] for chunk in layers["gold"])

    assert {chunk["key"] for chunk in layers["gold"]} == expected
    assert max(chunk_ids.values()) == 1


def test_gold_chunks_carry_the_source_acl(layers: dict[str, list[dict[str, Any]]]) -> None:
    groups = {doc["key"]: sorted(doc["allowed_groups"]) for doc in layers["silver"]}
    for chunk in layers["gold"]:
        assert sorted(chunk["allowed_groups"]) == groups[chunk["key"]]
    restricted = [c for c in layers["gold"] if "all-staff" not in c["allowed_groups"]]
    assert restricted, "seed data should include restricted documents"


def test_rerun_without_new_data_does_not_rewrite_gold(
    layers: dict[str, list[dict[str, Any]]],
) -> None:
    before = delta_table("gold", "chunks").version()
    run_ingest("bronze", "silver", "gold")

    assert delta_table("gold", "chunks").version() == before
