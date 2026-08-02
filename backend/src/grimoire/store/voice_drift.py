"""Voice-drift detection: judge a played scene's dialogue against a character's
voice anchor (store/voice_anchors.py), and remember an unresolved verdict so the
next turn can be told to correct it.

Two halves, both here because they are one loop:

- **The judge.** Prompt/parse only -- one LLM call per present NPC *that has an
  anchor*, made at absorb time in the route layer. "In voice or not" is a
  qualitative judgment, so it is asked of a model rather than inferred from text
  statistics; keying on the anchor's existence is what keeps the cost opt-in
  (a library with no anchors makes no extra calls at all).

- **The flag.** The unresolved verdict, campaign-local at
  <croot>/characters/<char_id>/voice_drift.md, holding the corrective note. Absent
  means "in voice". Campaign-local rather than world-level even though the
  anchor is world-level: the anchor says how the character sounds everywhere,
  but *this campaign's* last scene is what drifted, and a correction owed in one
  campaign must not follow the character into another.

Absorb never writes the flag itself -- it stages it, exactly as dossiers.py
stages a paragraph (#235). A finding that landed before the reviewer saved would
survive a Cancel, and would go on nagging the model about a scene the chronicle
never recorded.

The flag is consumed by context._assemble, which renders
templates/scene/voice_correction.j2 into the post-history system message: the
last thing said before generation, and so the closest available push-back --
the same slot, and the same reasoning, as the length corrective.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .. import prompts
from . import atomic, paths
from .frontmatter import dump_frontmatter, parse_frontmatter


class BadDriftId(Exception):
    """An id that could escape the characters directory."""


def _safe_id(char_id: str) -> bool:
    """Reject ids that could escape OR ALIAS the characters directory. Drift ids
    arrive on client-supplied PUT /chronicle edit rows, so they are untrusted.

    Delegates to `paths.safe_id` rather than re-deriving the rules. A local copy
    of the separator checks looked equivalent and was not: it accepted a colon
    (on Windows `store / "C:evil"` is `C:evil`, discarding the campaign prefix
    entirely) and a trailing dot or space (Win32 trims them, so `winifred.` and
    `winifred` are one directory). A blank clear reaches `flag_path` without
    passing the character-existence check above it, so aliasing here is enough
    to unlink a real character's flag.
    """
    return paths.safe_id(char_id)


def flag_path(croot: Path, char_id: str) -> Path:
    if not _safe_id(char_id):
        raise BadDriftId(char_id)
    # overlay-ok: voice_drift.md is campaign-local, merely filed inside the
    # actor's dir for locality (like dossier.md/state.md) -- it is never
    # inherited from the world, so there is nothing for store/overlay.py to
    # resolve here. The ANCHOR it is judged against is the world-level half,
    # and that one does go through overlay.voice_anchor().
    return croot / "characters" / char_id / "voice_drift.md"


def _read_file(croot: Path, char_id: str) -> tuple[dict, str]:
    """(frontmatter, note) for the stored flag; ({}, "") when there is none."""
    try:
        p = flag_path(croot, char_id)
    except BadDriftId:
        return {}, ""          # nothing can live there: read like a missing file
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        # Same race as voice_anchors.read_record, same hot path: a clear
        # committed by another request unlinks this file, and an exists-then-read
        # pair straddling that raises rather than reading it as resolved.
        return {}, ""
    meta, body = parse_frontmatter(raw)
    return meta, body.strip()


def read_record(croot: Path, char_id: str) -> dict:
    """{"note", "anchor"} for the stored flag, from ONE read of the file.

    Every caller that checks a note against its provenance must come through
    here rather than calling `read` and `judged_anchor` in turn. The flag is
    replaced atomically, so two reads can straddle a chronicle save committed
    between them and pair a stale note with the fresh fingerprint -- which
    validates it, and injects a retired correction into the next generation --
    or a fresh note with the stale fingerprint, which suppresses a live one.
    Neither pairing ever existed on disk.
    """
    meta, note = _read_file(croot, char_id)
    return {"note": note, "anchor": str(meta.get("anchor") or "")}


def read(croot: Path, char_id: str) -> str:
    """The unresolved corrective note, or "" when this character is in voice."""
    return _read_file(croot, char_id)[1]


def judged_anchor(croot: Path, char_id: str) -> str:
    """The fingerprint of the anchor the stored note was judged against.

    "" means "not recorded" -- either there is no flag, or the flag predates
    this field. Callers must treat an unrecorded provenance as valid rather than
    stale: invalidating on it would silently retire every flag written before
    the field existed, which is user data.

    Use `read_record` when you also need the note: reading the two separately
    can pair a note with provenance that never described it.
    """
    return read_record(croot, char_id)["anchor"]


def write(croot: Path, char_id: str, note: str, anchor_fp: str = "") -> None:
    """Raise the flag, or clear it when `note` is blank.

    `anchor_fp` is `anchor_fingerprint(...)` of the anchor this note was judged
    against, stored so the corrective can be suppressed if the anchor later
    moves. The apply-time guard in absorb only covers the pending-review window;
    a committed flag outlives it, and without this the note would go on citing a
    standard the user has since replaced until some later absorb happened to
    clear it.

    Clearing DELETES rather than blanking, because absence is the state: an
    empty file and a missing one must not read differently to `read`, and a
    resolved character should leave no residue behind for the next reader to
    interpret.
    """
    p = flag_path(croot, char_id)        # raises BadDriftId before mkdir touches disk
    if not note.strip():
        # `missing_ok`, not exists-then-unlink: clearing an already-cleared
        # flag is a success, and two blank PUTs from separate tabs would
        # otherwise have the loser raise FileNotFoundError and 500 -- for
        # reaching the state it asked for. Same race the readers just fixed,
        # on the write side.
        p.unlink(missing_ok=True)
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(p, dump_frontmatter({"anchor": anchor_fp}, note.strip() + "\n"))


def anchor_fingerprint(anchor: str, anchor_id: str = "") -> str:
    """A digest of the anchor a verdict was judged against.

    Carried on the staged edit so the apply-time guard can tell whether the
    reference moved between the judgment and the save. "" (no anchor)
    fingerprints to "" so the absent case stays obviously distinct rather than
    hashing to some opaque constant.

    Whitespace is normalized THROUGHOUT, not just at the ends: rewrapping a
    line or closing up a blank one is presentation, not a new standard, and the
    anchor's own text is what reaches the judge regardless. Stripping alone
    made those edits retire every committed flag, silently -- the reader just
    stops finding a match and the corrective vanishes from later prompts.
    Compare through `fingerprint_matches` rather than `==`, so a flag digested
    under the older spelling still matches.

    `anchor_id` is `voice_anchors.read_record`'s nonce, folded in so that an
    anchor deleted and recreated with the SAME words is a different anchor.
    Content alone cannot express that, and the difference matters: deletion is
    the documented opt-out, so a flag it silenced must not come back when the
    user later types the same sentence again.

    A legacy anchor (no nonce) keeps the content-only formula rather than
    hashing "" into it, so every flag written before the field existed still
    matches its anchor instead of being retired wholesale on upgrade.
    """
    return _digest(" ".join(anchor.split()), anchor_id)


def _digest(text: str, anchor_id: str) -> str:
    if not text:
        return ""
    payload = f"{anchor_id}\n{text}" if anchor_id else text
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fingerprint_matches(stored: str, anchor: str, anchor_id: str = "") -> bool:
    """True when `stored` is a fingerprint of THIS anchor.

    Not `stored == anchor_fingerprint(...)`, because the formula has changed
    once: it used to strip only the ends, so a flag committed before that
    normalization landed carries a digest of the un-normalized text. Comparing
    on equality alone would retire every one of those on upgrade -- the same
    harm the legacy no-nonce formula exists to avoid, arriving by a different
    route. So the older spelling still counts as naming the same anchor.

    Callers keep their own policy for a BLANK `stored`; it means "provenance not
    recorded", which `context` treats as valid (a flag predating the field) and
    `absorb.apply_edits` refuses on a raise (a client-supplied row must not
    claim that status). Both are about who wrote it, not about which anchor it
    names, so neither belongs here.
    """
    if stored == anchor_fingerprint(anchor, anchor_id):
        return True
    return bool(stored) and stored == _digest(anchor.strip(), anchor_id)


#: Longest corrective that may be staged or stored, in characters.
#:
#: The flag is rendered into the POST-HISTORY system message, which
#: `context.assemble` reserves and the packer is not allowed to drop or trim --
#: so an unbounded note is charged against every later generation's budget with
#: nothing able to compensate, and a big enough one pushes each of them over.
#: That makes the character unusable until someone clears the flag by hand.
#:
#: The template asks for one or two sentences, so this is far above any
#: well-formed corrective; it bounds the damage from a judge that ignored the
#: format, and from a client-supplied row, without second-guessing a reviewer
#: who wanted to be thorough.
MAX_NOTE = 1000


def build_prompt(name: str, anchor: str, transcript: str) -> list[dict]:
    return [{"role": "system", "content": prompts.render("voice_drift/system.j2")},
            {"role": "user", "content": prompts.render("voice_drift/user.j2", name=name,
                                                       anchor=anchor, transcript=transcript)}]


#: The judge's verdicts. Deliberately FOUR values, not a boolean, because
#: clearing a standing flag is a write and only one of these justifies it:
DRIFT = "drift"              # out of voice; `note` is the corrective
IN_VOICE = "in_voice"        # judged, and they sounded right -> safe to clear
NOT_ENOUGH = "not_enough"    # too little dialogue to judge either way
UNKNOWN = "unknown"          # no usable verdict came back at all

#: Synonyms the judge might reasonably use. Leniency is ASYMMETRIC on purpose:
#: IN_VOICE authorizes a destructive clear, so only spellings that can mean
#: nothing else map to it. NOT_ENOUGH is the conservative outcome (it preserves
#: a standing flag), so a loose word landing there costs nothing.
#:
#: "none" and "ok" were here and are deliberately gone: "none" can mean "no
#: drift" OR "no judgment"/"no dialogue", and "ok" can be an acknowledgement
#: rather than a verdict. An ambiguous token must never authorize a clear --
#: unmapped spellings fall through to UNKNOWN, which preserves the flag and
#: reports a failed check.
_VERDICTS = {DRIFT: DRIFT, IN_VOICE: IN_VOICE, NOT_ENOUGH: NOT_ENOUGH,
             "in voice": IN_VOICE, "in-voice": IN_VOICE,
             "not enough": NOT_ENOUGH, "not-enough": NOT_ENOUGH,
             "insufficient": NOT_ENOUGH, "unclear": NOT_ENOUGH,
             "unknown": UNKNOWN}


def _extract_object(text: str) -> dict | None:
    """The JSON object embedded in a reply, tolerating prose or a fence around it.

    Deliberately a copy of `absorb.parse.extract_object` rather than an import of
    it: `absorb/apply.py` imports THIS module to write an approved flag, so
    importing absorb back would make the store graph cyclic, which
    `tests/test_import_guard.py` forbids outright. The duplication is ten lines
    and `test_voice_drift_store.py` pins both parsers against the same fenced and
    prose-wrapped shapes, so they cannot silently drift apart.
    """
    start, end = text.find("{"), text.rfind("}")
    raw = text[start:end + 1] if start != -1 and end > start else ""
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def parse_output(text: str) -> dict:
    """{"verdict": one of the four above, "note": str} from the judge's reply.

    The verdict is read from an explicit enum rather than inferred from the
    note's prose: "no drift, though she was a little terse" and "drift: she was
    a little terse" are the same sentence with opposite meanings, and guessing
    between them is how a corrective ends up nagging a model that did nothing
    wrong.

    An unreadable reply is UNKNOWN, NOT "in voice". That distinction is the
    whole reason this is not a boolean. A no-drift verdict is not inert -- with
    a flag standing it proposes a CLEAR -- so collapsing "the model returned
    garbage" into "they sounded fine" would let a malformed reply silently
    retire a real corrective on a review the user approves by default.
    """
    obj = _extract_object(text)
    if not isinstance(obj, dict):
        return {"verdict": UNKNOWN, "note": ""}
    raw = obj.get("verdict")
    verdict = _VERDICTS.get(raw.strip().lower(), UNKNOWN) if isinstance(raw, str) else UNKNOWN
    # Only a STRING note survives. `str(...)` on an object or a list would
    # render it as Python source ("{'tone': 'terse'}") -- nonempty text that
    # reads as a usable corrective, gets staged default-approved, and is then
    # injected verbatim into every following turn's system prompt. Blanking it
    # instead routes a malformed drift reply to the caller's "drift reported
    # with no corrective" failure, which is exactly what it is.
    note = obj.get("note")
    return {"verdict": verdict, "note": note.strip() if isinstance(note, str) else ""}


def stage_edit(char_id: str, name: str, prior: str, finding: dict,
               anchor: str = "", anchor_id: str = "", prior_anchor_fp: str = "") -> dict | None:
    """The verdict as a StagedEdit against the stored flag, or None when there
    is nothing to propose.

    Only two verdicts are edits:

    - DRIFT, and the note differs from the stored one -> raise/replace the flag
    - IN_VOICE, and a flag is standing                -> clear it, so a
      character who has corrected course stops being told to

    Everything else proposes nothing, and that is load-bearing rather than
    merely tidy. Staged edits arrive in the review DEFAULT-APPROVED, so a
    proposal is very nearly a write: NOT_ENOUGH must not clear a flag (a
    character who simply stayed quiet has not demonstrated anything), and
    UNKNOWN must not clear one either (no judgment was made at all). Both keep
    the standing corrective until a scene actually shows the voice again.

    `prior` is the flag the PROMPT was built from, passed in rather than
    re-read, for dossiers.stage_edit's reason: another review can land between
    the read and the model's reply, and recording that newer text as `before`
    would let this staler proposal pass the apply-time conflict check.

    `anchor` is the reference the verdict was judged against; its fingerprint
    rides along so apply-time can tell whether the standard itself moved while
    the review sat open (see absorb.apply_edits).
    """
    before, note = prior.strip(), finding.get("note", "").strip()
    verdict = finding.get("verdict")
    fp = anchor_fingerprint(anchor, anchor_id)
    if verdict == DRIFT:
        if not note:
            return None
        if note == before:
            # Same corrective as the standing one. Normally nothing to propose --
            # EXCEPT when the stored provenance is stale, because the reader
            # suppresses a flag whose anchor moved. This scene revalidated that
            # exact correction against the CURRENT anchor, so without a
            # provenance-only refresh the flag stays suppressed forever while
            # absorb keeps reporting the character as flagged: a corrective that
            # exists, is re-confirmed every scene, and never reaches a prompt.
            if fp == prior_anchor_fp:
                return None
            after, label = note, f"{name} — voice drift re-confirmed"
        else:
            after, label = note, f"{name} — voice drift"
    elif verdict == IN_VOICE and before:
        after, label = "", f"{name} — voice drift cleared"
    else:
        return None
    return {"id": f"voice_drift:{char_id}", "kind": "voice_drift",
            "target": {"kind": "characters", "id": char_id},
            "label": label, "field": "voice_drift",
            "before": before, "after": after, "authored": False,
            # `op` states the row's INTENT, which `after` cannot: the reviewer
            # can edit the note, and a raise edited down to blank text would
            # otherwise read as a clear and unlink the standing corrective.
            # `before_anchor` is the provenance this row expects to find, so the
            # apply-time compare-and-swap covers a provenance-only write that
            # left the note identical.
            "payload": {"anchor": fp, "op": "clear" if after == "" else "raise",
                        "before_anchor": prior_anchor_fp}}
