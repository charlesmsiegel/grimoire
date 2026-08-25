"""Per-character voice anchor — the reference sample of how a character SOUNDS,
against which a played scene's dialogue is judged for drift (store/voice_drift.py).

World-level, like tagline.md: a voice is a library property of the character, not
something a campaign owns, so every campaign compares against the same reference
and a fix made once holds everywhere. Campaign-side reads go through
`overlay.voice_anchor()`, which resolves campaign-over-world per file.

Stored at <root>/characters/<char_id>/voice_anchor.md as plain prose. A separate
file from the card's `mes_example` because the two do different work: the
examples DEMONSTRATE a voice and the anchor DESCRIBES it, which is what lets it
state a rule ("clipped, never uses contractions") that a sample can only imply.
Both are sent, each in its own scene section: the anchor capped through
`effective()`, the examples through `truncate(..., VOICE_EXAMPLE_CAP)`.

That is a reversal. This docstring used to say the anchor "is never sent as part
of a scene", and the property it protected was real: an anchor could be edited to
sharpen the COMPARISON without changing what the model was told to imitate. It
was spent deliberately. A voice system that can only report failure after a scene
is played does not solve the problem it exists for -- the reader wants different
voices, not a report that they were the same -- and while the anchor was
judge-only, nothing in the app ever wrote one.

What that costs is worth stating plainly, because the code no longer implies it:
**the drift check is an approximate second opinion, not a proof.** It compares a
played scene against the character's CURRENT anchor, which may differ from what
any turn actually received -- `{{user}}` is substituted in the prompt copy only,
the packer may drop the anchors section under a budget, the reader may disable it
in the layout, the anchor may be edited between playing and absorbing, and
`templates/` is user-editable. A `drift` verdict means "worth looking at". Making
it exact would mean snapshotting the delivered brief with every accepted turn,
co-committed under the campaign lock; that is priced and not taken. What IS held
exactly: the judge and the generator receive the same `effective()` text, so a
rule past the cap is enforced against neither.

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

VOICE_ANCHOR_CAP = 1200
"""Longest anchor text the prompt or the drift judge ever sees, in characters.

An anchor is 3-6 short lines by construction, so this is headroom rather than a
limit on the author -- but `write` enforces nothing at all, and the anchor now
renders once per present character on every turn, so an unbounded value would
be multiplied by the cast.
"""

VOICE_EXAMPLE_CAP = 3000
"""Longest `mes_example` the prompt ever sees, in characters.

Room for several full exchanges before anything is cut: a ceiling on outliers,
not a target. Tune it against real prompts once the section is live rather than
treating the number as settled.
"""


def truncate(text: str, cap: int) -> str:
    """`text` shortened to at most `cap` characters, cut at a boundary.

    The boundary is a `<START>` marker or a blank line, whichever falls LATEST
    inside the capped prefix. A chosen `<START>` is cut *before* the marker, so
    the partial block it opened is discarded rather than left headerless.

    Two fallbacks, both deliberate:

    - No boundary in the prefix -> a hard character cut, which MAY land
      mid-line. A single-line example longer than the cap has nowhere else to
      go, and half a line beats nothing.
    - The boundary rule would keep less than half the cap -> the hard cut
      again. This is the leading-`<START>` case: cutting before a marker at
      position 0 leaves the empty string, and truncating a long sample to
      nothing because it opens with a marker is the worse failure.
    """
    # Newlines normalised FIRST, or the blank-line boundary is invisible in
    # exactly the text most likely to have one: a card imported or hand-edited
    # on Windows separates paragraphs with a carriage-return pair, which the
    # bare two-newline search below never matches. Such a value fell through
    # to the hard cut and was sent mid-line, which is the one thing the
    # boundary rule exists to avoid.
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) <= cap:
        return text
    prefix = text[:cap]
    cut = -1
    marker = prefix.rfind("<START>")
    if marker > 0:
        cut = marker
    blank = prefix.rfind("\n\n")
    if blank > cut:
        cut = blank
    if cut > 0 and cut >= cap // 2:
        return prefix[:cut].rstrip()
    return prefix


def effective(text: str) -> str:
    """The anchor as BOTH the scene prompt and the drift judge see it.

    One transformation, two consumers, and that is the whole point of it being
    a function rather than an inline slice: if the generator were handed a
    truncated anchor while the judge read the stored one, a rule past the cap
    would be invisible to the writer and still enforced against it. Capping is
    therefore not a source of generator/judge divergence -- both copies come
    from here.
    """
    return truncate(text, VOICE_ANCHOR_CAP)


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
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        # Read-and-catch, not exists-then-read: `write` CLEARS by unlinking, so
        # another tab or device removing the anchor between the two calls used
        # to raise here -- on the generation hot path, through
        # `overlay.voice_anchor_record`, with no failure boundary above it. A
        # concurrent clear is just the absent state arriving slightly late.
        return {"text": "", "id": "", "disabled": False}
    meta, body = parse_frontmatter(raw)
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
        # `missing_ok`, not exists-then-unlink: clearing an already-cleared
        # anchor is a success, and two blank PUTs from separate tabs would
        # otherwise have the loser raise FileNotFoundError and 500 -- for
        # reaching the state it asked for. Same race the readers just fixed,
        # on the write side.
        p.unlink(missing_ok=True)
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
