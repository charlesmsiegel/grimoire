"""Pass/fail scorers over one model output, and over the prompt that produced
it. Pure: no store reads, no network.

Every grader here re-uses the PRODUCTION code that consumes model output —
scenes.split_reply, length_drift.measure, fence.FenceWatcher,
absorb.extract_object — rather than reimplementing its parsing. That is the
whole point: an eval that parses output its own way stops testing the app the
moment the app's parser changes, and would have gone green through exactly the
regression it exists to catch.

The one place that rule is deliberately inverted is grade_absorb, which scores
the RAW object rather than parse_output's normalised result — see its docstring
for why re-using the tolerant parser there would make the checks unfailable.

Each grader returns a list of Check. A case passes when every check passes.
"""

from __future__ import annotations

from dataclasses import dataclass

from grimoire.store import absorb, fence, length_drift, scenes

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
    """Does the reply respect the resolved length budget?

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
    shape can only occur if single turns ignore the nomination. So the per-turn
    question IS the whole question, and it is the half no offline check can
    answer: `--live` is what these three score.

    Speakers are canonicalized through the same `match_name` the nomination and
    drift measurement use. A reply that stamps "Seraphine" for "Seraphine Vale"
    is that character speaking, not a stranger who happens to be quiet.

    `turns.lead_carries` short-circuits rather than failing alongside
    `turns.lead_speaks`: "the lead said nothing at all" and "the lead was
    out-talked by someone else" are different failures with different fixes,
    and reporting the second whenever the first fires would make it impossible
    for a counterexample to isolate either.
    """
    lead = nomination["lead"]
    spoken: dict[str, int] = {}
    for seg in scenes.split_reply(text, players):
        who = seg["speaker"] and scenes.match_name(seg["speaker"], npc_names)
        if who:
            spoken[who] = spoken.get(who, 0) + 1

    if not spoken.get(lead):
        return [Check("turns.lead_speaks", False,
                      f"nominated {lead!r} has no block in the reply; "
                      f"it was carried by {sorted(spoken) or 'nobody'}")]

    others = {n: c for n, c in spoken.items() if n != lead}
    loudest = max(others.values(), default=0)
    return [
        Check("turns.lead_speaks", True),
        Check("turns.lead_carries", spoken[lead] >= loudest,
              f"nominated {lead!r} took {spoken[lead]} block(s) against "
              f"{loudest} for " + ", ".join(
                  sorted(n for n, c in others.items() if c == loudest))),
        # "Do not give every character a turn" is the section's other
        # instruction, and the other half of the failure #82 names: four
        # characters answering the same question in sequence is not turn-taking
        # either, it is the monologue's mirror image.
        Check("turns.some_stay_quiet", len(spoken) < len(npc_names),
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
