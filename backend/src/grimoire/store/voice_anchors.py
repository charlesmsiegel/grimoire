"""Per-character voice anchor — the reference sample of how a character SOUNDS,
against which a played scene's dialogue is judged for drift (store/voice_drift.py).

World-level, like tagline.md: a voice is a library property of the character, not
something a campaign owns, so every campaign compares against the same reference
and a fix made once holds everywhere. Campaign-side reads go through
`overlay.voice_anchor()`, which resolves campaign-over-world per file.

Stored at <root>/characters/<char_id>/voice_anchor.md as plain prose. Deliberately a
separate file from the card's `mes_example`: that field is *injected into every
scene* as few-shot dialogue, so editing it to sharpen a comparison changes what
the model is told to imitate. The anchor is read-only reference material — it is
never sent as part of a scene — which is what lets it describe a voice ("clipped,
never uses contractions") as well as demonstrate it.

No staleness hash, for the same reason taglines.py has none: a hand-written
anchor must not silently expire when a card changes.

Pure file IO + prompt/parse only; the LLM call lives in the route layer and the
prompt text in templates/voice_anchor/.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from .. import prompts
from . import atomic, paths
from .frontmatter import dump_frontmatter, parse_frontmatter


class BadAnchorId(Exception):
    """An id that could escape the characters directory."""


def _safe_id(char_id: str) -> bool:
    """Reject ids that could escape OR ALIAS the characters directory. Anchor
    ids arrive on client-supplied route paths, so they are untrusted.

    Delegates to `paths.safe_id` for the reasons spelled out in
    `voice_drift._safe_id`: the separator checks alone let a colon or a trailing
    dot through, and both alias a real character's directory."""
    return paths.safe_id(char_id)


def anchor_path(root: Path, char_id: str) -> Path:
    if not _safe_id(char_id):
        raise BadAnchorId(char_id)
    return root / "characters" / char_id / "voice_anchor.md"


def read_record(root: Path, char_id: str) -> dict:
    """{"text", "id", "disabled"} for the stored anchor; blanks when there is none.

    `disabled` marks a TOMBSTONE -- a file that records "this root has no anchor
    for this character, and does not want one inherited either". It only means
    anything to a root that has something beneath it to inherit from, so in
    practice only campaigns write one (see `overlay.set_voice_anchor`); at world
    level absence and a tombstone are the same state, and both read as no anchor
    because the body is empty either way.

    `id` is a nonce minted when the anchor is first created and PRESERVED across
    edits. It exists so "deleted, then recreated with the same words" is
    distinguishable from "never touched": without it a content-only digest makes
    those identical, and a drift flag suppressed by the deletion springs back to
    life on the restore, citing a standard the user had deliberately retired.

    "" for a legacy anchor written before the field existed — see
    voice_drift.anchor_fingerprint, which keeps those on the old formula rather
    than invalidating every flag in the library on upgrade.
    """
    try:
        p = anchor_path(root, char_id)
    except BadAnchorId:
        # nothing can live there: read like a missing file
        return {"text": "", "id": "", "disabled": False}
    if not p.exists():
        return {"text": "", "id": "", "disabled": False}
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {"text": body.strip(), "id": str(meta.get("id") or ""),
            "disabled": str(meta.get("disabled") or "").strip().lower() == "true"}


def read(root: Path, char_id: str) -> str:
    return read_record(root, char_id)["text"]


def write(root: Path, char_id: str, text: str) -> None:
    """Persist the anchor; a blank `text` removes it.

    Deleting rather than storing an empty file matters: absence is the signal
    the whole feature keys on. `voice_drift` judges only characters that HAVE an
    anchor, so an emptied anchor has to turn drift detection back off for that
    character rather than leave a file that reads as "" and judges against
    nothing.
    """
    p = anchor_path(root, char_id)       # raises BadAnchorId before mkdir touches disk
    if not text.strip():
        if p.exists():
            p.unlink()
        return
    # Preserve the existing nonce, mint one only when the anchor is being
    # CREATED. Editing or reformatting an anchor is the same anchor and must not
    # invalidate findings judged against it; deleting and recreating one is a
    # different anchor even if the words happen to match.
    stored = read_record(root, char_id)
    # A pre-nonce anchor saved back UNCHANGED must stay pre-nonce. Minting an id
    # for it would move its fingerprint off the legacy content-only formula onto
    # the nonce one, and `context._voice_notes` would then read every flag judged
    # against it as citing a replaced standard -- silently retiring real
    # correctives on a save where the user changed no text. The whole point of
    # the legacy formula is that those flags keep matching.
    if not stored["id"] and stored["text"] == text.strip():
        return
    anchor_id = stored["id"] or uuid.uuid4().hex
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(p, dump_frontmatter({"id": anchor_id}, text.strip() + "\n"))


def disable(root: Path, char_id: str) -> None:
    """Record that this root deliberately has NO anchor for `char_id`, so a
    lower-precedence one does not show through.

    `write("")` cannot express this: it deletes, and deletion is exactly the
    state an overlay resolves by falling back. A campaign that clears an
    inherited anchor means "stop judging this character here", not "show me the
    world's again" -- and the editor promises the former.

    Carries no nonce, deliberately. Re-entering text after a tombstone mints a
    fresh one, which is `write`'s own rule: clearing an anchor retires it, so
    the anchor that replaces it is a new standard even when the words match, and
    findings judged against the retired one must not come back to life.
    """
    p = anchor_path(root, char_id)       # raises BadAnchorId before mkdir touches disk
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(p, dump_frontmatter({"disabled": "true"}, ""))


def build_prompt(card_data: dict) -> list[dict]:
    """Draft an anchor from the character's own card — the bootstrap path, so a
    library that has never had anchors can acquire them without hand-writing
    each one. Preview only: the route returns the draft and the caller persists
    it through PUT, so Generate-then-cancel leaves nothing written."""
    return [{"role": "system", "content": prompts.render("voice_anchor/system.j2")},
            {"role": "user", "content": prompts.render("voice_anchor/user.j2", card=card_data)}]


def parse_output(text: str) -> str:
    return text.strip()
