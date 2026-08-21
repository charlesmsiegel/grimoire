"""Forking a world: a deep copy that shares nothing with the world it came from (#41).

The three things a fork has to get right, and one it has to get right by
*construction* rather than by enumeration:

- **Completeness.** Every file travels, byte for byte, because the copy is the
  directory rather than a walk over the kinds someone remembered. So the test
  that matters most compares whole trees and would fail if a kind added
  tomorrow were skipped -- which is exactly the failure `create_campaign`'s
  hand-written walk keeps producing.
- **Independence.** Nothing in the fork is the source's file, and nothing the
  fork does reaches back.
- **Self-reference.** The serving URLs `store/localize.py` writes into cards
  and greetings name the world by id, so a copy that kept them would render
  every localized image out of the world it was forked from -- and go on
  working until that world was deleted, which is the worst way for this to
  fail.

And the one by construction: a failed fork leaves nothing behind. The tree is
assembled in `worlds.staging` and enters the library by one rename, so there is
no window in which `list_worlds` can see half a world.
"""

from __future__ import annotations

import json
import os
import shutil

import pytest

from grimoire.store import atomic, characters, entities, greetings, taglines, worlds
from grimoire.store.worlds import staging

from .world_fixtures import PNG, seed_world, tree


def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def _staging_is_empty() -> bool:
    root = staging.staging_root()
    return not root.exists() or not any(root.iterdir())


def _repointed(text: str, old: str, new: str) -> bool:
    return f"/api/worlds/{new}/" in text and f"/api/worlds/{old}/" not in text


# ---- the copy ----

def test_fork_copies_every_file_and_only_identity_differs(monkeypatch, tmp_path):
    """Whole-tree equality, minus the files a fork is *supposed* to change.

    Written as a diff over the two trees rather than a checklist of kinds: a
    checklist passes forever on a layout that has grown past it, which is the
    entire reason this is a `copytree` and not a walk.
    """
    _home(monkeypatch, tmp_path)
    wid = seed_world()
    new = worlds.fork_world(wid, "Saltmarch (fork)")
    src, dst = tree(worlds.world_root(wid)), tree(worlds.world_root(new))

    assert set(src) == set(dst)                     # nothing dropped, nothing invented
    differing = {rel for rel in src if src[rel] != dst[rel]}
    # world.md carries the name and the stamps; the two records holding a
    # localized URL carry the world's id. Nothing else may differ.
    assert differing == {
        "world.md",
        *(rel for rel in src if b"/api/worlds/" in src[rel]),
    }
    assert len(differing) >= 4, differing        # the seed really did localize some


def test_fork_carries_binary_assets_verbatim(monkeypatch, tmp_path):
    """Byte equality on the PNGs specifically: a rewrite that widened past
    `.md`/`.json` would corrupt an asset, and a tree-wide comparison that
    happened to be run over an empty gallery would not notice."""
    _home(monkeypatch, tmp_path)
    wid = seed_world()
    new = worlds.fork_world(wid, "Copy")
    pngs = [rel for rel in tree(worlds.world_root(new)) if rel.endswith(".png")]
    assert len(pngs) >= 3                            # two character versions and a greeting
    root = worlds.world_root(new)
    assert all((root / rel).read_bytes() == PNG for rel in pngs)


def test_fork_stamps_a_new_name_and_fresh_timestamps(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    wid = seed_world()
    before = worlds.read_world(wid)["meta"]
    new = worlds.fork_world(wid, "Saltmarch (fork)")
    meta = worlds.read_world(new)["meta"]
    assert meta["id"] == new != wid
    assert meta["name"] == "Saltmarch (fork)"
    assert meta["created"] == meta["updated"] >= before["created"]
    assert worlds.read_world(wid)["meta"] == before   # the source is untouched


def test_a_fork_sorts_to_the_front_of_the_shelf(monkeypatch, tmp_path):
    """`list_worlds` orders by `updated`, so a fork of a world last touched
    long ago has to stamp its own or it lands where the user will not look."""
    _home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    mp = worlds.world_meta_path(wid)
    mp.write_text(mp.read_text(encoding="utf-8").replace(
        worlds.read_world(wid)["meta"]["updated"], "2019-01-01T00:00:00Z"), encoding="utf-8")
    new = worlds.fork_world(wid, "Realm Again")
    assert [w["id"] for w in worlds.list_worlds()] == [new, wid]


def test_fork_carries_the_module_binding_and_the_body(monkeypatch, tmp_path):
    """Everything in the frontmatter that is not identity travels as it stands:
    a mechanics binding names a pack installed in this store, which is the same
    store the fork lives in."""
    _home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    mp = worlds.world_meta_path(wid)
    mp.write_text("---\nname: Realm\ncreated: 2020-01-01T00:00:00Z\n"
                  "updated: 2020-01-01T00:00:00Z\nmodule: wod20\n---\nA drowned coast.\n",
                  encoding="utf-8")
    new = worlds.fork_world(wid, "Realm Copy")
    got = worlds.read_world(new)
    assert got["meta"]["module"] == "wod20"
    assert got["body"].strip() == "A drowned coast."


# ---- self-reference ----

def test_fork_repoints_localized_urls_onto_the_copy(monkeypatch, tmp_path):
    """The card and the greeting must name the fork, and the source must still
    name the source -- half of this passing is the failure mode: a fork whose
    images serve out of the world it was forked from goes on working until that
    world is deleted."""
    _home(monkeypatch, tmp_path)
    wid = seed_world()
    new = worlds.fork_world(wid, "Saltmarch (fork)")

    for world in (wid, new):
        root = worlds.world_root(world)
        cid = characters.list_characters(root)[0]["id"]
        card = characters.read_card(root, cid, "default")
        gid = greetings.list_greetings(root)[0]["id"]
        body = greetings.read_greeting(root, gid)["body"]
        assert _repointed(card["data"]["description"], wid if world == new else new, world)
        assert _repointed(body, wid if world == new else new, world)


def test_fork_repoints_every_version_not_just_the_default(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    wid = seed_world()
    new = worlds.fork_world(wid, "Saltmarch (fork)")
    root = worlds.world_root(new)
    cid = characters.list_characters(root)[0]["id"]
    versions = [v["id"] for v in characters.read_character(root, cid)["versions"]]
    assert len(versions) == 2
    for vid in versions:
        card = characters.read_card(root, cid, vid)
        assert _repointed(card["data"]["description"], wid, new)


def test_a_world_id_that_prefixes_another_is_not_rewritten_by_half(monkeypatch, tmp_path):
    """`realm` sits inside `realm-2`. The substitution carries the trailing
    slash, so forking `realm` cannot corrupt a URL that names `realm-2`."""
    _home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    assert wid == "realm"
    other = worlds.create_world("Realm")
    assert other == "realm-2"
    lid = entities.create_entity(worlds.world_root(wid), "lore", "Signposts")
    body = (f"![](/api/worlds/{wid}/greetings/g/images/a)\n"
            f"![](/api/worlds/{other}/greetings/g/images/b)\n")
    entities.update_entity(worlds.world_root(wid), "lore", lid, body=body)
    new = worlds.fork_world(wid, "Third Realm")
    got = entities.read_entity(worlds.world_root(new), "lore", lid)["body"]
    assert f"/api/worlds/{new}/greetings/g/images/a" in got
    assert f"/api/worlds/{other}/greetings/g/images/b" in got   # untouched


def test_a_fork_of_a_fork_references_itself(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    wid = seed_world()
    once = worlds.fork_world(wid, "First")
    twice = worlds.fork_world(once, "Second")
    root = worlds.world_root(twice)
    cid = characters.list_characters(root)[0]["id"]
    card = characters.read_card(root, cid, "default")
    assert f"/api/worlds/{twice}/" in card["data"]["description"]
    assert wid not in card["data"]["description"]
    assert once not in card["data"]["description"]


# ---- independence ----

def test_editing_the_fork_leaves_the_source_alone(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    wid = seed_world()
    before = tree(worlds.world_root(wid))
    new = worlds.fork_world(wid, "Saltmarch (fork)")

    root = worlds.world_root(new)
    cid = characters.list_characters(root)[0]["id"]
    taglines.write(root, cid, "Rewritten.")
    entities.create_entity(root, "locations", "A New Wing")
    lid = entities.list_entities(root, "lore")[0]["id"]
    entities.delete_entity(root, "lore", lid)

    assert tree(worlds.world_root(wid)) == before


def test_editing_the_source_leaves_the_fork_alone(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    wid = seed_world()
    new = worlds.fork_world(wid, "Saltmarch (fork)")
    before = tree(worlds.world_root(new))
    entities.create_entity(worlds.world_root(wid), "locations", "A New Wing")
    worlds.rename_world(wid, "Renamed")
    assert tree(worlds.world_root(new)) == before


def test_deleting_the_source_leaves_the_fork_whole(monkeypatch, tmp_path):
    """The consequence that would expose a copy made of symlinks."""
    _home(monkeypatch, tmp_path)
    wid = seed_world()
    new = worlds.fork_world(wid, "Saltmarch (fork)")
    before = tree(worlds.world_root(new))
    worlds.delete_world(wid)
    assert tree(worlds.world_root(new)) == before
    assert [w["id"] for w in worlds.list_worlds()] == [new]


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs a privilege on Windows")
def test_a_symlink_in_the_source_becomes_a_real_file_in_the_fork(monkeypatch, tmp_path):
    """A store is plain files the user owns and syncs, so a symlink in one is
    not hypothetical -- and copied *as a link*, every write to the fork would
    land in whatever the source pointed at."""
    _home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    outside = tmp_path / "elsewhere.md"
    outside.write_text("shared\n", encoding="utf-8")
    (worlds.world_root(wid) / "lore").mkdir()
    (worlds.world_root(wid) / "lore" / "linked.md").symlink_to(outside)

    new = worlds.fork_world(wid, "Realm Copy")
    copied = worlds.world_root(new) / "lore" / "linked.md"
    assert not copied.is_symlink()
    assert copied.read_text(encoding="utf-8") == "shared\n"
    copied.write_text("mine\n", encoding="utf-8")
    assert outside.read_text(encoding="utf-8") == "shared\n"


# ---- ids and names ----

def test_the_fork_gets_its_own_id_even_under_the_same_name(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    assert worlds.fork_world(wid, "Realm") == "realm-2"
    assert worlds.fork_world(wid, "Realm") == "realm-3"


def test_fork_refuses_a_source_that_is_not_a_world(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    with pytest.raises(worlds.WorldNotFound):
        worlds.fork_world("nope", "Copy")
    # An id the resolvers refuse outright is as absent as a missing directory.
    with pytest.raises(worlds.WorldNotFound):
        worlds.fork_world("../evil", "Copy")
    assert worlds.list_worlds() == []


def test_a_refused_fork_creates_nothing(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    wid = seed_world()
    with pytest.raises(worlds.WorldNotFound):
        worlds.fork_world("nope", "Copy")
    assert [w["id"] for w in worlds.list_worlds()] == [wid]


def test_fork_reads_the_source_under_the_spelling_the_filesystem_holds(
        monkeypatch, tmp_path):
    """On Windows and macOS `worlds/REALM` opens `worlds/realm`. Asked under
    the wrong case, a fork would look for `/api/worlds/REALM/` in records that
    carry `/api/worlds/realm/`, find nothing, and publish a copy whose images
    all still serve out of the source."""
    _home(monkeypatch, tmp_path)
    wid = seed_world("Realm")
    if not worlds.world_exists(wid.upper()):
        pytest.skip("case-sensitive filesystem: REALM is genuinely absent")
    new = worlds.fork_world(wid.upper(), "Realm Copy")
    root = worlds.world_root(new)
    cid = characters.list_characters(root)[0]["id"]
    assert _repointed(characters.read_card(root, cid, "default")["data"]["description"],
                      wid, new)


def test_fork_canonicalizes_its_source_id(monkeypatch, tmp_path):
    """The same guarantee as the test above, on the filesystems where it cannot
    be reproduced: a case-insensitive lookup is simulated so a CI runner on a
    case-sensitive volume still fails if the canonicalizing call is removed."""
    _home(monkeypatch, tmp_path)
    wid = seed_world("Realm")
    real = worlds.lifecycle.paths.canonical_id
    monkeypatch.setattr(worlds.lifecycle.paths, "canonical_id",
                        lambda w: wid if w == wid.upper() else real(w))

    new = worlds.fork_world(wid.upper(), "Realm Copy")
    root = worlds.world_root(new)
    cid = characters.list_characters(root)[0]["id"]
    assert _repointed(characters.read_card(root, cid, "default")["data"]["description"],
                      wid, new)


# ---- failure leaves nothing behind ----

def test_a_fork_that_fails_partway_publishes_nothing(monkeypatch, tmp_path):
    """`list_worlds` calls any directory holding a `world.md` a world, so a
    copy made straight into the library would publish one partway through. The
    staging tree is what makes a failure invisible instead of half-visible."""
    _home(monkeypatch, tmp_path)
    wid = seed_world()

    boom = RuntimeError("disk full")

    def explode(*_a, **_k):
        raise boom

    monkeypatch.setattr(staging, "repoint_urls", explode)
    with pytest.raises(RuntimeError):
        worlds.fork_world(wid, "Doomed Copy")
    assert [w["id"] for w in worlds.list_worlds()] == [wid]
    assert not (worlds._worlds_dir() / "doomed-copy").exists()
    assert _staging_is_empty()


def test_a_successful_fork_leaves_no_staging_litter(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    wid = seed_world()
    worlds.fork_world(wid, "Saltmarch (fork)")
    assert _staging_is_empty()


def test_fork_reports_a_lost_id_race_rather_than_merging(monkeypatch, tmp_path):
    """`publish` re-picks and retries a taken id; when it runs out it says so.
    A world that publishes into somebody else's directory is the alternative."""
    _home(monkeypatch, tmp_path)
    wid = seed_world()
    # Every candidate id the publisher re-picks is one that already exists, so
    # it runs out of attempts. Patched rather than raced for: a real race is
    # not reproducible, and the branch under test is the exhaustion.
    taken = worlds._worlds_dir() / "doomed-copy"
    taken.mkdir(parents=True)
    (taken / "world.md").write_text("---\nname: Squatter\n---\n", encoding="utf-8")
    monkeypatch.setattr(staging, "uniquify", lambda base, _exists: "doomed-copy")
    monkeypatch.setattr(worlds.lifecycle, "uniquify", lambda base, _exists: "doomed-copy")

    with pytest.raises(staging.WorldIdConflictError):
        worlds.fork_world(wid, "Doomed Copy")
    # The squatter is intact -- publish refused rather than merging into it.
    assert list(tree(taken)) == ["world.md"]
    assert _staging_is_empty()


def test_fork_skips_a_write_temp_caught_mid_write(monkeypatch, tmp_path):
    """`store.atomic`'s temps are not part of the world, and the writer that
    owns one renames or unlinks it out from under the copy."""
    _home(monkeypatch, tmp_path)
    wid = seed_world()
    root = worlds.world_root(wid)
    (root / ".world.md.a1b2c3d4.tmp").write_text("half a record", encoding="utf-8")
    # A legitimate record that merely looks like one must still travel.
    (root / ".notes.tmp").write_text("mine", encoding="utf-8")
    assert atomic.is_write_temp(root / ".world.md.a1b2c3d4.tmp")
    assert not atomic.is_write_temp(root / ".notes.tmp")

    new = worlds.fork_world(wid, "Saltmarch (fork)")
    copied = tree(worlds.world_root(new))
    assert ".world.md.a1b2c3d4.tmp" not in copied
    assert copied[".notes.tmp"] == b"mine"


def test_the_staging_tree_is_never_listed_as_a_world(monkeypatch, tmp_path):
    """Belt and braces on the reason staging sits outside `worlds/`: even a
    complete world left in it by a crash is invisible to the library."""
    _home(monkeypatch, tmp_path)
    wid = seed_world()
    litter = staging.staging_root() / "abandoned" / "world"
    litter.parent.mkdir(parents=True)
    shutil.copytree(worlds.world_root(wid), litter)
    assert [w["id"] for w in worlds.list_worlds()] == [wid]


def test_calendar_and_plotmap_travel(monkeypatch, tmp_path):
    """Named explicitly because they are the two world-root JSON files no
    entity kind owns -- the ones a per-kind walk would have to remember."""
    _home(monkeypatch, tmp_path)
    wid = seed_world()
    new = worlds.fork_world(wid, "Saltmarch (fork)")
    root = worlds.world_root(new)
    assert json.loads((root / "calendar.json").read_text(encoding="utf-8"))["primary"] == "gregorian"
    assert (root / "plotmap.json").is_file()
    assert (root / "tags.md").read_text(encoding="utf-8") == \
        (worlds.world_root(wid) / "tags.md").read_text(encoding="utf-8")
