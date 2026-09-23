"""Opening a world: the character list answers from a memo that notices every
input moving, and the requests beside it on the world page stay small.

`characters.list_characters` memoizes each row on the stamps of every file and
directory the row is read from (`statcache.memo_stamped`), and the campaign
listing patches its rows from per-root memoized facts. A memo is only as good
as its key, so most of this file is one comparison repeated across mutations:
the memoized answer against the listing as it was written before the memo
existed, kept here as an oracle (`_reference_list`, `_reference_campaign`).
The mutations are the ones a key could miss -- above all an in-place rewrite,
which moves no directory -- and each is made with the store's files aged past
the racy window first, so the memo really is holding the rows it is asked to
invalidate.
"""

from __future__ import annotations

import builtins
import io
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient

from grimoire.main import create_app
from grimoire.store import (
    assets,
    campaigns,
    cards,
    characters,
    greetings,
    image_descriptions,
    overlay,
    statcache,
    taglines,
    voice_anchors,
    worlds,
)
from grimoire.store.frontmatter import parse_frontmatter

PNG = b"\x89PNG\r\n\x1a\n"


# ---- oracles: the listings as they were before the memo -------------------

def _card_facts(root: Path, cid: str, vid: str) -> tuple[str, int]:
    card = json.loads(characters._card_path(root, cid, vid).read_text(encoding="utf-8"))
    return (characters._version_label(card, vid),
            characters._greeting_count(cards.card_data(card)))


def _reference_list(root: Path) -> list[dict]:
    """`list_characters` as written before its rows were memoized: every read
    made fresh, cards parsed here rather than through the summary memo."""
    out = []
    for cd in characters._listed_dirs(root):
        cid = cd.name
        meta, _ = parse_frontmatter(characters._meta_path(root, cid).read_text(encoding="utf-8"))
        version_ids = characters._version_ids(root, cid)
        if not version_ids:
            continue
        default = characters._addressable_default(meta.get("default_version", ""), version_ids)
        images = assets.list_images(root, cid, default)
        names = [i["name"] for i in images]
        out.append({
            "id": cid,
            "name": meta.get("name", cid),
            "default_version": default,
            "has_avatar": assets.AVATAR in names,
            "avatar_v": next((i["v"] for i in images if i["name"] == assets.AVATAR), None),
            "avatar_focus": assets.read_focus(root, cid, default),
            "gallery_count": sum(1 for n in names if n.startswith("gallery_")),
            "localized_count": sum(1 for n in names if n.startswith("embed-")),
            "greeting_count": _card_facts(root, cid, default)[1],
            "tagline": taglines.read(root, cid),
            "has_voice_anchor": bool(voice_anchors.read(root, cid)),
            "versions": [{"id": v, "name": _card_facts(root, cid, v)[0]} for v in version_ids],
        })
    return out


def _reference_patch(v: overlay.View, item: dict) -> dict:
    """`overlay._patch_char_item` as written before it read memoized facts:
    the three overlay resolvers, asked per row."""
    images = overlay.list_images(v.cid, item["id"], item["default_version"], v=v)
    names = [i["name"] for i in images]
    return {**item,
            "has_avatar": assets.AVATAR in names,
            "avatar_v": next((i["v"] for i in images if i["name"] == assets.AVATAR), None),
            "avatar_focus": overlay.read_focus(v.cid, item["id"], item["default_version"], v=v),
            "gallery_count": sum(1 for n in names if n.startswith("gallery_")),
            "localized_count": sum(1 for n in names if n.startswith("embed-")),
            "tagline": overlay.tagline(v.cid, item["id"], v=v)}


def _reference_campaign(cid: str) -> list[dict]:
    v = overlay.view(cid)
    mine = _reference_list(v.croot)
    have = {c["id"] for c in mine}
    world_rows = _reference_list(v.wroot)
    inherited = [c for c in world_rows
                 if c["id"] not in have and f"characters/{c['id']}" not in v.gone]
    rows = [_reference_patch(v, c) for c in mine + inherited]
    world = {c["id"]: c["has_voice_anchor"] for c in world_rows}
    anchored = overlay._anchor_presence(v, [r["id"] for r in rows],
                                        lambda aid: world.get(aid, False))
    for row in rows:
        row["has_voice_anchor"] = anchored[row["id"]]
    return sorted(rows, key=lambda c: c["id"])


def _reference_list_in(d: Path) -> list[dict]:
    """`assets.list_in` as written over `iterdir` before the scandir rewrite."""
    if not d.exists():
        return []
    best: dict[str, Path] = {}
    for p in sorted(d.iterdir()):
        if p.is_file() and assets._norm_ext(p.suffix) and assets._addressable_name(p.stem):
            cur = best.get(p.stem)
            if cur is None or ((assets._mtime_ns(p), p.name)
                               > (assets._mtime_ns(cur), cur.name)):
                best[p.stem] = p
    out = []
    for name in sorted(best):
        p = best[name]
        out.append({"name": name, "ext": p.suffix.lstrip(".").lower(),
                    "v": assets.image_version(p)})
    return out


# ---- helpers ---------------------------------------------------------------

def _age(root: Path) -> None:
    """Back-date every file AND directory under `root` past the racy window.
    Directories too: their stamps vouch for listings, and a fresh one is never
    memoized."""
    old = time.time_ns() - 3 * statcache.RACY_WINDOW_NS
    for p in [root, *root.rglob("*")]:
        os.utime(p, ns=(old, old))


def _bump(p: Path, seconds: int = 2) -> None:
    """Move `p`'s mtime to a different, still-old value."""
    st = p.stat()
    t = st.st_mtime_ns - seconds * 1_000_000_000
    os.utime(p, ns=(t, t))


def _rewrite_in_place(p: Path, text: str) -> None:
    """Truncate-and-write the SAME inode, as a text editor or sync client may:
    no directory entry changes."""
    ino = p.stat().st_ino
    with open(p, "r+", encoding="utf-8") as f:
        f.seek(0)
        f.truncate()
        f.write(text)
    assert p.stat().st_ino == ino


def _same_size(old: str, new: str) -> str:
    assert len(old.encode()) == len(new.encode()), (old, new)
    return new


@pytest.fixture
def realm(monkeypatch, tmp_path):
    """A world with a varied cast: a multi-version character with a gallery,
    a crop, a tagline and a voice anchor; one with no art at all; and one
    whose avatar has a stale sibling of another extension."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    root = worlds.world_root(wid)
    ser, sv = characters.create_character(root, "Séraphine")
    alt = characters.create_version(root, ser, "Saltmarch", characters.blank_card("Séraphine"))
    assets.put_image(root, ser, sv, assets.AVATAR, PNG + b"s", "png")
    assets.put_image(root, ser, sv, "gallery_1", PNG + b"g1", "png")
    assets.put_image(root, ser, sv, "embed-map", PNG + b"e", "png")
    assets.write_focus(root, ser, sv, 30)
    taglines.write(root, ser, "Keeps the Saltmarch ledgers.")
    voice_anchors.write(root, ser, "Speaks in ledgers and tides.")
    mara, _ = characters.create_character(root, "Mara")
    win, wv = characters.create_character(root, "Winifred")
    assets.put_image(root, win, wv, assets.AVATAR, PNG + b"w", "png")
    (root / "characters" / win / "assets" / wv / "avatar.gif").write_bytes(b"GIF89a-old")
    (root / "characters" / "dossier-only").mkdir()      # no character.md: never listed
    _age(root)
    return {"wid": wid, "root": root, "ser": ser, "sv": sv, "alt": alt,
            "mara": mara, "win": win, "wv": wv}


def _check(root: Path) -> list[dict]:
    got = characters.list_characters(root)
    assert got == _reference_list(root)
    return got


def _held(root: Path) -> list[dict]:
    """`_check`, and then insist every listed row is IN the memo -- so the
    mutation that follows is one the memo has to notice, rather than one a
    recompute would have picked up anyway."""
    got = _check(root)
    chars = os.fspath(root / "characters")
    assert {(chars, r["id"]) for r in got} <= set(characters._ROW_POOL)
    return got


# ---- the memo is exact ------------------------------------------------------

def test_a_warm_listing_equals_the_reference_and_is_really_memoized(realm):
    root = realm["root"]
    first = _check(root)
    assert [r["id"] for r in first] == sorted([realm["ser"], realm["mara"], realm["win"]])
    # Every listed row was stored -- the rest of this file tests invalidation,
    # and invalidating an empty memo would prove nothing.
    chars = os.fspath(root / "characters")
    assert {(chars, r["id"]) for r in first} <= set(characters._ROW_POOL)
    assert _check(root) == first


def test_a_warm_listing_opens_no_file_and_stats_only_what_feeds_it(realm):
    root = realm["root"]
    characters.list_characters(root)
    feeding = sum(1 for p in (root / "characters").rglob("*")
                  if p.name != "descriptions.json") + 1
    opened: list[str] = []
    stats: list[str] = []
    real_open, real_stat = io.open, os.stat

    def counting_open(file, *a, **kw):
        opened.append(os.fspath(file) if isinstance(file, (str, os.PathLike)) else repr(file))
        return real_open(file, *a, **kw)

    def counting_stat(path, *a, **kw):
        stats.append(os.fspath(path) if isinstance(path, (str, os.PathLike)) else repr(path))
        return real_stat(path, *a, **kw)

    io.open = builtins.open = counting_open
    os.stat = counting_stat
    try:
        characters.list_characters(root)
    finally:
        io.open = builtins.open = real_open
        os.stat = real_stat
    assert opened == []                              # not one character.md, card or sidecar
    assert len(stats) <= feeding                     # O(files that feed rows), each at most once
    assert len(stats) == len(set(stats))


def test_rows_are_handed_out_as_copies(realm):
    root = realm["root"]
    rows = characters.list_characters(root)
    rows[0]["greeting_count"] += 99
    rows[0]["versions"][0]["name"] = "scribbled"
    assert characters.list_characters(root) == _reference_list(root)


def test_create_rename_and_delete_a_character(realm):
    root = realm["root"]
    _check(root)
    cid, _ = characters.create_character(root, "Winifred of Saltmarch")
    assert cid in {r["id"] for r in _check(root)}
    _age(root)
    _check(root)
    characters.set_name(root, realm["mara"], "Mara Vell")
    assert next(r for r in _check(root) if r["id"] == realm["mara"])["name"] == "Mara Vell"
    _age(root)
    _check(root)
    characters.delete_character(root, cid)
    assert cid not in {r["id"] for r in _check(root)}


def test_add_and_remove_a_version_and_move_the_default(realm):
    root = realm["root"]
    _check(root)
    era = characters.create_version(root, realm["mara"], "Older", characters.blank_card("Mara"))
    _check(root)
    _age(root)
    _check(root)
    characters.set_default_version(root, realm["mara"], era)
    assert next(r for r in _check(root) if r["id"] == realm["mara"])["default_version"] == era
    _age(root)
    _check(root)
    characters.delete_version(root, realm["mara"], era)
    _check(root)


def test_add_replace_and_remove_an_image(realm):
    root, ser, sv = realm["root"], realm["ser"], realm["sv"]
    _check(root)
    assets.put_image(root, ser, sv, "gallery_2", PNG + b"g2", "png")
    _check(root)
    _age(root)
    before = _check(root)
    assets.put_image(root, ser, sv, assets.AVATAR, PNG + b"a-different-avatar", "webp")
    after = _check(root)
    assert (next(r for r in after if r["id"] == ser)["avatar_v"]
            != next(r for r in before if r["id"] == ser)["avatar_v"])
    _age(root)
    _check(root)
    assets.delete_image(root, ser, sv, "gallery_1")
    _check(root)
    assets.delete_image(root, ser, sv, assets.AVATAR)
    assert next(r for r in _check(root) if r["id"] == ser)["has_avatar"] is False


def test_an_avatar_overwritten_in_place_moves_its_token(realm):
    """No directory entry changes: only the avatar file's own stamp can tell."""
    root, win, wv = realm["root"], realm["win"], realm["wv"]
    before = next(r for r in _held(root) if r["id"] == win)["avatar_v"]
    p = root / "characters" / win / "assets" / wv / "avatar.png"
    with open(p, "r+b") as f:
        f.write(b"\x89PNG-rewritten-in-place-and-longer")
    _bump(p)
    after = next(r for r in _check(root) if r["id"] == win)["avatar_v"]
    assert after != before


def test_a_stale_sibling_rewritten_in_place_can_win_the_avatar(realm):
    """Every file that could answer for `avatar` is stamped, not just the one
    that answered last time: newest wins, and an in-place edit can change which
    one is newest without touching the directory."""
    root, win, wv = realm["root"], realm["win"], realm["wv"]
    _held(root)
    gif = root / "characters" / win / "assets" / wv / "avatar.gif"
    with open(gif, "r+b") as f:
        f.write(b"GIF89a-new")
    t = time.time_ns() - 2 * statcache.RACY_WINDOW_NS     # newer than the aged png
    os.utime(gif, ns=(t, t))
    row = next(r for r in _check(root) if r["id"] == win)
    assert row["avatar_v"] == assets.image_version(gif)


def test_character_md_rewritten_in_place_same_size_new_mtime(realm):
    root, mara = realm["root"], realm["mara"]
    _held(root)
    meta = root / "characters" / mara / "character.md"
    old = meta.read_text(encoding="utf-8")
    _rewrite_in_place(meta, _same_size(old, old.replace("Mara", "MARA")))
    _bump(meta)
    assert next(r for r in _check(root) if r["id"] == mara)["name"] == "MARA"


@pytest.mark.skipif(os.name == "nt", reason="st_ctime is the creation time on Windows")
def test_a_same_size_rewrite_that_restores_the_old_mtime_is_still_seen(realm):
    """mtime, size and inode all come back as they were -- a sync client
    restoring the origin's timestamp. Only ctime moved, and the stamp has it."""
    root, mara = realm["root"], realm["mara"]
    _held(root)
    meta = root / "characters" / mara / "character.md"
    st = meta.stat()
    old = meta.read_text(encoding="utf-8")
    time.sleep(0.01)          # a ctime tick apart from the aging write
    _rewrite_in_place(meta, _same_size(old, old.replace("Mara", "mara")))
    os.utime(meta, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert (meta.stat().st_mtime_ns, meta.stat().st_size) == (st.st_mtime_ns, st.st_size)
    assert next(r for r in _check(root) if r["id"] == mara)["name"] == "mara"


def test_a_card_edited_in_place_relabels_its_version(realm):
    root, ser, alt = realm["root"], realm["ser"], realm["alt"]
    _held(root)
    card = characters.read_card(root, ser, alt)
    card["data"]["extensions"]["grimoire_label"] = "After the flood"
    card["data"]["alternate_greetings"] = ["one", "two"]
    _rewrite_in_place(characters._card_path(root, ser, alt), characters._dumps(card))
    _bump(characters._card_path(root, ser, alt))
    row = next(r for r in _check(root) if r["id"] == ser)
    assert {"id": alt, "name": "After the flood"} in row["versions"]


@pytest.mark.skipif(os.name == "nt", reason="st_ctime is the creation time on Windows")
def test_a_card_rewritten_at_its_old_mtime_relabels_its_version(realm):
    """The row notices the card's ctime moving; the card summary it rebuilds
    from has to notice it too, or the rebuild re-serves the old label and the
    row stores it under the new stamps."""
    root, ser, alt = realm["root"], realm["ser"], realm["alt"]
    _held(root)
    p = characters._card_path(root, ser, alt)
    st = p.stat()
    old = p.read_text(encoding="utf-8")
    time.sleep(0.01)          # a ctime tick apart from the aging write
    _rewrite_in_place(p, _same_size(old, old.replace("Saltmarch", "SALTMARCH")))
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert (p.stat().st_mtime_ns, p.stat().st_size) == (st.st_mtime_ns, st.st_size)
    row = next(r for r in _check(root) if r["id"] == ser)
    assert {"id": alt, "name": "SALTMARCH"} in row["versions"]


def test_tagline_and_voice_anchor_arrive_change_and_leave(realm):
    root, mara, ser = realm["root"], realm["mara"], realm["ser"]
    _check(root)
    taglines.write(root, mara, "Rows the ferry.")
    voice_anchors.write(root, mara, "Short, dry answers.")
    row = next(r for r in _check(root) if r["id"] == mara)
    assert (row["tagline"], row["has_voice_anchor"]) == ("Rows the ferry.", True)
    _age(root)
    _held(root)
    p = taglines.tagline_path(root, mara)
    _rewrite_in_place(p, _same_size(p.read_text(encoding="utf-8"), "Rows the barge.\n"))
    _bump(p)
    assert next(r for r in _check(root) if r["id"] == mara)["tagline"] == "Rows the barge."
    voice_anchors.write(root, ser, "")
    assert next(r for r in _check(root) if r["id"] == ser)["has_voice_anchor"] is False


def test_a_crop_written_and_edited_in_place(realm):
    root, win, wv = realm["root"], realm["win"], realm["wv"]
    _check(root)
    assets.write_focus(root, win, wv, 10)
    _age(root)
    assert next(r for r in _held(root) if r["id"] == win)["avatar_focus"] == 10
    focus = root / "characters" / win / "assets" / wv / assets.FOCUS_FILE
    _rewrite_in_place(focus, _same_size(focus.read_text(encoding="utf-8"),
                                        json.dumps({"avatar": 90})))
    _bump(focus)
    assert next(r for r in _check(root) if r["id"] == win)["avatar_focus"] == 90


def test_a_write_inside_the_racy_window_is_never_served_stale(realm):
    """Same size, same second: nothing but the racy window keeps the first
    answer out of the memo."""
    root, mara = realm["root"], realm["mara"]
    p = taglines.tagline_path(root, mara)
    for text in ("Salt.", "Tide."):
        p.write_text(text, encoding="utf-8")
        assert next(r for r in _check(root) if r["id"] == mara)["tagline"] == text


def test_an_art_folder_that_appears_later_is_noticed(realm):
    """Mara has no `assets/` at all, so her row is vouched for by her
    directory; the folder arriving two levels down must still be seen."""
    root, mara = realm["root"], realm["mara"]
    assert next(r for r in _check(root) if r["id"] == mara)["has_avatar"] is False
    vid = characters.default_version(root, mara)
    (root / "characters" / mara / "assets").mkdir()
    _age(root)
    _held(root)
    assets.put_image(root, mara, vid, assets.AVATAR, PNG + b"m", "png")
    assert next(r for r in _check(root) if r["id"] == mara)["has_avatar"] is True


def test_a_stranded_promotion_is_healed_and_never_remembered(realm):
    root, mara = realm["root"], realm["mara"]
    vid = characters.default_version(root, mara)
    d = root / "characters" / mara / "assets" / vid
    d.mkdir(parents=True)
    (d / "promote-tmp.png").write_bytes(PNG + b"stranded")
    _age(root)
    row = next(r for r in _check(root) if r["id"] == mara)
    assert row["has_avatar"] is True                  # adopted as the avatar
    assert not (d / "promote-tmp.png").exists()


def test_the_campaign_listing_matches_its_reference_across_the_overlay(realm):
    """Tombstones, a detachment, campaign-side art and crop and tagline: the
    patched rows from per-root facts against the three resolvers."""
    wid, root, ser, sv, win, wv, mara = (realm[k] for k in
                                          ("wid", "root", "ser", "sv", "win", "wv", "mara"))
    cid = campaigns.create_campaign("Saltmarch", wid)
    croot = campaigns.campaign_root(cid)
    assert overlay.list_characters(cid) == _reference_campaign(cid)
    # a campaign tagline, campaign art over an inherited character, a crop
    taglines.write(croot, mara, "Rows the campaign's ferry.")
    assets.put_image(croot, ser, sv, "gallery_9", PNG + b"c9", "png")
    assets.write_focus(croot, win, wv, 70)
    overlay.add_deleted(cid, f"assets/characters/{ser}/{sv}/gallery_1")
    _age(croot)
    _age(root)
    assert overlay.list_characters(cid) == _reference_campaign(cid)
    assert overlay.list_characters(cid) == _reference_campaign(cid)       # warm
    held = {k[1:4] for k in characters._FACTS_POOL}
    assert {(os.fspath(r), ser, sv) for r in (croot, root)} <= held       # both roots' facts
    # the world moves under a warm campaign memo
    assets.put_image(root, ser, sv, "gallery_3", PNG + b"g3", "png")
    taglines.write(root, win, "Keeps the lamp.")
    assert overlay.list_characters(cid) == _reference_campaign(cid)
    _age(root)
    overlay.list_characters(cid)
    # detaching severs the world's facts for that record
    overlay.add_detached(cid, f"characters/{win}")
    assert overlay.list_characters(cid) == _reference_campaign(cid)
    # a campaign-owned avatar file of an unlisted extension owns the crop,
    # though no `avatar` appears among the images
    d = croot / "characters" / ser / "assets" / sv
    (d / "avatar.bmp").write_bytes(b"BM")
    got = overlay.list_characters(cid)
    assert got == _reference_campaign(cid)
    assert next(r for r in got if r["id"] == ser)["avatar_focus"] is None
    (d / "avatar.bmp").unlink()
    assert next(r for r in overlay.list_characters(cid) if r["id"] == ser)["avatar_focus"] == 30
    # ...and so does a tombstone over the world's avatar
    overlay.add_deleted(cid, f"assets/characters/{ser}/{sv}/{assets.AVATAR}")
    got = overlay.list_characters(cid)
    assert got == _reference_campaign(cid)
    assert next(r for r in got if r["id"] == ser)["avatar_focus"] is None


# ---- assets.list_in over scandir ------------------------------------------

def test_list_in_matches_the_iterdir_implementation(tmp_path):
    d = tmp_path / "art"
    d.mkdir()
    old = time.time_ns() - 10 * statcache.RACY_WINDOW_NS
    for name, data, age in [
        ("avatar.png", b"a-old", 3), ("avatar.gif", b"a-new", 1),     # newest sibling wins
        ("gallery_1.png", b"tie", 2), ("gallery_1.webp", b"tie", 2),  # equal mtime: name decides
        ("Gallery_2.PNG", b"upper", 1), ("embed-x.JpEg", b"mixed", 1),
        ("promote-tmp.png", b"stranded", 1),                          # shown on purpose (#253)
        ("a.b.png", b"dotted", 1), ("bad[1].png", b"glob", 1), (".png", b"dotfile", 1),
        ("noext", b"x", 1), ("notes.txt", b"x", 1),
    ]:
        p = d / name
        p.write_bytes(data)
        t = old + (10 - age) * 1_000_000_000
        os.utime(p, ns=(t, t))
    (d / "folder.png").mkdir()                                        # not a file
    expected = ["Gallery_2", "avatar", "embed-x", "gallery_1", "promote-tmp"]
    if os.name != "nt":      # Win32 trims a trailing dot, and links need elevation
        (d / "trail.").write_bytes(b"x")
        (d / "link.png").symlink_to(d / "avatar.png")                 # a file, through a link
        (d / "broken.png").symlink_to(d / "nowhere.png")              # not a file
        expected = sorted([*expected, "link"])
    assert assets.list_in(d) == _reference_list_in(d)
    assert [i["name"] for i in assets.list_in(d)] == expected
    assert assets.list_in(tmp_path / "missing") == _reference_list_in(tmp_path / "missing") == []


def test_list_in_still_raises_for_a_path_that_exists_and_cannot_be_listed(tmp_path):
    f = tmp_path / "a-file"
    f.write_bytes(b"x")
    with pytest.raises(NotADirectoryError):
        assets.list_in(f)
    with pytest.raises(NotADirectoryError):
        _reference_list_in(f)


# ---- the routes ------------------------------------------------------------

@pytest.fixture
def client(realm):
    return TestClient(create_app())


def _default_body(rows) -> bytes:
    """What FastAPI renders for a route returning `rows` with no response
    model: `jsonable_encoder`, then its default `JSONResponse`."""
    return json.dumps(jsonable_encoder(rows), ensure_ascii=False, allow_nan=False,
                      indent=None, separators=(",", ":")).encode("utf-8")


def test_the_world_roster_body_is_byte_identical_to_the_encoder_path(realm, client):
    root = realm["root"]
    greetings.create_greeting(root, "Ferry at dawn", realm["mara"], "",
                              present=[realm["mara"], realm["ser"]])
    r = client.get(f"/api/worlds/{realm['wid']}/characters")
    assert r.status_code == 200
    expected = greetings.add_featuring_counts(_reference_list(root),
                                              greetings.list_greetings(root))
    assert r.content == _default_body(expected)
    assert "Séraphine".encode() in r.content                    # not \u-escaped


def test_the_campaign_roster_body_is_byte_identical_to_the_encoder_path(realm, client):
    cid = campaigns.create_campaign("Saltmarch", realm["wid"])
    r = client.get(f"/api/campaigns/{cid}/characters")
    assert r.status_code == 200
    expected = greetings.add_featuring_counts(_reference_campaign(cid),
                                              overlay.list_greetings(cid))
    assert r.content == _default_body(expected)


def test_world_meta_counts_the_campaigns_played_in_it(realm, client):
    wid = realm["wid"]
    assert client.get(f"/api/worlds/{wid}").json()["campaigns"] == 0
    campaigns.create_campaign("Saltmarch", wid)
    assert client.get(f"/api/worlds/{wid}").json()["campaigns"] == 1
    campaigns.create_campaign("Winifred's Run", wid)
    campaigns.create_campaign("Mara's Run", wid)
    other = worlds.create_world("Saltmarch")
    campaigns.create_campaign("Seraphine's Run", other)
    body = client.get(f"/api/worlds/{wid}").json()
    assert body["campaigns"] == 3
    # the count the world page used to derive from the whole shelf
    assert body["campaigns"] == sum(1 for c in client.get("/api/campaigns").json()
                                    if c["world"] == wid)
    assert client.get(f"/api/worlds/{other}").json()["campaigns"] == 1
    assert set(body) >= {"meta", "body", "counts", "campaigns"}   # additive


def _stranded_version_art(root: Path, cid: str) -> None:
    """Art under a version that no longer exists -- the queue must leave it
    out, and so must its count."""
    d = root / "characters" / cid / "assets" / "gone-version"
    d.mkdir(parents=True)
    (d / "gallery_1.png").write_bytes(PNG)


def test_the_world_queue_count_is_the_length_of_the_list(realm, client):
    wid, root = realm["wid"], realm["root"]
    _stranded_version_art(root, realm["ser"])
    (root / "characters" / "ghost" / "assets" / "default").mkdir(parents=True)
    (root / "characters" / "ghost" / "assets" / "default" / "avatar.png").write_bytes(PNG)
    image_descriptions.set_description(root, realm["ser"], realm["sv"], "gallery_1", "")
    queue = client.get(f"/api/worlds/{wid}/images/undescribed").json()
    count = client.get(f"/api/worlds/{wid}/images/undescribed?count=1").json()
    assert count == {"count": len(queue)}
    assert len(queue) > 0
    assert not any(i["vid"] == "gone-version" or i["id"] == "ghost" for i in queue)
    # the backlog now walks names, not the gallery's catalog -- same images
    assert image_descriptions.undescribed(root, "characters") == [
        {"id": i["id"], "vid": i["vid"], "name": i["name"]}
        for i in image_descriptions.catalog(root, "characters") if not i["described"]]


def test_the_campaign_queue_count_is_the_length_of_the_list(realm, client):
    cid = campaigns.create_campaign("Saltmarch", realm["wid"])
    croot = campaigns.campaign_root(cid)
    ser, sv = realm["ser"], realm["sv"]
    overlay.materialize_actor(cid, "characters", ser)
    assets.put_image(croot, ser, sv, "gallery_7", PNG + b"c", "png")
    _stranded_version_art(croot, ser)
    client.put(f"/api/campaigns/{cid}/images/harbour",
               files={"file": ("h.png", PNG, "image/png")})
    queue = client.get(f"/api/campaigns/{cid}/images/undescribed").json()
    count = client.get(f"/api/campaigns/{cid}/images/undescribed?count=1").json()
    assert count == {"count": len(queue)}
    assert {(i["kind"], i["name"]) for i in queue} >= {("characters", "gallery_7")}
    assert not any(i["vid"] == "gone-version" for i in queue)


def test_the_queue_names_records_without_reading_a_card(realm, client, monkeypatch):
    """The light lookup: the name and version ids, never a full read -- so a
    malformed card cannot take the queue down with it either."""
    root, ser = realm["root"], realm["ser"]
    characters._card_path(root, ser, realm["alt"]).write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(characters, "read_character",
                        lambda *a: pytest.fail("the queue read a whole character"))
    queue = client.get(f"/api/worlds/{realm['wid']}/images/undescribed").json()
    assert {i["record_name"] for i in queue if i["id"] == ser} == {"Séraphine"}


# ---- statcache.memo_stamped -------------------------------------------------

def test_memo_stamped_reuses_until_a_stamp_moves(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("one", encoding="utf-8")
    _age(tmp_path)
    pool: dict = {}
    calls = []

    def compute():
        s = statcache.stamp(p)
        calls.append(1)
        return p.read_text(encoding="utf-8"), (s,)

    assert statcache.memo_stamped("k", compute, pool=pool, max_entries=4) == "one"
    assert statcache.memo_stamped("k", compute, pool=pool, max_entries=4) == "one"
    assert len(calls) == 1
    p.write_text("two", encoding="utf-8")
    assert statcache.memo_stamped("k", compute, pool=pool, max_entries=4) == "two"


def test_memo_stamped_stores_nothing_racy_or_unvouched(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("fresh", encoding="utf-8")
    pool: dict = {}
    statcache.memo_stamped("racy", lambda: ("v", (statcache.stamp(p),)), pool=pool, max_entries=4)
    statcache.memo_stamped("none", lambda: ("v", None), pool=pool, max_entries=4)
    assert pool == {}


def test_memo_stamped_stays_bounded(tmp_path):
    _age(tmp_path)
    pool: dict = {}
    for i in range(10):
        statcache.memo_stamped(i, lambda: ("v", (statcache.stamp(tmp_path),)),
                               pool=pool, max_entries=4)
    assert len(pool) <= 4


def test_an_unreadable_campaign_costs_the_count_not_the_world_page(realm, client):
    """The count is the one field of GET /worlds/{wid} that reads outside the
    world, and `list_campaigns` raises on a campaign.md it cannot read. Before
    the count moved here the page learned it from its own GET /campaigns and
    drew a dash when that failed; the world read itself never depended on
    another folder. It still must not: an unknown count is None, which the page
    renders as unknown, and the rest of the world answers as before."""
    wid = realm["wid"]
    cid = campaigns.create_campaign("Saltmarch", wid)
    mp = campaigns.campaign_root(cid) / "campaign.md"
    mp.unlink()
    mp.mkdir()                      # present, so listed -- and unreadable
    r = client.get(f"/api/worlds/{wid}")
    assert r.status_code == 200
    body = r.json()
    assert body["campaigns"] is None
    assert body["meta"]["name"] == "Realm"
