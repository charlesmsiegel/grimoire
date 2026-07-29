"""The commit-token ledger behind PUT /chronicle's replay guard (#235)."""

import json

from grimoire.store import campaigns, commits, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("W"))


def _age(cid, token, when):
    """Backdate a ledger entry -- cheaper and steadier than waiting."""
    p = campaigns.campaign_root(cid) / "commits.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data[token]["at"] = when
    p.write_text(json.dumps(data), encoding="utf-8")


def test_an_unseen_token_has_no_result(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert commits.lookup(cid, "tok") is None


def test_a_recorded_token_returns_its_result(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    commits.record(cid, "tok", {"applied": ["a"], "failures": []})
    entry = commits.lookup(cid, "tok")
    assert entry["done"] is True and entry["result"] == {"applied": ["a"], "failures": []}


def test_a_reserved_token_is_seen_but_not_done(monkeypatch, tmp_path):
    """The window the reserve exists for: effects have begun, the result is not
    known, and a replay must not run them again."""
    cid = _campaign(monkeypatch, tmp_path)
    commits.reserve(cid, "tok", "fp")
    entry = commits.lookup(cid, "tok")
    assert entry is not None and entry["done"] is False


def test_recording_completes_a_reservation(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    commits.reserve(cid, "tok", "fp")
    commits.record(cid, "tok", {"applied": []})
    assert commits.lookup(cid, "tok")["done"] is True


def test_an_empty_token_is_never_recorded_or_matched(monkeypatch, tmp_path):
    """A client that sends no token opts out of the guard -- it must not collide
    with every other tokenless save."""
    cid = _campaign(monkeypatch, tmp_path)
    commits.record(cid, "", {"applied": []})
    commits.reserve(cid, "", "fp")
    assert commits.lookup(cid, "") is None


def test_a_token_survives_many_later_saves(monkeypatch, tmp_path):
    """Eviction by count would drop a token whose review is still open on
    someone's screen, and its retry would then replay every append."""
    cid = _campaign(monkeypatch, tmp_path)
    commits.record(cid, "mine", {"applied": ["a"]})
    for i in range(50):
        commits.record(cid, f"other{i}", {"applied": []})
    assert commits.lookup(cid, "mine")["result"] == {"applied": ["a"]}


def test_completed_entries_expire_by_age(monkeypatch, tmp_path):
    """Bounded some other way, or commits.json becomes a permanent log."""
    cid = _campaign(monkeypatch, tmp_path)
    commits.record(cid, "old", {"applied": []})
    _age(cid, "old", "2000-01-01T00:00:00Z")
    commits.record(cid, "fresh", {"applied": []})     # any write prunes
    assert commits.lookup(cid, "old") is None
    assert commits.lookup(cid, "fresh") is not None


def test_an_unfinished_reservation_never_expires(monkeypatch, tmp_path):
    """The dangerous entry: dropping it lets a retry replay a commit that may
    have partly landed. It has no result to age out into, so it stays."""
    cid = _campaign(monkeypatch, tmp_path)
    commits.reserve(cid, "wedged", "fp")
    _age(cid, "wedged", "2000-01-01T00:00:00Z")
    commits.record(cid, "fresh", {"applied": []})
    assert commits.lookup(cid, "wedged") is not None
    assert commits.lookup(cid, "wedged")["done"] is False


def test_the_fingerprint_is_stable_and_order_insensitive(monkeypatch, tmp_path):
    a = commits.fingerprint({"one_line": "o", "edits": [{"id": "x", "after": "y"}]})
    b = commits.fingerprint({"edits": [{"after": "y", "id": "x"}], "one_line": "o"})
    assert a == b
    assert a != commits.fingerprint({"one_line": "CHANGED", "edits": []})


def test_a_recorded_token_keeps_its_fingerprint(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    commits.record(cid, "tok", {"applied": []}, "fp1")
    assert commits.lookup(cid, "tok")["fingerprint"] == "fp1"


def test_a_garbled_ledger_reads_as_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "commits.json").write_text("{ not json", encoding="utf-8")
    assert commits.lookup(cid, "tok") is None
