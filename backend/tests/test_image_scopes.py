"""Scope lifecycle on the image object (`store.image_scopes`).

A subject tag lives on the shared object under a scope -- ``world:<wid>`` or
``campaign:<cid>`` (R10) -- and those ids are slugs, so a slug can come back.
Deleting a world or a campaign strips its scope from every object, so a
re-created one of the same name starts untagged; forking copies the scope onto
the fork's own (Review Focus 4).
"""

from __future__ import annotations

import pytest

from grimoire.store import (
    assets,
    campaigns,
    characters,
    greetings,
    image_scopes,
    image_store,
    image_subjects,
    locks,
    worlds,
)


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


def _assoc(scope, cid="seraphine"):
    return {"kind": "character", "relation": "subject", "scope": scope, "id": cid}


def _object(data: bytes, assoc: list[dict], subjects: list[str]) -> str:
    image_id = image_store.ingest(data, "png").id

    def seed(raw):
        raw["associations"] = assoc
        raw["reviews"] = {"subjects": subjects}
        return raw

    assert image_store.update(image_id, seed)
    return image_id


def _raw(image_id):
    return image_store.read_fresh(image_id).raw


#: A variable rather than a literal at the call: `image_scopes.strip("...")`
#: reads to ruff's B005 as `str.strip` with a multi-character argument.
REALM = "world:realm"


# ---- iter_ids ----

def test_iter_ids_lists_well_formed_sidecars_sorted_and_skips_the_rest(home):
    ids = sorted(image_store.ingest(f"png-{n}".encode(), "png").id for n in range(3))
    shard = image_store.object_path(ids[0]).parent
    (shard / "not-an-id.json").write_text("{}", encoding="utf-8")
    (shard / f"{ids[0]}.txt").write_text("{}", encoding="utf-8")
    (shard / "px1-zz.json").write_text("{}", encoding="utf-8")
    # A well-formed name in the wrong shard is not where `object_path` would
    # look for it, so it is not an object of this store.
    stray = image_store.store_root() / "objects" / "zz"
    stray.mkdir()
    (stray / image_store.object_path(ids[1]).name).write_text("{}", encoding="utf-8")
    (image_store.store_root() / "objects" / "loose.json").write_text("{}", encoding="utf-8")

    assert list(image_store.iter_ids()) == ids


def test_iter_ids_on_an_empty_store_is_empty(home):
    assert list(image_store.iter_ids()) == []


# ---- strip / copy ----

def test_strip_removes_only_that_scope(home):
    first = _object(b"png-1",
                    [_assoc("world:realm"), _assoc("world:saltmarch"),
                     _assoc("campaign:realm", "mara")],
                    ["campaign:realm", "world:realm", "world:saltmarch"])
    second = _object(b"png-2", [_assoc("world:saltmarch")], ["world:saltmarch"])
    untouched = image_store.object_path(second).stat().st_mtime_ns

    assert image_scopes.strip(REALM) == 1

    raw = _raw(first)
    assert raw["associations"] == [_assoc("world:saltmarch"), _assoc("campaign:realm", "mara")]
    assert raw["reviews"] == {"subjects": ["campaign:realm", "world:saltmarch"]}
    # Nothing to change: not rewritten at all.
    assert image_store.object_path(second).stat().st_mtime_ns == untouched
    assert _raw(second)["associations"] == [_assoc("world:saltmarch")]
    # Idempotent.
    assert image_scopes.strip(REALM) == 0


def test_strip_counts_a_review_without_associations(home):
    """A scope reviewed as "nobody" has no association to remove, and its
    review still has to go -- or a re-created slug inherits the answer."""
    image_id = _object(b"png-1", [], ["world:realm"])
    assert image_scopes.strip(REALM) == 1
    assert _raw(image_id)["reviews"] == {"subjects": []}


def test_strip_tolerates_malformed_fields(home):
    image_id = image_store.ingest(b"png-1", "png").id

    def garble(raw):
        raw["associations"] = "nonsense"
        raw["reviews"] = ["world:realm"]
        return raw

    assert image_store.update(image_id, garble)
    assert image_scopes.strip(REALM) == 0
    assert _raw(image_id)["associations"] == "nonsense"


def test_copy_unions_into_the_new_scope(home):
    image_id = _object(b"png-1",
                       [_assoc("world:realm"), _assoc("world:realm", "mara"),
                        _assoc("world:copy"), _assoc("world:saltmarch", "winifred")],
                       ["world:realm"])
    other = _object(b"png-2", [_assoc("world:saltmarch")], ["world:saltmarch"])

    assert image_scopes.copy("world:realm", "world:copy") == 1

    raw = _raw(image_id)
    # The source stays; what the destination already had is not duplicated.
    assert raw["associations"] == [
        _assoc("world:realm"), _assoc("world:realm", "mara"), _assoc("world:copy"),
        _assoc("world:saltmarch", "winifred"), _assoc("world:copy", "mara")]
    assert raw["reviews"] == {"subjects": ["world:copy", "world:realm"]}
    assert _raw(other)["associations"] == [_assoc("world:saltmarch")]
    # A union, so a second copy changes nothing.
    assert image_scopes.copy("world:realm", "world:copy") == 0


def test_copy_carries_a_review_with_no_subjects(home):
    image_id = _object(b"png-1", [], ["world:realm"])
    assert image_scopes.copy("world:realm", "world:copy") == 1
    assert _raw(image_id)["reviews"] == {"subjects": ["world:copy", "world:realm"]}


def test_world_scopes_are_canonical(home, monkeypatch):
    """R10: the scope is the world id as the filesystem spells it."""
    monkeypatch.setattr(worlds.paths, "canonical_id",
                        lambda wid: "realm" if wid == "REALM" else wid)
    assert image_scopes.world_scope("REALM") == "world:realm"
    assert image_scopes.campaign_scope("saltmarch") == "campaign:saltmarch"
    image_id = _object(b"png-1", [_assoc("world:realm")], ["world:realm"])
    assert image_scopes.strip_world("REALM") == 1
    assert _raw(image_id)["associations"] == []


def test_campaign_scope_wrappers_take_the_campaign_lock(home, monkeypatch):
    taken = []
    real = locks.campaign_lock

    def spy(cid):
        taken.append(cid)
        return real(cid)

    monkeypatch.setattr(locks, "campaign_lock", spy)
    image_id = _object(b"png-1", [_assoc("campaign:saltmarch")], ["campaign:saltmarch"])
    assert image_scopes.copy_campaign("saltmarch", "saltmarch-2") == 1
    assert image_scopes.strip_campaign("saltmarch") == 1
    assert set(taken) >= {"saltmarch", "saltmarch-2"}
    assert _raw(image_id)["associations"] == [_assoc("campaign:saltmarch-2")]


# ---- wired into delete ----

def test_a_recreated_world_slug_starts_untagged(home):
    """Review Focus 4: a world deleted and made again under its slug, with the
    same picture placed again, must not inherit the dead world's tags."""
    def build():
        root = worlds.world_root(worlds.create_world("Realm"))
        cid, vid = characters.create_character(root, "Seraphine", "main")
        gid = greetings.create_greeting(root, "Opener", cid, vid, "body")
        assets.put_image(root, gid, "default", "art_1", b"png-1", "png", base="greetings")
        return root, cid, gid

    root, cid, gid = build()
    assert root.name == "realm"
    image_subjects.set_image_subjects(root, gid, "art_1", [cid])
    image_id = assets.image_id(root, gid, "default", "art_1", base="greetings")
    assert image_subjects.read_subjects(root, gid) == {"art_1": [cid]}

    worlds.delete_world("realm")
    root, cid, gid = build()

    assert assets.image_id(root, gid, "default", "art_1", base="greetings") == image_id
    assert image_subjects.read_subjects(root, gid) == {}
    assert [(a["gid"], a["name"]) for a in image_subjects.untagged(root)] == [(gid, "art_1")]
    raw = image_store.read(image_id).raw
    assert "world:realm" not in [a.get("scope") for a in raw.get("associations", [])]
    assert "world:realm" not in raw.get("reviews", {}).get("subjects", [])


def test_a_refused_world_delete_strips_nothing(home):
    wid = worlds.create_world("Realm")
    campaigns.create_campaign("Saltmarch", wid)
    image_id = _object(b"png-1", [_assoc("world:realm")], ["world:realm"])
    with pytest.raises(worlds.WorldInUse):
        worlds.delete_world(wid)
    assert _raw(image_id)["associations"] == [_assoc("world:realm")]


def test_deleting_a_campaign_strips_campaign_scope(home):
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    image_id = _object(b"png-1",
                       [_assoc(f"campaign:{cid}"), _assoc("world:realm")],
                       [f"campaign:{cid}", "world:realm"])

    campaigns.delete_campaign(cid)

    raw = _raw(image_id)
    assert raw["associations"] == [_assoc("world:realm")]
    assert raw["reviews"] == {"subjects": ["world:realm"]}
