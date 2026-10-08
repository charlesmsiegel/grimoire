"""Today's three decision parsers, frozen: the legacy side of the decide gate.

The gate (`evals/gate.py`) scores each conversion's structured parse against
the parse the call site used before it switched. The switch tasks delete those
parsers from production, so the gate keeps its own copies here, and nothing in
this module is imported from a production parser: a later edit there -- or the
deletion itself -- must not move the side the structured parse is measured
against.

**These copies are never edited.** Each was copied verbatim at slice F's base
(`claude/inference-slice-f`, before Task 6); a bug in one is part of the
behaviour the gate compares against, not something to fix here. The only
departures from the originals are the two names the originals share: each
`parse_output` is renamed for its module (`scene_break_parse_output`,
`voice_drift_parse_output`), with its body untouched, and the selector's
inline parse is lifted out of `routes/character_turns._select` into
`selector_parse`, its lines unchanged but for `store.response_protocol.`
dropped from the `validate_handoff` it calls (the frozen copy below).

- `store/scene_break.py`: `_extract_json` and `parse_output`.
- `store/voice_drift.py`: the four verdict constants, `_VERDICTS`,
  `_extract_object` and `parse_output`.
- `store/response_protocol.py`: `validate_handoff`, and the `json.loads` that
  `_select` runs before it (production keeps its own `validate_handoff` for
  handoff fences, so a later edit there must not move this side either).
"""

from __future__ import annotations

import json

# --- store/scene_break.py ---------------------------------------------------


def _extract_json(text: str):
    """The reply as an object, tolerant of a model that wrapped it in prose.

    Objects only, deliberately narrower than `suggest._extract_json`, which
    also accepts a bare top-level array because the reply it parses is a LIST
    of openings and answering with the array alone is the natural deviation
    there. This reply is a single verdict; a bare array is not a shape it has a
    reading for, so accepting one would only widen what can be misread.
    """
    candidates = [text.strip()]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def scene_break_parse_output(text: str) -> dict:
    """`{"break": bool, "reason": str, "title": str}` out of the model's reply.

    An unreadable reply is `break: false` with empty prose rather than an
    exception, and that is the safe direction on purpose: this route runs
    automatically off the play loop, and the cost of a missed suggestion is
    that nobody is asked, where the cost of raising is an error banner over a
    scene the player is in the middle of.

    Prose is collapsed to one line for `scenes.set_scene_break`'s reason --
    frontmatter is one line per key and its writer does not escape newlines --
    but the store collapses again anyway, since that invariant must not depend
    on which parser fed it.
    """
    parsed = _extract_json(text) or {}
    return {"break": parsed.get("break") is True,
            "reason": " ".join(str(parsed.get("reason") or "").split()),
            "title": " ".join(str(parsed.get("title") or "").split())}


# --- store/voice_drift.py ---------------------------------------------------

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


def voice_drift_parse_output(text: str) -> dict:
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


# --- store/response_protocol.py and routes/character_turns._select ---------


def validate_handoff(payload, eligible, used):
    """`(next, issue)` for a handoff payload. A ref that is eligible but has
    already spoken is reported as `"repeated speaker"`, apart from an
    ineligible one: with automatic rounds remaining, the caller turns that
    repeat into the next round's lead instead of rejecting it."""
    if not isinstance(payload, dict) or set(payload) != {"next"}:
        return None, "missing or invalid handoff"
    ref = payload["next"]
    if ref is None:
        return None, None
    if not isinstance(ref, str) or ref not in eligible:
        return None, "ineligible or repeated speaker"
    if ref in used:
        return None, "repeated speaker"
    return ref, None


def selector_parse(answer, eligible):
    """`_select`'s parse of the selector's reply, from the line after its
    `meter.done()`: `eligible` is the round record's `eligible` list (dicts
    with a `ref`). `_select` answers a round of one or none before it calls
    anything, so a gate entry always has two or more."""
    try:
        payload = json.loads(answer)
    except ValueError:
        payload = None
    return validate_handoff(
        payload, [r["ref"] for r in eligible] + ["grimoire"], []
    )
