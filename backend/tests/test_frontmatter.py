import pytest

from grimoire.store import dump_frontmatter, parse_frontmatter
from grimoire.store.frontmatter import LINE_BREAKS, parse_frontmatter_head


def test_roundtrip_plain_values():
    text = "---\nmodel: anthropic/claude-opus-4.1\ntheme: occult\n---\n\nbody here\n"
    meta, body = parse_frontmatter(text)
    assert meta == {"model": "anthropic/claude-opus-4.1", "theme": "occult"}
    assert body == "body here\n"


def test_value_needing_quotes_roundtrips():
    meta = {"title": "Chat: part one's tale", "openrouter_key": ""}
    rebuilt, body = parse_frontmatter(dump_frontmatter(meta, "the body\n"))
    assert rebuilt == meta
    assert body == "the body\n"


def test_missing_frontmatter_returns_empty_meta():
    meta, body = parse_frontmatter("no fences here\n")
    assert meta == {}
    assert body == "no fences here\n"


# ---- head-only parsing (list endpoints skip the body) ----
def _roundtrip_head(tmp_path, meta, body):
    p = tmp_path / "f.md"
    p.write_text(dump_frontmatter(meta, body), encoding="utf-8")
    return parse_frontmatter_head(p)


def test_head_matches_full_parse(tmp_path):
    meta = {"title": "A Scene", "model": "gpt", "created": "2026-01-01T00:00:00",
            "updated": "2026-07-04T10:00:00", "quoted": "with: colon", "empty": ""}
    body = "**You:** hello\n\n**Grimoire:** hi\n" * 2000  # a large transcript
    assert _roundtrip_head(tmp_path, meta, body) == meta


def test_head_no_frontmatter_returns_empty(tmp_path):
    p = tmp_path / "f.md"
    p.write_text("just a body\nwith --- inside\n", encoding="utf-8")
    assert parse_frontmatter_head(p) == {}


def test_head_matches_full_parse_on_unterminated_block(tmp_path):
    p = tmp_path / "f.md"
    text = "---\ntitle: t\nno terminator"
    p.write_text(text, encoding="utf-8")
    assert parse_frontmatter_head(p) == parse_frontmatter(text)[0] == {}


def test_four_dashes_are_not_the_closing_fence_of_an_empty_block():
    # The empty-block special case reads the closing fence at rest[0]; a
    # `startswith("---")` there took `----` for it and handed back `-` as the
    # first line of the body. A fence is a whole line, so this is a document
    # with no frontmatter at all.
    text = "---\n----\nbody\n"
    assert parse_frontmatter(text) == ({}, text)


def test_a_fence_is_a_whole_line_in_both_parsers(tmp_path):
    # The same rule on the general path and in the head parser, held against
    # each other: a list endpoint and a full read must not describe one file
    # differently. `----` after a block is a body line (an unterminated block,
    # so no frontmatter); a real `---` later closes it.
    cases = {
        "---\nk: v\n----\nbody\n": ({}, "---\nk: v\n----\nbody\n"),
        "---\n----\nk: v\n---\nbody\n": ({"k": "v"}, "body\n"),
        "---\nk: v\n---x\n---\nbody\n": ({"k": "v"}, "body\n"),
    }
    for text, expected in cases.items():
        assert parse_frontmatter(text) == expected, text
        p = tmp_path / "f.md"
        p.write_text(text, encoding="utf-8")
        assert parse_frontmatter_head(p) == expected[0], text


def test_empty_block_still_parses_with_and_without_a_trailing_newline():
    assert parse_frontmatter("---\n---\nbody\n") == ({}, "body\n")
    assert parse_frontmatter("---\n---\n\nbody\n") == ({}, "body\n")
    assert parse_frontmatter("---\n---") == ({}, "")


# ---- line boundaries (Codex, slice C round 4) ----

def test_line_breaks_are_every_boundary_splitlines_splits_on():
    """The parser splits the block with `str.splitlines`, so the writer's
    notion of a line break has to be exactly that one -- every code point."""
    splits = {c for c in map(chr, range(0x110000))
              if len(f"a{c}b".splitlines()) > 1}
    assert splits == set(LINE_BREAKS)


@pytest.mark.parametrize("brk", sorted(LINE_BREAKS), ids=lambda c: f"U+{ord(c):04X}")
def test_a_value_holding_a_line_break_cannot_add_a_key(brk):
    """A value is one line whatever it holds: a break inside it is written as
    a space, so nothing after it is read back as a key of its own."""
    text = dump_frontmatter({"model": f"vendor/m{brk}inference_format: 3"}, "")
    meta, body = parse_frontmatter(text)
    assert meta == {"model": "vendor/m inference_format: 3"}
    assert body == ""
