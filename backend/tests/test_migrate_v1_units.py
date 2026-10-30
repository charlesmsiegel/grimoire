"""Units of the V0.2.1 inventory: V1 text rules, transcript roles, the walker and purity."""

import hashlib
import os
from pathlib import Path

import pytest

import purity
from grimoire.migrate_v1 import _scenes, _text
from grimoire.migrate_v1._fs import Snapshot, SourceChanged
from grimoire.migrate_v1._layout import classify

# ---- V1 text rules ---------------------------------------------------------------------------


@pytest.mark.parametrize("text, meta, body", [
    ("---\nname: A\n---\n\nBody", {"name": "A"}, "Body"),
    ("---\r\nname: A\r\n---\r\n\r\nBody", {"name": "A"}, "Body"),
    ("---\n---\n\n", {}, ""),
    ("---\nname: A\nnever closed", {}, "---\nname: A\nnever closed"),
    ("no fence", {}, "no fence"),
    ("---\nk: 'it''s: quoted'\nno colon line\n---\n", {"k": "it's: quoted"}, ""),
    ("---\nk: v\n----\n---\nb", {"k": "v"}, "b"),
])
def test_frontmatter_matches_v1(text, meta, body):
    assert _text.parse_frontmatter(text) == (meta, body)


def test_hashes_follow_v1_newline_normalization():
    assert _text.v1_hash(_text.decode(b"a\r\nb\rc")) == hashlib.sha256(b"a\nb\nc").hexdigest()
    with pytest.raises(_text.Undecodable):
        _text.decode(b"\xff")


@pytest.mark.parametrize("payload", [b"[" * 100000, b"1" * 5000, b"\xef\xbb\xbf{}", b"\xff"])
def test_json_loader_never_crashes(payload):
    value, problem = _text.load_json(payload)
    assert value is None and problem in ("malformed_json", "invalid_utf8")


@pytest.mark.parametrize("value, ok", [
    ("mara", True), ("Mara", True), ("", False), (".", False), ("..", False), ("a/b", False),
    ("a\\b", False), ("c:x", False), ("trail.", False), ("trail ", False), (3, False)])
def test_safe_id_matches_v1(value, ok):
    assert _text.safe_id(value) is ok


def test_collision_groups():
    normal, case = _text.collision_groups(["café", "café", "A", "a", "b"])
    assert normal == [["café", "café"]]
    assert case == [["A", "a"]]


def test_image_urls_and_redaction():
    text = '![a](/api/x.png) ![b](<https://u:p@host:8/p/q.png?token=T#f> "t") ![](https://h/y.png \'t\')'
    assert _text.image_urls(text) == ["/api/x.png", "https://u:p@host:8/p/q.png?token=T#f", "https://h/y.png"]
    assert _text.redact_url("https://u:p@host:8/p/q.png?token=T#f") == "https://host:8/p/q.png"
    assert _text.decoded_names("a%20b") == ["a b", "a%20b"]


def test_escape_neutralizes_terminal_controls():
    assert _text.escape("a\x1b[1m‮\udcff\\") == "a\\u001b[1m\\u202e\\udcff\\\\"


# ---- transcript roles (port of V1 scenes/serialize.py) ---------------------------------------

def roles(body, players):
    return [(p.speaker, p.role) for p in _scenes.parse_posts(body, players)[0]]


def test_word_boundary_prefix_names_a_player():
    assert roles("**Winifred:** hi", ["Winifred Vance"]) == [("Winifred", "user")]


def test_an_ambiguous_prefix_is_not_a_player():
    assert roles("**Winifred:** hi", ["Winifred Vance", "Winifred Vale"]) == [("Winifred", "assistant")]


def test_reserved_labels_hand_the_post_to_the_sub_speaker():
    assert roles("**You (Bob):** a\n\n**Grimoire:** b", []) == [("Bob", "user"), (None, "assistant")]


def test_markers_count_only_at_paragraph_starts_and_the_preamble_is_reported():
    posts, preamble = _scenes.parse_posts("lost text\n\n**A:** one **B:** inline\n**C:** same para", [])
    assert [p.speaker for p in posts] == ["A"]
    assert preamble == "lost text\n\n"


def test_metadata_is_stripped_only_when_it_parses():
    good, _ = _scenes.parse_posts('**A:** <!-- grimoire-response {"post_id": "p", "x": 1} -->\nText', [])
    assert (good[0].post_id, good[0].content, good[0].unknown_metadata) == ("p", "Text", ["x"])
    bad, _ = _scenes.parse_posts("**A:** <!-- grimoire-response {nope} -->\nText", [])
    assert bad[0].content.startswith("<!--") and bad[0].post_id is None


def test_synthetic_speakers_are_flagged():
    [post], _ = _scenes.parse_posts("**⁣Roll:** 4", [])
    assert post.synthetic and post.role == "assistant"


@pytest.mark.parametrize("sid, grammar", [("001--a", "modern"), ("12--2026-01-01--a", "modern"),
                                          ("2026-01-01-a", "legacy_date"), ("a-b", "invalid"),
                                          ("²--a", "invalid")])
def test_scene_id_grammar(sid, grammar):
    assert _scenes.sid_grammar(sid)[0] == grammar


# ---- layout ----------------------------------------------------------------------------------

@pytest.mark.parametrize("path, disposition, family", [
    ("llm_connections", "excluded", "excluded"),
    ("config.md", "interpreted", "config"),
    ("calendars/hebrew.py", "recognized_unsupported", "settings"),
    ("worlds/w/world.md", "interpreted", "world_meta"),
    ("worlds/w/lore/x.md", "interpreted", "flat_record"),
    ("worlds/w/characters/c/v.json", "interpreted", "card"),
    ("worlds/w/pcs/p/v.md", "interpreted", "persona"),
    ("worlds/w/characters/c/assets/v/image-refs/avatar.json", "interpreted", "placement"),
    ("worlds/w/characters/c/assets/v/image-refs/.promote.json", "interpreted", "promote_journal"),
    ("worlds/w/characters/c/assets/v/old.PNG", "hashed", "legacy_image"),
    ("worlds/w/characters/c/assets/v/expressions/revisions/r/o/image-refs/e.json", "interpreted", "placement"),
    ("worlds/w/assets/images/image-refs/coast.json", "interpreted", "placement"),
    ("worlds/w/assets/image-collections/c.json", "interpreted", "collection"),
    ("campaigns/c/scenes/001--a.md", "interpreted", "scene"),
    ("campaigns/c/scenes/001--a.alts.json", "recognized_unsupported", "scene_alts"),
    ("campaigns/c/knowledge.json", "recognized_unsupported", "campaign_state"),
    ("campaigns/c/scene-history/i/p.json", "recognized_unsupported", "campaign_state"),
    ("assets/image-store/blobs/ab/ab.png", "hashed", "blob"),
    ("assets/image-store/objects/ab/px1-ab.json", "interpreted", "image_object"),
    ("worlds/w/Thumbs.db", "ignored", "os_litter"),
    ("worlds/w/random.bin", "unknown", "unknown"),
])
def test_layout(path, disposition, family):
    layout = classify(tuple(path.split("/")), False)
    assert (layout.disposition, layout.family) == (disposition, family)


# ---- walker ----------------------------------------------------------------------------------

def make(root: Path) -> Path:
    (root / "worlds" / "w").mkdir(parents=True)
    (root / "worlds" / "w" / "world.md").write_text("---\nname: W\n---\n", encoding="utf-8")
    return root


@pytest.mark.parametrize("path_walk", [False, True])
def test_a_file_replaced_after_the_walk_is_never_returned(tmp_path, path_walk):
    from grimoire.migrate_v1._fs import Options
    root = make(tmp_path / "v1")
    snap = Snapshot.take(str(root), Options(path_walk=path_walk))
    try:
        target = root / "worlds" / "w" / "world.md"
        target.unlink()
        target.write_text("---\nname: Swapped\n---\n", encoding="utf-8")
        with pytest.raises(SourceChanged):
            snap.read("worlds/w/world.md")
    finally:
        snap.close()


def test_a_directory_swapped_for_a_link_is_never_entered(tmp_path):
    root = make(tmp_path / "v1")
    elsewhere = make(tmp_path / "elsewhere")
    snap = Snapshot.take(str(root))
    try:
        os.rename(root / "worlds", tmp_path / "moved")
        try:
            (root / "worlds").symlink_to(elsewhere / "worlds", target_is_directory=True)
        except OSError:
            pytest.skip("symlinks cannot be created here")
        with pytest.raises(SourceChanged):
            snap.read("worlds/w/world.md")
    finally:
        snap.close()


# ---- purity of the pure modules --------------------------------------------------------------

#: `json` and `urllib.parse` compute, but sit outside the shared pure allowlist,
#: so these two modules are declared as parsing edges that may use them -- and
#: nothing else: files, the OS, clocks and the network stay banned.
PURE_MODULES = purity.PurePackage(
    "grimoire.migrate_v1", ("grimoire.migrate_v1",),
    io_modules=frozenset({"_text.py", "_scenes.py"}), io_stdlib=frozenset({"json", "urllib.parse"}))


@pytest.mark.parametrize("module", ["_text.py", "_layout.py", "_scenes.py"])
def test_the_text_layout_and_transcript_rules_are_pure(module):
    path = purity.GRIMOIRE / "migrate_v1" / module
    problems = purity.boundary_problems(path.read_text(encoding="utf-8"), f"grimoire/migrate_v1/{module}",
                                        PURE_MODULES, "grimoire.migrate_v1")
    assert problems == []
