"""World tag vocabulary: tag-id -> display name, stored in <world>/tags.md frontmatter."""

from __future__ import annotations

from pathlib import Path

from . import atomic
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import slugify, uniquify


class TagNotFound(Exception):
    pass


def _path(root: Path) -> Path:
    return root / "tags.md"


def read_tags(root: Path) -> dict[str, str]:
    p = _path(root)
    if not p.exists():
        return {}
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return meta


def _write(root: Path, vocab: dict[str, str]) -> None:
    atomic.write_text(_path(root), dump_frontmatter(vocab, ""))


def has_tag(root: Path, tag_id: str) -> bool:
    return tag_id in read_tags(root)


def add_tag(root: Path, name: str) -> str:
    vocab = read_tags(root)
    tag_id = uniquify(slugify(name), lambda c: c in vocab)
    vocab[tag_id] = name
    _write(root, vocab)
    return tag_id


def rename_tag(root: Path, tag_id: str, name: str) -> None:
    vocab = read_tags(root)
    if tag_id not in vocab:
        raise TagNotFound(tag_id)
    vocab[tag_id] = name
    _write(root, vocab)


def delete_tag(root: Path, tag_id: str) -> None:
    vocab = read_tags(root)
    if tag_id not in vocab:
        raise TagNotFound(tag_id)
    del vocab[tag_id]
    _write(root, vocab)
