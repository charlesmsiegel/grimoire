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


#: The campaign-side capsule (#62) reads a character's first scene as well as
#: the card, because an emergent NPC's card is nearly empty. What is sent is the
#: excerpt from the character's entrance (the caller starts it there): a bounded
#: prefix of that, not the whole transcript.
CAPSULE_SOURCE_CHARS = 4000
TAGS_MAX = 8
TAG_MAX_CHARS = 40

_CAPSULE_LABEL = "capsule:"
_TAGS_LABEL = "tags:"


def build_capsule_prompt(card_data: dict, transcript: str = "") -> list[dict]:
    """The capsule-and-tags prompt: the tagline's card, plus an excerpt of the
    first scene the character appears in when there is one (#62)."""
    user = prompts.render("tagline/user.j2", card=card_data)
    excerpt = transcript.strip()[:CAPSULE_SOURCE_CHARS]
    if excerpt:
        user = f"{user.rstrip()}\n\nThe first scene this character appears in:\n{excerpt}"
    return [{"role": "system", "content": prompts.render("tagline/capsule_system.j2")},
            {"role": "user", "content": user}]


def parse_capsule_output(text: str) -> dict:
    """Read a capsule-and-tags reply by its labels.

    A reply with no `Capsule:` label still yields its first unlabelled line as
    the capsule, so a model that ignores the format is not a failed draft. Tags
    are cleaned, de-duplicated (case-insensitively) and capped; an overlong tag
    is dropped rather than truncated, because a clipped word is not a tag.
    """
    capsule = ""
    loose = ""
    tags: list[str] = []
    for raw in text.strip().split("\n"):
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith(_CAPSULE_LABEL):
            capsule = capsule or line[len(_CAPSULE_LABEL):].strip()
        elif low.startswith(_TAGS_LABEL):
            tags = tags or _clean_tags(line[len(_TAGS_LABEL):])
        else:
            loose = loose or line
    return {"capsule": capsule or loose, "tags": tags}


def _clean_tags(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for part in text.split(","):
        tag = part.strip().strip("\"'").strip().rstrip(".").strip()
        if not tag or len(tag) > TAG_MAX_CHARS or tag.casefold() in seen:
            continue
        seen.add(tag.casefold())
        out.append(tag)
        if len(out) == TAGS_MAX:
            break
    return out


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
