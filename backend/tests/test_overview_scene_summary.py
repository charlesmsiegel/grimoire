"""04-S4: the in-process scene summary and its consumers.

`overview.scene_summary` folds a campaign's scene heads, read head-only, into
what `GET /campaigns`, the shell and Todo share. Against the pre-04 code (kept
here as a test-only oracle), the payloads are identical except for the
expected differences the spec lists, each with its own test: the `last_scene`
tie-break, and a scene whose HEAD has a bad byte degrading instead of 500ing.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app
from grimoire.store import overview, readstats
from tests.overview_support import audited, clear_memos, clock_forward


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    clear_memos()
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def world(client):
    return client.post("/api/worlds", json={"name": "Saltmarch"}).json()["id"]


def _campaign(client, wid: str, name: str) -> str:
    return client.post("/api/campaigns", json={"name": name, "world": wid}).json()["id"]


def _scene(cid: str, title: str, *, updated: str | None = None) -> str:
    sid = store.scenes.create_scene(cid, title)
    if updated is not None:
        _set_head(cid, sid, "updated", updated)
    return sid


def _path(cid: str, sid: str) -> Path:
    return store.scenes.paths._scene_path(cid, sid)


def _set_head(cid: str, sid: str, key: str, value: str) -> None:
    p = _path(cid, sid)
    meta, body = store.frontmatter.parse_frontmatter(p.read_text(encoding="utf-8"))
    meta[key] = value
    p.write_text(store.frontmatter.dump_frontmatter(meta, body), encoding="utf-8")


def _oracle_cards() -> list[dict]:
    """`GET /campaigns` as it was before 04: `list_scenes` per campaign."""
    out = []
    for c in store.campaigns.list_campaigns():
        scene_list = store.scenes.list_scenes(c["id"])
        out.append({**c, "scenes": len(scene_list),
                    "cover": store.covers.cover_version(c["id"]),
                    "last_scene": scene_list[0]["title"] if scene_list else "",
                    "absorbed": sum(1 for s in scene_list if s.get("done")),
                    "activity": store.campaigns.best_stamp(
                        c["updated"], store.campaigns.read_activity(c["id"]),
                        *(s["updated"] for s in scene_list))})
    return out


def _sans_module(cards: list[dict]) -> list[dict]:
    """The cards without `module`, the S7 expected difference that
    `test_overview_card_rows.py` holds on its own."""
    return [{k: v for k, v in c.items() if k != "module"} for c in cards]


def _oracle_open(cid: str) -> list[dict]:
    return [{"sid": s["id"], "title": s["title"]} for s in store.scenes.list_scenes(cid)
            if not s["done"] and not s.get("closed_by")]


def _library(client, wid):
    a = _campaign(client, wid, "A Long Run")
    b = _campaign(client, wid, "Mara's Wake")
    _scene(a, "Saltmarch Gate", updated="2026-03-01T10:00:00Z")
    done = _scene(a, "The Quay", updated="2026-03-02T10:00:00Z")
    store.scenes.mark_absorbed(a, done, "done", "summary")
    _set_head(a, done, "updated", "2026-03-02T10:00:00Z")
    _scene(a, "Untitled", updated="2026-03-03T10:00:00Z")
    _scene(b, "Winifred's Door", updated="2026-02-01T10:00:00Z")
    _campaign(client, wid, "No Scenes Yet")
    return a, b


def test_campaigns_payload_equals_the_pre_04_oracle(client, world):
    _library(client, world)
    assert _sans_module(client.get("/api/campaigns").json()) == _oracle_cards()


def test_shell_and_todo_scene_fields_equal_the_oracle(client, world):
    a, _b = _library(client, world)
    block = client.get("/api/shell", params={"campaign": a}).json()["campaign"]
    assert block["scenes"] == len(store.scenes.list_scenes(a))
    assert [{"sid": o["sid"], "title": o["title"]} for o in block["open"]] == _oracle_open(a)
    chore = next(c for c in client.get("/api/todo", params={"campaign": a}).json()["chores"]
                 if c["id"] == "open-scenes")
    assert chore["n"] == len(_oracle_open(a))


def test_a_closed_branch_is_not_open_and_names_its_absorbed_sibling(client, world):
    cid = _campaign(client, world, "A Long Run")
    first = _scene(cid, "Saltmarch Gate", updated="2026-03-01T10:00:00Z")
    second = _scene(cid, "Saltmarch Gate, again", updated="2026-03-02T10:00:00Z")
    group = store.scenes.scene_identity(cid, first)
    _set_head(cid, second, "branch_group", group)
    store.scenes.mark_absorbed(cid, first, "done", "summary")
    summary = overview.scene_summary(cid)
    by_sid = {r["sid"]: r for r in summary["rows"]}
    assert by_sid[second]["closed_by"] == {"sid": first, "title": "Saltmarch Gate"}
    assert summary["open"] == []
    assert summary["open"] == _oracle_open(cid)


# ---- expected differences ----

def test_tie_break_is_updated_then_sid(client, world):
    cid = _campaign(client, world, "A Long Run")
    stamp = "2026-03-01T10:00:00Z"
    later = _scene(cid, "Winifred", updated=stamp)
    earlier = _scene(cid, "Mara", updated=stamp)
    first = min(later, earlier)
    summary = overview.scene_summary(cid)
    assert [r["sid"] for r in summary["rows"]] == sorted([later, earlier])
    [card] = client.get("/api/campaigns").json()
    assert card["last_scene"] == ("Winifred" if first == later else "Mara")


def test_a_head_with_a_bad_byte_degrades_that_scene_where_the_oracle_500s(client, world):
    cid = _campaign(client, world, "A Long Run")
    _scene(cid, "Saltmarch Gate", updated="2026-03-01T10:00:00Z")
    bad = _scene(cid, "Mara", updated="2026-03-02T10:00:00Z")
    p = _path(cid, bad)
    p.write_bytes(p.read_bytes().replace(b"title: Mara", b"title: Mara\xff"))
    with pytest.raises(UnicodeDecodeError):
        _oracle_cards()
    with readstats.collect() as counters:
        [card] = client.get("/api/campaigns").json()
    assert counters.get("overview.unreadable_scene") == 1
    assert (card["scenes"], card["last_scene"]) == (2, "Saltmarch Gate")
    block = client.get("/api/shell", params={"campaign": cid}).json()["campaign"]
    assert block["scenes"] == 2 and [o["title"] for o in block["open"]] == ["Saltmarch Gate"]
    todo = client.get("/api/todo", params={"campaign": cid})
    assert todo.status_code == 200
    assert next(c for c in todo.json()["chores"] if c["id"] == "open-scenes")["n"] == 1
    assert client.get("/api/todo").status_code == 200


def test_a_body_bad_byte_past_8kib_lists_exactly_as_today(client, world):
    cid = _campaign(client, world, "A Long Run")
    sid = _scene(cid, "Saltmarch Gate", updated="2026-03-01T10:00:00Z")
    p = _path(cid, sid)
    p.write_bytes(p.read_bytes() + b"**Mara:** " + b"Realm " * 2000 + b"\xff\n")
    assert _sans_module(client.get("/api/campaigns").json()) == _oracle_cards()
    block = client.get("/api/shell", params={"campaign": cid}).json()["campaign"]
    assert block["open"] == [{"sid": sid, "title": "Saltmarch Gate", "turns": None}]


# ---- change detection ----

def test_scene_changes_are_seen_on_the_next_read(client, world, monkeypatch):
    clock_forward(monkeypatch)
    cid = _campaign(client, world, "A Long Run")
    sid = _scene(cid, "Saltmarch Gate", updated="2026-03-01T10:00:00Z")
    assert overview.scene_summary(cid)["count"] == 1
    store.scenes.append_message(cid, sid, "user", "Realm")
    assert overview.scene_summary(cid)["rows"][0]["updated"] != "2026-03-01T10:00:00Z"
    renamed = store.scenes.rename_scene(cid, sid, "The Long Quay")
    assert [r["title"] for r in overview.scene_summary(cid)["rows"]] == ["The Long Quay"]
    other = _scene(cid, "Winifred")
    assert overview.scene_summary(cid)["count"] == 2
    store.scenes.delete_scene(cid, other)
    assert [r["sid"] for r in overview.scene_summary(cid)["rows"]] == [renamed]
    clear_memos()
    assert [r["sid"] for r in overview.scene_summary(cid)["rows"]] == [renamed]


def test_a_hand_edit_through_os_is_seen(client, world, monkeypatch):
    clock_forward(monkeypatch)
    cid = _campaign(client, world, "A Long Run")
    sid = _scene(cid, "Saltmarch Gate")
    assert overview.scene_summary(cid)["open"] == [{"sid": sid, "title": "Saltmarch Gate"}]
    _set_head(cid, sid, "done", "true")
    assert overview.scene_summary(cid)["open"] == []


def test_an_unlistable_scenes_dir_answers_as_an_unknown_campaign(client, world, monkeypatch):
    cid = _campaign(client, world, "A Long Run")
    other = _campaign(client, world, "Mara's Wake")
    _scene(cid, "Saltmarch Gate")
    _scene(other, "Winifred")
    d = store.scenes.paths._scenes_dir(cid)
    real = Path.glob

    def glob(self, pattern, *a, **k):
        if self == d:
            raise PermissionError(13, "denied", str(d))
        return real(self, pattern, *a, **k)
    monkeypatch.setattr(Path, "glob", glob)
    assert client.get("/api/todo", params={"campaign": cid}).status_code == 404
    chores = client.get("/api/todo").json()["chores"]
    assert {c["campaign_id"] for c in chores if c.get("campaign_id")} <= {other}
    assert any(c.get("campaign_id") == other for c in chores)


def test_the_summary_hands_out_copies(client, world):
    cid = _campaign(client, world, "A Long Run")
    _scene(cid, "Saltmarch Gate")
    overview.scene_summary(cid)["rows"][0]["title"] = "scribbled"
    assert overview.scene_summary(cid)["rows"][0]["title"] == "Saltmarch Gate"


# ---- counters ----

def test_warm_same_process_lists_no_scenes_dir(client, world, monkeypatch):
    clock_forward(monkeypatch)
    _library(client, world)
    client.get("/api/campaigns")
    with audited() as audit, readstats.collect() as counters:
        client.get("/api/campaigns")
    assert not [p for p in audit.listed if p.endswith("scenes")]
    assert counters.get("frontmatter.parse_head") == 0


def test_a_relevant_change_costs_one_head_and_one_listing(client, world, monkeypatch):
    clock_forward(monkeypatch)
    a, _b = _library(client, world)
    sid = store.scenes.list_scenes(a)[-1]["id"]
    client.get("/api/campaigns")
    store.scenes.append_message(a, sid, "user", "Saltmarch")
    with audited() as audit, readstats.collect() as counters:
        client.get("/api/campaigns")
    assert counters.get("frontmatter.parse_head") == 1
    listed = [p for p in audit.listed if p.endswith("scenes")]
    assert listed == [str(store.scenes.paths._scenes_dir(a))]


def test_warm_overview_reads_never_open_a_closed_scene(client, world, monkeypatch):
    clock_forward(monkeypatch)
    a, _b = _library(client, world)
    closed = [s["id"] for s in store.scenes.list_scenes(a) if s["done"]]
    for url in ("/api/campaigns", f"/api/shell?campaign={a}", "/api/todo"):
        client.get(url)
    with audited() as audit:
        for url in ("/api/campaigns", f"/api/shell?campaign={a}", "/api/todo",
                    f"/api/todo?campaign={a}"):
            assert client.get(url).status_code == 200
    assert not [p for p in audit.opened if any(p.endswith(f"{sid}.md") for sid in closed)]


def test_a_same_size_head_rewrite_with_mtime_restored_is_seen(client, world, monkeypatch):
    """The summary's stamps carry ctime, and the fold re-reads a head whose
    stamp moved while the head memo's signature did not (spec 04 section 3.4)."""
    import os
    clock_forward(monkeypatch)
    cid = _campaign(client, world, "A Long Run")
    sid = _scene(cid, "Saltmarch Gate")
    p = _path(cid, sid)
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns - 10_000_000_000))
    assert overview.scene_summary(cid)["rows"][0]["title"] == "Saltmarch Gate"
    st = p.stat()
    with open(p, "r+b") as f:   # in place: same inode, same size
        data = f.read().replace(b"title: Saltmarch Gate", b"title: Saltmarch Gale")
        f.seek(0)
        f.write(data)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert p.stat().st_mtime_ns == st.st_mtime_ns and p.stat().st_size == st.st_size
    assert overview.scene_summary(cid)["rows"][0]["title"] == "Saltmarch Gale"


def test_a_head_rewritten_before_the_first_fold_is_not_believed(client, world, monkeypatch):
    """`list_scenes` filled the head memo; the head is then rewritten in place
    with its mtime restored; the FIRST overview fold in the process must not
    take the memo's stale row and pin it under the file's new ctime."""
    import os
    clock_forward(monkeypatch)
    cid = _campaign(client, world, "A Long Run")
    sid = _scene(cid, "Saltmarch Gate")
    p = _path(cid, sid)
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns - 10_000_000_000))
    assert store.scenes.list_scenes(cid)[0]["title"] == "Saltmarch Gate"   # head memo filled
    st = p.stat()
    with open(p, "r+b") as f:
        data = f.read().replace(b"title: Saltmarch Gate", b"title: Saltmarch Gale")
        f.seek(0)
        f.write(data)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert overview.scene_summary(cid)["rows"][0]["title"] == "Saltmarch Gale"
