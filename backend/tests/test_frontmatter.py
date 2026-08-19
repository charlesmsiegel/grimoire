from grimoire.store import dump_frontmatter, parse_frontmatter
from grimoire.store.frontmatter import parse_frontmatter_head


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
