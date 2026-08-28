"""statcache: stat-keyed memo for derivations of file content, + its hash consumers."""

import hashlib
import os
import time

from grimoire.store import characters, entities, statcache


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _age(*paths):
    """Back-date mtimes past the racy window so the cache is allowed to hold them."""
    old = time.time_ns() - 2 * statcache.RACY_WINDOW_NS
    for p in paths:
        os.utime(p, ns=(old, old))


def _age_tree(root):
    _age(*(p for p in root.rglob("*") if p.is_file()))


# ---- the memo itself ----
def test_memo_computes_once_while_file_unchanged(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("hello", encoding="utf-8")
    _age(p)
    calls = []
    for _ in range(3):
        got = statcache.memo("t", statcache.signature(p), lambda: calls.append(1) or "value")
    assert got == "value"
    assert len(calls) == 1


def test_same_size_rapid_rewrite_is_never_served_stale(tmp_path):
    # "Old." -> "New." is a same-size write that can share an mtime tick; the
    # racy window must keep such fresh files out of the cache
    p = tmp_path / "a.md"
    for content in ("Old.", "New."):
        p.write_text(content, encoding="utf-8")
        got = statcache.memo("racy", statcache.signature(p),
                             lambda: p.read_text(encoding="utf-8"))
        assert got == content


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
    _age(p)
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


def test_signature_changes_on_inode_replacement(tmp_path):
    """A rename-replace that preserves mtime and size still invalidates."""
    p = tmp_path / "a.md"
    p.write_text("aaaa", encoding="utf-8")
    _age(p)
    before = statcache.signature(p)
    st = p.stat()
    q = tmp_path / "a.md.tmp"
    q.write_text("bbbb", encoding="utf-8")   # same size, new inode
    os.utime(q, ns=(st.st_mtime_ns, st.st_mtime_ns))
    os.replace(q, p)
    assert statcache.signature(p) != before


def test_signature_absent_ok_yields_cacheable_sentinel(tmp_path):
    """A deliberately-absent companion file is a valid, cacheable state."""
    p = tmp_path / "present.md"
    p.write_text("x", encoding="utf-8")
    _age(p)
    missing = tmp_path / "missing.json"
    sig = statcache.signature(p, missing, absent_ok=True)
    assert sig is not None
    # ...and its creation invalidates.
    missing.write_text("{}", encoding="utf-8")
    _age(missing)
    assert statcache.signature(p, missing, absent_ok=True) != sig
    # Without the flag, a missing path still voids the signature.
    assert statcache.signature(p, tmp_path / "also-missing") is None


def test_memo_pool_budget_override(tmp_path):
    pool: dict = {}
    paths = []
    for i in range(4):
        p = tmp_path / f"f{i}.md"
        p.write_text(str(i), encoding="utf-8")
        paths.append(p)
    _age(*paths)
    for p in paths:
        statcache.memo("k", statcache.signature(p), lambda p=p: p.name,
                       pool=pool, max_entries=2)
    assert len(pool) <= 2


def test_memo_pool_holds_working_set_larger_than_shared_budget(tmp_path):
    """A repeated sweep bigger than MAX_ENTRIES still hits in its own pool."""
    pool: dict = {}
    n = statcache.MAX_ENTRIES + 8
    paths = []
    for i in range(n):
        p = tmp_path / f"s{i}.md"
        p.write_text("x", encoding="utf-8")
        paths.append(p)
    _age(*paths)
    calls = []
    def sweep():
        for p in paths:
            statcache.memo("s", statcache.signature(p),
                           lambda p=p: calls.append(p) or p.name,
                           pool=pool, max_entries=n + 16)
    sweep()
    first = len(calls)
    sweep()
    assert len(calls) == first   # second sweep: all hits
