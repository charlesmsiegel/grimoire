"""The live resolver and the effective projections every current-state reader uses
(capstone spec §7.1).

**One meaning of canonical.** `canon.resolve` says what the stored alias graph
says; `live_canon` says what the campaign's current state is, and the two differ
exactly where it matters. An alias is followed only to a record that is really
there -- same type, thread or commitment, the target existing as a record -- and
a walk stops at the last hop that holds. So ``A -> B, B -> gone`` merges A into
B, and a dangling or wrong-type alias leaves its source standing as its own
record. Everything that answers a current-state question (the projections here,
involvement, a write redirect, link creation) uses this, because a raw
resolution that ignored existence would hand a writer the id of a record that is
not there and `set_movement` would conjure it.

Existence follows the row set `open_threads` iterates: a physical record exists
iff its value is an object. A ledger that will not parse makes existence
UNKNOWN rather than false (`Ledgers.exists` -> None): an unreadable plot.json
must not get every thread alias reported as dangling, nor every link through a
thread reported as broken.

**The identity law.** For a campaign with no live aliases, `threads` and
`commitments` return exactly what `plot.open_threads` /
`commitments.open_commitments` return -- same rows, values, order -- plus
``aliases: []``. That is not a nicety. `set_movement` stamps ``last_scene`` on a
status-only move, and ``latest_beat`` is the last APPENDED beat rather than the
latest by scene; aging, dormancy, the briefing and the frozen-campaign sweep all
read those fields, so any reinterpretation applied to unaliased records would
quietly change prompts and the ledger for every existing campaign. The rows are
therefore built FROM the physical projections, and only a group with live
members is rewritten.

A merged group is the canonical's beats followed by each member's, stably
ordered by scene play order. An uncomparable beat (no scene, or an id outside
the grammar) travels with the beat before it, and an exact (scene, text)
duplicate is dropped only ACROSS records -- one record saying the same thing
twice is its own business. Title, kind, due and status are the canonical's.

Nothing here writes, and nothing here takes a lock: callers that need a
consistent read hold `campaign_lock` themselves.
"""

from __future__ import annotations

from ... import prompts
from .. import commitments as commitments_store
from .. import events, fieldtext, plot, scene_ids
from . import canon, doc

#: relation -> (allowed a-prefixes, allowed b-prefixes, directional). Spec §5.3:
#: every link reads ``a <relation> b``.
RELATIONS: dict[str, tuple[frozenset[str], frozenset[str], bool]] = {
    "continues": (frozenset({"thread"}), frozenset({"thread"}), True),
    "subthread_of": (frozenset({"thread"}), frozenset({"thread"}), True),
    "pays_off": (frozenset({"thread"}), frozenset({"commitment"}), True),
    **{rel: (frozenset({"thread", "commitment"}), frozenset({"event"}), True)
       for rel in ("before", "on", "after", "by")},
    "related_to": (frozenset({"thread", "commitment", "event"}),
                   frozenset({"thread", "commitment", "event"}), False),
}

#: §20's link-relation vocabulary, in the table's order: derived from it rather
#: than restated, so the tuple the Story Graph and the client pin cannot drift
#: from the rules a link is validated against.
LINK_RELATIONS: tuple[str, ...] = tuple(RELATIONS)

#: The record kinds an alias may join.
ALIASABLE = ("thread", "commitment")


def is_live(kind: str, status) -> bool:
    """Still owed / still open -- the predicate `open_threads` and
    `open_commitments` filter on, stated once."""
    text = fieldtext.text(status).lower()
    if kind == "thread":
        return text != "closed"
    return text not in commitments_store.RESOLVED


class Ledgers:
    """The three ledgers continuity refs point into, read once.

    A file that fails to parse, or parses to something that is not an object,
    is ``None`` and named in `unreadable`; questions about it answer "unknown".
    """

    def __init__(self, threads, commitments, events_, unreadable):
        self.threads = threads
        self.commitments = commitments
        self.events = events_
        self.unreadable = unreadable

    @classmethod
    def load(cls, cid: str) -> Ledgers:
        unreadable: list[str] = []

        def attempt(name, read):
            try:
                value = read(cid)
            except Exception:  # noqa: BLE001 -- an unreadable ledger is "unknown", not a crash
                value = None
            if not isinstance(value, dict):
                unreadable.append(name)
                return None
            return value

        threads = attempt("plot", plot.read)
        owed = attempt("commitments", commitments_store.read)
        # `events.read` answers a corrupt file with `{}` rather than raising, so
        # its unreadability has to be asked separately or every event would
        # read as deleted.
        planned = attempt("events", events.read) if events.readable(cid) else None
        if planned is None and "events" not in unreadable:
            unreadable.append("events")
        return cls(threads, owed, planned, unreadable)

    def _table(self, prefix: str):
        return {"thread": self.threads, "commitment": self.commitments,
                "event": self.events}.get(prefix, False)

    def exists(self, ref) -> bool | None:
        """True/False for a thread, commitment or event ref; None when its ledger
        could not be read; False for anything else."""
        try:
            prefix, rid = canon.split_ref(ref)
        except ValueError:
            return False
        table = self._table(prefix)
        if table is False:
            return False
        if table is None:
            return None
        return isinstance(table.get(rid), dict)


def _hop_problem(ledgers: Ledgers, src: str, record) -> tuple[str, str] | None:
    """Why the single hop ``src -> record["to"]`` cannot be followed, as
    ``(reason, to)``; None when it can (an unknown existence is passable)."""
    to = record.get("to") if isinstance(record, dict) else None
    if not isinstance(to, str) or not to:
        return "malformed_record", ""
    try:
        sp, _ = canon.split_ref(src)
        tp, _ = canon.split_ref(to)
    except ValueError:
        return "malformed_record", to
    if sp != tp or sp not in ALIASABLE:
        return "wrong_type", to
    if ledgers.exists(src) is False:
        return "missing_source", to
    if ledgers.exists(to) is False:
        return "missing_target", to
    return None


def _in_cycle(aliases: dict, src: str) -> bool:
    try:
        canon.resolve(aliases, src, strict=True)
    except doc.ContinuityError:
        return True
    return False


def live_canon(cid: str, ledgers: Ledgers | None = None) -> dict[str, str]:
    """Alias source -> live canonical, for every source whose live canonical is
    not itself (spec §7.1, "Live canonical")."""
    ledgers = ledgers or Ledgers.load(cid)
    aliases = doc.read(cid)["aliases"]
    out: dict[str, str] = {}
    for src in aliases:
        if not isinstance(src, str) or _in_cycle(aliases, src):
            continue
        seen, cur = {src}, src
        while True:
            record = aliases.get(cur)
            if record is None or _hop_problem(ledgers, cur, record) is not None:
                break
            nxt = record["to"]
            if nxt in seen:
                break
            seen.add(nxt)
            cur = nxt
        if cur != src:
            out[src] = cur
    return out


def diagnostics(cid: str, ledgers: Ledgers | None = None) -> dict:
    """What the effective view had to set aside, and why (spec §26)."""
    ledgers = ledgers or Ledgers.load(cid)
    aliases = doc.read(cid)["aliases"]
    dangling = []
    for src in sorted(a for a in aliases if isinstance(a, str)):
        record = aliases[src]
        problem = _hop_problem(ledgers, src, record)
        if problem is not None:
            reason, to = problem
            dangling.append({"ref": src, "to": to, "reason": reason})
        elif _in_cycle(aliases, src):
            dangling.append({"ref": src, "to": record["to"], "reason": "cycle"})
    _, broken, hidden = _classify_links(cid, ledgers)
    return {"dangling_aliases": dangling, "broken_links": broken,
            "hidden_links": hidden, "unreadable": list(ledgers.unreadable)}


def _classify_links(cid: str, ledgers: Ledgers):
    live = live_canon(cid, ledgers)
    stored = doc.read(cid)["links"]
    kept, broken, hidden, seen = [], [], [], set()
    for lid in sorted(k for k in stored if isinstance(k, str)):
        record = stored[lid]
        if not isinstance(record, dict) or not all(
                isinstance(record.get(k), str) for k in ("a", "b", "relation")):
            broken.append({"id": lid, "reason": "malformed_record"})
            continue
        a: str = record["a"]
        b: str = record["b"]
        relation: str = record["relation"]
        try:
            ap, _ = canon.split_ref(a)
            bp, _ = canon.split_ref(b)
        except ValueError:
            broken.append({"id": lid, "reason": "malformed_record"})
            continue
        rule = RELATIONS.get(relation)
        if rule is None or ap not in rule[0] or bp not in rule[1]:
            broken.append({"id": lid, "reason": "invalid_relation"})
            continue
        ca, cb = live.get(a, a), live.get(b, b)
        if ledgers.exists(ca) is False or ledgers.exists(cb) is False:
            broken.append({"id": lid, "reason": "missing_endpoint"})
            continue
        if ca == cb:
            broken.append({"id": lid, "reason": "self_collapsing"})
            continue
        key = (relation, *sorted((ca, cb))) if not rule[2] else (relation, ca, cb)
        if key in seen:
            hidden.append({"id": lid, "reason": "duplicate"})
            continue
        seen.add(key)
        kept.append({"id": lid, "relation": relation, "a": ca, "b": cb,
                     "a_raw": a, "b_raw": b,
                     "scene": fieldtext.text(record.get("scene")),
                     "note": fieldtext.text(record.get("note")),
                     "created": fieldtext.text(record.get("created"))})
    return kept, broken, hidden


def links(cid: str, ledgers: Ledgers | None = None) -> list[dict]:
    """The effective links: canonical endpoints, broken and duplicate ones set aside."""
    kept, _, _ = _classify_links(cid, ledgers or Ledgers.load(cid))
    return kept


# ------------------------------------------------------------------- records


def _projected(cid: str, kind: str) -> list[dict]:
    """The physical projected rows (closed/resolved included). Raises exactly
    what the physical projection raises."""
    if kind == "thread":
        return plot.open_threads(cid, include_closed=True)
    return commitments_store.open_commitments(cid, include_resolved=True)


def _raw(cid: str, kind: str) -> dict:
    """The stored records, beats and all -- what only a merge needs."""
    return plot.read(cid) if kind == "thread" else commitments_store.read(cid)


def _physical(cid: str, kind: str) -> tuple[dict, list[dict]]:
    """The stored records and their projected rows (closed/resolved included).
    Raises exactly what the physical projection raises."""
    return _raw(cid, kind), _projected(cid, kind)


def _beats(record) -> list[dict]:
    beats = record.get("beats") if isinstance(record, dict) else None
    return [b for b in beats if isinstance(b, dict)] if isinstance(beats, list) else []


def _order(scene) -> int | None:
    text = fieldtext.text(scene)
    parsed = scene_ids.parse_sid(text) if text else None
    return parsed["number"] if parsed is not None else None


def _merge_beats(members: list[list[dict]]) -> list[dict]:
    """The members' beats as one list: the canonical's stored beats followed by
    each alias's, stably sorted by scene play order. An uncomparable beat keeps
    its place right after its stored predecessor in that concatenation -- for
    an alias's leading unscened beat, that is the previous member's last beat."""
    runs: list[tuple[int, int, list[tuple[int, dict]]]] = []
    key = -1
    current: list[tuple[int, dict]] = []
    for m_index, beats in enumerate(members):
        for beat in beats:
            number = _order(beat.get("scene"))
            if number is not None:
                if current:
                    runs.append((key, len(runs), current))
                key, current = number, [(m_index, beat)]
            else:
                current.append((m_index, beat))
    if current:
        runs.append((key, len(runs), current))
    runs.sort(key=lambda r: (r[0], r[1]))
    out: list[dict] = []
    owner: dict[tuple[str, str], int] = {}
    for _, _, run in runs:
        for m_index, beat in run:
            sig = (fieldtext.text(beat.get("scene")), fieldtext.text(beat.get("text")))
            if owner.get(sig, m_index) != m_index:
                continue                      # an exact duplicate ACROSS records only
            owner.setdefault(sig, m_index)
            out.append(beat)
    return out


def play_ordered(beats: list[dict]) -> list[dict]:
    """One record's beats in scene play order, by the rule a merged group's
    beats are already in. An unmerged record stores its beats in the order
    reviews were saved, which need not be the order the scenes were played."""
    return _merge_beats([beats])


def later_scene(stored: str, other: str) -> str:
    """Whichever of two scene ids comes later in play order. An id outside the
    grammar loses to one inside it; when neither parses, `stored` is kept
    unless it is empty -- a closure applied with an earlier evidence scene must
    never move a record's ``last_scene`` backwards (§12.4)."""
    a, b = _order(stored), _order(other)
    if a is not None and b is not None:
        return other if b > a else stored
    if a is not None:
        return stored
    if b is not None:
        return other
    return stored or other


def _latest_scene(candidates: list[str], fallback: str) -> str:
    best, best_n = None, None
    for scene in candidates:
        number = _order(scene)
        if number is not None and (best_n is None or number > best_n):
            best, best_n = scene, number
    return best if best is not None else fallback


def _groups(cid: str, kind: str, rows: list[dict]) -> dict[str, list[str]]:
    """Canonical id -> [canonical id, member ids in sorted-ref order], for every
    group with at least one live member.

    No stored alias means no group, so the common path answers without
    `live_canon` -- which would load all three ledgers to say the same thing,
    on every effective read (the shell badge runs on every navigation)."""
    if not doc.read(cid)["aliases"]:
        return {}
    ids = {row["id"] for row in rows}
    out: dict[str, list[str]] = {}
    for src, target in sorted(live_canon(cid).items()):
        sp, sid = canon.split_ref(src)
        _, tid = canon.split_ref(target)
        if sp != kind or sid not in ids or tid not in ids:
            continue
        out.setdefault(tid, [tid]).append(sid)
    return out


def _merged(kind: str, raw: dict, by_id: dict, members: list[str]) -> dict:
    head = by_id[members[0]]
    merged = _merge_beats([_beats(raw.get(m)) for m in members])
    scenes = [fieldtext.text((raw.get(m) or {}).get("last_scene")) for m in members]
    scenes += [fieldtext.text(b.get("scene")) for b in merged]
    last = merged[-1] if merged else None
    return {
        "beats": merged,
        "last_scene": _latest_scene(scenes, head["last_scene"]),
        "latest_beat": fieldtext.text(last.get("text")) if last is not None else "",
        "aliases": [{"ref": f"{kind}:{m}", "title": by_id[m]["title"],
                     "status": by_id[m]["status"]} for m in members[1:]],
    }


def records(cid: str, kind: str) -> dict[str, dict]:
    """Every effective record of `kind` ("thread" | "commitment"), closed and
    resolved included, keyed by canonical ref, with its merged beats."""
    raw, rows = _physical(cid, kind)
    by_id = {row["id"]: row for row in rows}
    groups = _groups(cid, kind, rows)
    hidden = {m for members in groups.values() for m in members[1:]}
    out: dict[str, dict] = {}
    for row in rows:
        rid = row["id"]
        if rid in hidden:
            continue
        members = groups.get(rid, [rid])
        if len(members) == 1:
            extra = {"beats": _beats(raw.get(rid)), "last_scene": row["last_scene"],
                     "latest_beat": row["latest_beat"], "aliases": []}
        else:
            extra = _merged(kind, raw, by_id, members)
        out[f"{kind}:{rid}"] = {**row, **extra, "ref": f"{kind}:{rid}", "members": members}
    return out


def _rows(cid: str, kind: str, include: bool) -> list[dict]:
    rows = _projected(cid, kind)
    by_id = {row["id"]: row for row in rows}
    groups = _groups(cid, kind, rows)
    # Only a merge needs the raw beats, so a campaign with no group parses its
    # ledger once (inside the projection) rather than twice.
    raw = _raw(cid, kind) if groups else {}
    hidden = {m for members in groups.values() for m in members[1:]}
    out = []
    for row in rows:
        rid = row["id"]
        if rid in hidden:
            continue
        projected = {**row, "aliases": []}
        if rid in groups:
            merged = _merged(kind, raw, by_id, groups[rid])
            projected.update(last_scene=merged["last_scene"],
                             latest_beat=merged["latest_beat"], aliases=merged["aliases"])
        if include or is_live(kind, projected["status"]):
            out.append(projected)
    out.sort(key=lambda r: (r["last_scene"], r["id"]))
    return out


def threads(cid: str, include_closed: bool = False) -> list[dict]:
    return _rows(cid, "thread", include_closed)


def commitments(cid: str, include_resolved: bool = False) -> list[dict]:
    return _rows(cid, "commitment", include_resolved)


def _or_physical(canonical, physical, cid: str) -> list[dict]:
    """The live effective rows, or the physical projection's (each with an
    empty ``aliases``) when the continuity side fails -- the render helpers'
    fallback, for a reader that wants rows. A garbled ledger still raises out
    of ``physical``, for the caller's own tolerance to answer."""
    try:
        return canonical(cid)
    except Exception:  # noqa: BLE001 -- a continuity-side failure degrades to the physical rows, never drops them (spec §3.9)
        return [{**row, "aliases": []} for row in physical(cid)]


def threads_or_physical(cid: str) -> list[dict]:
    """`threads(cid)`, degrading to `plot.open_threads` (spec §3.9)."""
    return _or_physical(threads, plot.open_threads, cid)


def commitments_or_physical(cid: str) -> list[dict]:
    """`commitments(cid)`, degrading to `commitments.open_commitments` (spec §3.9)."""
    return _or_physical(commitments, commitments_store.open_commitments, cid)


# ------------------------------------------------------------------ renders


def render_threads(cid: str, with_id: bool) -> list[str]:
    """`plot.render_open`'s lines over the effective threads: canonical ids
    only, merged beats. With no live alias it IS `plot.render_open` (the
    identity law). Only the row read is guarded, exactly as there: a garbled
    plot.json still costs the section (the physical render's own tolerance
    answers ``[]``), and a broken snippet template still raises."""
    try:
        rows = threads(cid)
    except Exception:  # noqa: BLE001 -- a continuity-side failure degrades to the physical lines, never drops the section (spec §3.9)
        return plot.render_open(cid, with_id)
    template = f"snippets/plot_thread_line/{'absorb' if with_id else 'context'}.j2"
    return [prompts.render(template, t=t) for t in rows]


def render_commitments(cid: str, with_id: bool) -> list[str]:
    """`commitments.render_open`'s lines over the effective commitments, with
    `render_threads`' shape and tolerance."""
    try:
        rows = commitments(cid)
    except Exception:  # noqa: BLE001 -- a continuity-side failure degrades to the physical lines, never drops the section (spec §3.9)
        return commitments_store.render_open(cid, with_id)
    template = f"snippets/commitment_line/{'absorb' if with_id else 'context'}.j2"
    return [prompts.render(template, c=c) for c in rows]
