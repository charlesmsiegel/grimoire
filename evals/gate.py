"""The decide gate: a conversion switches its call site to `decide()` only
when the structured parse equals or beats today's parse, offline (spec 7.4,
plan ruling 7).

What it compares is *parsers*, on recorded reply shapes, scored by what the
call site would do with each:

- **A conversion** (`Conversion`) is one call site moving to `decide()`. It
  names the items its production builder makes (from fixed fixture inputs,
  at most one call's worth), today's parse (`legacy`, a frozen copy in
  `evals/legacy.py`), the production mapping from the parsed batch (one
  `ItemResult` per item) to what the call site stores or raises
  (`decide`), and its corpus.
- **A corpus** (`evals/gate/<id>.json`) is a list of entries. Each pairs a
  reply to today's prompt with a reply *of the same shape* to the decide
  prompt, and says what the reply means (`intended`). The legacy replies start
  from today's parser tests: every input string those tests feed the legacy
  parser is the `legacy` of some entry (`legacy_cases`, held by
  `test_every_legacy_parse_case_is_a_gate_entry`), so the tests can be deleted
  without losing a shape.
- **An outcome** is the whole value the call site acts on -- a stored verdict
  with its reason, a speaker with its issue string -- never a boolean alone,
  compared as JSON values (a tuple equals the corpus's list). A call site that
  raises is mapped by its conversion to a value of its own; a mapping that
  raises is a bug in the conversion, and the gate fails loudly rather than
  scoring it. Where the call site checks the parsed value further before it
  stores anything (voice drift's `check_failure`), that production check is
  the conversion's `settle`, applied to *both* sides' parsed values: it is the
  call site, not a parser, so it moves both sides together and leaves today's
  parse the frozen copy alone.
- **The rule**: on every entry, `legacy == intended` implies
  `decide == intended`. The structured parse may beat today's on an entry, and
  may never lose one.

Offline only: nothing here makes a call, and `evals/run.py --gate` refuses
`--live`, `--record` and `--case`. Whether a model *follows* the decide prompt
is `--live`'s question, which spends money and is never automatic.
"""

from __future__ import annotations

import functools
import json
import types
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from grimoire import decisions
from grimoire.store import response_protocol, scene_break, voice_drift
from grimoire.store.continuity import identity, similarity

from . import legacy

GATE_DIR = Path(__file__).resolve().parent / "gate"

_KEYS = frozenset({"shape", "intended", "legacy", "decide", "aux", "ruling"})
_REQUIRED = _KEYS - {"aux", "ruling"}


@dataclass(frozen=True)
class Entry:
    """One recorded shape: `legacy` replies to today's prompt, `decide` to the
    decide prompt, and `intended` is what the call site should end up with.
    `aux` carries a conversion's other replies (scene-break: `aux["title"]`,
    the reply to the title prompt). `ruling`, when present, says why
    `intended` is a deliberate change of behaviour rather than today's
    reading: an entry today's parse may lose BY that ruling, which the report
    prints rather than leaving inside the score."""

    shape: str
    intended: object
    legacy: str
    decide: str
    aux: Mapping[str, str] = field(default_factory=dict)
    ruling: str = ""


@dataclass(frozen=True)
class Conversion:
    """One call site's move to `decide()`, as the gate sees it."""

    id: str
    #: The items the production builder makes, from fixed fixture inputs.
    items: Callable[[], tuple[decisions.Item, ...]]
    #: Whether the decide prompt asks for a rationale (`decisions.parse`).
    explain: bool
    #: Today's reply -> today's outcome, through the frozen parser.
    legacy: Callable[[str], object]
    #: The parsed batch (one `ItemResult` per item, in order) -> the outcome,
    #: through the production mapping.
    decide: Callable[[tuple[decisions.ItemResult, ...], Entry], object]
    #: `GATE_DIR / f"{id}.json"`.
    corpus: Path
    #: The input strings of today's parse tests, copied verbatim.
    legacy_cases: tuple[str, ...]
    #: The call site's own mapping from either side's parsed value to its
    #: outcome, applied to both (`legacy` and `decide` return what it takes).
    #: Production code shared by the two sides, never a parser: the identity
    #: where the parse already is the outcome. `judge` refuses one that binds
    #: anything from `evals.legacy`, at any depth (it would carry today's
    #: parse onto the decide side), and one that merges outcomes the corpus tells apart (a
    #: settle that collapses everything to one value passes any gate).
    settle: Callable[[object], object] = lambda value: value


@dataclass(frozen=True)
class GateResult:
    conversion: str
    entries: int
    legacy_right: int
    decide_right: int
    #: One line per entry today's parse got right and the decide parse did
    #: not, naming its shape.
    regressions: tuple[str, ...]
    #: One line per entry whose `intended` was settled by ruling, with the
    #: ruling and whether today's parse loses it.
    rulings: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.regressions


def _refuse(conv: Conversion, why: str) -> ValueError:
    return ValueError(f"{conv.corpus.name}: {why}")


def _entry(conv: Conversion, index: int, raw: object) -> Entry:
    if not isinstance(raw, dict):
        raise _refuse(conv, f"entry {index} is not an object")
    keys = set(raw)
    if keys - _KEYS or _REQUIRED - keys:
        raise _refuse(conv, f"entry {index} has keys {sorted(keys)}; "
                            f"it needs {sorted(_REQUIRED)} (and may carry 'aux')")
    if not all(isinstance(raw[k], str) for k in ("shape", "legacy", "decide")):
        raise _refuse(conv, f"entry {index}: shape, legacy and decide are strings")
    aux = raw.get("aux", {})
    if not isinstance(aux, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in aux.items()):
        raise _refuse(conv, f"entry {index}: aux maps names to reply strings")
    ruling = raw.get("ruling", "")
    if "ruling" in raw and not (isinstance(ruling, str) and ruling.strip()):
        raise _refuse(conv, f"entry {index}: a ruling is the sentence saying why")
    return Entry(raw["shape"], raw["intended"], raw["legacy"], raw["decide"], aux,
                 ruling.strip())


def load(conv: Conversion) -> tuple[Entry, ...]:
    """The conversion's corpus, refused (ValueError naming the file) when it is
    not a non-empty list of well-formed entries: a gate of no entries would
    pass while proving nothing."""
    try:
        raw = json.loads(conv.corpus.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise _refuse(conv, f"not JSON ({exc})") from exc
    if not isinstance(raw, list) or not raw:
        raise _refuse(conv, "a corpus is a non-empty list of entries")
    return tuple(_entry(conv, i, item) for i, item in enumerate(raw))


def _key(value: object) -> str:
    """A value as JSON text, the form outcomes are compared in."""
    return json.dumps(value, sort_keys=True)


def _same(outcome: object, intended: object) -> bool:
    """Equal as JSON text, not as Python values: Python's `True == 1` would
    let a stored `1` pass for an intended `true`, and a tuple is written as
    the corpus's list. A value JSON cannot hold raises, loudly."""
    return _key(outcome) == _key(intended)


def _short(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _binds(fn: object) -> tuple[str, list[object]]:
    """`fn`'s own module, and every value it binds: those of every global and
    closure value its code (nested code included) names, and the arguments a
    partial wrapping it holds."""
    bound: list[object] = []
    while isinstance(fn, functools.partial):
        bound += [fn.func, *fn.args, *fn.keywords.values()]
        fn = fn.func
    if isinstance(fn, types.MethodType):
        bound.append(fn.__self__)
        fn = fn.__func__
    names: set[str] = set()
    code = getattr(fn, "__code__", None)
    stack = [code] if isinstance(code, types.CodeType) else []
    while stack:
        current = stack.pop()
        names |= set(current.co_names) | set(current.co_freevars)
        stack += [c for c in current.co_consts if isinstance(c, types.CodeType)]
    scope = getattr(fn, "__globals__", {})
    bound += [scope[n] for n in names if n in scope]
    bound += [cell.cell_contents for cell in getattr(fn, "__closure__", None) or ()]
    return getattr(fn, "__module__", "") or "", bound


def _homes(fn: object) -> set[str]:
    """The modules `fn` comes from and binds, to any depth: its own, those of
    every value it binds (`_binds`), and -- for each function, method or
    partial among those -- the same again, so a settle that reaches today's
    parse through a helper of a helper is seen (CODE-M7). Modules are named,
    never walked; each function is walked once."""
    homes: set[str] = set()
    seen: set[int] = set()
    pending = [fn]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        own, bound = _binds(current)
        homes.add(own)
        for value in bound:
            if isinstance(value, types.ModuleType):
                homes.add(value.__name__)
                continue
            homes.add(getattr(value, "__module__", "") or "")
            if isinstance(value, (types.FunctionType, types.MethodType, functools.partial)):
                pending.append(value)
    return homes


def _check_settle(conv: Conversion) -> None:
    if legacy.__name__ in _homes(conv.settle):
        raise ValueError(f"{conv.id}: `settle` binds {legacy.__name__}; it is the call "
                         f"site's check on a parsed value, and may not reach today's parse")


def judge(conv: Conversion) -> GateResult:
    """Score both sides of every entry by its whole outcome."""
    _check_settle(conv)
    items = conv.items()
    decisions.validate(items)
    calls = len(decisions.chunks(items))
    if calls != 1:
        raise ValueError(f"{conv.id}: the gate scores one call's batch; "
                         f"this one needs {calls} calls")
    legacy_right = decide_right = 0
    regressions: list[str] = []
    rulings: list[str] = []
    seen: set[str] = set()
    entries = load(conv)
    for index, entry in enumerate(entries):
        legacy = conv.settle(conv.legacy(entry.legacy))
        parsed = decisions.parse(entry.decide, items, explain=conv.explain)
        decided = conv.settle(conv.decide(parsed, entry))
        seen |= {_key(legacy), _key(decided)}
        legacy_ok, decide_ok = _same(legacy, entry.intended), _same(decided, entry.intended)
        legacy_right += legacy_ok
        decide_right += decide_ok
        if entry.ruling:
            rulings.append(f"entry {index} ({entry.shape}), "
                           f"{'legacy right' if legacy_ok else 'legacy loses'}: "
                           f"{entry.ruling}")
        if legacy_ok and not decide_ok:
            regressions.append(
                f"entry {index} ({entry.shape}): intended {_short(entry.intended)}, "
                f"decide gave {_short(decided)}")
    intended = {_key(entry.intended) for entry in entries}
    if len(seen) < len(intended):
        raise ValueError(f"{conv.id}: settled, both sides produce {len(seen)} distinct "
                         f"outcomes where the corpus intends {len(intended)}; `settle` "
                         f"merges what the corpus tells apart")
    return GateResult(conv.id, len(entries), legacy_right, decide_right, tuple(regressions),
                      tuple(rulings))


# --- scene-break ------------------------------------------------------------
#
# The outcome is what `routes/scenes._break_commit` and `_break_title_commit`
# store: the verdict, its reason, and the title only on a break. The legacy
# title rode in the verdict's reply; since the switch it is a second call's
# reply, which an entry carries as `aux["title"]` and the production
# `parse_title` cleans.

_BREAK_TRANSCRIPT = ("Seraphine Vale: Count it, Mara. Every coin you are owed.\n\n"
                     "Mara: It is all here. The ledger is yours.")
_BREAK_SIGNALS = [{"kind": "length", "weight": 2,
                   "detail": "24 posts since this was last considered"}]
_BREAK_FACTS = {"location": "Saltmarch Pier", "date": "2026-07-05",
                "cast": ["characters/seraphine-vale", "characters/mara"]}


def _break_items() -> tuple[decisions.Item, ...]:
    return (scene_break.build_item(_BREAK_TRANSCRIPT, _BREAK_SIGNALS, _BREAK_FACTS,
                                   "The Debt at the Pier"),)


def _break_legacy(text: str) -> object:
    answer = legacy.scene_break_parse_output(text)
    return [answer["break"], answer["reason"], answer["title"] if answer["break"] else ""]


def _break_decide(results: tuple[decisions.ItemResult, ...], entry: Entry) -> object:
    verdict = scene_break.verdict_of(results[0])
    title = scene_break.parse_title(entry.aux.get("title", "")) if verdict["break"] else ""
    return [verdict["break"], verdict["reason"], title]


SCENE_BREAK = Conversion(
    id="scene-break",
    items=_break_items,
    explain=True,
    legacy=_break_legacy,
    decide=_break_decide,
    corpus=GATE_DIR / "scene-break.json",
    # Verbatim, every input `test_scene_break_store.py`'s parse tests hand
    # `parse_output`.
    legacy_cases=(
        '{"break": true, "reason": "The ledger changed hands.", "title": "The Long Walk Back"}',
        'Sure!\n```json\n{"break": false, "reason": "They are mid-argument."}\n```',
        "", "no idea", "[1, 2, 3]", "null",
        '{"break": true, "reason": "They parted.\\n---\\ndone: false", "title": "A\\nB"}',
        '{"break": "yes", "reason": "r"}',
    ),
)

# --- voice drift ------------------------------------------------------------
#
# The outcome is what `routes/scenes._stage_voice_drift` stores or reports for
# one NPC: `["failed", reason]` when `voice_drift.check_failure` refuses the
# finding, else `[verdict, note]`. Today's side is the frozen parse; the
# decide side maps the parsed item through the production `finding_of`; both
# are settled by the production `check_failure`.

_DRIFT_ANCHOR = "Clipped. Never uses contractions.\nAnswers questions with questions."
_DRIFT_TRANSCRIPT = ("Winifred: Where were you last night?\n\n"
                     "Seraphine Vale: Oh, I was just down at the pier, y'know, "
                     "couldn't sleep, didn't want to wake anybody.")


def _drift_items() -> tuple[decisions.Item, ...]:
    return (voice_drift.build_item("Seraphine Vale", _DRIFT_ANCHOR, _DRIFT_TRANSCRIPT),)


def _drift_decide(results: tuple[decisions.ItemResult, ...], entry: Entry) -> object:
    return voice_drift.finding_of(results[0])


def _drift_settle(finding: object) -> object:
    if not isinstance(finding, dict):
        raise TypeError(f"voice-drift settles a finding dict, not {finding!r}")
    reason = voice_drift.check_failure(finding)
    return ["failed", reason] if reason else [finding["verdict"], finding["note"]]


VOICE_DRIFT = Conversion(
    id="voice-drift",
    items=_drift_items,
    explain=True,
    legacy=legacy.voice_drift_parse_output,
    decide=_drift_decide,
    corpus=GATE_DIR / "voice-drift.json",
    # Verbatim, every input `test_voice_drift_store.py`'s parse tests hand
    # `parse_output`, the `%s` ones with each value they substitute.
    legacy_cases=(
        '{"verdict": "in_voice", "note": "a little terse"}',
        'Here you go:\n```json\n{"verdict": "drift", "note": "She used contractions."}\n```',
        '{"verdict": "in voice"}', '{"verdict": "in-voice"}', '{"verdict": "not enough"}',
        '{"verdict": "insufficient"}', '{"verdict": "unclear"}', '{"verdict": "  DRIFT  "}',
        '{"verdict": "none"}', '{"verdict": "ok"}', '{"verdict": "fine"}',
        '{"verdict": "n/a"}', '{"verdict": "yes"}',
        "I'm sorry, I can't do that.", '{"note": "no verdict"}',
        '{"verdict": null}', '{"verdict": "maybe?"}', '{"verdict": true}',
        '{"verdict": "drift", "note": null}',
        '{"verdict": "drift", "note": {"tone": "terse"}}',
        '{"verdict": "drift", "note": ["terse", "clipped"]}',
        '{"verdict": "drift", "note": 42}',
        '{"verdict": "drift", "note": true}',
    ),
    settle=_drift_settle,
)

# --- speaker ----------------------------------------------------------------
#
# The outcome is the `(next, issue)` pair `routes/character_turns._select`
# hands `_round_state`: a speaker with no issue, a hand-back (`None`, `None`),
# or `None` with one of today's two issue strings. Today's side is the frozen
# `selector_parse` over the round's eligible refs (it adds `grimoire` itself);
# the decide side maps the parsed item through the production `selection_of`.
# A round of one or none never asks, so the roster has two.
#
# The `case-folded` entry records a widening the switch brings:
# `decisions.normalise` (casefolded, runs of spaces or hyphens as `_`) reaches
# refs, so `Characters:Winifred` is Winifred where today's exact match says
# ineligible. Its edge: the collision refusal covers only refs the round
# OFFERS, so with `characters:mara-vale` eligible and a near-twin
# `characters:mara_vale` sitting out, a reply naming `mara_vale` resolves to
# `mara-vale` (today: ineligible). Store ids cannot form such a pair --
# `store.paths.slugify` mints only lowercase letters, digits and single
# hyphens, and on those `normalise` is one-to-one -- so it takes a hand-made
# directory whose id `safe_id` still accepts. Pinned, not changed, by
# `test_speaker_near_twin_out_of_the_roster_resolves_to_the_listed_ref`.

_SPEAKER_ROSTER = [{"ref": "characters:mara", "name": "Mara"},
                   {"ref": "characters:winifred", "name": "Winifred"}]
_SPEAKER_CONVERSATION = [
    {"speaker": "You", "content": "I set the lantern on the crate between them."},
    {"speaker": "Mara", "content": "Well? Somebody say something."},
    {"speaker": "You", "content": "Winifred, where were you when the tide turned?"},
]


def _speaker_items() -> tuple[decisions.Item, ...]:
    return (response_protocol.selector_item(_SPEAKER_ROSTER, _SPEAKER_CONVERSATION),)


def _speaker_decide(results: tuple[decisions.ItemResult, ...], entry: Entry) -> object:
    return response_protocol.selection_of(results[0])


SPEAKER = Conversion(
    id="speaker",
    items=_speaker_items,
    explain=False,
    legacy=functools.partial(legacy.selector_parse, eligible=_SPEAKER_ROSTER),
    decide=_speaker_decide,
    corpus=GATE_DIR / "speaker.json",
    # Verbatim, every whole selector reply today's tests script: the invalid
    # ones `test_response_controls_routes.py` parametrizes, and the replies
    # `test_character_turns.py` and `test_group_play_turns.py` hand `_select`.
    legacy_cases=(
        "not json", "{}", '{"next":"pcs:seraphine"}', '{"next":"absent"}',
        '{"next":null}', '{"next":"characters:mara"}', '{"next":"characters:winifred"}',
    ),
)

# --- continuity identity ----------------------------------------------------
#
# The outcome is what `routes/scenes._resolve_identity` leaves on the
# examination: whether `identity.take` decided (False is today's undecodable
# reply, every row a hint only), and each examined row's decision, status,
# reason and target once `Examination.decide` has run its acceptance guard.
# Today's side is the frozen `identity_parse_output`; the decide side maps the
# parsed batch through the production `answers_of`; both are settled by the
# production `take` on a fresh in-memory examination, so the guard that
# accepts or downgrades an `existing` is the same code on both sides.
#
# The fixture holds every case the guard tells apart: r1 is offered two open
# records and a closed one, one of them the live canonical of an alias source;
# r2 is offered a record r1 is also offered, and one an explicit row already
# moves; r3 is a commitment with one candidate, so its `id` question offers a
# single option beside null.

_IDENTITY_SIGNALS = {"title_equal": False, "slug_equal": False, "tokens": 0.4,
                     "chars": 0.5, "cosine": None, "actors": [], "scenes": [],
                     "anchors": [], "via": "lexical"}

#: id -> (kind, the stored record) of each record a row is offered.
_IDENTITY_RECORDS: dict[str, tuple[similarity.Kind, dict]] = {
    "find-the-ledger": ("thread", {"title": "Find the ledger", "status": "open",
                                   "beat": "Winifred learned the harbour ledger exists."}),
    "maras-map": ("thread", {"title": "Mara's map", "status": "open",
                             "beat": "Mara's map is torn."}),
    "the-burned-chart": ("thread", {"title": "The burned chart", "status": "closed",
                                    "beat": "Seraphine burned the chart."}),
    "winifreds-chart": ("thread", {"title": "Winifred's chart", "status": "open",
                                   "beat": "Winifred's chart shows a reef."}),
    "the-midnight-deadline": ("commitment", {
        "title": "The midnight deadline", "status": "open", "kind": "threat",
        "due": "midnight", "beat": "Seraphine gave Winifred until midnight."}),
}

#: (section, kind, row, candidate ids in rank order) per examined row.
_IDENTITY_ROWS = (
    ("plot_movements", "thread",
     {"title": "Recover the harbour ledger",
      "beat": "Winifred went looking for the harbour ledger.", "status": "open"},
     ("find-the-ledger", "maras-map", "the-burned-chart")),
    ("plot_movements", "thread",
     {"title": "Recover the harbour ledger again",
      "beat": "Winifred searched the harbour for the ledger.", "status": "open"},
     ("find-the-ledger", "winifreds-chart")),
    ("commitment_movements", "commitment",
     {"title": "Seraphine's midnight deadline", "kind": "threat", "status": "open",
      "beat": "Seraphine must pay by midnight.", "due": "midnight"},
     ("the-midnight-deadline",)),
)


def _identity_exam() -> identity.Examination:
    """A fresh in-memory examination of the fixture above: no store."""
    def candidate(rid: str) -> tuple[similarity.Subject, dict]:
        kind, stored = _IDENTITY_RECORDS[rid]
        record = {key: value for key, value in stored.items() if key != "beat"}
        record["beats"] = [{"text": stored["beat"], "scene": "saltmarch-docks"}]
        return similarity.subject(kind, f"{kind}:{rid}", record), dict(_IDENTITY_SIGNALS)

    rows = [identity.Examined(section, index, f"r{index + 1}", kind, dict(row),
                              f"proposed-{index + 1}", [candidate(rid) for rid in ids], [])
            for index, (section, kind, row, ids) in enumerate(_IDENTITY_ROWS)]
    return identity.Examination(rows, len(rows), "basic", "off", "", 0,
                                {("thread", "winifreds-chart")},
                                {"thread:the-old-map": "thread:maras-map"})


def _identity_items() -> tuple[decisions.Item, ...]:
    exam = _identity_exam()
    return identity.build_items(exam.prompt_rows(), exam.live)


def _identity_decide(results: tuple[decisions.ItemResult, ...], entry: Entry) -> object:
    return identity.answers_of(_identity_exam().prompt_rows(), results)


def _identity_settle(answers: object) -> object:
    if answers is not None and not isinstance(answers, list):
        raise TypeError(f"continuity-identity settles decision dicts, not {answers!r}")
    exam = _identity_exam()
    taken = identity.take(exam, answers)
    return [taken, [[e.decision, e.status, e.reason, e.target] for e in exam.rows]]


#: Verbatim, every input `test_continuity_identity.py`'s parse tests hand
#: `parse_output`: the fenced one built by the test's own expression.
_IDENTITY_FENCED = "Here you go:\n```json\n" + json.dumps({"decisions": [
    "not a row",
    {"decision": "new", "id": "", "reason": "no row key"},
    {"row": "   ", "decision": "new"},
    {"row": 3, "decision": "new"},
    {"row": "Row r1", "decision": "EXISTING", "id": " find-the-ledger ",
     "reason": " Same ledger. "},
    {"row": "r2", "decision": "maybe", "id": 7, "reason": "x" * 400},
    {"row": "r1", "decision": "new", "id": "", "reason": "a second answer for r1"},
    {"row": "r3", "decision": None, "reason": ["not", "text"]},
]}) + "\n```"

CONTINUITY_IDENTITY = Conversion(
    id="continuity-identity",
    items=_identity_items,
    explain=True,
    legacy=legacy.identity_parse_output,
    decide=_identity_decide,
    corpus=GATE_DIR / "continuity-identity.json",
    legacy_cases=(
        "I think so.", "{}", '{"decisions": 3}',
        *(json.dumps({"decisions": [{"row": label, "decision": "new"}]})
          for label in ("Row r1", " R1 ", "r1", "ROW R1")),
        _IDENTITY_FENCED,
    ),
    settle=_identity_settle,
)

#: One conversion per prepare task (scene-break, voice drift, speaker,
#: continuity identity), each appended by the task that writes its corpus.
GATES: tuple[Conversion, ...] = (SCENE_BREAK, VOICE_DRIFT, SPEAKER, CONTINUITY_IDENTITY)


def report(results: list[GateResult]) -> str:
    """One line per conversion, each regression and then each entry settled
    by ruling indented under its line."""
    if not results:
        return "gate: no conversions yet"
    lines = []
    for r in results:
        verdict = "PASS" if r.passed else "FAIL"
        lines.append(f"{r.conversion}: legacy {r.legacy_right}/{r.entries}, "
                     f"decide {r.decide_right}/{r.entries} -- {verdict}")
        lines.extend(f"    {line}" for line in r.regressions)
        lines.extend(f"    ruling: {line}" for line in r.rulings)
    return "\n".join(lines)
