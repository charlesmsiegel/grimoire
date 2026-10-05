"""The ledger records how many images a call sent (#377), so a turn that got
expensive because it carried pictures says so -- and a modelled figure that
cannot price an image is not silently taken as the whole story."""

from __future__ import annotations

import json

import pytest

from grimoire.store import usage, usage_rollup


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    return tmp_path


def _rows(home):
    return [json.loads(line)
            for path in sorted((home / "usage").glob("*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_a_row_carries_the_count_only_when_there_is_one(home):
    usage.record(task="chat", images=2)
    usage.record(task="chat", images=0)
    first, second = _rows(home)
    assert first["images"] == 2
    assert "images" not in second


def test_the_meter_writes_what_the_facade_counted(home):
    with usage.meter("chat", campaign="saltmarch", scene="001-arrival") as m:
        m.usage.update({"model": "realm/opus", "images": 1, "prompt_tokens": 9})
    row, = _rows(home)
    assert row["images"] == 1


def test_a_post_and_its_reroll_sum_their_images(home):
    for task, n in (("chat", 2), ("regenerate", 1)):
        usage.record(task=task, campaign="saltmarch", scene="001-arrival", post=6,
                     images=n, ts="2026-08-14T00:00:00Z")
    usage.record(task="chat", campaign="saltmarch", scene="001-arrival", post=8,
                 ts="2026-08-14T00:00:00Z")
    by_post = usage.scene_usage("saltmarch", "001-arrival", since="2026-08-01")["by_post"]
    assert by_post[0]["images"] == 3
    assert "images" not in by_post[1]


def test_the_empty_bucket_keeps_its_shape():
    assert "images" not in usage._ZERO


def test_a_bucket_written_before_images_existed_still_folds_one(home):
    old_bucket = dict(usage._ZERO)
    usage._add(old_bucket, {"task": "chat", "images": 2})
    assert old_bucket["images"] == 2
    usage._add(old_bucket, {"task": "chat"})
    assert old_bucket["images"] == 2


def test_the_rollup_survives_rows_with_images(home):
    usage.record(task="chat", campaign="saltmarch", images=2, ts="2026-08-01T00:00:00Z")
    assert usage_rollup.campaign_totals("saltmarch")["calls"] == 1
