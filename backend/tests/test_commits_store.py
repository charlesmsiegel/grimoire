"""The commit-token ledger behind PUT /chronicle's replay guard (#235)."""

from grimoire.store import campaigns, commits, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("W"))


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
    commits.reserve(cid, "tok")
    entry = commits.lookup(cid, "tok")
    assert entry is not None and entry["done"] is False


def test_recording_completes_a_reservation(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    commits.reserve(cid, "tok")
    commits.record(cid, "tok", {"applied": []})
    assert commits.lookup(cid, "tok")["done"] is True


def test_an_empty_token_is_never_recorded_or_matched(monkeypatch, tmp_path):
    """A client that sends no token opts out of the guard -- it must not collide
    with every other tokenless save."""
    cid = _campaign(monkeypatch, tmp_path)
    commits.record(cid, "", {"applied": []})
    commits.reserve(cid, "")
    assert commits.lookup(cid, "") is None


def test_the_ledger_is_capped(monkeypatch, tmp_path):
    """Unbounded growth would make commits.json a log; only recent saves can
    plausibly be retried."""
    cid = _campaign(monkeypatch, tmp_path)
    for i in range(commits.KEEP + 5):
        commits.record(cid, f"tok{i}", {"applied": [str(i)]})
    assert commits.lookup(cid, "tok0") is None                # oldest evicted
    assert commits.lookup(cid, f"tok{commits.KEEP + 4}")["result"] == {"applied": [str(commits.KEEP + 4)]}


def test_a_garbled_ledger_reads_as_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "commits.json").write_text("{ not json", encoding="utf-8")
    assert commits.lookup(cid, "tok") is None
