"""Absorb identity: which proposed records might already exist (capstone spec §10).

Before absorb treats a plot thread or commitment as NEW, this module asks which
of the extraction's rows would open a record, and which stored same-type
records each of those is plausibly the same business as. It reads; it never
writes, and it decides nothing on its own -- the resolver (and after it, the
reviewer) does that, and `similarity` only chooses what they see.

**Proposed-new is the materializer's answer, not a second opinion.** A row is
proposed-new iff the id `absorb.materializer.assign_ids` gives it names no
stored record of its type -- the same call `materialize` makes, over the same
ledgers, so the two cannot disagree about which slug collisions are honoured,
which `slug-N` a CJK title lands on, or which ids earlier rows in the batch
reserved. A row the assignment drops (blank beat, no usable id or title, a
second move of one record) is ignored. An id-less row whose slug collision the
§10.5 predicate honours -- an open record with the same title -- is the model
naming a record it was shown, and materialize stages it onto that record
whatever a resolver would say; it is a target here, like an explicit id, and is
not asked about (deviation 2). A proposed row is matched under the title the
new record would carry -- its title, or else its assigned id (§10.2) -- so an
id-only row is not compared by a blank title line; §9.1's "no ids" rule is
about stored records' ids, not this.

**Neighbours** come from `similarity.pool`: effective records, closed and
resolved included (a closed thread is shown so the resolver knows it was
settled), keyed by canonical ref, so a merged-away alias source is never a
candidate. At most `similarity.IDENTITY_TOP_K` per row, never padded.

**Embeddings** are an enhancement. When a space is configured, the proposed
texts are always embedded, and the pool's uncached texts that pass the weak
lexical floors against some proposal are warmed (up to the warm limit); every
other pool text is read from the cache only. So a lexically unrelated
paraphrase is found only once its neighbour's vector is already cached -- by an
earlier absorb's warm, or by Slice D's reconcile sweep, which warms the whole
ledger. An embedding failure is a mode, never an exception: the lexical
and structural candidates still stand.

**The resolver** is one batched call over every examined row (§10.2): its
prompt (`build_prompt`, the `continuity_identity/` templates) shows each row,
its citation and identity fields, and its candidates with their signals, and
nothing else of the campaign. `parse_output` rebuilds the reply field by field;
a reply with no decodable object is None -- a failed check -- never ``[]``,
which is a decodable reply with nothing usable in it (§24).
"""

from __future__ import annotations

import math
import re
from typing import Literal

from ... import prompts
from .. import fieldtext
from ..absorb import materializer as absorb_materializer
from ..absorb import parse as absorb_parse
from . import canon, effective, involvement, similarity

#: Parsed section -> the record kind its rows open.
SECTIONS: dict[str, similarity.Kind] = {"plot_movements": "thread",
                                        "commitment_movements": "commitment"}

#: The private row key an accepted retarget carries its original row under, for
#: materialize to build the as-new alternative from.
AS_NEW_KEY = "_identity_as_new"

#: What the resolver may answer for a row (spec §10.2).
DECISIONS = ("existing", "new", "uncertain")

#: What a row's `identity_check.decision` may say: a resolver word, or
#: ``unchecked`` for a row the check never answered.
CHECK_DECISIONS = (*DECISIONS, "unchecked")

#: How a row's decision came to stand: as given, downgraded by the acceptance
#: guard, or a hint only (no usable answer).
STATUSES = ("accepted", "downgraded", "hint_only")

#: A resolver reason is display text; clipping it keeps a runaway reply out of
#: the stored review. To be tuned against real prompts later.
REASON_CHARS = 280

#: What the alias reader can raise on a hand-edited continuity.json whose shape
#: it did not expect; any of them reads as "no aliases", as everywhere else.
_UNREADABLE = (OSError, ValueError, TypeError, KeyError, AttributeError)


def has_proposals(parsed: dict) -> bool:
    """Any row in either section. Cheap: it is what lets an absorb with
    neither section skip the worker thread."""
    return any(parsed.get(section) for section in SECTIONS)


class Examined:
    """One proposed-new row that has at least one plausible neighbour."""

    def __init__(self, section: str, index: int, key: str, kind: similarity.Kind,
                 row: dict, assigned: str,
                 candidates: list[tuple[similarity.Subject, dict]],
                 distinguished_from: list[str]):
        self.section = section
        self.index = index            # position in its section
        self.key = key                # "r1", "r2", ... in examination order
        self.kind = kind
        self.row = row                # the parsed row as given
        self.assigned = assigned      # the id materialize would open it under
        self.title = _staged_title(row, assigned)
        self.candidates = candidates
        self.distinguished_from = distinguished_from
        self.decision = "unchecked"
        self.status = "hint_only"
        self.reason = ""
        self.target: str | None = None   # an accepted canonical id


def _certainty(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _candidate(subject: similarity.Subject, signals: dict) -> dict:
    """One neighbour as the resolver prompt shows it: its bare canonical id,
    what it is, its latest beat and up to `similarity.PREVIOUS_BEATS` earlier
    ones, newest first."""
    rec = subject.record
    latest, earlier = similarity.beat_lines(rec)
    return {"id": subject.ref.partition(":")[2], "title": subject.title,
            "status": subject.status, "kind": _text(rec.get("kind")),
            "due": _text(rec.get("due")), "latest_beat": latest, "earlier": earlier,
            "signals": dict(signals)}


def _prompt_row(e: Examined) -> dict:
    row = e.row
    return {"key": e.key, "kind": e.kind, "title": e.title, "beat": _text(row.get("beat")),
            "status": _text(row.get("status")), "commitment_kind": _text(row.get("kind")),
            "due": _text(row.get("due")), "quote": _text(row.get("quote")),
            "speaker": _text(row.get("speaker")),
            "certainty": _certainty(row.get("certainty")),
            "why_new": _text(row.get("why_new")),
            "distinguished_from": list(e.distinguished_from),
            "candidates": [_candidate(s, sig) for s, sig in e.candidates]}


class Examination:
    """What `examine` found: the examined rows and how it looked for them."""

    def __init__(self, rows: list[Examined], proposed: int,
                 matching: Literal["basic", "semantic"],
                 embedding: Literal["off", "configured", "failure"],
                 embedding_error: str, embedded: int,
                 targets: set[tuple[str, str]], live: dict[str, str]):
        self.rows = rows
        self.proposed = proposed
        self.matching = matching
        self.embedding = embedding
        self.embedding_error = embedding_error
        self.embedded = embedded
        self.targets = targets   # (kind, canonical id) non-proposed rows stage onto
        self.live = live         # the live_canon map examine loaded

    def prompt_rows(self) -> list[dict]:
        """The examined rows as the resolver prompt reads them (`build_prompt`).
        Within one row every candidate is the row's own type, so a bare id is
        unambiguous."""
        return [_prompt_row(e) for e in self.rows]


def _text(value) -> str:
    return fieldtext.text(value).strip()


def _staged_title(row: dict, assigned: str) -> str:
    """The title the record materialize would open carries: the row's title,
    or else its assigned id (`materialize`'s ``title or pid``, spec §10.2).

    The proposed subject is matched under it, not under the blank title line.
    §9.1's "no ids" rule is about a STORED record's id; this is the title the
    new record will actually be shown and stored under, so a row that names a
    half-remembered id and no title (``the-lost-map`` beside a stored "The lost
    map") is compared by the words it will be read by.
    """
    return _text(row.get("title")) or assigned


def _live(cid: str, ledgers: effective.Ledgers) -> dict[str, str]:
    try:
        return effective.live_canon(cid, ledgers)
    except _UNREADABLE:
        return {}


def _canonical(live: dict[str, str], kind: str, rid: str) -> str:
    """`rid` mapped through the live alias map, as a bare id."""
    ref = live.get(f"{kind}:{rid}")
    return ref.partition(":")[2] if ref else rid


def _bare(kind: str, value) -> str:
    """A row-supplied id, stripped, with a leading ``<kind>:`` removed."""
    return _text(value).removeprefix(f"{kind}:")


def _distinguished(row: dict, kind: str, stored: dict, live: dict[str, str]) -> list[str]:
    """The row's `distinguished_from`, kept only where it names a stored record
    of the row's type, canonicalized and deduped."""
    named = row.get("distinguished_from")
    out: list[str] = []
    for value in named if isinstance(named, list) else ():
        rid = _bare(kind, value)
        if rid and isinstance(stored.get(rid), dict):
            canonical = _canonical(live, kind, rid)
            if canonical not in out:
                out.append(canonical)
    return out


_Pools = dict[similarity.Kind, list[similarity.Subject]]


class _Proposal:
    """A proposed-new row on its way to being examined."""

    __slots__ = ("assigned", "index", "kind", "row", "section", "subject")

    def __init__(self, section: str, index: int, kind: similarity.Kind, row: dict,
                 assigned: str, subject: similarity.Subject):
        self.section = section
        self.index = index
        self.kind = kind
        self.row = row
        self.assigned = assigned
        self.subject = subject


def _subject(kind: similarity.Kind, n: int, row: dict, assigned: str, sid: str,
             actors: set[str]) -> similarity.Subject:
    record = {"title": _staged_title(row, assigned),
              "beats": [{"text": _text(row.get("beat")), "scene": sid}],
              "kind": _text(row.get("kind")), "due": _text(row.get("due"))}
    # The ref never leaves `examine`: keys are given to examined rows only.
    return similarity.subject(kind, f"proposed:{n}", record, actors=actors, scenes={sid})


def _actors(cid: str, sid: str, facts: dict) -> set[str]:
    cast = facts.get("cast")
    present = {canon.actor_ref(c) for c in (cast if isinstance(cast, list) else ())
               if isinstance(c, str) and c}
    return present | involvement.scene_actors(cid).get(sid, set())


def _classify(cid: str, sid: str, parsed: dict, facts: dict, ledgers: effective.Ledgers,
              live: dict[str, str]) -> tuple[list[_Proposal], set[tuple[str, str]]]:
    """Split the assigned rows into proposals and the targets the rest stage onto."""
    threads = ledgers.threads if ledgers.threads is not None else {}
    assigned = absorb_materializer.assign_ids(threads, ledgers.commitments, parsed, live)
    proposals: list[_Proposal] = []
    targets: set[tuple[str, str]] = set()
    actors: set[str] | None = None
    for section, kind in SECTIONS.items():
        for i, row in enumerate(parsed.get(section) or []):
            slot = assigned.get((section, i))
            if slot is None:
                continue
            if slot.existing:
                targets.add((kind, _canonical(live, kind, slot.id)))
                continue
            if actors is None:
                actors = _actors(cid, sid, facts)
            proposals.append(_Proposal(section, i, kind, row, slot.id,
                                       _subject(kind, len(proposals) + 1, row, slot.id, sid,
                                                actors)))
    return proposals, targets


def _warm(proposals: list[_Proposal], pools: _Pools) -> list[str]:
    """Pool texts lexically near some proposal (`similarity.weak`), best first."""
    best: dict[str, tuple] = {}
    for p in proposals:
        for other in pools[p.kind]:
            signals = similarity.lexical(p.subject, other)
            if not similarity.weak(signals):
                continue
            key = similarity.rank_key(signals, other.ref)
            if other.text not in best or key < best[other.text]:
                best[other.text] = key
    return sorted(best, key=lambda text: best[text])


def _semantic(proposals: list[_Proposal], pools: _Pools,
              embed_deadline: float | None) -> similarity.Semantic:
    space = similarity.available()
    if space is None:
        return similarity.Semantic({}, "off")
    if not proposals:
        return similarity.Semantic({}, "configured")
    cached = [s.text for pool in pools.values() for s in pool]
    return similarity.semantic([p.subject.text for p in proposals], _warm(proposals, pools),
                               deadline=embed_deadline, space=space, cached=cached)


def examine(cid: str, sid: str, parsed: dict, facts: dict, *,
            embed_deadline: float | None) -> Examination:
    """Find the proposed-new rows in `parsed` and their plausible neighbours.

    Runs in a worker thread: it reads the ledgers, the alias map, the scene
    cast and (when configured) the vector cache, and may call the embeddings
    provider until `embed_deadline`. Writes nothing but that cache."""
    ledgers = effective.Ledgers.load(cid)
    live = _live(cid, ledgers)
    proposals, targets = _classify(cid, sid, parsed, facts, ledgers, live)
    kinds = sorted({p.kind for p in proposals})
    pools = {kind: similarity.pool(cid, kind) for kind in kinds}
    sem = _semantic(proposals, pools, embed_deadline)
    vecs = sem.vectors if sem.mode != "off" else None
    stored = {"thread": ledgers.threads or {}, "commitment": ledgers.commitments or {}}
    rows: list[Examined] = []
    for p in proposals:
        candidates = similarity.nearest(p.subject, pools[p.kind], vecs)
        if not candidates:
            continue
        rows.append(Examined(p.section, p.index, f"r{len(rows) + 1}", p.kind, p.row,
                             p.assigned, candidates,
                             _distinguished(p.row, p.kind, stored[p.kind], live)))
    matching: Literal["basic", "semantic"] = (
        "semantic" if similarity.matching() == "semantic" else "basic")
    return Examination(rows, len(proposals), matching, sem.mode, sem.error, sem.embedded,
                       targets, live)


# ------------------------------------------------------------ resolver prompt


def _signal_text(signals: dict) -> str:
    """The signals as fixed-order phrases. Hints for the resolver, never a
    verdict -- the system prompt says so."""
    parts: list[str] = []
    if signals.get("title_equal"):
        parts.append("same title")
    if signals.get("slug_equal"):
        parts.append("same slug")
    for key, label in (("tokens", "word overlap"), ("chars", "text overlap"),
                       ("cosine", "meaning")):
        value = signals.get(key)
        if value:
            parts.append(f"{label} {value:.2f}")
    if signals.get("actors"):
        parts.append("shared characters: " + ", ".join(signals["actors"]))
    if signals.get("scenes"):
        parts.append(f"shared scenes: {len(signals['scenes'])}")
    if signals.get("anchors"):
        parts.append("shared dates: " + ", ".join(signals["anchors"]))
    return "; ".join(parts)


def _line(kind: str, c: dict) -> str:
    if kind == "thread":
        return prompts.render("snippets/plot_thread_line/absorb.j2",
                              t={"id": c["id"], "title": c["title"], "status": c["status"],
                                 "latest_beat": c["latest_beat"]})
    return prompts.render("snippets/commitment_line/absorb.j2",
                          c={"id": c["id"], "title": c["title"],
                             "kind": c["kind"] or "promise", "status": c["status"],
                             "due": c["due"], "latest_beat": c["latest_beat"]})


def template_rows(rows: list[dict]) -> list[dict]:
    """`prompt_rows` output as `continuity_identity/user.j2` reads it: each
    candidate gains its snippet `line` and its `signal_text`. Snippets are
    rendered here, by Python, as every other prompt's are. The input is not
    mutated."""
    return [{**row, "candidates": [{**c, "line": _line(row["kind"], c),
                                    "signal_text": _signal_text(c["signals"])}
                                   for c in row["candidates"]]}
            for row in rows]


def build_prompt(rows: list[dict]) -> list[dict]:
    """The resolver's messages for `prompt_rows` output: one batched call for
    every examined row (spec §10.2), never one per row."""
    return [{"role": "system", "content": prompts.render("continuity_identity/system.j2")},
            {"role": "user", "content": prompts.render("continuity_identity/user.j2",
                                                       rows=template_rows(rows))}]


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
            "decision": word if word in DECISIONS else "uncertain",
            "id": rid.strip() if isinstance(rid, str) else "",
            "reason": reason.strip()[:REASON_CHARS] if isinstance(reason, str) else ""}


def parse_output(text: str) -> list[dict] | None:
    """The resolver's decisions, rebuilt field by field, or None when the reply
    holds no decodable object at all -- a failed check, which is not the same
    as a decodable reply with nothing usable in it (``[]``, spec §24).

    Each usable element becomes ``{row, decision, id, reason}``: the row key as
    the prompt printed it, without its ``Row`` label; an unknown decision word
    as ``uncertain``; a reason clipped to `REASON_CHARS`. A duplicate row key
    keeps its first answer. Nothing here raises on bad JSON."""
    obj = absorb_parse.extract_object(text)
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
