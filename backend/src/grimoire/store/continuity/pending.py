"""What a cached finding means NOW: its current fingerprint and its verdict.

The candidate cache (`candidates`) records what a sweep FOUND. Whether that
finding still deserves a reader's attention is a question about the ledgers and
continuity.json as they stand, and five places ask it: the candidates GET, the
campaign Todo chores, apply, dismiss and both reconcile persists. If each
answered for itself they could disagree -- Todo counting a finding the Ledger
hides, apply accepting one the GET calls stale -- so this module is the one
definition (§5.5: each identifier defined in one module; §18.2: Todo's live
filter is the GET's).

**Why a module of its own** (Decision 1). Neither host the package layout
offers can hold it. `candidates` and `canon` are leaves and cannot import
`effective`; `reconcile` would drag `pressure` and the embeddings client into
Todo's import graph; `review` would drag `undo`. So `pending` imports
`candidates`, `canon`, `doc` and `effective` only, and writes nothing.

**Verdicts** (Decision 2), the first that matches:

1. ``unknown`` -- a ref's ledger cannot be read, continuity.json (or its
   aliases, links or suppressions) is malformed, or the current fingerprint
   cannot be computed. A malformed continuity.json reads as no decisions at
   all, and treating that as truth would resurrect every dismissed finding, so
   while it lasts the finding is hidden and kept, never acted on.
2. ``gone`` -- a ref is not a current canonical record: deleted, merged away,
   or never there. A pair now aliased together is gone too.
3. ``satisfied`` -- a pair with an effective link between its refs, either
   direction, or a lifecycle target that is no longer live.
4. ``suppressed`` -- the current fingerprint is a dismissal's key.
5. ``settled`` -- a model-only nomination the model declined (Decision 9),
   while its stored fingerprint is the current one. Once its records move it
   reads ``gone`` instead: it is not the question the model answered, and it
   was never the reader's, so it must not come back as ``stale`` -- surfacing
   model-only nominations is the flood Decision 9 prevents. ``gone`` hides it,
   404s apply and dismiss, and lets the next persist drop it, so a later
   nomination re-asks the model. (This check precedes ``stale``, against
   Decision 2's written order.)
6. ``stale`` -- the stored fingerprint is not the current one.
7. ``live``.

The GET shows `VISIBLE`; Todo counts ``live``; apply and dismiss accept
``live`` (and ``stale`` resubmitted against the current fingerprint); a persist
keeps ``live``, ``settled`` and ``unknown``, and a `model_only` record while it
reads ``suppressed`` -- a restore has nothing else to bring back.

**Todo's cost rule** (§18.2). Todo calls `findings` and nothing else: three
small JSON reads (the cache, continuity.json, the two ledgers plus events) and
no involvement, chronicle, calendar or embedding work. An empty cache answers
before any ledger is opened.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from .. import fieldtext
from . import candidates, canon, doc, effective

#: How many of a record's merged beats a review row carries.
ROW_BEATS = 3

#: Kind -> Continuity review group (spec §6.2).
GROUP_OF: dict[str, str] = {
    "possible_duplicate": "overlaps",
    "possible_relation": "overlaps",
    "possible_thread_closure": "closures",
    "possible_commitment_resolution": "resolutions",
}

#: Kind -> campaign Todo chore (spec §6.2, §18.2).
CHORE_OF: dict[str, str] = {
    "possible_duplicate": "continuity-overlaps",
    "possible_relation": "continuity-overlaps",
    "possible_thread_closure": "continuity-closures",
    "possible_commitment_resolution": "continuity-closures",
}

#: The verdicts the candidates read lists.
VISIBLE = frozenset({"live", "stale"})
VERDICTS = ("unknown", "gone", "satisfied", "suppressed", "stale", "settled", "live")
TEMPORAL_RELATIONS = ("before", "on", "after", "by")

#: A proposal's decision as the reader sees it -- hedged, never the raw word
#: (Decision 25). `components/continuity/labels.ts` carries the same strings.
DECISION_LABELS: dict[str, str] = {
    "duplicate": "Suggested: same business (merge)",
    "continuation": "Suggested: continuation of",
    "subthread": "Suggested: subthread of",
    "related": "Suggested: related",
    "pays_off": "Suggested: pays off",
    "distinct": "Suggested: different business",
    "close": "Suggested: may be finished",
    "fulfilled": "Suggested: fulfilled",
    "broken": "Suggested: broken",
    "expired": "Suggested: expired",
    "keep_open": "Suggested: keep open",
    **{word: f"Suggested: {word}" for word in TEMPORAL_RELATIONS},
    "unrelated": "No clear suggestion",
    "uncertain": "No clear suggestion",
}

# Ledgers.unreadable names files; a verdict asks by ref prefix.
_PREFIX_OF_LEDGER = {"plot": "thread", "commitments": "commitment", "events": "event"}
_MALFORMED_SECTIONS = frozenset({"file", "aliases", "links", "suppressions"})
# What one record no normalization foresaw can raise out of a verdict.
_RECORD_FAILURES = (AttributeError, TypeError, ValueError, UnicodeEncodeError)


class Current:
    """The current view a verdict is judged against, read once per request."""

    def __init__(self, *, records: dict[str, dict], events: dict[str, dict],
                 live: dict[str, str], links: list[dict], suppressions: dict,
                 unreadable: frozenset[str], continuity_malformed: bool,
                 ledgers: effective.Ledgers):
        self.records = records
        self.events = events
        self.live = live
        self.links = links
        self.suppressions = suppressions
        self.unreadable = unreadable
        self.continuity_malformed = continuity_malformed
        self.ledgers = ledgers

    @classmethod
    def load(cls, cid: str, ledgers: effective.Ledgers | None = None) -> Current:
        ledgers = ledgers or effective.Ledgers.load(cid)
        unreadable = {_PREFIX_OF_LEDGER[name] for name in ledgers.unreadable
                      if name in _PREFIX_OF_LEDGER}
        records: dict[str, dict] = {}
        for kind in ("thread", "commitment"):
            try:
                records.update(effective.records(cid, kind))
            except Exception:  # noqa: BLE001 -- a ledger that will not project is "unknown", never a 500
                unreadable.add(kind)
        events: dict[str, dict] = {}
        for eid, rec in (ledgers.events or {}).items():
            if isinstance(eid, str) and isinstance(rec, dict):
                events[f"event:{eid}"] = {"title": fieldtext.text(rec.get("name")) or eid,
                                          "date": fieldtext.text(rec.get("date"))}
        stored = doc.read(cid)
        return cls(records=records, events=events,
                   live=effective.live_canon(cid, ledgers),
                   links=effective.links(cid, ledgers),
                   suppressions=stored["suppressions"],
                   unreadable=frozenset(unreadable),
                   continuity_malformed=bool(set(doc.malformed(cid)) & _MALFORMED_SECTIONS),
                   ledgers=ledgers)


def _prefix(ref: str) -> str:
    return canon.split_ref(ref)[0]


def is_temporal(record: dict) -> bool:
    """A relation between a commitment and the event it may be dated against."""
    return record.get("kind") == "possible_relation" and any(
        isinstance(r, str) and r.startswith("event:") for r in record.get("refs") or ())


def side(current: Current, ref: str) -> dict | None:
    """One side of a pair fingerprint (Decision 3), or None when `ref` is not a
    current canonical record. Threads carry no kind or due; an event carries its
    date as the due, so rescheduling it changes what a temporal pairing meant."""
    if _prefix(ref) == "event":
        event = current.events.get(ref)
        if event is None:
            return None
        return {"ref": ref, "title": event["title"], "status": "", "kind": "",
                "due": event["date"]}
    rec = current.records.get(ref)
    if rec is None:
        return None
    is_commitment = _prefix(ref) == "commitment"
    return {"ref": ref, "title": fieldtext.text(rec.get("title")),
            "status": fieldtext.text(rec.get("status")),
            "kind": fieldtext.text(rec.get("kind")) if is_commitment else "",
            "due": fieldtext.text(rec.get("due")) if is_commitment else ""}


def _temporal_link_ids(current: Current, ref: str) -> list[str]:
    return [link["id"] for link in current.links
            if link["a"] == ref and link["relation"] in TEMPORAL_RELATIONS]


def _lifecycle_fingerprint(current: Current, kind: str, ref: str) -> str | None:
    rec = current.records.get(ref)
    if rec is None:
        return None
    due = fieldtext.text(rec.get("due")) if _prefix(ref) == "commitment" else ""
    return canon.lifecycle_fingerprint(
        kind, ref, fieldtext.text(rec.get("status")), len(rec.get("beats") or []),
        fieldtext.text(rec.get("latest_beat")), due, _temporal_link_ids(current, ref))


def fingerprint(current: Current, kind: str, refs: list[str]) -> str | None:
    """The finding's fingerprint as the records stand now -- the only place it
    is computed (§5.5). None when a side is missing, or when a title or beat
    cannot be encoded (a lone surrogate a model wrote): that costs this one
    finding an ``unknown``, never the request."""
    try:
        if kind in candidates.PAIR_KINDS:
            sides = [side(current, ref) for ref in refs]
            if any(s is None for s in sides):
                return None
            return canon.pair_fingerprint(kind, [s for s in sides if s is not None])
        return _lifecycle_fingerprint(current, kind, refs[0])
    except UnicodeEncodeError:
        return None


def evidence_ok(proposal: dict | None, scene_ids: set[str]) -> bool:
    """False when the proposal cites a scene that no longer exists (a rename, a
    first datetime stamp, a repad): fingerprints exclude scene ids, so this is
    what makes such a proposal void (§5.6)."""
    if proposal is None:
        return True
    return all(sid in scene_ids for sid in proposal.get("evidence_scenes") or [])


def satisfied(current: Current, kind: str, refs: list[str]) -> bool:
    """A pair the reader already linked, or a lifecycle target already closed
    or resolved -- the question answered some other way."""
    if kind in candidates.PAIR_KINDS:
        pair = set(refs)
        return any({link["a"], link["b"]} == pair for link in current.links)
    ref = refs[0]
    rec = current.records.get(ref)
    return rec is not None and not effective.is_live(_prefix(ref), rec.get("status"))


def model_only(record: dict) -> bool:
    """A nomination no sweep persists on its own (Decision 9): a touched
    lifecycle re-check, which only an incremental sweep makes, or a temporal
    pair, which lands only with the model's proposal. Being cached is its only
    way back, so a dismissal keeps it cached rather than dropping it (§12.7)."""
    return (record.get("signals") or {}).get("reason") == "touched" or is_temporal(record)


def settled(record: dict) -> bool:
    """A model-only nomination the model declined (Decision 9): kept cached so
    it is not re-asked while its fingerprint holds, and shown to nobody."""
    proposal = record.get("proposal")
    if not proposal:
        return False
    decision = proposal.get("decision")
    signals = record.get("signals") or {}
    if signals.get("reason") == "touched" and decision in ("keep_open", "uncertain"):
        return True
    return is_temporal(record) and decision in ("unrelated", "uncertain")


def verdict(current: Current, record: dict) -> str:
    """Decision 2's first match for one cached record."""
    kind, refs = record["kind"], record["refs"]
    if current.continuity_malformed or {_prefix(r) for r in refs} & current.unreadable:
        return "unknown"
    if any(side(current, ref) is None for ref in refs):
        return "gone"
    fp = fingerprint(current, kind, refs)
    if fp is None:
        return "unknown"
    if satisfied(current, kind, refs):
        return "satisfied"
    if fp in current.suppressions:
        return "suppressed"
    if settled(record):
        # Before the stale check: a declined model-only nomination was never
        # the reader's, so records moving under it must not surface it.
        return "settled" if fp == record["fingerprint"] else "gone"
    if fp != record["fingerprint"]:
        return "stale"
    return "live"


def findings(cid: str, current: Current | None = None) -> list[tuple[str, dict, str]]:
    """``(candidate id, cached record, verdict)`` for every cached record, by id.

    An empty cache answers before any ledger is read (Todo's cost rule). Each
    verdict is guarded on its own: a record no normalization foresaw reads as
    ``gone`` -- hidden, and dropped by the next persist -- rather than failing
    the GET or the global To do page (§26 "no campaign failure")."""
    stored = candidates.read(cid)["records"]
    if not stored:
        return []
    current = current or Current.load(cid)
    out: list[tuple[str, dict, str]] = []
    for key in sorted(stored):
        record = stored[key]
        try:
            v = verdict(current, record)
        except _RECORD_FAILURES:
            v = "gone"
        out.append((key, record, v))
    return out


# -------------------------------------------------------------------- rows


def _physical_title(current: Current, ref: str) -> str:
    """The stored title of a record the current view no longer shows -- an
    alias source, a record whose ledger will not project -- or the ref."""
    try:
        prefix, rid = canon.split_ref(ref)
    except ValueError:
        return ref if isinstance(ref, str) else ""
    table = {"thread": current.ledgers.threads, "commitment": current.ledgers.commitments,
             "event": current.ledgers.events}.get(prefix)
    rec = table.get(rid) if isinstance(table, dict) else None
    name = rec.get("name" if prefix == "event" else "title") if isinstance(rec, dict) else None
    return fieldtext.text(name) or ref


def _kind_of(ref) -> str:
    try:
        return _prefix(ref)
    except ValueError:
        return ""


def _empty_row(ref: str, title: str) -> dict:
    return {"ref": ref, "kind": _kind_of(ref), "title": title, "status": "",
            "latest_beat": "", "last_scene": {"id": "", "title": ""}, "pressure": None,
            "aliases": [], "due": "", "beats": [], "gone": False}


def _row(current: Current, ref: str, scene_title: Callable[[str], str],
         pressure: dict) -> dict:
    s = side(current, ref) if _kind_of(ref) else None
    if s is None:
        return {**_empty_row(ref, _physical_title(current, ref)), "gone": True}
    row = {**_empty_row(ref, s["title"]), "due": s["due"], "pressure": pressure.get(ref)}
    rec = current.records.get(ref)
    if rec is None:                              # an event
        return row
    last = fieldtext.text(rec.get("last_scene"))
    beats = [{"scene": fieldtext.text(b.get("scene")), "text": fieldtext.text(b.get("text"))}
             for b in (rec.get("beats") or [])[-ROW_BEATS:]]
    row.update(status=s["status"], latest_beat=fieldtext.text(rec.get("latest_beat")),
               last_scene={"id": last, "title": scene_title(last) if last else ""},
               aliases=list(rec.get("aliases") or []), beats=beats)
    return row


def rows(current: Current, refs: Iterable[str], *,
         scene_title: Callable[[str], str] = lambda sid: "",
         pressure: dict | None = None) -> list[dict]:
    """One review row per ref, joined from the current records. A ref that is
    not a current canonical record reads as ``gone: True`` with its stored
    title (or the ref), never an exception."""
    return [_row(current, ref, scene_title, pressure or {}) for ref in refs]


def label(current: Current, refs: Iterable[str]) -> str:
    """The records' titles joined with " / ", a ref standing in for a missing one."""
    titles = []
    for ref in refs:
        s = side(current, ref) if _kind_of(ref) else None
        titles.append((s["title"] if s is not None else "") or ref)
    return " / ".join(titles)
