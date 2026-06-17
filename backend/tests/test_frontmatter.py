from grimoire.store import parse_frontmatter, dump_frontmatter


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
