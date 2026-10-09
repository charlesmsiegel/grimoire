"""Absorb identity: which proposed records might already exist (capstone spec §10).

Before absorb treats a plot thread or commitment as NEW, this module asks which
of the extraction's rows would open a record, and which stored same-type
records each of those is plausibly the same business as. It reads; it never
writes, and it decides nothing on its own -- the model's answers (and after
them, the reviewer) do that, and `similarity` only chooses what they see.

**Proposed-new is the materializer's answer, not a second opinion.** A row is
proposed-new iff the id `absorb.materializer.assign_ids` gives it names no
stored record of its type -- the same call `materialize` makes, over the same
ledgers, so the two cannot disagree about which slug collisions are honoured,
which `slug-N` a CJK title lands on, or which ids earlier rows in the batch
reserved. A row the assignment drops (blank beat, no usable id or title, a
second move of one record) is ignored. An id-less row whose slug collision the
§10.5 predicate honours -- an open record with the same title -- is the model
naming a record it was shown, and materialize stages it onto that record
whatever the model would say; it is a target here, like an explicit id, and is
not asked about (deviation 2). A proposed row is matched under the title the
new record would carry -- its title, or else its assigned id (§10.2) -- so an
id-only row is not compared by a blank title line; §9.1's "no ids" rule is
about stored records' ids, not this.

**Neighbours** come from `similarity.pool`: effective records, closed and
resolved included (a closed thread is shown so the model knows it was
settled), keyed by canonical ref, so a merged-away alias source is never a
candidate -- less any record whose id and kind alone overrun the identity-text
bound (`_offerable`), which the model could not be shown. At most
`similarity.IDENTITY_TOP_K` per row, never padded.

**Embeddings** are an enhancement. When a space is configured, each proposed
text whose kind has a same-type record to compare against is embedded (one with
an empty pool sends nothing to the provider: no cosine of it could name a
candidate), and the pool's uncached texts that pass the weak lexical floors
against some proposal are warmed (up to the warm limit); every other pool text
is read from the cache only. So a lexically unrelated
paraphrase is found only once its neighbour's vector is already cached -- by an
earlier absorb's warm, or by Slice D's reconcile sweep, which warms the whole
ledger. An embedding failure is a mode, never an exception: the lexical
and structural candidates still stand.

**The check is a set of `decide()` items** (spec §7.4): `build_items` makes one
per examined row, showing the row, its citation and identity fields, and its
candidates with their signals, and nothing else of the campaign. Each asks one
question, `decision`: ``existing:<id>`` once per candidate the row may offer,
then ``new`` and ``uncertain``. The id rides in the decision rather than in a
question of its own, because a native decisions endpoint answers each question
of an item alone, and an `id` asked "unless the decision is existing" was
answered without that decision (spec §7.4). The
phase is one `decide()` over every examined row, chunked, so a long batch is
several metered calls and the per-item wording repeats once per row.
`answers_of` rebuilds the answers field by field into the dicts
`Examination.decide` reads; a reply with no decodable object is None -- a
failed check -- never ``[]``, which is a decodable reply with nothing usable in
it (§24).

**An item the reply never reached is not an item it answered badly.** A row
whose chunk failed, was refused or held nothing that reads as it stays
`unchecked` (I1), whatever the other chunks said, and the phase is never `ok`.
A row the reply did reach and answered badly -- an empty answers object, a null
decision, a word outside the options -- was read, and is `uncertain` (N8), as
today's unknown word has always been. The rationale is display text and
nothing stands on it (I4).

**Deciding** (`Examination.decide`) is where the reply is not trusted: an
``existing`` is accepted only onto an offered, live candidate no other row in
the batch already moves, and is otherwise downgraded to ``uncertain``.
`Examination.rewritten` then retargets the accepted rows by the §10.2 rewrite
(never ``open``, kind and unresolving status kept as stored) and stamps every
examined row with its `identity_check`; materialize stages what it is given.

**A retarget frees the id its row held**, and materialize assigns the batch
again. A later row `assign_ids` dropped as a second move of that proposed
record -- the same title again, or the id it was given spelled out -- was never
examined, and on that second run it would be handed the freed id and open the
very record the model said already exists, with no `identity_check` to say
so. `examine` remembers those siblings per proposal (the rows that stage once
it stops holding its id), and `rewritten` retargets them onto the same record,
where materialize drops them as a second move again: the drop they already had.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Literal

from ... import decisions, prompts
from .. import commitments, fieldtext
from ..absorb import materializer as absorb_materializer
from . import canon, effective, involvement, similarity

#: Parsed section -> the record kind its rows open.
SECTIONS: dict[str, similarity.Kind] = {"plot_movements": "thread",
                                        "commitment_movements": "commitment"}

#: The private row key an accepted retarget carries its original row under, for
#: materialize to build the as-new alternative from.
AS_NEW_KEY = "_identity_as_new"

#: What the decision may answer for a row (spec §10.2).
DECISIONS = ("existing", "new", "uncertain")

#: What a row's `identity_check.decision` may say: a decision word, or
#: ``unchecked`` for a row the check never answered.
CHECK_DECISIONS = (*DECISIONS, "unchecked")

#: How a row's decision came to stand: as given, downgraded by the acceptance
#: guard, or a hint only (no usable answer).
STATUSES = ("accepted", "downgraded", "hint_only")

#: A decision's reason is display text; clipping it keeps a runaway reply out of
#: the stored review. To be tuned against real prompts later.
REASON_CHARS = 280

#: What the alias reader can raise on a hand-edited continuity.json whose shape
#: it did not expect; any of them reads as "no aliases", as everywhere else.
_UNREADABLE = (OSError, ValueError, TypeError, KeyError, AttributeError)

#: The id of each item's first question, a choice over `DECISIONS`.
DECISION_ID = "decision"

#: What a folded ``existing`` option's id begins with: ``existing:<id>`` names
#: the offered candidate whose record the row's beat moves (spec §7.4), so the
#: decision and the record it names are one answer.
EXISTING_PREFIX = "existing:"

#: Why every examined row is a hint only when the check's reply held no
#: object at all (§24): a failed check, not a decodable reply with nothing in it.
UNREADABLE = "the duplicate check returned no readable answer"


def has_proposals(parsed: dict) -> bool:
    """Any row in either section. Cheap: it is what lets an absorb with
    neither section skip the worker thread."""
    return any(parsed.get(section) for section in SECTIONS)


class Examined:
    """One proposed-new row that has at least one plausible neighbour."""

    def __init__(self, section: str, index: int, key: str, kind: similarity.Kind,
                 row: dict, assigned: str,
                 candidates: list[tuple[similarity.Subject, dict]],
                 distinguished_from: list[str], siblings: tuple[int, ...] = ()):
        self.section = section
        self.index = index            # position in its section
        self.key = key                # "r1", "r2", ... in examination order
        self.kind = kind
        self.row = row                # the parsed row as given
        self.assigned = assigned      # the id materialize would open it under
        self.title = _staged_title(row, assigned)
        self.candidates = candidates
        self.distinguished_from = distinguished_from
        self.siblings = siblings      # later rows dropped only because this one holds its id
        self.decision = "unchecked"
        self.status = "hint_only"
        self.reason = ""
        self.target: str | None = None   # an accepted canonical id


def _certainty(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _fixed(subject: similarity.Subject) -> tuple[str, str]:
    """The candidate fields the prompt cannot cut: its bare canonical id, which
    the decision names it back by, and its kind, which a cut to nothing would
    show as the ``promise`` a blank kind means."""
    return subject.ref.partition(":")[2], _text(subject.record.get("kind"))


def _offerable(subject: similarity.Subject) -> bool:
    """`_fixed` fits `similarity.CONTINUITY_IDENTITY_BYTES`. A ledger route
    slugifies a title of any length into the record's id, and an id cannot be
    clipped without losing the record it names, so a record whose id and kind
    together overrun the bound is never a candidate: it is left out of the pool rather
    than carried whole into the shared decide prompt."""
    return (sum(len(text.encode("utf-8")) for text in _fixed(subject))
            <= similarity.CONTINUITY_IDENTITY_BYTES)


def _candidate(subject: similarity.Subject, signals: dict) -> dict:
    """One neighbour as the decide item shows it: its bare canonical id,
    what it is, its latest beat and up to `similarity.PREVIOUS_BEATS` earlier
    ones, newest first.

    Its stored text -- id, kind, status, title, due, latest beat, earlier beats --
    shares one `similarity.CONTINUITY_IDENTITY_BYTES` budget, the bound its
    identity text was embedded under: a closed record is in the pool though not
    in the extraction's snapshot, and its title can select it whatever length
    its text runs to. The `_fixed` fields come first and whole (`examine` offers
    only records they fit, `_offerable`); the rest share what they leave
    (`similarity.clip_fields`), in that priority. An earlier beat the budget no
    longer reaches is left out rather than shown blank."""
    rec = subject.record
    rid, kind = _fixed(subject)
    latest, earlier = similarity.beat_lines(rec)
    left = similarity.CONTINUITY_IDENTITY_BYTES - len(rid.encode("utf-8"))
    # The status rides the shared budget too: a route stores only a known one,
    # but plot.json and commitments.json are hand-editable and the effective
    # record passes a status through as written.
    kind, status, title, due, latest, *earlier = similarity.clip_fields(
        [kind, subject.status, subject.title, _text(rec.get("due")), latest, *earlier], left)
    return {"id": rid, "title": title,
            "status": status, "kind": kind, "due": due, "latest_beat": latest,
            "earlier": [beat for beat in earlier if beat], "signals": dict(signals)}


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
        """The examined rows as the decision items read them (`build_items`).
        Within one row every candidate is the row's own type, so a bare id is
        unambiguous."""
        return [_prompt_row(e) for e in self.rows]

    def decide(self, decisions: list[dict]) -> None:
        """Take the model's decisions (`answers_of`'s dicts, spec §10.2).

        Rows are visited in examination order. A row the reply never answered
        is `unchecked` / `hint_only`; ``new`` and ``uncertain`` stand as given;
        ``existing`` is accepted only when the id it names -- canonicalized
        through the alias map -- is one of the row's live candidates and no
        other row in this batch (an explicit or honoured target, or an earlier
        accepted row) already moves that record. Otherwise it is downgraded to
        ``uncertain``: identity resolution never reopens a record, and a second
        move of one record would lose a beat. Replaces any earlier decision."""
        by_key: dict[str, dict] = {}
        for answer in decisions:
            if isinstance(answer, dict) and isinstance(answer.get("row"), str):
                by_key.setdefault(answer["row"], answer)   # a repeated key keeps its first
        taken = set(self.targets)
        for e in self.rows:
            d = by_key.get(e.key)
            if d is None:
                _settle(e, "unchecked", "hint_only", NO_ANSWER)
            elif d.get("decision") == "existing":
                self._existing(e, d, taken)
            else:
                word = d.get("decision")
                _settle(e, word if word in DECISIONS else "uncertain", "accepted",
                        _text(d.get("reason")))

    def _existing(self, e: Examined, d: dict, taken: set[tuple[str, str]]) -> None:
        rid = _bare(e.kind, d.get("id"))
        rid = _canonical(self.live, e.kind, rid) if rid else ""
        offered = {s.ref.partition(":")[2]: s for s, _ in e.candidates}
        cand = offered.get(rid)
        if cand is None:
            _settle(e, "uncertain", "downgraded", NOT_OFFERED)
        elif not cand.live:
            _settle(e, "uncertain", "downgraded", NOT_LIVE)
        elif (e.kind, rid) in taken:
            _settle(e, "uncertain", "downgraded", ALREADY_MOVED)
        else:
            _settle(e, "existing", "accepted", _text(d.get("reason")), target=rid)
            taken.add((e.kind, rid))

    def hint_only(self, reason: str) -> None:
        """No usable answer (no connection, a failed or refused call, an
        undecodable reply): every examined row becomes `unchecked` /
        `hint_only` with `reason`, discarding any earlier `decide`."""
        for e in self.rows:
            _settle(e, "unchecked", "hint_only", reason)

    def rewritten(self, parsed: dict) -> dict:
        """`parsed` with every examined row replaced by its decided form.

        A shallow copy with fresh lists for the two sections; `parsed` itself
        is not mutated. An accepted ``existing`` row is retargeted onto its
        canonical record by the §10.2 rewrite and carries its original under
        `AS_NEW_KEY`; every other examined row is the row as given. Both carry
        `identity_check` (without ``alternatives``, which materialize adds).
        An accepted row's siblings -- the rows dropped only because it held its
        id -- are retargeted onto the same record without a check, so they stay
        a dropped second move rather than opening the record under the freed
        id. Rows with no plausible candidate were never examined and are
        untouched."""
        out = dict(parsed)
        for section in SECTIONS:
            rows = parsed.get(section)
            if isinstance(rows, list):
                out[section] = list(rows)
        for e in self.rows:
            ic = _identity_check(e)
            if e.decision == "existing" and e.status == "accepted" and e.target:
                new = _onto_existing(e.kind, e.row, e.target)
                new["identity_check"] = ic
                new[AS_NEW_KEY] = dict(e.row)
                for j in e.siblings:
                    sibling = parsed[e.section][j]
                    if out[e.section][j] is sibling and isinstance(sibling, dict):
                        out[e.section][j] = _onto_existing(e.kind, sibling, e.target)
            else:
                new = {**e.row, "identity_check": ic}
            out[e.section][e.index] = new
        return out

    def counts(self) -> dict[str, int]:
        """Flat counts for the phase block and the log row (spec §29): no
        titles, texts or reasons. ``deterministic + semantic == candidates``."""
        vias = [sig.get("via") for e in self.rows for _, sig in e.candidates]
        out = {"proposed": self.proposed, "examined": len(self.rows),
               "candidates": len(vias),
               "deterministic": sum(v in ("lexical", "structural") for v in vias),
               "semantic": sum(v == "semantic" for v in vias),
               "embedded": self.embedded}
        for word in CHECK_DECISIONS:
            out[word] = sum(e.decision == word for e in self.rows)
        for status in ("downgraded", "hint_only"):
            out[status] = sum(e.status == status for e in self.rows)
        return {k: int(v) for k, v in out.items()}

    def all_unchecked(self) -> bool:
        """Rows were examined and the check answered none of them."""
        return bool(self.rows) and all(e.decision == "unchecked" for e in self.rows)


#: Reasons the acceptance guard gives (display text, never a prompt).
NO_ANSWER = "the check gave no answer for this row"
NOT_OFFERED = "named a record that was not offered"
NOT_LIVE = "that record is already closed or resolved"
ALREADY_MOVED = "another row in this scene already moves that record"


def _settle(e: Examined, decision: str, status: str, reason: str, *,
            target: str | None = None) -> None:
    e.decision, e.status, e.reason, e.target = decision, status, reason, target


def _onto_existing(kind: str, row: dict, rid: str) -> dict:
    """The §10.2 rewrite of `row` onto the existing record `rid`.

    The id becomes the canonical one and the title blank (keep stored). A plot
    status is the model's ``closed`` or ``advanced``, else ``advanced`` -- never
    ``open``, which the extraction's own default is and which would regress an
    advanced thread. A commitment keeps its stored kind and keeps a status only
    when it resolves the record. A stated ``due`` is kept and an absent one
    stays absent; a blank one is dropped, because ``""`` means "lift the
    deadline" only on a row that named the record -- on a row that would have
    opened one there was no deadline to lift, so carrying it across would
    clear the stored record's. Citation keys are untouched."""
    new = {**row, "id": rid, "title": ""}
    if "due" in row and not _text(row.get("due")):
        del new["due"]
    status = row.get("status")
    if kind == "thread":
        new["status"] = status if status in ("closed", "advanced") else "advanced"
    else:
        new["kind"] = ""
        new["status"] = status if status in commitments.RESOLVED else ""
    return new


def _identity_check(e: Examined) -> dict:
    """The row's `identity_check`, as the review stores and shows it (§10.3)."""
    return {"decision": e.decision, "status": e.status, "reason": e.reason,
            "proposed": {"title": e.title, "why_new": _text(e.row.get("why_new")),
                         "distinguished_from": list(e.distinguished_from)},
            "candidates": [{"ref": s.ref, "title": s.title, "status": s.status,
                            "latest_beat": similarity.beat_lines(s.record)[0],
                            "signals": dict(sig)}
                           for s, sig in e.candidates]}


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

    __slots__ = ("assigned", "index", "kind", "row", "section", "siblings", "subject")

    def __init__(self, section: str, index: int, kind: similarity.Kind, row: dict,
                 assigned: str, subject: similarity.Subject, siblings: tuple[int, ...]):
        self.section = section
        self.index = index
        self.kind = kind
        self.row = row
        self.assigned = assigned
        self.subject = subject
        self.siblings = siblings


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


def _siblings(threads: dict, owed: dict | None, parsed: dict, live: dict[str, str],
              before: dict[tuple[str, int], absorb_materializer.Assigned | None],
              section: str, index: int) -> tuple[int, ...]:
    """The later rows of `section` that `assign_ids` drops only because row
    `index` holds its id: the rows it stages once that row stops holding one.

    Asked by assigning the batch again with the row blanked (a blank beat is
    dropped before it reserves anything), which is what a retarget does to the
    id it held. Earlier rows cannot change; the record it is retargeted onto
    is stored, so reserving it frees nothing else."""
    rows = list(parsed.get(section) or [])
    rows[index] = {}
    after = absorb_materializer.assign_ids(threads, owed, {**parsed, section: rows}, live)
    return tuple(j for j in range(index + 1, len(rows))
                 if before.get((section, j)) is None and after.get((section, j)) is not None)


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
            proposals.append(_Proposal(
                section, i, kind, row, slot.id,
                _subject(kind, len(proposals) + 1, row, slot.id, sid, actors),
                _siblings(threads, ledgers.commitments, parsed, live, assigned, section, i)))
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


def _semantic(proposals: list[_Proposal], pools: _Pools, embed_deadline: float | None, *,
              campaign: str = "", scene: str = "") -> similarity.Semantic:
    space = similarity.available()
    if space is None:
        return similarity.Semantic({}, "off")
    # A proposal with no record of its own type to compare against cannot
    # produce a candidate: embedding its text would send campaign prose to the
    # provider, on the extraction's critical path, for nothing (spec §10.4).
    comparable = [p for p in proposals if pools[p.kind]]
    if not comparable:
        return similarity.Semantic({}, "configured")
    cached = [s.text for pool in pools.values() for s in pool]
    # Charged to the absorb's campaign and scene, as its LLM calls are.
    return similarity.semantic([p.subject.text for p in comparable], _warm(comparable, pools),
                               deadline=embed_deadline, space=space, cached=cached,
                               campaign=campaign, scene=scene)


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
    pools = {kind: [s for s in similarity.pool(cid, kind) if _offerable(s)]
             for kind in kinds}
    sem = _semantic(proposals, pools, embed_deadline, campaign=cid, scene=sid)
    vecs = sem.vectors if sem.mode != "off" else None
    stored = {"thread": ledgers.threads or {}, "commitment": ledgers.commitments or {}}
    rows: list[Examined] = []
    for p in proposals:
        candidates = similarity.nearest(p.subject, pools[p.kind], vecs)
        if not candidates:
            continue
        rows.append(Examined(p.section, p.index, f"r{len(rows) + 1}", p.kind, p.row,
                             p.assigned, candidates,
                             _distinguished(p.row, p.kind, stored[p.kind], live), p.siblings))
    matching: Literal["basic", "semantic"] = (
        "semantic" if similarity.matching() == "semantic" else "basic")
    return Examination(rows, len(proposals), matching, sem.mode, sem.error, sem.embedded,
                       targets, live)


# ------------------------------------------------------ the item context


def _signal_text(signals: dict) -> str:
    """The signals as fixed-order phrases. Hints for the decision, never a
    verdict -- the question says so."""
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
    """`prompt_rows` output as `continuity_identity/item.j2` reads it: each
    candidate gains its snippet `line` and its `signal_text`. Snippets are
    rendered here, by Python, as every other prompt's are. The input is not
    mutated."""
    return [{**row, "candidates": [{**c, "line": _line(row["kind"], c),
                                    "signal_text": _signal_text(c["signals"])}
                                   for c in row["candidates"]]}
            for row in rows]


# ------------------------------------------------------- as decision items
#
# The check as `decide()` items (spec §7.4): one per examined row, asking
# `decision`, its ``existing`` folded with the candidate it names. These are
# what absorb sends and reads (`routes.scenes._resolve_identity`).


def _spelled(seen: set[str], spelling: str) -> bool:
    """Take `spelling` into `seen` and say True, or say False when the
    decision contract would refuse it: unofferable, or one normalised
    spelling already taken."""
    key = decisions.normalise(spelling)
    if not decisions.offerable(spelling) or key in seen:
        return False
    seen.add(key)
    return True


#: The decision words a row is always offered, after its folded ``existing``
#: options.
_OTHER_WORDS = tuple(word for word in DECISIONS if word != "existing")


def _fits(options: int, chars: int) -> bool:
    """Whether a `decision` choice of `options` options whose ids total
    `chars` characters is one `decisions.validate` accepts and a decisions
    endpoint carries: at most `decisions.MAX_OPTIONS` options (which is
    `decisions.NATIVE_MAX_OPTIONS`; the choice allows no none, so no reserved
    none is added), and past `decisions.ENUM_STRING_CHARS_ABOVE` options, at
    most `decisions.MAX_ENUM_STRING_CHARS` characters."""
    return (options <= min(decisions.MAX_OPTIONS, decisions.NATIVE_MAX_OPTIONS)
            and (options <= decisions.ENUM_STRING_CHARS_ABOVE
                 or chars <= decisions.MAX_ENUM_STRING_CHARS))


def _offered(row: dict, live: Mapping[str, str]) -> tuple[list[dict], list[decisions.Option]]:
    """The row's candidates the `decision` choice may offer, in rank order,
    and their folded ``existing:<id>`` options (Review Focus 3, M4).

    Two passes, so no hand-edited ledger yields a request `decisions.validate`
    refuses. Ids first: a candidate whose bare id is not offerable (an empty
    id from a ``thread:`` key, which would fold into an option naming
    nothing), whose folded option collides once normalised with a
    higher-ranked one's, or that the choice has no room left for (`_fits`,
    beside ``new`` and ``uncertain``) is dropped -- from the options and from
    the row the context shows. `examine` offers at most
    `similarity.IDENTITY_TOP_K`, far inside that room, so the last never binds
    on a row it built. Then aliases, the spellings today's `_existing`
    canonicalises, each folded: the ref form ``<kind>:<id>``, and each live
    alias source of that ref with its bare id (a source of another kind,
    which `_existing` would never reach, is not one) -- and every spelling
    again, the id included, with one space after the colon
    (``existing: <id>``), the most natural way to follow "existing:"
    followed by the id, which `decisions.normalise` would otherwise read as
    no option (it folds spaces into ``_``, never away). Each kept id's spaced
    form is taken before any other alias, so it is shadowed by nothing but
    an id. An alias that is not
    offerable, or collides with any kept option or an earlier alias, is
    dropped alone, so an earlier candidate's alias source never shadows a
    later candidate's id *in the options*: that holds up to the parse.
    `Examination.decide` maps the answered id through the alias map again, so
    an answer naming a later candidate whose id is also an earlier
    candidate's alias source lands the row on the earlier candidate, as
    today's `_existing` does."""
    kind = row["kind"]
    seen = {decisions.normalise(word) for word in _OTHER_WORDS}
    kept: list[dict] = []
    chars = sum(len(word) for word in _OTHER_WORDS)
    for c in row["candidates"]:
        option = EXISTING_PREFIX + c["id"]
        if not decisions.offerable(c["id"]) or decisions.normalise(option) in seen:
            continue
        if not _fits(len(kept) + 1 + len(_OTHER_WORDS), chars + len(option)):
            break
        _spelled(seen, option)
        kept.append(c)
        chars += len(option)
    spaced = [f"{EXISTING_PREFIX} {c['id']}" for c in kept]
    spaced_ids = [s if _spelled(seen, s) else "" for s in spaced]
    options = []
    for c, spaced_id in zip(kept, spaced_ids, strict=True):
        ref = f"{kind}:{c['id']}"
        spellings = [ref]
        for src, to in live.items():
            if to == ref and src != to and src.startswith(f"{kind}:"):
                spellings += [src, src.removeprefix(f"{kind}:")]
        plain = tuple(s for s in (EXISTING_PREFIX + s for s in spellings) if _spelled(seen, s))
        rest = tuple(s for s in (f"{EXISTING_PREFIX} {s}" for s in spellings)
                     if _spelled(seen, s))
        aliases = (*plain, *((spaced_id,) if spaced_id else ()), *rest)
        options.append(decisions.Option(
            EXISTING_PREFIX + c["id"],
            prompts.render("continuity_identity/option.j2", decision="existing",
                           title=c["title"] or c["id"]),
            aliases))
    return kept, options


def build_items(rows: list[dict], live: Mapping[str, str]) -> tuple[decisions.Item, ...]:
    """One `decide()` item per `prompt_rows()` row, in order.

    Each item's context is `continuity_identity/item.j2` over the row as
    `template_rows` shapes it, holding only the candidates it offers (so it
    stands alone: a native backend sends each item by itself). It asks one
    question, `decision`: ``existing:<id>`` per offered candidate, in rank
    order (`_offered`), each described by `option.j2` under the candidate's
    title, then ``new`` and ``uncertain``. A row left with no offerable
    candidate is offered no ``existing`` at all, so it cannot answer one.
    Pure, but it renders: callers run it in the threadpool."""
    question = prompts.render("continuity_identity/question.j2")
    words = tuple(decisions.Option(word, prompts.render("continuity_identity/option.j2",
                                                        decision=word))
                  for word in _OTHER_WORDS)
    items = []
    for row in rows:
        kept, options = _offered(row, live)
        [shown] = template_rows([{**row, "candidates": kept}])
        items.append(decisions.Item(
            prompts.render("continuity_identity/item.j2", r=shown),
            (decisions.Choice(DECISION_ID, question, (*options, *words)),)))
    return tuple(items)


def unfolded(answer: str) -> tuple[str, str]:
    """`(decision, id)` of a `decision` answer: a folded ``existing:<id>``
    split back into ``existing`` and the id it names, and any other answer as
    itself with no id -- the decision and `id` of today's decision dict, which
    `Examination.decide` reads."""
    if answer.startswith(EXISTING_PREFIX):
        return "existing", answer.removeprefix(EXISTING_PREFIX).strip()
    return answer, ""


def explain() -> str:
    """The rationale instruction: each row's display-only reason."""
    return prompts.render("continuity_identity/explain.j2")


def _reweighed(answer: decisions.Answer) -> tuple[str, str]:
    """`(decision, id)` of a read `decision` answer (`unfolded`), unless the
    distribution an endpoint reported puts more mass on another decision
    than on the chosen option's once every ``existing:<id>`` is summed as one
    ``existing`` (`decisions.regrouped`): an endpoint scoring options one by
    one splits a row between two near-identical candidates across their
    options, and reads 0.3 + 0.3 as losing to a ``new`` of 0.4 -- which opens
    a new record with no flag. Then ``existing`` winning over a chosen ``new``
    or ``uncertain`` is ``uncertain``, which names no record (none won) and
    flags the row; ``new`` or ``uncertain`` winning is that word. A chosen
    ``existing:<id>`` inside the winning ``existing`` stands. With no
    distribution (a structured reply), as chosen."""
    chosen = answer.answer if isinstance(answer.answer, str) else ""
    group = decisions.regrouped(answer, lambda oid: unfolded(oid)[0], DECISIONS)
    if group is None:
        return unfolded(chosen)
    return ("uncertain" if group == "existing" else group), ""


def answers_of(rows: list[dict],
               results: Sequence[decisions.ItemResult]) -> list[dict] | None:
    """The parsed batch as today's decision dicts, for `Examination.decide`.

    An item counts as answered only when its `decision` answer `was_read`
    (I1); one that was not -- the reply held no object, or nothing in it
    reads as that item, or it errored, was refused or abstained -- is left
    out, so `Examination.decide` leaves its row `unchecked` and the phase is
    never `ok`. Every read item gives ``{row, decision, id, reason}``: the
    decision as answered (``""`` when unreadable, which `decide` takes as
    ``uncertain``; where a native endpoint reported a distribution, read with
    every ``existing`` summed, `_reweighed`) with a folded ``existing:<id>``
    split back into ``existing`` and its id (`unfolded`), and the rationale
    clipped to
    `REASON_CHARS` (``""`` when none came back: it is display-only, I4). An
    ``existing`` naming no offered candidate is no option of the item, so it
    is unreadable like any word outside the options, and `decide` takes it as
    ``uncertain``.

    A garbled chunk is not a garbled item (N8): "never ``uncertain``" is about
    an item the reply never reached. An object that reaches an item and
    answers it badly -- ``{"answers": {}}``, a null decision, ``maybe`` -- was
    read, and becomes ``uncertain`` as today's unknown word does.

    None only when no item was read and none held an object: today's
    undecodable reply. An object holding no item (``{}``, today's format) is
    ``[]``, every row unchecked (§24). A chunk error with nothing read is the
    call site's to report before it asks this (M12)."""
    out = []
    for row, result in zip(rows, results, strict=True):
        decision = result.answers.get(DECISION_ID)
        if decision is None or not decisions.was_read(decision):
            continue
        word, rid = _reweighed(decision)
        out.append({"row": row["key"], "decision": word, "id": rid,
                    "reason": result.rationale.strip()[:REASON_CHARS]})
    if not out and results and all(decisions.held_no_object(result.answers.get(DECISION_ID))
                                    for result in results):
        return None
    return out


def take(exam: Examination, answers: list[dict] | None) -> bool:
    """Decide `exam` by `answers` (`answers_of`), or, for
    None -- no readable answer at all -- make every row a hint only with
    `UNREADABLE`. Whether it decided."""
    if answers is None:
        exam.hint_only(UNREADABLE)
        return False
    exam.decide(answers)
    return True
