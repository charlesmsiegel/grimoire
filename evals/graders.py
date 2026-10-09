"""Pass/fail scorers over one model output, and over the prompt that produced
it. Pure: no store reads, no network.

Every grader here re-uses the PRODUCTION code that consumes model output —
scenes.split_reply, length_drift.measure, fence.FenceWatcher,
absorb.extract_object — rather than reimplementing its parsing. That is the
whole point: an eval that parses output its own way stops testing the app the
moment the app's parser changes, and would have gone green through exactly the
regression it exists to catch.

The first place that rule is deliberately inverted is grade_absorb, which scores
the RAW object rather than parse_output's normalised result — see its docstring
for why re-using the tolerant parser there would make the checks unfailable.
The continuity graders and grade_scene_suggestions invert it the same way for
the same reason, each for the checks its docstring names.

Each grader returns a list of Check. A case passes when every check passes.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from grimoire import decisions
from grimoire.store import absorb, fence, length_drift, scenes, suggest
from grimoire.store.continuity import drivers, identity, reconcile

from . import slop

# Eval-owned, unlike TRIM: the app has no opinion about a reply being too SHORT
# (drift correction only ever trims), so there is no production constant to
# borrow. Deliberately far below the budget — this is collapse detection, for
# the template edit that renders an empty instruction block and turns 550-word
# replies into 30-word ones. A merely terse dramatic beat must not trip it.
COLLAPSE_RATIO = 0.25

# Feed size for the fence watcher. Small and deliberately not a divisor of any
# fixture length, so the recorded output crosses chunk boundaries mid-opener
# and exercises FenceWatcher's split-delta holdback the way a real stream does.
CHUNK = 7

# Eval-owned too: two suggestion premises sharing at least this share of their
# combined words (casefolded, by word) are one premise reworded, §28.10 case
# 9's clone. Distinct scenes in one campaign share a cast and a setting, so
# some overlap is expected; a reworded clone keeps the sentence and swaps a
# verb or a noun, which leaves well over half of it in common. Tune it against
# live replies if it ever misjudges one.
PREMISE_OVERLAP = 0.5


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


def _chunks(text: str, size: int = CHUNK):
    for i in range(0, len(text), size):
        yield text[i:i + size]


# --------------------------------------------------------------- length budget

def grade_length(text: str, budget: dict, players: frozenset[str],
                 cast_names: list[str]) -> list[Check]:
    """Legacy ensemble drift diagnostic, not actor ceiling conformance.

    Measured through the same two functions the app uses on every real turn:
    split_reply to find the blocks, length_drift.measure to score them. The
    reply is treated as ONE turn (turn_sizes=[n]), which is what it is.
    """
    segments = scenes.split_reply(text, players)
    if not segments:
        return [Check("length.nonempty", False, "reply split into zero blocks")]

    messages = [{"role": "assistant", **seg} for seg in segments]
    m = length_drift.measure(messages, [len(segments)], cast_names, budget, window=1)
    if m is None:
        # Reachable, and worth naming: measure() counts only non-synthetic
        # blocks, so a reply made entirely of blocks labelled with the reserved
        # roll/transition speakers has nothing to measure. That is the model
        # forging a dice result in the transcript -- the one thing the roll
        # protocol forbids outright -- so it fails here rather than being
        # scored as a compliant zero-word turn.
        return [Check("length.measurable", False,
                      "reply contained no measurable model blocks (every block "
                      "was labelled with a reserved synthetic speaker)")]

    ratio = m["max_ratio"]
    words = m["totals"][0]
    return [
        Check("length.reply_words", COLLAPSE_RATIO <= ratio < length_drift.TRIM,
              f"{words} words vs budget {budget['reply_words']} "
              f"(ratio {ratio:.2f}; allowed {COLLAPSE_RATIO}-{length_drift.TRIM})"),
        Check("length.blocks", not m["blocks"],
              f"{len(segments)} blocks vs max {budget['blocks']}"),
        Check("length.paragraphs", not m["paragraphs"],
              f"max paragraphs per block {budget['paragraphs']}"),
        Check("length.speakers", not m["speakers"],
              f"max distinct speakers {budget['speakers']}"),
        Check("length.blocks_per_speaker", not m["blocks_per_speaker"],
              f"max blocks per speaker {budget['blocks_per_speaker']}"),
    ]


# -------------------------------------------------------------- turn taking

def grade_turn_taking(text: str, nomination: dict, players: frozenset[str],
                      npc_names: list[str]) -> list[Check]:
    """Did the reply hand the turn to the character the prompt nominated?

    #82's question, asked one turn at a time. The failure the active-speaker
    layer exists to prevent is a multi-turn shape — one character monologues
    while three stand silent — but with a lead nominated on every turn, that
    shape can only re-form if single turns ignore the nomination. So the
    per-turn question IS the whole question, and it is the half no offline
    check can answer: `--live` is what these score.

    "Carries" is measured in WORDS, not blocks. Block counts are what the
    monologue hides behind: one obliging four-word line for the nominated lead
    and three paragraphs for the character who has been talking all scene ties
    1-1 and scores green, which is the reply this case exists to catch. Words
    come from `length_drift._words`, so a roll fence is not credited to
    whoever's block it landed in — the same subtraction drift measurement
    makes.

    Speakers are canonicalized through `match_name`, the same function the
    nomination and drift measurement use: a reply stamped "Seraphine" for
    "Seraphine Vale" is that character speaking, not a stranger who happens to
    be quiet. A label naming nobody present is dropped rather than counted as
    an actor — `length_drift._identity` keeps it as itself instead, which is
    right for measuring reply LENGTH and wrong here, where every question is
    about who among the present cast took the turn.
    """
    lead = (nomination or {}).get("lead")
    if not lead:
        # Nothing was nominated: `nominate` returns None below two present
        # NPCs, so a fixture that lost a cast member lands here. Reported
        # rather than raised, for grade_absorb's reason — a grader that raises
        # takes the whole run down instead of reporting what it was handed.
        return [Check("turns.nominated", False,
                      "no lead was nominated; there is nothing to score against")]

    blocks: dict[str, int] = {}
    words: dict[str, int] = {}
    strays: set[str] = set()
    for seg in scenes.split_reply(text, players):
        if not seg["speaker"]:
            continue                     # narration, or a block routed to it
        who = scenes.match_name(seg["speaker"], npc_names)
        if not who:
            strays.add(seg["speaker"])
            continue
        blocks[who] = blocks.get(who, 0) + 1
        words[who] = words.get(who, 0) + length_drift._words(seg["content"])

    if not blocks.get(lead):
        # Three different things send a live run to three different places, so
        # the detail distinguishes them: somebody present took the turn, nobody
        # did because the reply carried no speaker markers at all (a reply
        # FORMAT failure, not a nomination one), or the blocks went to labels
        # naming nobody on stage. A report that read the same for all three
        # would have #82 answered from the wrong evidence.
        if blocks:
            carried = f"it was carried by {', '.join(sorted(blocks))}"
        elif strays:
            carried = ("its blocks name nobody present: "
                       f"{', '.join(sorted(strays))}")
        else:
            carried = "the reply carried no **Name:** blocks at all"
        return [Check("turns.lead_speaks", False,
                      f"nominated {lead!r} has no block in the reply; {carried}")]

    # Short-circuited above rather than reported alongside: "the lead said
    # nothing at all" and "the lead was out-talked" are different failures with
    # different fixes, and a counterexample cannot isolate either one if both
    # always fire together.
    rivals = {n: w for n, w in words.items() if n != lead}
    loudest = max(rivals.values(), default=0)
    top = ", ".join(sorted(n for n, w in rivals.items() if w == loudest))
    return [
        Check("turns.lead_speaks", True),
        Check("turns.lead_carries", words[lead] >= loudest,
              f"nominated {lead!r} got {words[lead]} words against "
              f"{loudest} for {top}" if rivals else ""),
        # "Do not give every character a turn" is the section's other
        # instruction, and the other half of the failure #82 names: four
        # characters answering the same question in sequence is not turn-taking
        # either, it is the monologue's mirror image.
        Check("turns.some_stay_quiet", len(blocks) < len(npc_names),
              f"all {len(npc_names)} present NPCs took a block"),
    ]



# ------------------------------------------------------------------ roll fence

def grade_roll_fence(text: str, allowed_checks: set[str],
                     allowed_actors: set[str]) -> list[Check]:
    """Does a roll-requiring prompt emit a parseable ```roll fence?

    Streamed through FenceWatcher in small chunks, so this scores the fence the
    way the live route sees it (split across deltas) rather than the way a
    whole-string regex would.

    Both id checks matter and neither implies the other: the protocol's one
    hard rule is "use only the check ids and actor references listed below,
    never invent ids", and a fence naming a real check against an actor who
    isn't on stage fails the roll just as surely as the reverse.
    """
    watcher = fence.FenceWatcher()
    for chunk in _chunks(text):
        watcher.feed(chunk)
    watcher.finish()

    if not watcher.complete and watcher.body is None:
        return [Check("fence.present", False, "no ```roll fence in the reply")]

    out = [Check("fence.present", True),
           Check("fence.closed", watcher.complete and not watcher.truncated,
                 "fence opened but never closed" if watcher.truncated else ""),
           Check("fence.narration", bool(watcher.narration.strip()),
                 "nothing narrated before the fence")]

    # Diagnostic, not an independent gate: parse_roll_body only reports a
    # problem when it recovered no fields or no check id, and either way
    # fence.check_known below fails too. It is kept because "the body was
    # unparseable" and "the body named a check that does not exist" send you to
    # different places, and a report that only ever says the latter costs
    # someone the first ten minutes of debugging.
    fields, problems = fence.parse_roll_body(watcher.body or "")
    out.append(Check("fence.parses", not problems, "; ".join(problems)))
    named = fields.get("check", "")
    out.append(Check("fence.check_known", named in allowed_checks,
                     f"check {named!r} not among {sorted(allowed_checks)}"))
    actor = fields.get("actor", "")
    out.append(Check("fence.actor_known", actor in allowed_actors,
                     f"actor {actor!r} not among {sorted(allowed_actors)}"))
    return out


# ---------------------------------------------------------------------- absorb

# The absorb contract, DERIVED from parse_output rather than restated here.
# parse_output rebuilds a dict of every key it knows, so parsing an empty
# object yields the full contract with each key at its default: str for the two
# prose fields, list for every section. Deriving it means a section added to
# absorb/parse.py and templates/absorb/system.j2 is graded from the day it lands,
# where a hand-kept tuple would silently stop covering it — which is exactly
# how this grader originally shipped missing four sections.
_ABSORB_CONTRACT = absorb.parse_output("{}")
ABSORB_TEXT = tuple(k for k, v in _ABSORB_CONTRACT.items() if isinstance(v, str))
ABSORB_LISTS = tuple(k for k, v in _ABSORB_CONTRACT.items() if isinstance(v, list))


def grade_absorb(text: str) -> tuple[list[Check], dict]:
    """Does absorb produce parseable output with the required sections?

    Graded against the RAW extracted object, not parse_output's result. That
    distinction is the whole check: parse_output is deliberately tolerant — it
    substitutes [] for a missing or wrongly-typed list, and str()s a JSON null
    into the literal "None" — so every "is it a list?" question asked of its
    output answers yes no matter what the model sent, and `"summary": null`
    reads as a four-character summary. Scoring the normalised value would make
    this grader unfailable, which is worse than not having it.

    Returns the checks AND the parsed object, so a caller with a live store can
    push it through materialize() as a further check (see cases.py).
    """
    raw = absorb.extract_object(text)
    if raw is None:
        return ([Check("absorb.json", False,
                       "no JSON object recoverable from the reply")], {})

    out = [Check("absorb.json", True)]
    for field in ABSORB_TEXT:
        value = raw.get(field)
        out.append(Check(f"absorb.{field}",
                         isinstance(value, str) and bool(value.strip()),
                         f"{field} was {value!r}, wanted a non-empty string"))
    for field in ABSORB_LISTS:
        value = raw.get(field)
        if not isinstance(value, list):
            out.append(Check(f"absorb.{field}", False,
                             f"{field} was {type(value).__name__}, wanted a list"))
            continue
        # A null ENTRY is the other thing the tolerant parser swallows: every
        # section loop skips non-dicts, so [null, {...}] reads downstream as a
        # clean one-entry section. Element TYPE is deliberately not checked
        # beyond this — sections hold dicts but `keywords` holds strings, and
        # the derived contract cannot tell them apart. Null is the one value
        # that is wrong in every section.
        nulls = sum(1 for e in value if e is None)
        out.append(Check(f"absorb.{field}", not nulls,
                         f"{field} held {nulls} null entr{'y' if nulls == 1 else 'ies'}"))

    try:
        parsed = absorb.parse_output(text)
    except Exception as exc:                                    # noqa: BLE001
        # parse_output assumes shapes the checks above may just have rejected
        # (iterating a null section, indexing a string). A grader that raises
        # takes the whole run down instead of reporting the bad output it was
        # handed, so the failure is recorded as a check like any other.
        return out + [Check("absorb.parses", False,
                            f"{type(exc).__name__}: {exc}")], {}
    return out + [Check("absorb.parses", True)], parsed


# -------------------------------------------------------------------- identity

#: What the identity check may answer for a row, borrowed rather than
#: restated, so a decision word added to the app is graded the day it lands.
IDENTITY_DECISIONS = identity.DECISIONS


def _identity_word(value) -> str | None:
    """A decision word as the app compares it (case and padding ignored), or
    None for anything that is not a string."""
    return value.strip().lower() if isinstance(value, str) else None


def _identity_id(item: dict, kind: str | None) -> str:
    """The id an ``existing`` answer names, read the way
    `identity.Examination.decide` reads it: through `identity._bare`, which
    removes a ``<kind>:`` prefix only for the row's OWN kind. A thread row
    answered with ``commitment:<id>`` keeps that prefix, so it matches no
    offered id -- exactly as the app downgrades it as not offered. A row the
    prompt never listed has no kind and no prefix is removed: the app ignores
    such a row, and nothing was offered to it either way."""
    if kind is None:
        rid = item.get("id")
        return rid.strip() if isinstance(rid, str) else ""
    return identity._bare(kind, item.get("id"))


def _identity_verdict(key: str, got: dict, want: dict, kind: str | None) -> Check:
    word = _identity_word(got.get("decision"))
    rid = _identity_id(got, kind)
    ok = word == want["decision"] and (want["decision"] != "existing" or rid == want["id"])
    wanted = want["decision"] + (f" {want['id']}" if want["decision"] == "existing" else "")
    return Check(f"identity.{want['check']}", ok,
                 f"row {key} was {got.get('decision')!r} {rid!r}, wanted {wanted}")


def grade_identity_decision(text: str, items: Sequence[decisions.Item],
                            rows: list[dict], expected: dict[str, dict]) -> list[Check]:
    """The duplicate check through `decide()`: does the reply decode, answer
    every row in the contract's words, name only ids it was offered -- and give
    the right verdict on each scored row?

    Read the app's way after the switch, through `decisions.parse` and
    `identity.answers_of`, except where that reading would make a check
    unfailable: the parse turns a word outside the decisions into an unread
    answer, which `answers_of` hands on as ``""`` (``uncertain``), so the enum
    check is scored on the parsed answers' `NOT_AN_OPTION` detail rather than
    on the mapping. `identity.json` is the one check on the raw reply
    (`decisions.find_object`), and with no object nothing else is reported.

    An item the reply never reached (`NO_ITEM`) fails `identity.covers_rows`
    alone: its own verdict is left out rather than failed beside it, so "the
    row was skipped" and "the row was misjudged" stay separable. `known_ids`
    fails an ``existing`` whose `id` was not read (an id the row was not
    offered, or none). `expected` maps a row key to ``{"decision", "id",
    "check"}``: the verdict that row should get, and the name of the check that
    reports it."""
    if decisions.find_object(text) is None:
        return [Check("identity.json", False, "no JSON object recoverable from the reply")]
    results = decisions.parse(text, items, explain=True)
    decided = [r.answers[identity.DECISION_ID] for r in results]
    skipped = [row["key"] for row, answer in zip(rows, decided, strict=True)
               if answer.detail == decisions.NO_ITEM]
    unknown = [row["key"] for row, answer in zip(rows, decided, strict=True)
               if answer.detail == decisions.NOT_AN_OPTION]
    unnamed = [row["key"] for row, result in zip(rows, results, strict=True)
               if result.answers[identity.DECISION_ID].answer == "existing"
               and not isinstance(_record_answer(result), str)]
    by_row = {a["row"]: a for a in identity.answers_of(rows, results) or []}
    return [
        Check("identity.json", True),
        Check("identity.covers_rows", not skipped, f"no decision for {skipped}"),
        Check("identity.enum", not unknown,
              f"decisions outside {list(IDENTITY_DECISIONS)} for {unknown}"),
        Check("identity.known_ids", not unnamed,
              f"existing named no offered id on {unnamed}"),
    ] + [_identity_verdict(key, by_row[key], want, None)
         for key, want in expected.items() if key in by_row]


def _record_answer(result: decisions.ItemResult) -> object:
    answer = result.answers.get(identity.RECORD_ID)
    return answer.answer if answer is not None else None


# ------------------------------------------------------------------- reconcile

#: The status words that need positive evidence -- borrowed, so the grader and
#: the parser cannot disagree about the list.
RECONCILE_STATUS = frozenset(reconcile._STATUS_OF)


def _letter(value) -> str:
    """A direction letter as the parser reads it (padding and case ignored)."""
    return value.strip().upper() if isinstance(value, str) else ""


def _reconcile_verdict(key: str, got: dict, want: dict) -> Check:
    word = _identity_word(got.get("decision"))
    frm = _letter(got.get("from"))
    directions: dict[str, str] = want.get("from") or {}
    ok = word in want["decisions"] and frm == directions.get(word, frm)
    wanted = " or ".join(w + (f" from {directions[w]}" if w in directions else "")
                         for w in want["decisions"])
    return Check(f"reconcile.{want['check']}", ok,
                 f"candidate {key} was {got.get('decision')!r} from {frm or '-'}, "
                 f"wanted {wanted}")


def grade_reconcile_decision(text: str, items: Sequence[decisions.Item], payload: dict,
                             expected: dict[str, dict]) -> list[Check]:
    """The reconciliation sweep through `decide()`: does the reply decode,
    answer every candidate in its own vocabulary, found every status word on a
    scene its item showed -- and give the right verdict on each scored
    candidate?

    Read through `decisions.parse`, and scored on the RAW parsed answers
    rather than `reconcile.proposals_of`: the mapping rewrites a word outside
    the vocabulary, a refused direction and an unfounded closure all as
    ``uncertain``, so an enum or evidence check over its output could never
    fail. `reconcile.json` is the
    one check on the raw reply (`decisions.find_object`), and with no object
    nothing else is reported.

    A candidate the reply never reached (`NO_ITEM`) fails `reconcile.covers`
    alone: its own verdict is left out rather than failed beside it.
    `reconcile.enum` fails a decision answered with no option of its item
    (`NOT_AN_OPTION`). `reconcile.evidence` fails a status word with no
    evidence scene read in any `reconcile.EVIDENCE_IDS` slot; a rationale is
    not required (I4), and an item's options are only the scenes it shows.
    Each verdict reads the decision answer and the `from` letter.
    `expected` maps a candidate key to ``{"check", "decisions", "from"}``: the
    words that candidate may be decided as, the check that reports it, and,
    per directed word, the letter its ``from`` must name."""
    if decisions.find_object(text) is None:
        return [Check("reconcile.json", False, "no JSON object recoverable from the reply")]
    results = decisions.parse(text, items, explain=True)
    keyed = [(cand["key"], result) for cand, result in
             zip(payload["candidates"], results, strict=True)]
    decided = {key: result.answers[reconcile.DECISION_ID] for key, result in keyed}
    skipped = [key for key, answer in decided.items() if answer.detail == decisions.NO_ITEM]
    unknown = [key for key, answer in decided.items()
               if answer.detail == decisions.NOT_AN_OPTION]
    unfounded = [key for key, result in keyed
                 if decided[key].answer in RECONCILE_STATUS
                 and not any(isinstance(_answer_of(result, slot), str)
                             for slot in reconcile.EVIDENCE_IDS)]
    read = {key: {"decision": decided[key].answer,
                  "from": _answer_of(result, reconcile.FROM_ID)}
            for key, result in keyed if decisions.was_read(decided[key])}
    return [
        Check("reconcile.json", True),
        Check("reconcile.covers", not skipped, f"no decision for {skipped}"),
        Check("reconcile.enum", not unknown,
              f"decisions outside their candidate's vocabulary: {unknown}"),
        Check("reconcile.evidence", not unfounded,
              f"status words without an evidence scene the item showed: {unfounded}"),
    ] + [_reconcile_verdict(key, read[key], want)
         for key, want in expected.items() if key in read]


def _answer_of(result: decisions.ItemResult, question: str) -> object:
    answer = result.answers.get(question)
    return answer.answer if answer is not None else None


# ----------------------------------------------------------- scene suggestions

def _raw_driver_misses(entries: list[dict], kinds: dict[str, str]) -> list[str]:
    """Every raw `drivers` entry that names no driver of the index, or an
    action its kind does not take -- read as the model wrote it, so nothing
    `claim` canonicalizes or drops is forgiven here."""
    misses: list[str] = []
    for n, entry in enumerate(entries, 1):
        raw = entry.get("drivers", [])
        if not isinstance(raw, list):
            misses.append(f"suggestion {n}: drivers was {type(raw).__name__}")
            continue
        for e in raw:
            ref = e.get("ref") if isinstance(e, dict) else None
            action = e.get("action") if isinstance(e, dict) else None
            allowed = drivers.ACTIONS_BY_KIND.get(kinds.get(ref, "") if isinstance(ref, str)
                                                  else "", ())
            if action not in allowed:
                misses.append(f"suggestion {n}: {ref!r} as {action!r}")
    return misses


def _raw_anchor_misses(entries: list[dict], anchors: set[str]) -> list[str]:
    misses: list[str] = []
    for n, entry in enumerate(entries, 1):
        raw = entry.get("time_anchor")
        if raw is None:
            continue
        if not isinstance(raw, dict):
            misses.append(f"suggestion {n}: time_anchor was {type(raw).__name__}")
        elif "ref" in raw and not (isinstance(raw["ref"], str) and raw["ref"] in anchors):
            misses.append(f"suggestion {n}: {raw['ref']!r}")
    return misses


def _notation_ok(provider, raw) -> bool:
    """`raw` is a date the calendar itself writes: it round-trips its own
    parse and format unchanged."""
    if not isinstance(raw, str):
        return False
    try:
        return provider.format(provider.parse(raw)) == raw
    except Exception:  # noqa: BLE001 -- calendar plugin code can raise anything; unreadable is not its notation
        return False


def _date_misses(entries: list[dict], claims: list[dict], snapshot: dict, controls,
                 provider) -> tuple[list[str], list[str]]:
    """`(misses, checked dates)`: each date normalized the way the app reads
    model text, then held to the anchor and time rules by the app's own
    `check_date`. Outside an `on` batch the raw string must also be in the
    calendar's own notation, which the tolerant normalizer would launder."""
    on = bool(controls.anchor) and controls.relation == "on"
    now = snapshot.get("now") or ""
    misses: list[str] = []
    checked: list[str] = []
    for n, (entry, claimed) in enumerate(zip(entries, claims, strict=True), 1):
        raw = entry.get("date", "")
        normalized = suggest.normalize_date(provider, now, raw)
        date, rejected = suggest.check_date(provider, snapshot, controls,
                                            claimed["time_anchor"], normalized)
        checked.append(date)
        if rejected or not date:
            misses.append(f"suggestion {n}: {raw!r} "
                          + ("fails the anchor or time rule" if rejected else "is no date"))
        elif not on and not _notation_ok(provider, raw):
            misses.append(f"suggestion {n}: {raw!r} is not in the calendar's own notation")
    return misses, checked


def _on_derived(checked: list[str], snapshot: dict, controls, provider) -> Check:
    option = next((a for a in snapshot.get("anchors", []) if a["ref"] == controls.anchor), {})
    fixed = option.get("fixed")
    try:
        want = provider.format(fixed) if isinstance(fixed, int) else ""
    except Exception:  # noqa: BLE001 -- calendar plugin code can raise anything
        want = ""
    wrong = [d for d in checked if d != want]
    return Check("suggest.on_derived", bool(want) and not wrong,
                 f"dates {wrong} are not the anchor's own {want!r}")


def _premise_words(entry: dict) -> frozenset[str]:
    return frozenset(re.findall(r"\w+", str(entry.get("premise", "")).casefold()))


def _premise_clones(entries: list[dict]) -> list[tuple[int, int]]:
    """Pairs of suggestions (1-based) whose premises are one premise reworded:
    their word sets overlap by at least `PREMISE_OVERLAP` of their union."""
    words = [_premise_words(e) for e in entries]
    pairs: list[tuple[int, int]] = []
    for i, a in enumerate(words):
        for j in range(i + 1, len(words)):
            union = a | words[j]
            if union and len(a & words[j]) / len(union) >= PREMISE_OVERLAP:
                pairs.append((i + 1, j + 1))
    return pairs


def _focus_holders(focus, claimed: list[set[str]]) -> list[int]:
    """The suggestions (1-based) that claim at least one focus ref."""
    return [n for n, refs in enumerate(claimed, 1) if refs & set(focus)]


def grade_scene_suggestions(text: str, snapshot: dict, controls, provider) -> list[Check]:
    """Do the suggestions spread the focus, cite only known drivers, and carry
    dates the anchor rule accepts, in the calendar's own notation?

    §28.10 case 9 is read off the premises and the spread, not only titles and
    claims: `distinct` also fails two premises that are one reworded
    (`PREMISE_OVERLAP`), and `focus_spread` fails a batch of two or more cards
    whose two or more covered focus refs one card holds alone. A spread asks
    for two holders, not one card per ref: a card serving both beside one
    serving either is spread. It reads only the covered refs, so a ref no card
    serves is `focus_coverage`'s miss and never a spread miss too.

    Pure: the snapshot, the controls and the calendar provider are handed in.
    The reply is decoded by the app's `suggest.raw_suggestions`, a claim is
    resolved by `suggest.claim` and a date judged by `suggest.check_date`.
    Two checks score the RAW entry instead, as `grade_absorb` does and for its
    reason: `known_refs` and `anchor_known`, because `claim` drops an unknown
    ref and the batch anchor overrides the model's, so the claimed result
    could never show either miss; and the notation half of `date_consistent`,
    because the tolerant normalizer reads the friendly form too.

    Every check reads only the entries the app keeps (`suggest.is_card`: a
    title and a premise). A dropped entry is no card the player sees, so it
    covers no focus and makes no batch distinct; and its raw refs, anchor and
    date are not graded either, since none of them reaches the player. A reply
    whose every entry is dropped fails `suggest.json`, as one that decodes to
    nothing does.
    """
    raw = suggest.raw_suggestions(text)
    entries = [e for e in raw or [] if suggest.is_card(e)]
    if not entries:
        return [Check("suggest.json", False,
                      "no suggestion list recoverable from the reply" if raw is None
                      else "the reply decoded to no suggestions" if not raw
                      else "every suggestion lacks a title or a premise")]
    claims = [suggest.claim(e, snapshot, controls) for e in entries]
    kinds = {d["ref"]: d["kind"] for d in snapshot.get("driver_index", [])}
    anchors = {a["ref"] for a in snapshot.get("anchors", [])}
    claimed = [{d["ref"] for d in c["drivers"]}
               | ({c["time_anchor"]["ref"]} if c["time_anchor"] else set()) for c in claims]
    uncovered = [r for r in controls.focus if not any(r in refs for refs in claimed)]
    holders = _focus_holders(controls.focus, claimed)
    titles = [str(e.get("title", "")).strip().casefold() for e in entries]
    clones = _premise_clones(entries)
    served = [frozenset(d["ref"] for d in c["drivers"] if d["action"] != "anchor")
              for c in claims]
    unknown = _raw_driver_misses(entries, kinds)
    invented = _raw_anchor_misses(entries, anchors)
    bad_dates, checked = _date_misses(entries, claims, snapshot, controls, provider)
    out = [
        Check("suggest.json", True),
        Check("suggest.known_refs", not unknown, f"unknown drivers or actions: {unknown}"),
        Check("suggest.anchor_known", not invented,
              f"time anchors outside the anchor options: {invented}"),
        Check("suggest.focus_coverage", not uncovered,
              f"focus drivers no suggestion claims: {uncovered}"),
        Check("suggest.focus_spread",
              len(controls.focus) - len(uncovered) < 2 or len(entries) < 2 or len(holders) >= 2,
              f"every focus driver is claimed by suggestion {holders} alone"),
        Check("suggest.distinct",
              len(entries) >= 2 and len(set(titles)) == len(titles) and len(set(served)) > 1
              and not clones,
              f"{len(entries)} suggestions, titles {titles}, "
              f"claims {[sorted(s) for s in served]}, one premise reworded: {clones}"),
        Check("suggest.date_consistent", not bad_dates, f"dates: {bad_dates}"),
    ]
    if controls.anchor and controls.relation == "on":
        out.append(_on_derived(checked, snapshot, controls, provider))
    return out


# ------------------------------------------------------------------ decisions

#: The detail of a `decide.rationale` that is not applicable: the item was
#: answered by a native decisions endpoint, which carries no rationale.
NATIVE_RATIONALE = "n/a: answered natively, which carries no rationale"


def grade_decision(text: str, items: Sequence[decisions.Item], *, explain: bool,
                   question: str, expected: object,
                   max_rationale: int | None = None, native: bool = False) -> list[Check]:
    """Does a one-item decision reply decode, answer `question` with
    `expected`, and -- when `explain` asked it to -- say why?

    Read the app's way, through `decisions.parse`, which never raises and reads
    a value of the wrong type (the string "true", a `1` for a predicate) as
    `None` rather than a default. `decide.json` is the one check scored on the
    raw reply, because `parse` answers every item whatever it was sent: whether
    the reply held an object at all is `decisions.find_object`'s answer, the
    parser's own first step. With no object the answer and rationale checks are
    not reported, as `fence.present` short-circuits the fence checks.

    `decide.answer` compares the type as well as the value, so a choice id and
    a level index cannot pass for each other, nor `1` for `True`.
    `max_rationale` caps `decide.rationale` too, where the call site refuses a
    longer one (voice drift's `MAX_NOTE`). A decision asked with no rationale
    (`explain=False`, the speaker pick) has no `decide.rationale` to fail.
    Nor does an item a native endpoint answered (`native`, a live run's
    `native_items`): it was never asked for one, so its `decide.rationale`
    passes visibly as `NATIVE_RATIONALE` rather than failing a backend for
    what its wire cannot carry. A structured answer keeps the real check.
    """
    if decisions.find_object(text) is None:
        return [Check("decide.json", False, "no JSON object recoverable from the reply")]
    (result,) = decisions.parse(text, items, explain=explain)
    answer = result.answers[question]
    checks = [Check("decide.json", True),
              Check("decide.answer", answer.answer == expected
                    and type(answer.answer) is type(expected),
                    f"{question} answered {answer.answer!r} ({answer.reason or 'read'}), "
                    f"expected {expected!r}")]
    if not explain:
        return checks
    if native:
        return [*checks, Check("decide.rationale", True, NATIVE_RATIONALE)]
    return [*checks, _rationale_check(result.rationale, max_rationale)]


def _rationale_check(rationale: str, cap: int | None) -> Check:
    if not rationale.strip():
        return Check("decide.rationale", False, "no rationale, though the prompt asked for one")
    return Check("decide.rationale", cap is None or len(rationale) <= cap,
                 f"a rationale of {len(rationale)} characters, over the {cap} the call "
                 f"site accepts")


# ------------------------------------------------------------ prompt contract

def grade_prompt(messages: list[dict], required: dict[str, str]) -> list[Check]:
    """Is the instruction this case's property depends on still IN the prompt?

    Replay mode scores a fixed recording, so nothing it does to the output can
    notice a template edit. These checks close that gap from the other side:
    they run against the freshly assembled prompt, so deleting the response
    budget section, or the roll protocol, or a key from the absorb contract,
    fails offline and immediately.

    Needles are ids, headings and resolved VALUES, never sentences. Pinning
    prose would make every reword a failure and push people to stop editing
    prompts, which is the opposite of the point — templates/ is meant to be
    edited freely. What must not change silently is whether the instruction is
    there at all.

    The known cost of that choice: a template gutted down to just the tokens —
    the heading and the number with the sentence around them deleted — still
    passes. These checks catch the section going AWAY, not the section going
    vague. Only --live can judge the second.
    """
    text = prompt_text(messages)
    return [Check(f"prompt.{name}", needle in text,
                  f"assembled prompt no longer contains {needle!r}")
            for name, needle in required.items()]


def prompt_text(messages: list[dict]) -> str:
    return "\n".join(m["content"] for m in messages)


def grade_prompt_section(messages: list[dict], name: str, template: str,
                         **vars) -> list[Check]:
    """Did this whole rendered SECTION reach the assembled prompt, intact?

    Stronger than a needle and no more brittle. The expected text is produced
    by rendering the template itself, so a reword moves both sides together and
    still passes — but every value the section interpolates is covered, not
    just the one or two a hand-written needle happens to name. That is what
    lets "the budget reached the model" mean all five knobs rather than the
    word count alone.

    An empty render is its own failure. A section emptied out would otherwise
    satisfy `"" in text` and report success for the exact edit this exists to
    catch.
    """
    from grimoire import prompts

    rendered = prompts.render(template, **vars).strip()
    if not rendered:
        return [Check(f"prompt.{name}", False, f"{template} rendered nothing")]
    return [Check(f"prompt.{name}", rendered in prompt_text(messages),
                  f"{template} no longer reaches the assembled prompt")]


# ------------------------------------------------------------ lore containment

def grade_containment(text: str, secret: str) -> list[Check]:
    """Did the model keep a secret it was never told out of its prose?

    The prompt-side half of this hypothesis (that the secret is absent from the
    assembled prompt in the first place, and present when its owner IS on
    stage) is graded in cases.py, where the store is available. Both halves
    matter: containment that holds only because the entry never activates at
    all is not containment, it is a broken fixture.
    """
    return [Check("containment.output", secret.lower() not in text.lower(),
                  f"reply leaked the owned lore marker {secret!r}")]


# ------------------------------------------------------------------ slop

def grade_slop(text: str, players: frozenset[str], established: frozenset[str],
               rendered_block: str) -> list[Check]:
    """Score a reply against the graded subset of the selected prose guide.

    A strict subset, deliberately: the template's semantic instructions -- the
    rule of three, redundant adjective pairs, explaining an emotion just shown,
    decorative metaphor -- are not gradable by regex, and the spec's ungraded
    inventory names every one. A green result means the graded subset held, not
    that the block was obeyed. slop.not_x_but_y in particular is a floor on its
    family rather than a decision procedure for it.

    `rendered_block` is the CURRENT selected prose guide, so the drift
    guard fails when an instruction this grader scores against has left the
    template.
    """
    prose, names = slop.normalize(text, players)
    gone = slop.missing_sources(rendered_block)
    measurable, m_detail = slop.is_measurable(prose)
    phrases = slop.found_phrases(prose)
    stock = slop.found_stock_names(prose, names, established)
    beats = slop.overused_beats(prose)
    constructions = slop.found_constructions(prose)
    s_ok, s_detail = slop.sentence_variance(prose)
    p_ok, p_detail = slop.paragraph_variance(prose)
    return [
        Check("slop.list_current", not gone,
              f"no longer in selected prose guide: {gone}"),
        Check("slop.measurable", measurable, m_detail),
        Check("slop.phrases", not phrases, f"banned phrases present: {phrases}"),
        Check("slop.stock_names", not stock,
              f"unestablished stock names present: {stock}"),
        Check("slop.beat_words", not beats,
              f"beat words past {slop.BEAT_REPEAT_MAX}: {beats}"),
        Check("slop.not_x_but_y", not constructions,
              f"banned constructions present: {constructions}"),
        Check("slop.sentence_variance", s_ok, s_detail),
        Check("slop.paragraph_uniformity", p_ok, p_detail),
        Check("slop.em_dash_spacing", not slop.em_dash_adjacent(prose),
              "consecutive paragraphs both use an em dash"),
    ]
