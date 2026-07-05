"""statcache: stat-keyed memo for derivations of file content, + its hash consumers."""

import hashlib

from grimoire.store import characters, entities, statcache


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---- the memo itself ----
def test_memo_computes_once_while_file_unchanged(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("hello", encoding="utf-8")
    calls = []
    for _ in range(3):
        got = statcache.memo("t", statcache.signature(p), lambda: calls.append(1) or "value")
    assert got == "value"
    assert len(calls) == 1


def test_memo_recomputes_when_file_changes(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("one", encoding="utf-8")
    first = statcache.memo("t", statcache.signature(p), lambda: p.read_text(encoding="utf-8"))
    p.write_text("two-longer", encoding="utf-8")
    second = statcache.memo("t", statcache.signature(p), lambda: p.read_text(encoding="utf-8"))
    assert (first, second) == ("one", "two-longer")


def test_signature_covers_every_file(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")
    sig1 = statcache.signature(a, b)
    b.write_text("b-changed", encoding="utf-8")
    assert statcache.signature(a, b) != sig1


def test_signature_none_when_any_file_missing(tmp_path):
    a = tmp_path / "a"
    a.write_text("a", encoding="utf-8")
    assert statcache.signature(a, tmp_path / "missing") is None


def test_memo_stays_bounded(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("x", encoding="utf-8")
    sig = statcache.signature(p)
    for i in range(statcache.MAX_ENTRIES + 100):
        statcache.memo(f"kind-{i}", sig, lambda: i)
    assert len(statcache._cache) <= statcache.MAX_ENTRIES


# ---- cached consumers keep exact hash semantics ----
def test_entity_hash_tracks_updates(tmp_path):
    eid = entities.create_entity(tmp_path, "lore", "Thing", "body one")
    h1 = entities.entity_hash(tmp_path, "lore", eid)
    assert h1 == _sha((tmp_path / "lore" / f"{eid}.md").read_text(encoding="utf-8"))
    assert entities.entity_hash(tmp_path, "lore", eid) == h1  # cached hit, same value
    entities.update_entity(tmp_path, "lore", eid, body="body two, different length")
    h2 = entities.entity_hash(tmp_path, "lore", eid)
    assert h2 != h1
    assert h2 == _sha((tmp_path / "lore" / f"{eid}.md").read_text(encoding="utf-8"))


def test_entity_hash_none_after_delete(tmp_path):
    eid = entities.create_entity(tmp_path, "lore", "Thing", "body")
    assert entities.entity_hash(tmp_path, "lore", eid) is not None
    entities.delete_entity(tmp_path, "lore", eid)
    assert entities.entity_hash(tmp_path, "lore", eid) is None


def test_card_hash_tracks_updates(tmp_path):
    cid, vid = characters.create_character(tmp_path, "Ada")
    h1 = characters.card_hash(tmp_path, cid, vid)
    assert characters.card_hash(tmp_path, cid, vid) == h1
    card = characters.read_card(tmp_path, cid, vid)
    card["data"]["description"] = "now with a much longer description"
    characters.update_version(tmp_path, cid, vid, card)
    assert characters.card_hash(tmp_path, cid, vid) != h1


def test_dir_hash_tracks_version_add_and_delete(tmp_path):
    cid, vid = characters.create_character(tmp_path, "Ada")
    h1 = characters.dir_hash(tmp_path, cid)
    assert characters.dir_hash(tmp_path, cid) == h1
    v2 = characters.create_version(tmp_path, cid, "alt", characters.blank_card("Ada"))
    h2 = characters.dir_hash(tmp_path, cid)
    assert h2 != h1
    characters.delete_version(tmp_path, cid, v2)
    assert characters.dir_hash(tmp_path, cid) == h1  # same content set again
