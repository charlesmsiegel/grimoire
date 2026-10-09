"""Voice-drift detection: judge a played scene's dialogue against a character's
voice anchor (store/voice_anchors.py), and remember an unresolved verdict so the
next turn can be told to correct it.

Two halves, both here because they are one loop:

- **The judge.** The question and its reading only -- one `decide()` item per
  present NPC *that has an anchor*, asked at absorb time in the route layer. "In voice or not" is a
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
from pathlib import Path

from .. import decisions, prompts
from . import atomic, characters, paths, voice_anchors
from .appearances import paths as appearances_paths
from .appearances import versions as appearances_versions
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


#: The judge's verdicts. Deliberately FOUR values, not a boolean, because
#: clearing a standing flag is a write and only one of these justifies it. The
#: verdict is read from an explicit enum rather than inferred from the note's
#: prose: "no drift, though she was a little terse" and "drift: she was a
#: little terse" are the same sentence with opposite meanings.
DRIFT = "drift"              # out of voice; `note` is the corrective
IN_VOICE = "in_voice"        # judged, and they sounded right -> safe to clear
NOT_ENOUGH = "not_enough"    # too little dialogue to judge either way
UNKNOWN = "unknown"          # no usable verdict came back at all -- never an option

# --- the judge, asked through decide() (spec 7.4) ---------------------------
#
# `build_item` is the judge's request as a decision item, `explain` the
# corrective it asks for, and `finding_of` maps the answer back to the
# `{"verdict", "note", "native"}` finding `stage_edit` and the route read.
# `check_failure` is the route's per-finding refusal, with its words.

#: The choice's id.
QUESTION_ID = "verdict"

#: The legacy parser's two word-synonyms, kept as the option's aliases (the
#: structured parser accepts them after `decisions.normalise`, which also
#: covers the spaced and hyphenated spellings of every id). Leniency is
#: ASYMMETRIC on purpose: only NOT_ENOUGH has any, because it is the
#: conservative outcome (it preserves a standing flag), so a loose word landing
#: there costs nothing -- where IN_VOICE authorizes a destructive clear, and an
#: ambiguous token ("none" can mean "no drift" or "no judgment"; "ok" can be an
#: acknowledgement) must never reach it. Anything off the vocabulary is
#: unreadable, which is UNKNOWN. `decisions.validate` refuses an alias that
#: collides with another spelling once normalised.
ALIASES: dict[str, tuple[str, ...]] = {NOT_ENOUGH: ("insufficient", "unclear")}


def build_item(name: str, anchor: str, transcript: str,
               correction: str = "") -> decisions.Item:
    """The judge's request as a decision item: `voice_drift/user.j2` (the
    legacy one-call prompt's user message, unchanged) as its context, and one
    choice over the three verdicts a judge can give, whose instructions are the
    legacy system prompt's standard and criteria (`voice_drift/question.j2`)
    and whose options carry its bullets (`voice_drift/option.j2`). The reply
    format is `decide`'s own, and the corrective travels as the item's
    rationale (`explain`).

    No `allow_none`: UNKNOWN is what an unreadable answer becomes, never
    something the judge is offered. `correction` is the character's
    outstanding drift note, optional because it usually is not there, and the
    CALLER owns deciding that it is still in force (`judge_item` does, through
    `live_correction`): this module does not read the store to build an item.
    """
    context = prompts.render("voice_drift/user.j2", name=name, anchor=anchor,
                             transcript=transcript, correction=correction)
    options = tuple(
        decisions.Option(verdict, prompts.render("voice_drift/option.j2", verdict=verdict),
                         ALIASES.get(verdict, ()))
        for verdict in (DRIFT, IN_VOICE, NOT_ENOUGH))
    return decisions.Item(context, (decisions.Choice(
        QUESTION_ID, prompts.render("voice_drift/question.j2"), options),))


def locked_name(cid: str, char_id: str) -> object:
    """The raw `data.name` of the character's LOCKED card -- what the
    transcript labels its lines with -- or None when the card's `data` is not
    an object. Not checked: cards are arbitrary dicts, so this can be a number
    or an object, and the caller decides what an unusable name costs.

    The locked version's card rather than the container's meta name, which can
    differ from it, and rather than `_actor_name`'s id fallback, which nothing
    labels a line with. Raises what reading the card raises, and LookupError
    for a character the campaign's appearance record does not hold -- which a
    cast member never is, since the cast is read from that record.
    """
    vid = appearances_versions.locked_version(cid, "characters", char_id)
    if vid is None:
        raise LookupError(f"{char_id!r} has no locked version in this campaign")
    data = characters.read_card(appearances_paths.locked_actor_root(cid), char_id,
                                vid).get("data")
    return data.get("name") if isinstance(data, dict) else None


def live_correction(flag: dict, anchor_record: dict) -> str:
    """The stored note, when it is still in force against `anchor_record`
    (`overlay.voice_anchor_record`'s `{"text", "id"}`), else "".

    `flag` is ONE `read_record` snapshot, so the note and its provenance came
    from the same committed file. A blank provenance is a flag that predates
    the field, and counts as in force (`anchor_fingerprint`'s reason). A note
    fingerprinted to a REPLACED anchor is suppressed for the writer
    (`context/cast.py` applies this same test), so handing it to the judge as
    current would mint a fresh flag against the anchor that replaced it.
    """
    note, stored = flag["note"], flag["anchor"]
    if not stored or fingerprint_matches(stored, anchor_record["text"], anchor_record["id"]):
        return note
    return ""


def judge_item(name: str, anchor_record: dict, transcript: str, flag: dict) -> decisions.Item:
    """`build_item` for one NPC as the absorb phase gathers it: the EFFECTIVE
    anchor (`voice_anchors.effective`, all the generator ever saw, so a rule
    past the cap is enforced against neither) and the correction only while it
    is in force (`live_correction`). The fingerprint stays on the raw stored
    text -- capping it would retire every correction whose anchor is long."""
    return build_item(name, voice_anchors.effective(anchor_record["text"]), transcript,
                      correction=live_correction(flag, anchor_record))


def explain() -> str:
    """The rationale instruction: the corrective the next turn is given, empty
    unless the verdict is drift. It becomes the finding's note."""
    return prompts.render("voice_drift/explain.j2")


def finding_of(result: decisions.ItemResult) -> dict:
    """`{"verdict", "note", "native"}` from the item's result: the finding
    `stage_edit` and `check_failure` read.

    `native` is whether the native decisions endpoint answered. It gives a
    verdict and never a rationale, so its drift always comes with no note --
    which is a drift with no corrective to store, not a judge that failed to
    explain (`check_failure`).

    An answer of `None`, whatever its reason, is UNKNOWN -- the failed check --
    and NOT "in voice". That distinction is the whole reason the verdict is not
    a boolean: a no-drift verdict with a flag standing proposes a CLEAR, so
    collapsing "the model returned garbage" into "they sounded fine" would let
    a malformed reply silently retire a real corrective on a review the user
    approves by default. The note is the rationale, which the parser has
    already stripped, and blanked when it was not a string: `str(...)` on an
    object would render Python source that reads as a usable corrective."""
    answer = result.answers.get(QUESTION_ID)
    verdict = answer.answer if answer is not None and isinstance(answer.answer, str) else None
    return {"verdict": verdict or UNKNOWN, "note": result.rationale,
            "native": result.backend == decisions.NATIVE_BACKEND}


def check_failure(finding: dict) -> str | None:
    """Why `_stage_voice_drift` reports this finding as a failed check, or
    None when it is usable. Its three per-finding checks, in its order and
    with its words.

    - An UNKNOWN verdict is a failed call, not a quiet pass: conflated with
      "in voice" it would stage a default-approved clear of a standing flag on
      the strength of a garbled reply.
    - Both note checks are DRIFT-only, because only a drift verdict stores a
      note: `stage_edit` writes `after=""` for IN_VOICE and proposes nothing
      for NOT_ENOUGH, so their notes never reach a prompt, and failing them on
      a chatty note would leave an obsolete corrective standing.
    - A drift with no note is unusable -- the note IS the corrective -- and one
      over MAX_NOTE would be charged against every later generation from the
      post-history message, which the packer cannot trim. The first applies
      to a structured answer only: a native one never has a note, so its
      drift is reported (`noteless`) and stores nothing, and a failure for it
      would hide a verdict the reviewer should see.
    """
    verdict, note = finding.get("verdict"), finding.get("note", "")
    if verdict == UNKNOWN:
        return "unreadable verdict from the voice judge"
    if verdict == DRIFT:
        if not note and not finding.get("native"):
            return "drift reported with no corrective"
        if len(note) > MAX_NOTE:
            return (f"the voice judge returned a corrective over {MAX_NOTE} characters, "
                    f"too long to put in front of every following turn")
    return None


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
