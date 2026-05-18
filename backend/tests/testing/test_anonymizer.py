"""Tests for the snapshot anonymization pipeline (spec 17 §L4)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from grimoire.storage import Database, apply_migrations
from grimoire.testing.anonymizer import (
    DEFAULT_FIRST_NAMES,
    DEFAULT_PLACE_NAMES,
    Anonymizer,
    sidecar_path,
)


def test_anonymize_text_rewrites_known_names() -> None:
    anon = Anonymizer({"winifred": "Alice", "Camden Market": "Riverdell"})
    text = "winifred walked through Camden Market with winifred's twin."
    assert anon.anonymize_text(text) == "Alice walked through Riverdell with Alice's twin."


def test_anonymize_text_is_case_insensitive_but_keeps_replacement_case() -> None:
    anon = Anonymizer({"alistair": "Bob"})
    assert anon.anonymize_text("Alistair nodded; ALISTAIR smiled.") == "Bob nodded; Bob smiled."


def test_anonymize_text_leaves_unknown_names() -> None:
    anon = Anonymizer({"winifred": "Alice"})
    assert anon.anonymize_text("Roland waved.") == "Roland waved."


def test_anonymize_text_handles_word_boundaries() -> None:
    anon = Anonymizer({"Cat": "Dog"})
    # "Cattle" must not match (substring inside word).
    assert anon.anonymize_text("The Cat watched the cattle.") == "The Dog watched the cattle."


def test_learn_names_produces_stable_mapping() -> None:
    a = Anonymizer()
    a.learn_names(real_first_names=["winifred", "Alistair"], real_place_names=["Camden"])
    b = Anonymizer()
    b.learn_names(real_first_names=["winifred", "Alistair"], real_place_names=["Camden"])
    assert a.anonymize_mapping() == b.anonymize_mapping()
    # Mapping pulls from the configured pools.
    assert set(a.anonymize_mapping().values()).issubset(
        set(DEFAULT_FIRST_NAMES) | set(DEFAULT_PLACE_NAMES)
    )


def test_with_passthrough_skips_specified_names() -> None:
    base = Anonymizer({"winifred": "Alice", "Roland": "Bob"})
    relaxed = base.with_passthrough({"winifred"})
    out = relaxed.anonymize_text("winifred met Roland.")
    assert "winifred" in out
    assert "Bob" in out
    # The base anonymizer is untouched.
    assert base.anonymize_text("winifred met Roland.") == "Alice met Bob."


def test_save_and_load_mapping(tmp_path: Path) -> None:
    anon = Anonymizer({"winifred": "Alice"})
    snap = tmp_path / "snap.sqlite"
    target = sidecar_path(snap)
    anon.save_mapping(target)
    reloaded = Anonymizer.load_mapping(target)
    assert reloaded.anonymize_mapping() == {"winifred": "Alice"}


async def test_anonymize_sqlite_rewrites_prose_columns(tmp_path: Path) -> None:
    snap = tmp_path / "snap.sqlite"
    db = Database(snap, pool_size=1)
    await db.connect()
    await apply_migrations(db)

    # Seed a couple of rows touching the columns the anonymizer rewrites.
    await db.execute(
        "INSERT INTO posts (id, scene_id, campaign_id, branch_id, order_in_scene,"
        " body_excerpt) VALUES (?, NULL, ?, ?, ?, ?)",
        ("p1", "cmp", "b", 0, "winifred smiled."),
    )
    await db.execute(
        "INSERT INTO facts (id, campaign_id, branch_id, text) VALUES (?, ?, ?, ?)",
        ("f1", "cmp", "b", "winifred promised to meet at Camden."),
    )
    await db.close()

    anon = Anonymizer({"winifred": "Alice", "Camden": "Riverdell"})
    mapping = anon.anonymize_sqlite(snap)
    assert mapping == {"winifred": "Alice", "Camden": "Riverdell"}

    # Reopen with stdlib sqlite3 to verify the data on disk.
    conn = sqlite3.connect(snap)
    try:
        row = conn.execute("SELECT body_excerpt FROM posts WHERE id = 'p1'").fetchone()
        assert row[0] == "Alice smiled."
        row = conn.execute("SELECT text FROM facts WHERE id = 'f1'").fetchone()
        assert row[0] == "Alice promised to meet at Riverdell."
    finally:
        conn.close()


def test_anonymize_sqlite_tolerates_missing_tables(tmp_path: Path) -> None:
    # Build a tiny db with one of the target tables and a few stray tables;
    # the anonymizer must skip the missing ones without raising.
    snap = tmp_path / "early.sqlite"
    conn = sqlite3.connect(snap)
    conn.execute("CREATE TABLE posts (id TEXT, body_excerpt TEXT)")
    conn.execute("INSERT INTO posts VALUES ('p1', 'Hello winifred')")
    conn.commit()
    conn.close()

    anon = Anonymizer({"winifred": "Alice"})
    anon.anonymize_sqlite(snap)

    conn = sqlite3.connect(snap)
    row = conn.execute("SELECT body_excerpt FROM posts").fetchone()
    conn.close()
    assert row[0] == "Hello Alice"


def test_anonymize_text_passes_through_none_and_empty() -> None:
    anon = Anonymizer({"winifred": "Alice"})
    assert anon.anonymize_text(None) is None
    assert anon.anonymize_text("") == ""


def test_record_replay_uses_anonymizer(tmp_path: Path) -> None:
    """Smoke test: ``RecordReplayLLM._save_completion`` honours the configured anonymizer."""

    from grimoire.testing.record_replay import RecordReplayLLM, ReplayMode
    from grimoire.types.llm import (
        CompletionRequest,
        CompletionResponse,
        Message,
        MessageRole,
        TokenUsage,
    )

    class _Real:
        async def complete(self, task, request, campaign_id=None):  # type: ignore[no-untyped-def]
            return CompletionResponse(
                text="winifred nods.",
                model=request.model,
                finish_reason="stop",
                usage=TokenUsage(input_tokens=1, output_tokens=2),
                latency_ms=1,
            )

    anon = Anonymizer({"winifred": "Alice"})
    llm = RecordReplayLLM(
        fixture_dir=tmp_path,
        mode=ReplayMode.RECORD,
        real_gateway=_Real(),
        anonymizer=anon,
    )
    req = CompletionRequest(
        model="m",
        messages=[Message(role=MessageRole.USER, content="hi winifred")],
        max_tokens=4,
        temperature=0.0,
    )

    import asyncio

    asyncio.run(llm.complete("primary", req))

    # Inspect the saved fixture.
    paths = list((tmp_path / "llm" / "by_hash").glob("*.json"))
    assert len(paths) == 1
    import json

    with paths[0].open() as f:
        data = json.load(f)
    assert data["response"]["text"] == "Alice nods."
    assert data["request"]["messages"][0]["content"] == "hi Alice"


@pytest.mark.asyncio
async def test_anonymize_sqlite_rewrites_summary_and_commitment(tmp_path: Path) -> None:
    snap = tmp_path / "snap.sqlite"
    db = Database(snap, pool_size=1)
    await db.connect()
    await apply_migrations(db)
    await db.execute(
        "INSERT INTO scenes (id, campaign_id, branch_id, ordinal, slug, file_path, summary)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("s1", "cmp", "b", 0, "intro", "scenes/intro.md", "winifred enters."),
    )
    await db.execute(
        "INSERT INTO commitments (id, campaign_id, branch_id, text, status) VALUES (?, ?, ?, ?, ?)",
        ("c1", "cmp", "b", "winifred will ride.", "OPEN"),
    )
    await db.close()

    anon = Anonymizer({"winifred": "Alice"})
    anon.anonymize_sqlite(snap)

    conn = sqlite3.connect(snap)
    try:
        scene = conn.execute("SELECT summary FROM scenes WHERE id = 's1'").fetchone()
        commitment = conn.execute("SELECT text FROM commitments WHERE id = 'c1'").fetchone()
    finally:
        conn.close()
    assert scene[0] == "Alice enters."
    assert commitment[0] == "Alice will ride."
