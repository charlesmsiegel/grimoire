"""Per-character one-line tagline — world-level identity feeding the off-scene cast's
"Known to exist" tier. Character-level (not per-version), plain text, no staleness hash:
a hand-written tagline must not silently expire when a card changes.

Stored at <root>/characters/<cid>/tagline.md as the trimmed sentence. Pure file IO +
prompt/parse only; the LLM call lives in the route layer and the prompt text in
templates/tagline/. `dossiers.py` records why the campaign-level half of this pair
has no hash either (#57) -- the same absence, arrived at by a different argument.

The rule that reason implies is the one the bulk route
(`POST /worlds/{wid}/characters/taglines/generate`) is built around: a derive across a
world only ever fills a blank. `tagline.md` has exactly two writers -- the PUT behind a
person's edit, and that derive -- and only the first one ever replaces a sentence.
"""

from __future__ import annotations

from pathlib import Path

from .. import prompts
from . import atomic


def tagline_path(root: Path, cid: str) -> Path:
    return root / "characters" / cid / "tagline.md"


def read(root: Path, cid: str) -> str:
    p = tagline_path(root, cid)
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def write(root: Path, cid: str, text: str) -> None:
    p = tagline_path(root, cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(p, text.strip() + "\n")


def build_prompt(card_data: dict) -> list[dict]:
    return [{"role": "system", "content": prompts.render("tagline/system.j2")},
            {"role": "user", "content": prompts.render("tagline/user.j2", card=card_data)}]


def parse_output(text: str) -> str:
    for ln in text.strip().split("\n"):
        if ln.strip():
            return ln.strip()
    return ""
