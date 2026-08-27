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


def untagged_ids(root: Path, char_ids: list[str]) -> list[str]:
    """Which of `char_ids` have no tagline, without reading a file per character.

    It exists because the to-do list counts this for every character of every
    world on every read. Opening the two sidecars per record instead costs
    orders of magnitude more on a cold cache -- one small file per record,
    spread across the whole store, is the worst shape a filesystem can be
    handed, and the first read after a restart would stall the page the count
    is drawn on.

    Exact, not approximate, and the size test alone is not what makes it so.
    A missing file is untagged; a file comfortably larger than any blank one
    could be has content; and anything in between is opened and asked properly.
    That third case is the one a bare threshold gets wrong -- `write` leaves a
    lone line ending, which is one byte or two depending on the platform, and a
    genuinely short tagline can be smaller than the bound. It is also rare, so
    reading it costs nothing on the sweep this is here for.
    """
    out = []
    for cid in char_ids:
        try:
            size = tagline_path(root, cid).stat().st_size
        except OSError:
            out.append(cid)          # no file at all
            continue
        if size > _AMBIGUOUS_MAX or read(root, cid):
            continue
        out.append(cid)
    return out


#: Above this, a tagline file cannot be one `write` blanked -- it leaves only a
#: line ending. At or below it the file is read rather than guessed at, which
#: is what keeps `untagged_ids` exact rather than nearly right. Deliberately
#: loose: the cost of being generous is opening a handful of tiny files, and
#: the cost of being tight is a wrong answer.
_AMBIGUOUS_MAX = 8
