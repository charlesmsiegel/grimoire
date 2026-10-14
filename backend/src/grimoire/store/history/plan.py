"""Retrieval query planning (roadmap 10-C1): one bounded question about history.

Theory: the planner never searches. It is shown only what 09's filtered seeds
offer -- each record as a short handle (`s1`, `s2`, ...) beside its name, the
refs kept here -- and answers with a `Plan` that 09 turns into one more
`Query`. A handle it was not offered fails validation exactly, so a plan can
name only what the prompt carried, and no store id reaches the provider.

The schema carries shapes only (01f-C3's portable subset refuses every bound),
so every bound lives in `read`, which is pure and total: a field that is
missing or the wrong type takes its empty default rather than voiding the
plan, the per-section tolerance `continuity/doc.py` uses for a hand-edited
file. A previous plan's text (the repair hop's `tried`) is model-written, so
it is rendered as quoted data after this same normalisation, never as
instruction; signals, ranks and scores are never shown, so a planner cannot
learn to ask for the reasons 09 already used.

Pure apart from rendering templates, so a caller runs it in a worker.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from ... import prompts, schemas
from . import model, query

#: The planner's whole data input, in bytes; the turn window is never cut.
PLAN_INPUT_BYTES = 6000
#: The turn window's own bound (the recent end of 09's prompt-view window).
WINDOW_BYTES = 2000
#: Per-part caps (spec 5.1).
MAX_CAST = 12
MAX_MENTIONED = 12
MAX_THREADS = 10
MAX_GROUPS = 8
MAX_EVIDENCE = 6
#: Parse-side bounds (spec 5.2: the schema cannot carry them).
MAX_SUBJECTS = 6
MAX_QUESTIONS = 3
QUESTION_CHARS = 200
MAX_TERMS = 8
TERM_CHARS = 60
#: One line of found evidence, in characters.
SUMMARY_CHARS = 160

#: Why a reply yielded the plan it did, as the trace spells it.
OK = "ok"
DECLINED = "declined"
UNREADABLE = "unreadable"
EMPTY = "empty"

_ACTOR_KINDS = ("characters", "pcs")
_THREAD_KINDS = ("thread", "commitment")


@dataclass(frozen=True)
class Record:
    """One record the planner may name: its ref (kept server-side), the name
    it is shown by, and the part of the input it belongs to."""

    ref: str
    name: str
    kind: str


@dataclass(frozen=True)
class Found:
    """One piece of evidence already found, as the planner sees it."""

    title: str
    date: str = ""
    summary: str = ""


@dataclass(frozen=True)
class Tried:
    """A previous plan, as quoted data: subjects as the handles it was
    offered under, questions and terms already normalised by `read`."""

    subjects: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanInput:
    window: str
    cast: tuple[Record, ...] = ()
    mentioned: tuple[Record, ...] = ()
    threads: tuple[Record, ...] = ()
    groups: tuple[Record, ...] = ()
    found: tuple[Found, ...] = ()
    tried: Tried | None = None
    coverage: str = ""
    tiers: tuple[int, ...] = ()
    depth: int = 1
    perspective: str = "narrator"


@dataclass(frozen=True)
class Offered:
    """The handles a prompt carried: `handles` maps each to its ref,
    `names` each ref to the name it was shown by."""

    handles: Mapping[str, str] = field(default_factory=dict)
    names: Mapping[str, str] = field(default_factory=dict)

    def handle_of(self, ref: str) -> str:
        return next((h for h, r in self.handles.items() if r == ref), "")


@dataclass(frozen=True)
class Plan:
    needs_history: bool
    subjects: tuple[str, ...]          # refs, validated
    questions: tuple[str, ...]
    terms: tuple[str, ...]
    scopes: frozenset[str]
    max_scenes: int
    perspective: str = "narrator"
    dropped: Mapping[str, int] = field(default_factory=dict)   # for the trace only
    #: The names the subjects were offered under, in order: `query_of`'s
    #: extra terms. Not part of the contract.
    subject_names: tuple[str, ...] = ()

    def adds_nothing_to(self, other: Plan) -> bool:
        """True when this plan's subjects, questions and terms are each a
        subset of `other`'s after normalisation: it would retrieve nothing
        the other did not (the repair hop's novelty rule, spec 8)."""
        return (set(self.subjects) <= set(other.subjects)
                and {_key(q) for q in self.questions} <= {_key(q) for q in other.questions}
                and {_key(t) for t in self.terms} <= {_key(t) for t in other.terms})


def _key(text: str) -> str:
    return query.normalized(text)


def _check_perspective(perspective: str) -> None:
    # Until 11-C1 classifies what an actor may know, matching 09-C1.
    if perspective != "narrator":
        raise ValueError("history planning supports the narrator perspective only")


# --- the input ------------------------------------------------------------------


def tail_bytes(text: str, limit: int) -> str:
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= limit:
        return text
    clipped = raw[-limit:].decode("utf-8", errors="ignore")
    # A cut word is not a word: start at the first whole one.
    return re.sub(r"^\S*\s*", "", clipped, count=1) or clipped


def one_line(text: str, limit: int) -> str:
    first = " ".join(str(text or "").split())
    return clip_words(first, limit)


def clip_words(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > 0 else cut).rstrip()


def build_input(window: str, catalog: query.Catalog, subjects: Sequence[str],
                evidence: model.Evidence, *, depth: int, tried: Tried | None = None,
                perspective: str = "narrator",
                involved: frozenset[str] = frozenset()) -> PlanInput:
    """`PlanInput` from data 09's query step already read: the window, the
    catalog, its FILTERED seeds (`Query.subjects`, after 09 section 5.1's
    gates: a ref the reader excluded and a gm-only record are never seeds,
    and `catalog.allowed` is asked again here), and the evidence so far.

    `involved` names the threads and commitments that involve a present
    actor; they come first, before `MAX_THREADS` cuts the rest (spec 5.1)."""
    _check_perspective(perspective)

    def name(ref: str) -> str:
        if ref in catalog.threads:
            title = str(catalog.threads[ref].get("title") or "")
            if title:
                return title
        names = catalog.names.get(ref) or ()
        # Never the ref's id as a name: an unnamed record is not offered,
        # since a handle exists to keep store ids server-side.
        return names[0] if names else ""

    def kind(ref: str) -> str:
        return ref.partition(":")[0]

    allowed = [ref for ref in subjects if catalog.allowed(ref) and name(ref)]
    cast = [r for r in allowed if r in catalog.present and kind(r) in _ACTOR_KINDS]
    threads = [r for r in allowed if kind(r) in _THREAD_KINDS]
    threads.sort(key=lambda r: r not in involved)   # stable: cast-related first
    groups = [r for r in allowed if kind(r) == "groups"]
    rest = [r for r in allowed if r not in cast and r not in threads and r not in groups]
    found = []
    for item in evidence.items[:MAX_EVIDENCE]:
        summary = item.summary if item.summary and not item.summary_omission else ""
        if not summary and item.excerpts:
            summary = item.excerpts[0].text
        found.append(Found(one_line(item.title, SUMMARY_CHARS), one_line(item.date, 60),
                           one_line(summary, SUMMARY_CHARS)))
    return PlanInput(
        window=tail_bytes(window, WINDOW_BYTES),
        cast=tuple(Record(r, name(r), kind(r)) for r in cast[:MAX_CAST]),
        mentioned=tuple(Record(r, name(r), kind(r)) for r in rest[:MAX_MENTIONED]),
        threads=tuple(Record(r, name(r), kind(r)) for r in threads[:MAX_THREADS]),
        groups=tuple(Record(r, name(r), kind(r)) for r in groups[:MAX_GROUPS]),
        found=tuple(found), tried=tried, coverage=evidence.coverage.verdict,
        tiers=tuple(evidence.coverage.tiers), depth=max(depth, 1), perspective=perspective)


def _handles(inp: PlanInput) -> dict[str, Record]:
    """Every offered record by its handle, numbered in input order."""
    records = (*inp.cast, *inp.mentioned, *inp.threads, *inp.groups)
    return {f"s{n}": r for n, r in enumerate(records, 1)}


def _quote(text: str) -> str:
    # Inside a fenced block: a backtick run could close the fence early.
    return text.replace("`", "'")


def _data(inp: PlanInput, by_handle: Mapping[str, Record]) -> dict:
    handle = {r.ref: h for h, r in by_handle.items()}

    def rows(records):
        return [{"handle": handle[r.ref], "name": r.name, "kind": r.kind}
                for r in records if r.ref in handle]

    tried = None
    if inp.tried is not None:
        tried = {"subjects": list(inp.tried.subjects),
                 "questions": [_quote(q) for q in inp.tried.questions],
                 "terms": [_quote(t) for t in inp.tried.terms]}
    return {"window": inp.window, "cast": rows(inp.cast), "mentioned": rows(inp.mentioned),
            "threads": rows(inp.threads), "groups": rows(inp.groups),
            "found": [{"title": f.title, "date": f.date, "summary": f.summary}
                      for f in inp.found],
            "tried": tried, "coverage": inp.coverage, "tiers": list(inp.tiers),
            "depth": inp.depth}


def _render_user(inp: PlanInput, by_handle: Mapping[str, Record]) -> str:
    return prompts.render("history_plan/user.j2", **_data(inp, by_handle)).strip()


def clip(inp: PlanInput) -> PlanInput:
    """`inp` within `PLAN_INPUT_BYTES` of rendered data, dropping from the
    bottom of spec 5.1's table upward, one entry at a time -- coverage, what
    was tried, evidence found, groups, threads, mentioned records, the cast --
    and never the turn window. A record dropped here is not offered."""
    by_handle = _handles(inp)

    def size(candidate: PlanInput) -> int:
        kept = {h: r for h, r in by_handle.items()
                if r in (*candidate.cast, *candidate.mentioned, *candidate.threads,
                         *candidate.groups)}
        return len(_render_user(candidate, kept).encode("utf-8"))

    while size(inp) > PLAN_INPUT_BYTES:
        if inp.coverage or inp.tiers:
            inp = replace(inp, coverage="", tiers=())
        elif inp.tried is not None:
            inp = replace(inp, tried=None)
        elif inp.found:
            inp = replace(inp, found=inp.found[:-1])
        elif inp.groups:
            inp = replace(inp, groups=inp.groups[:-1])
        elif inp.threads:
            inp = replace(inp, threads=inp.threads[:-1])
        elif inp.mentioned:
            inp = replace(inp, mentioned=inp.mentioned[:-1])
        elif inp.cast:
            inp = replace(inp, cast=inp.cast[:-1])
        else:
            break   # the window alone: never cut further
    return inp


def offered(inp: PlanInput) -> Offered:
    """The handles the prompt `build_messages(inp)` builds carries."""
    clipped = clip(inp)
    by_handle = _handles(inp)
    kept = {*clipped.cast, *clipped.mentioned, *clipped.threads, *clipped.groups}
    return Offered({h: r.ref for h, r in by_handle.items() if r in kept},
                   {r.ref: r.name for h, r in by_handle.items() if r in kept})


def schema_for(offered: Offered, depth: int) -> dict:
    """The plan's JSON Schema: shapes only, inside 01f-C3's portable subset.
    The bounds it cannot carry ride as descriptions and are enforced by
    `read`. With nothing offered, `subjects` is a plain string array: an
    empty `enum` is accepted by no strict mode."""
    subject: dict = {"type": "string"}
    if offered.handles:
        subject["enum"] = sorted(offered.handles, key=lambda h: int(h[1:]))
    return {
        "type": "object", "additionalProperties": False,
        "required": ["needs_history", "subjects", "questions", "terms", "scopes",
                     "max_scenes"],
        "properties": {
            "needs_history": {"type": "boolean"},
            "subjects": {"type": "array", "items": subject,
                         "description": f"At most {MAX_SUBJECTS} handles from the list."},
            "questions": {"type": "array", "items": {"type": "string"},
                          "description": f"At most {MAX_QUESTIONS}, each under "
                                         f"{QUESTION_CHARS} characters."},
            "terms": {"type": "array", "items": {"type": "string"},
                      "description": f"At most {MAX_TERMS}, each under {TERM_CHARS} "
                                     "characters."},
            "scopes": {"type": "array", "items": {"type": "string",
                                                  "enum": sorted(model.RELATIONS)}},
            "max_scenes": {"type": "integer",
                           "description": f"From 1 to {max(depth, 1)}."},
        },
    }


def build_messages(inp: PlanInput) -> list[dict]:
    """The planner's prompt: the system instruction carrying
    `schemas.render(schema)` byte-for-byte (01f-C1 refuses a prompt without
    it), and the clipped data as the user message."""
    _check_perspective(inp.perspective)
    clipped = clip(inp)
    by_handle = _handles(inp)
    kept = {h: r for h, r in by_handle.items()
            if r in (*clipped.cast, *clipped.mentioned, *clipped.threads, *clipped.groups)}
    schema = schema_for(offered(inp), inp.depth)
    system = prompts.render(
        "history_plan/system.j2", schema=schema, repair=inp.tried is not None,
        max_subjects=MAX_SUBJECTS, max_questions=MAX_QUESTIONS,
        question_chars=QUESTION_CHARS, max_terms=MAX_TERMS, term_chars=TERM_CHARS,
        depth=inp.depth).strip()
    return [{"role": "system", "content": system},
            {"role": "user", "content": _render_user(clipped, kept)}]


# --- the reply -------------------------------------------------------------------


def _strings(value: object, dropped: dict[str, int], name: str) -> list[str]:
    if not isinstance(value, list):
        if value is not None:
            dropped[name] = dropped.get(name, 0) + 1
        return []
    out = []
    for entry in value:
        if isinstance(entry, str) and entry.strip():
            out.append(" ".join(entry.split()))
        else:
            dropped[name] = dropped.get(name, 0) + 1
    return out


def _bounded(found: list[str], limit: int, chars: int, keep, dropped: dict[str, int],
             name: str) -> tuple[str, ...]:
    out: dict[str, str] = {}
    for raw in found:
        text = clip_words(raw, chars)
        if not keep(text) or _key(text) in out:
            dropped[name] = dropped.get(name, 0) + 1
            continue
        if len(out) >= limit:
            dropped[name] = dropped.get(name, 0) + 1
            continue
        out[_key(text)] = text
    return tuple(out.values())


def _question(text: str) -> bool:
    return any(ch.isalpha() for ch in text)


def _term(text: str) -> bool:
    key = _key(text)
    return len(key) > 1 and key not in query.STOPWORDS and _question(text)


def read(text: object, offered: Offered, depth: int, *,
         perspective: str = "narrator") -> tuple[Plan | None, str]:
    """`(plan, why)`: the validated plan (or None) and `OK`, `DECLINED`,
    `UNREADABLE` or `EMPTY`. Pure and total: it never raises on any reply."""
    try:
        return _read(text, offered, depth, perspective)
    except Exception:  # noqa: BLE001 -- total by contract; a reply is untrusted text
        return None, UNREADABLE


def _read(text: object, offered: Offered, depth: int,
          perspective: str) -> tuple[Plan | None, str]:
    value = schemas.find_value(text, arrays=False) if isinstance(text, str) else None
    if not isinstance(value, dict):
        return None, UNREADABLE
    dropped: dict[str, int] = {}
    depth = max(int(depth), 1)
    subjects: list[str] = []
    names: list[str] = []
    for handle in _strings(value.get("subjects"), dropped, "subjects"):
        ref = offered.handles.get(handle.strip())
        if ref is None or ref in subjects or len(subjects) >= MAX_SUBJECTS:
            dropped["subjects"] = dropped.get("subjects", 0) + 1
            continue
        subjects.append(ref)
        names.append(offered.names.get(ref, ""))
    questions = _bounded(_strings(value.get("questions"), dropped, "questions"),
                         MAX_QUESTIONS, QUESTION_CHARS, _question, dropped, "questions")
    terms = _bounded(_strings(value.get("terms"), dropped, "terms"),
                     MAX_TERMS, TERM_CHARS, _term, dropped, "terms")
    scopes = frozenset(s for s in _strings(value.get("scopes"), dropped, "scopes")
                       if s in model.RELATIONS)
    raw_max = value.get("max_scenes")
    if isinstance(raw_max, int) and not isinstance(raw_max, bool):
        max_scenes = min(max(raw_max, 1), depth)
    else:
        max_scenes = depth
    survived = bool(subjects or questions or terms)
    raw_needs = value.get("needs_history")
    # Absent (or not a boolean) reads as true: with something surviving it is
    # a plan, with nothing it is `EMPTY`. Only an explicit false declines.
    needs = raw_needs if isinstance(raw_needs, bool) else True
    plan = Plan(needs, tuple(subjects), questions, terms, scopes, max_scenes,
                perspective, dropped, tuple(n for n in names if n))
    if not needs:
        return plan, DECLINED
    if not survived:
        return None, EMPTY
    return plan, OK


def parse(text: object, offered: Offered, depth: int) -> Plan | None:
    """10-C1's reader: the validated `Plan`, or None for an unreadable or
    empty reply. A `needs_history: false` reply is a plan (declined), never
    None. Total: it never raises."""
    return read(text, offered, depth)[0]


def tried_of(plan: Plan, offered: Offered) -> Tried:
    """`plan` as the repair prompt's quoted data: its subjects as the handles
    they were offered under (dropped when the repair is not offered them)."""
    return Tried(tuple(h for h in (offered.handle_of(r) for r in plan.subjects) if h),
                 plan.questions, plan.terms)


def query_of(plan: Plan, turn_index: int | None) -> model.Query:
    """One planned round's `Query`: the questions as texts (the turn window
    is not repeated -- the first retrieval already ran on it, and merging
    keeps its signals), the terms plus the subjects' names, the subjects,
    the scopes or every relation, and the plan's own scene count."""
    _check_perspective(plan.perspective)
    terms = tuple(dict.fromkeys((*plan.terms, *plan.subject_names)))
    texts = plan.questions or ((" ".join(terms),) if terms else ())
    return model.Query(texts, terms=terms, subjects=plan.subjects,
                       scopes=plan.scopes or model.RELATIONS, max_scenes=plan.max_scenes,
                       perspective=plan.perspective, turn_index=turn_index)


def projection(plan: Plan | None, names: Mapping[str, str]) -> dict | None:
    """A plan as the trace shows it: refs as names, JSON-safe."""
    if plan is None:
        return None
    return {"needs_history": plan.needs_history,
            "subjects": [names.get(r, r) for r in plan.subjects],
            "questions": list(plan.questions), "terms": list(plan.terms),
            "scopes": sorted(plan.scopes), "max_scenes": plan.max_scenes,
            "dropped": dict(plan.dropped)}

