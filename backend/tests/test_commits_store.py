"""The commit-token ledger behind PUT /chronicle's replay guard (#235)."""

from grimoire.store import campaigns, commits, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("W"))


def test_an_unseen_token_has_no_result(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert commits.result_for(cid, "tok") is None


def test_a_recorded_token_returns_its_result(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    commits.record(cid, "tok", {"applied": ["a"], "failures": []})
    assert commits.result_for(cid, "tok") == {"applied": ["a"], "failures": []}


def test_an_empty_token_is_never_recorded_or_matched(monkeypatch, tmp_path):
    """A client that sends no token opts out of the guard -- it must not collide
    with every other tokenless save."""
    cid = _campaign(monkeypatch, tmp_path)
    commits.record(cid, "", {"applied": []})
    assert commits.result_for(cid, "") is None


def test_the_ledger_is_capped(monkeypatch, tmp_path):
    """Unbounded growth would make commits.json a log; only recent saves can
    plausibly be retried."""
    cid = _campaign(monkeypatch, tmp_path)
    for i in range(commits.KEEP + 5):
        commits.record(cid, f"tok{i}", {"applied": [str(i)]})
    assert commits.result_for(cid, "tok0") is None            # oldest evicted
    assert commits.result_for(cid, f"tok{commits.KEEP + 4}") == {"applied": [str(commits.KEEP + 4)]}


def test_a_garbled_ledger_reads_as_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "commits.json").write_text("{ not json", encoding="utf-8")
    assert commits.result_for(cid, "tok") is None
