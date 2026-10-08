"""Today's decision parsers, frozen: the legacy side of the decide gate.

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

Slice G's continuity parsers were copied the same way, verbatim at slice G's
base `567dc10` (`claude/inference-slice-g`, before its Task 5 switch). Names
the reconcile copy shares, and the module constants that read ambiguously in a
module holding both, take an `identity_` prefix (`identity_parse_output`,
`IDENTITY_DECISIONS`, `IDENTITY_REASON_CHARS`), with the bodies changed only
for those renames and for `parse_output` calling the copied `extract_object`
by its bare name:

- `store/absorb/parse.py`: `extract_object`, unchanged and unprefixed (the
  reconcile copy reuses it).
- `store/continuity/identity.py`: `DECISIONS`, `REASON_CHARS`, `_ROW_WORD`,
  `_row_key`, `_decision` and `parse_output`.

The reconcile copy was taken the same way at `2c201fc` (still before the
switch), its shared names with a `reconcile_` prefix (`reconcile_parse_output`,
`RECONCILE_DECISIONS`; `RECONCILE_REASON_CHARS` already had one). Its bodies
change only for those renames and for three module prefixes dropped: the
`extract_object` above, and the two domain tables it reads, copied as frozen
values beside it (`effective.RELATIONS` as `RELATIONS`,
`pending.TEMPORAL_RELATIONS` as `TEMPORAL_RELATIONS`):

- `store/continuity/reconcile.py`: `RECONCILE_REASON_CHARS`, `DECISIONS`,
  `_RELATION_OF`, `_DIRECTED`, `_STATUS_OF`, `_CANDIDATE_WORD`,
  `_candidate_key`, `_ref_of`, `_evidence`, `_allowed`, `_temporal_word`,
  `_pair`, `_lifecycle_word`, `_decide` and `parse_output` -- today's rule,
  a status word standing only with a reason and a known evidence scene.
"""

from __future__ import annotations

import json
import re

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


# --- store/absorb/parse.py --------------------------------------------------


def extract_object(text: str) -> dict | None:
    """The JSON object embedded in a reply, tolerating prose or a markdown
    fence around it. None when there is no decodable object at all.

    None rather than {} — and public rather than private — because "the model
    returned no JSON" (a format failure: it refused, or wrote prose, or got
    truncated) and "the model returned an empty object" (an extraction failure:
    it understood the format and found nothing to say) have different causes
    and different fixes. parse_output cannot tell them apart on its own; both
    arrive as a dict of empty defaults. evals/graders.py reports them
    separately, which is only possible if this function keeps the difference.
    """
    start, end = text.find("{"), text.rfind("}")
    raw = text[start:end + 1] if start != -1 and end > start else ""
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


# --- store/continuity/identity.py -------------------------------------------

#: What the resolver may answer for a row (spec §10.2).
IDENTITY_DECISIONS = ("existing", "new", "uncertain")

#: A resolver reason is display text; clipping it keeps a runaway reply out of
#: the stored review. To be tuned against real prompts later.
IDENTITY_REASON_CHARS = 280

#: A leading ``row`` word, as the user prompt prints a key (``Row r1``).
_ROW_WORD = re.compile(r"^row(?![a-z0-9])[\s:#.-]*")


def _row_key(value) -> str:
    if not isinstance(value, str):
        return ""
    return _ROW_WORD.sub("", value.strip().casefold()).strip()


def _decision(item: dict) -> dict:
    word = item.get("decision")
    word = word.strip().lower() if isinstance(word, str) else ""
    rid, reason = item.get("id"), item.get("reason")
    return {"row": _row_key(item.get("row")),
            "decision": word if word in IDENTITY_DECISIONS else "uncertain",
            "id": rid.strip() if isinstance(rid, str) else "",
            "reason": reason.strip()[:IDENTITY_REASON_CHARS] if isinstance(reason, str) else ""}


def identity_parse_output(text: str) -> list[dict] | None:
    """The resolver's decisions, rebuilt field by field, or None when the reply
    holds no decodable object at all -- a failed check, which is not the same
    as a decodable reply with nothing usable in it (``[]``, spec §24).

    Each usable element becomes ``{row, decision, id, reason}``: the row key as
    the prompt printed it, without its ``Row`` label; an unknown decision word
    as ``uncertain``; a reason clipped to `REASON_CHARS`. A duplicate row key
    keeps its first answer. Nothing here raises on bad JSON."""
    obj = extract_object(text)
    if obj is None:
        return None
    items = obj.get("decisions")
    out: list[dict] = []
    seen: set[str] = set()
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict):
            continue
        decision = _decision(item)
        if not decision["row"] or decision["row"] in seen:
            continue
        seen.add(decision["row"])
        out.append(decision)
    return out


# --- store/continuity/reconcile.py ------------------------------------------

#: A proposal's reason, clipped -- the bound identity's resolver uses
#: (`identity.REASON_CHARS`), one short sentence.
RECONCILE_REASON_CHARS = 280

#: What the model may answer, per vocabulary (spec §11.3). §5.3 allows
#: ``continues`` and ``subthread_of`` between threads only, so the same-type
#: list is read per type (deviation 18), and no vocabulary but a same-type one
#: offers ``duplicate`` (§3.6).
RECONCILE_DECISIONS: dict[str, tuple[str, ...]] = {
    "same_thread": ("duplicate", "continuation", "subthread", "related", "distinct",
                    "uncertain"),
    "same_commitment": ("duplicate", "related", "distinct", "uncertain"),
    "cross": ("pays_off", "related", "distinct", "uncertain"),
    "thread": ("close", "keep_open", "uncertain"),
    "commitment": ("fulfilled", "broken", "expired", "keep_open", "uncertain"),
    "temporal": ("before", "on", "after", "by", "unrelated", "uncertain"),
}

#: A pair word -> the link relation it proposes (§5.3); ``duplicate`` proposes
#: an alias, which is not a link.
_RELATION_OF = {"duplicate": "", "continuation": "continues", "subthread": "subthread_of",
                "related": "related_to", "pays_off": "pays_off"}
#: The words whose meaning depends on which record is which (§11.3).
_DIRECTED = frozenset({"duplicate", "continuation", "subthread", "pays_off"})
#: The lifecycle words that propose a status, and so need positive evidence
#: (§11.4); ``keep_open`` writes no status.
_STATUS_OF = {"close": "closed", "fulfilled": "fulfilled", "broken": "broken",
              "expired": "expired"}
#: A leading ``candidate`` word, as the user prompt prints a key (``Candidate c1``).
_CANDIDATE_WORD = re.compile(r"^candidate(?![a-z0-9])[\s:#.-]*")

#: `store/continuity/effective.py`'s `RELATIONS` and `store/continuity/pending.py`'s
#: `TEMPORAL_RELATIONS`, the two domain tables the parse reads, as frozen values:
#: this module imports no `grimoire`. Each relation maps to (the record kinds it
#: may run from, the kinds it may run to, whether it is directed).
RELATIONS: dict[str, tuple[frozenset[str], frozenset[str], bool]] = {
    "continues": (frozenset({"thread"}), frozenset({"thread"}), True),
    "subthread_of": (frozenset({"thread"}), frozenset({"thread"}), True),
    "pays_off": (frozenset({"thread"}), frozenset({"commitment"}), True),
    **{rel: (frozenset({"thread", "commitment"}), frozenset({"event"}), True)
       for rel in ("before", "on", "after", "by")},
    "related_to": (frozenset({"thread", "commitment", "event"}),
                   frozenset({"thread", "commitment", "event"}), False),
}
TEMPORAL_RELATIONS = ("before", "on", "after", "by")


def _candidate_key(value) -> str:
    if not isinstance(value, str):
        return ""
    return _CANDIDATE_WORD.sub("", value.strip().casefold()).strip()


def _ref_of(value, refs: dict[str, str]) -> str:
    return refs.get(value.strip().upper(), "") if isinstance(value, str) else ""


def _evidence(value, known: set[str]) -> list[str]:
    """The cited scene ids the prompt showed, deduped in order; the rest dropped."""
    out: list[str] = []
    for sid in value if isinstance(value, list) else ():
        if isinstance(sid, str) and sid in known and sid not in out:
            out.append(sid)
    return out


def _allowed(relation: str, frm: str, to: str) -> bool:
    rule = RELATIONS.get(relation)
    return rule is not None and frm.partition(":")[0] in rule[0] \
        and to.partition(":")[0] in rule[1]


def _temporal_word(word: str, refs: dict[str, str], base: dict) -> dict:
    """A temporal word runs from the commitment to the event, whatever letters
    the reply gave; ``unrelated`` and ``uncertain`` propose nothing."""
    event = next((r for r in refs.values() if r.startswith("event:")), "")
    owed = next((r for r in refs.values() if r.startswith("commitment:")), "")
    if word not in TEMPORAL_RELATIONS:
        return {**base, "decision": word}
    if not event or not owed or not _allowed(word, owed, event):
        return base
    return {**base, "decision": word, "from": owed, "to": event, "relation": word}


def _pair(word: str, item: dict, refs: dict[str, str], base: dict) -> dict:
    """A pair word with its direction (§11.3). A directed word needs two
    different letters; ``related`` takes A to B when it was given none. A
    relation `effective.RELATIONS` refuses for ``(from, to)`` is ``uncertain``,
    so no proposal names a link `create_link` would reject."""
    if word not in _RELATION_OF:
        return {**base, "decision": word}
    frm, to = _ref_of(item.get("from"), refs), _ref_of(item.get("to"), refs)
    if word not in _DIRECTED and (not frm or not to or frm == to):
        frm, to = refs.get("A", ""), refs.get("B", "")
    if not frm or not to or frm == to:
        return base
    relation = _RELATION_OF[word]
    if relation and not _allowed(relation, frm, to):
        return base
    if not relation and frm.partition(":")[0] != to.partition(":")[0]:
        return base
    return {**base, "decision": word, "from": frm, "to": to, "relation": relation}


def _lifecycle_word(word: str, base: dict) -> dict:
    """A status word stands only with a reason and a known evidence scene (§11.4)."""
    if word in _STATUS_OF:
        if not base["reason"] or not base["evidence_scenes"]:
            return base
        return {**base, "decision": word, "status": _STATUS_OF[word]}
    return {**base, "decision": word}


def _decide(item: dict, cand: dict, known: set[str]) -> dict:
    """One reply element rebuilt field by field into a cache proposal (§6)."""
    vocab = cand["vocabulary"]
    word = item.get("decision")
    word = word.strip().casefold() if isinstance(word, str) else ""
    word = word if word in RECONCILE_DECISIONS[vocab] else "uncertain"
    reason = item.get("reason")
    base = {"decision": "uncertain", "from": "", "to": "", "relation": "", "status": "",
            "reason": reason.strip()[:RECONCILE_REASON_CHARS] if isinstance(reason, str) else "",
            "evidence_scenes": _evidence(item.get("evidence_scenes"), known)}
    refs = {r["letter"]: r["ref"] for r in cand["records"]}
    if vocab in ("thread", "commitment"):
        return _lifecycle_word(word, base)
    if vocab == "temporal":
        return _temporal_word(word, refs, base)
    return _pair(word, item, refs, base)


def reconcile_parse_output(text: str, payload: dict) -> dict[str, dict] | None:
    """The reply's proposals, ``{candidate id: proposal}``, or None when it
    holds no decodable object at all -- a failed run, whose deterministic
    findings stand, which is not the same as a decodable reply with nothing
    usable in it (``{}``, spec §24).

    A key is matched without its ``Candidate`` label; an unknown one is
    dropped, and a repeated one keeps its first answer. A word outside the
    candidate's vocabulary, a direction §5.3 does not allow, or a status word
    without a reason and a known evidence scene is ``uncertain``. Unknown
    scene ids are dropped, and the reason is clipped. Nothing here raises on
    bad JSON."""
    obj = extract_object(text)
    if obj is None:
        return None
    items = obj.get("decisions")
    by_key = {c["key"]: c for c in payload["candidates"]}
    known = set(payload["known_scenes"])
    out: dict[str, dict] = {}
    seen: set[str] = set()
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict):
            continue
        key = _candidate_key(item.get("candidate"))
        cand = by_key.get(key)
        if cand is None or key in seen:
            continue
        seen.add(key)
        out[cand["id"]] = _decide(item, cand, known)
    return out
