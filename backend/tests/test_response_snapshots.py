"""Historical prompts are immutable files, not a growing mutable campaign row."""

import json

import pytest

from grimoire import store
from grimoire.store import response_snapshots


@pytest.fixture
def campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    world = store.worlds.create_world("Realm")
    return store.campaigns.create_campaign("Saltmarch", world)


def test_snapshot_roundtrip_is_independent_of_later_in_memory_edits(campaign):
    snapshot = {"profiles": {"": [{"role": "user", "content": "Wait here."}]}}
    ref = response_snapshots.write(campaign, "a" * 32, "primary", snapshot)
    snapshot["profiles"][""][0]["content"] = "Leave."
    loaded = response_snapshots.read(campaign, ref)
    assert loaded["profiles"][""][0]["content"] == "Wait here."
    assert response_snapshots.write(campaign, "a" * 32, "primary", loaded) == ref


def test_resume_snapshots_do_not_overwrite_the_previous_roll_context(campaign):
    first = response_snapshots.write(campaign, "b" * 32, "resume", {"roll": "first"})
    second = response_snapshots.write(campaign, "b" * 32, "resume", {"roll": "second"})
    assert first != second
    assert response_snapshots.read(campaign, first) == {"roll": "first"}
    assert response_snapshots.read(campaign, second) == {"roll": "second"}


@pytest.mark.parametrize("rid,kind", [("../outside", "primary"), ("a" * 32, "../outside"),
                                     ("A" * 32, "resume"), ("a" * 32 + "/extra", "primary")])
def test_snapshot_writer_rejects_unsafe_identifiers(campaign, rid, kind):
    with pytest.raises(ValueError):
        response_snapshots.write(campaign, rid, kind, {})


@pytest.mark.parametrize("reference", ["../outside.json", "/absolute.json",
                                      "a" * 32 + "/../../outside", "C:/outside.json"])
def test_snapshot_reader_rejects_unsafe_references(campaign, reference):
    with pytest.raises(ValueError):
        response_snapshots.read(campaign, reference)


def test_a_changed_snapshot_fails_closed_instead_of_becoming_historical_context(campaign):
    ref = response_snapshots.write(campaign, "c" * 32, "primary", {"past": "known"})
    target = store.campaigns.campaign_root(campaign) / "response-prompts" / ref
    target.write_text(json.dumps({"future": "secret"}), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        response_snapshots.read(campaign, ref)


def test_remove_only_deletes_the_validated_snapshot_and_is_retryable(campaign):
    first = response_snapshots.write(campaign, "d" * 32, "primary", {"one": 1})
    other = response_snapshots.write(campaign, "d" * 32, "resume", {"two": 2})
    response_snapshots.remove(campaign, first)
    response_snapshots.remove(campaign, first)
    with pytest.raises(FileNotFoundError):
        response_snapshots.read(campaign, first)
    assert response_snapshots.read(campaign, other) == {"two": 2}
    with pytest.raises(ValueError):
        response_snapshots.remove(campaign, "../outside.json")
