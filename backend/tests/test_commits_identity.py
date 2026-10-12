"""A corrupt account cannot bless replay or lose unfinished identity fences."""

import json
from pathlib import Path

import pytest

from grimoire.store import campaigns, commits, group_refs
from tests.test_commits_store import _campaign


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    return cid, campaigns.campaign_root(cid) / "commits.json"


@pytest.mark.parametrize("raw", [
    b"{invalid", b"\xff", b"[]", b"null", b'{"mara":1}',
    b'{"v":3,"tokens":{},"scenes":{}}', b'{"v":true,"tokens":{},"scenes":{}}',
    b'{"v":2,"tokens":[],"scenes":{}}', b'{"v":2,"tokens":{},"scenes":[]}',
    b'{"v":2,"tokens":{"mara":{"done":"yes"}},"scenes":{}}',
    b'{"v":2,"tokens":{},"scenes":{"001--saltmarch":true}}',
    b'{"v":2,"tokens":{},"scenes":{"001--saltmarch":-1}}',
    b'{"v":2,"tokens":{"mara":{"done":false,"progress":{"membership_targets":null}}},"scenes":{}}',
    b'{"v":2,"tokens":{"mara":{"done":false,"progress":{"membership_targets":{"0":true}}}},"scenes":{}}',
    # A target whose text fields are not text: refused up front, never a
    # crash in a later fence or Undo.
    (b'{"v":2,"tokens":{"mara":{"done":false,"progress":{"membership_targets":'
     b'{"0":{"kind":"groups","id":"salt-circle","ref":1}}}}},"scenes":{}}'),
    (b'{"v":2,"tokens":{"mara":{"done":false,"progress":{"membership_targets":'
     b'{"0":{"kind":"groups","id":7}}}}},"scenes":{}}'),
    (b'{"v":2,"tokens":{"mara":{"done":false,"membership_targets":'
     b'{"0":{"kind":"groups","id":"salt-circle","actor":["characters:mara"]}}}},"scenes":{}}'),
    (b'{"v":2,"tokens":{"mara":{"done":false,"progress":{"membership_targets":'
     b'{"0":{"id":"salt-circle","token":5}}}}},"scenes":{}}'),
    # Valid targets beside a deferred view the fence would walk as a container.
    (b'{"v":2,"tokens":{"mara":{"done":false,"progress":{"journalled":1,"membership_targets":'
     b'{"0":{"kind":"groups","id":"salt-circle"}}}}},"scenes":{}}'),
    (b'{"v":2,"tokens":{"mara":{"done":false,"progress":{"journalled":[{"ref":"groups/x"}]},'
     b'"membership_targets":{"0":{"kind":"groups","id":"salt-circle"}}}},"scenes":{}}'),
    (b'{"v":2,"tokens":{"mara":{"done":false,"progress":{"recorded":{"groups/salt-circle":{}},'
     b'"membership_targets":{"0":{"kind":"groups","id":"salt-circle"}}}}},"scenes":{}}'),
    (b'{"v":2,"tokens":{"mara":{"done":false,"progress":{"cited":[],"membership_targets":'
     b'{"0":{"kind":"groups","id":"salt-circle"}}}}},"scenes":{}}'),
])
def test_every_account_owner_refuses_invalid_documents_without_writing(ledger, raw):
    cid, path = ledger
    path.write_bytes(raw)
    owners = [
        lambda: commits.lookup(cid, "mara"),
        lambda: commits.scene_epoch(cid, "001--saltmarch"),
        lambda: commits.reserve(cid, "mara", sid="001--saltmarch"),
        lambda: commits.checkpoint(cid, "mara", {}),
        lambda: commits.record(cid, "mara", {}),
        lambda: commits.retire_scene(cid, "001--saltmarch"),
        lambda: commits.repoint_scenes(cid, {"001--saltmarch": "002--saltmarch"}),
    ]
    for owner in owners:
        with pytest.raises(group_refs.GroupIdentityConflict):
            owner()
        assert path.read_bytes() == raw


def test_denied_account_is_not_absent(ledger, monkeypatch):
    cid, path = ledger
    real = Path.read_text

    def denied(self, *args, **kwargs):
        if self == path:
            raise PermissionError("account unavailable")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(group_refs.GroupIdentityConflict):
        commits.reserve(cid, "mara")
    assert not path.exists()


def test_legacy_marker_named_tokens_and_undated_completed_entries_survive(ledger):
    cid, path = ledger
    legacy = {name: {"done": True, "result": {"id": name}}
              for name in ("tokens", "scenes", "v")}
    path.write_text(json.dumps(legacy), encoding="utf-8")
    commits.reserve(cid, "mara", progress={"membership_targets": {"0": {"token": "realm"}}})
    for name in legacy:
        assert commits.lookup(cid, name)["result"] == {"id": name}
    assert commits.lookup(cid, "mara")["progress"]["membership_targets"]["0"]["token"] == "realm"


def test_stale_checkpoints_and_finalization_preserve_membership_fences(ledger):
    cid, path = ledger
    target = {"token": "realm", "ref": "groups:salt-circle", "retired": False, "suspended": ""}
    original = {"membership_targets": {"0": target}, "timeline": "pending"}
    commits.reserve(cid, "mara", progress=original)
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["tokens"]["mara"]["progress"]["membership_targets"]["0"]["retired"] = True
    path.write_text(json.dumps(stored), encoding="utf-8")
    commits.checkpoint(cid, "mara", original)
    assert commits.lookup(cid, "mara")["progress"]["membership_targets"]["0"]["retired"] is True
    commits.record(cid, "mara", {"applied": []})
    assert commits.lookup(cid, "mara")["membership_targets"]["0"]["retired"] is True
