from pathlib import Path

from grimoire.files.hashing import content_hash


def test_deterministic_for_same_text() -> None:
    assert content_hash("hello") == content_hash("hello")


def test_distinct_for_different_text() -> None:
    assert content_hash("hello") != content_hash("world")


def test_normalizes_line_endings() -> None:
    assert content_hash("a\nb\nc") == content_hash("a\r\nb\r\nc")
    assert content_hash("a\nb\nc") == content_hash("a\rb\rc")


def test_accepts_str_bytes_and_path(tmp_path: Path) -> None:
    text = "id: x\nname: y\n"
    path = tmp_path / "x.yaml"
    path.write_text(text, encoding="utf-8")
    assert content_hash(text) == content_hash(text.encode("utf-8")) == content_hash(path)


def test_hex_sha256_format() -> None:
    h = content_hash("anything")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
