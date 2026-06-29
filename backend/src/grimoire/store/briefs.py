"""Per-character 'brief': a one-line tagline + a one-paragraph summary derived from
the character's default-version card. Staleness is tracked by `base` — the hash of
the default card the brief was derived from (mirrors appearances' sync base).

Stored at <root>/characters/<cid>/brief.md:
  ---
  tagline: A silent snowleopardgirl who speaks through written notes.
  base: <sha256 of the default-version card>
  ---
  <paragraph body>

Pure file IO + prompt/parse helpers only — the LLM call lives in the route layer.
"""

from __future__ import annotations

from pathlib import Path

from . import characters
from .frontmatter import dump_frontmatter, parse_frontmatter

SUMMARY_INSTRUCTION = (
    "Summarize the following character for a game master's quick reference. "
    "Reply with exactly two parts: the first line is a single-sentence tagline; "
    "then a blank line; then one short paragraph of 3-4 sentences. "
    "Write in third person, present tense. Do not add headings, labels, or quotes."
)


def brief_path(root: Path, cid: str) -> Path:
    return root / "characters" / cid / "brief.md"


def default_card_hash(root: Path, cid: str) -> str | None:
    """Hash of the character's default-version card, or None if the character is absent."""
    try:
        meta = characters.read_character(root, cid)["meta"]
    except characters.CharacterNotFound:
        return None
    return characters.card_hash(root, cid, meta["default_version"])


def read_brief(root: Path, cid: str) -> dict | None:
    p = brief_path(root, cid)
    if not p.exists():
        return None
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {"tagline": meta.get("tagline", ""), "base": meta.get("base", ""), "body": body.strip()}


def write_brief(root: Path, cid: str, tagline: str, body: str, base: str) -> None:
    p = brief_path(root, cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dump_frontmatter({"tagline": tagline, "base": base}, body.strip() + "\n"),
                 encoding="utf-8")


def is_stale(root: Path, cid: str) -> bool:
    """Stale when missing, or when its base != the current default-card hash."""
    b = read_brief(root, cid)
    if b is None:
        return True
    return b["base"] != (default_card_hash(root, cid) or "")


def build_prompt(card_data: dict) -> list[dict]:
    fields = [card_data.get(f, "") for f in ("name", "description", "personality", "scenario")]
    card_text = "\n".join(x for x in fields if x)
    return [{"role": "system", "content": SUMMARY_INSTRUCTION},
            {"role": "user", "content": card_text}]


def parse_output(text: str) -> tuple[str, str]:
    """First non-empty line -> tagline; everything after it -> paragraph body."""
    lines = text.strip().split("\n")
    for i, ln in enumerate(lines):
        if ln.strip():
            return ln.strip(), "\n".join(lines[i + 1:]).strip()
    return "", ""
