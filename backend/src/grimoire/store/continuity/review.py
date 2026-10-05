"""Validated, journalled writes to continuity.json (capstone spec §5.1, §5.3, §5.7).

`doc` stores whatever it is handed; this module decides what may be handed to
it. Every refusal is one a reader can act on, carried as `RefusedError` with an HTTP
status and a machine-readable kind, and every accepted write goes through
`undo.journalled` -- so a merge or a link lands in the play view's Changes panel
as a ``manual`` row with an Undo button, under the same best-effort policy as
the ledger's hand edits (the write is authoritative; a failed journal append is
logged, not raised).

Two rules worth stating before reading the code:

- **Current-state questions use `effective.live_canon`.** The group an alias
  joins, the endpoints a link is stored under, the sources a merge affects: all
  judged against the records that are really there, never the raw alias graph.
- **A merge is not a back door to closure** (spec §3.7). Merging an open thread
  into a closed one -- or an unresolved commitment into a resolved one, or the
  reverse -- changes what the reader is told is still owed, so it is refused
  until the request says it accepts that (``accept_status_change``). The refusal
  carries both records' status, kind and due, which is what the review shows
  and what lets it offer the explicit due copy as a separate ledger edit.

`forget_ref` lives here rather than in `doc` because each removal is journalled
and `doc` cannot import `undo` (`undo` imports it).

Every public mutator holds `campaign_lock` across its read-check-write, so a
concurrent ledger delete or another review cannot slip between the validation
and the write. The lock is reentrant; the ledger routes that call in here
already hold it.
"""

from __future__ import annotations

from .. import commitments as commitments_store
from .. import events, fieldtext, locks, paths, plot, undo
from . import canon, doc, effective


class RefusedError(Exception):
    """A write this module will not make, with the HTTP shape to report it in."""

    def __init__(self, status: int, kind: str, detail: str, extra: dict | None = None):
        super().__init__(detail)
        self.status = status
        self.kind = kind
        self.detail = detail
        self.extra = extra or {}


_LEDGER_FILE = {"thread": "plot", "commitment": "commitments", "event": "events"}


def describe(cid: str, ref: str, ledgers: effective.Ledgers | None = None) -> str:
    """A record's display name -- a title, an event's name -- or the ref itself.
    Never raises: it labels journal rows and refusals, including ones about a
    record that has just been deleted or a ledger that will not read. A caller
    labelling many rows passes the `Ledgers` it already loaded, so each label is
    a lookup rather than a re-parse of the whole file."""
    try:
        prefix, rid = canon.split_ref(ref)
        if ledgers is not None:
            table = {"thread": ledgers.threads, "commitment": ledgers.commitments,
                     "event": ledgers.events}[prefix]
        else:
            table = {"thread": plot.read, "commitment": commitments_store.read,
                     "event": events.read}[prefix](cid)
        record = table.get(rid) if isinstance(table, dict) else None
        name = record.get("name" if prefix == "event" else "title") \
            if isinstance(record, dict) else None
        return fieldtext.text(name) or ref
    except Exception:  # noqa: BLE001 -- a label must never be the thing that fails
        return ref


def _split(ref) -> tuple[str, str]:
    try:
        return canon.split_ref(ref)
    except ValueError as e:
        raise RefusedError(400, "bad_ref", f"{ref!r} is not a record reference") from e


def _require_wellformed(cid: str, sections: set[str]) -> None:
    if set(doc.malformed(cid)) & ({"file"} | sections):
        raise RefusedError(409, "malformed",
                      "continuity.json cannot be read, so nothing in it can be changed safely")


def _require_readable(ledgers: effective.Ledgers, *prefixes: str) -> None:
    bad = sorted({_LEDGER_FILE[p] for p in prefixes} & set(ledgers.unreadable))
    if bad:
        raise RefusedError(409, "unreadable", f"{', '.join(bad)} cannot be read right now")


def _require_exists(ledgers: effective.Ledgers, cid: str, *refs: str) -> None:
    for ref in refs:
        if ledgers.exists(ref) is False:
            raise RefusedError(404, "not_found", f"{describe(cid, ref)} does not exist")


def _record(ledgers: effective.Ledgers, ref: str) -> dict:
    prefix, rid = canon.split_ref(ref)
    table = ledgers.threads if prefix == "thread" else ledgers.commitments
    record = (table or {}).get(rid)
    return record if isinstance(record, dict) else {}


def _standing(ledgers: effective.Ledgers, ref: str) -> dict:
    record = _record(ledgers, ref)
    commitment = ref.startswith("commitment:")
    return {"status": fieldtext.text(record.get("status"), "open"),
            "kind": fieldtext.text(record.get("kind"), "promise") if commitment else "",
            "due": fieldtext.text(record.get("due")) if commitment else ""}


def _journal_alias(cid: str, ref: str, label: str):
    return undo.journalled(cid, {"w": "continuity_alias", "ref": ref},
                           kind="continuity_alias",
                           ref={"kind": "continuity_alias", "id": ref},
                           field="alias", label=label)


def _journal_link(cid: str, lid: str, label: str):
    return undo.journalled(cid, {"w": "continuity_link", "id": lid},
                           kind="continuity_link",
                           ref={"kind": "continuity_link", "id": lid},
                           field="link", label=label)


def create_alias(cid: str, ref: str, to: str, *, replace: bool = False,
                 accept_status_change: bool = False, note: str = "",
                 source: str = "manual") -> dict:
    """Merge `ref` into `to`: `ref` stops being its own effective record."""
    with locks.campaign_lock(cid):
        sp, _ = _split(ref)
        tp, _ = _split(to)
        if ref == to:
            raise RefusedError(400, "self_alias", "a record cannot be merged into itself")
        if sp != tp or sp not in effective.ALIASABLE:
            raise RefusedError(400, "wrong_type",
                          "only a thread into a thread, or a commitment into a commitment")
        _require_wellformed(cid, {"aliases"})
        ledgers = effective.Ledgers.load(cid)
        _require_readable(ledgers, sp)
        _require_exists(ledgers, cid, ref, to)
        aliases = doc.read(cid)["aliases"]
        if doc.reaches(aliases, to, ref):
            raise RefusedError(409, "alias_cycle",
                          f"{describe(cid, to)} is already merged into {describe(cid, ref)}")
        if ref in aliases and not replace:
            current = aliases[ref].get("to") if isinstance(aliases[ref], dict) else ""
            raise RefusedError(409, "alias_exists",
                          f"{describe(cid, ref)} is already merged elsewhere",
                          {"to": current if isinstance(current, str) else ""})
        before = effective.live_canon(cid, ledgers)
        canonical = before.get(to, to)
        mine, theirs = _standing(ledgers, ref), _standing(ledgers, canonical)
        if (effective.is_live(sp, mine["status"]) != effective.is_live(sp, theirs["status"])
                and not accept_status_change):
            raise RefusedError(409, "liveness_mismatch",
                          f"{describe(cid, ref)} is {mine['status']} but "
                          f"{describe(cid, canonical)} is {theirs['status']}",
                          {"source": mine, "canonical": {"ref": canonical, **theirs}})
        record = {"to": to, "created": paths.now_iso(), "source": source, "note": note}
        with _journal_alias(cid, ref,
                            f"{describe(cid, ref)} → merged into {describe(cid, to)}"):
            doc.put_alias(cid, ref, record)
        after = effective.live_canon(cid, effective.Ledgers.load(cid))
        affected = sorted(s for s in after
                          if s != ref and before.get(s, s) != after.get(s, s))
        out = {"alias": {"ref": ref, **record}, "affected": affected}
        # The canonical's due is authoritative, so a deadline only the merged
        # record carried drops out of the effective view. It is never inherited
        # silently (spec §5.1): the response says so, and copying it is the
        # reader's explicit ledger edit.
        if mine["due"] and not theirs["due"]:
            out["dues"] = {"source": mine["due"], "canonical": theirs["due"]}
        return out


def remove_alias(cid: str, ref: str) -> dict:
    """Unmerge `ref`: it is its own effective record again."""
    with locks.campaign_lock(cid):
        _require_wellformed(cid, {"aliases"})
        aliases = doc.read(cid)["aliases"]
        if ref not in aliases:
            raise RefusedError(404, "not_found", f"{describe(cid, ref)} is not merged")
        record = aliases[ref]
        to = record.get("to") if isinstance(record, dict) else ""
        with _journal_alias(cid, ref, f"{describe(cid, ref)} — unmerged from "
                                      f"{describe(cid, to if isinstance(to, str) else '')}"):
            doc.drop_alias(cid, ref)
        return {"ok": True}


def create_link(cid: str, a: str, b: str, relation: str, *, scene: str = "",
                note: str = "") -> dict:
    """Record ``a <relation> b`` between the records' current canonicals."""
    with locks.campaign_lock(cid):
        ap, _ = _split(a)
        bp, _ = _split(b)
        rule = effective.RELATIONS.get(relation)
        if rule is None or ap not in rule[0] or bp not in rule[1]:
            raise RefusedError(400, "invalid_relation",
                          f"{relation!r} cannot join a {ap} to a {bp}")
        _require_wellformed(cid, {"aliases", "links"})
        ledgers = effective.Ledgers.load(cid)
        _require_readable(ledgers, ap, bp)
        live = effective.live_canon(cid, ledgers)
        ca, cb = live.get(a, a), live.get(b, b)
        _require_exists(ledgers, cid, ca, cb)
        if ca == cb:
            raise RefusedError(400, "self_link", "both ends are the same record")
        key = (relation, *sorted((ca, cb))) if not rule[2] else (relation, ca, cb)
        for link in effective.links(cid, ledgers):
            other = ((link["relation"], *sorted((link["a"], link["b"]))) if not rule[2]
                     else (link["relation"], link["a"], link["b"]))
            if other == key:
                raise RefusedError(409, "link_exists", "that link already exists",
                              {"id": link["id"]})
        lid = canon.link_id(relation, ca, cb)
        # Membership, not `get_link(...) is not None`: a hand-edited null stored
        # under this id is still a record, and writing over it would replace it.
        if lid in doc.read(cid)["links"]:
            raise RefusedError(409, "link_exists", "that link already exists", {"id": lid})
        record = {"a": ca, "b": cb, "relation": relation, "created": paths.now_iso(),
                  "scene": scene, "note": note}
        with _journal_link(cid, lid, f"{describe(cid, ca)} {relation} {describe(cid, cb)}"):
            doc.put_link(cid, lid, record)
        return {"link": {"id": lid, **record}, "given": {"a": a, "b": b}}


def _link_label(cid: str, record) -> str:
    """``<a> <relation> <b>`` for a stored link of any shape."""
    if not isinstance(record, dict):
        return "link"

    def part(value, name: bool) -> str:
        if not isinstance(value, str):
            return "?"
        return describe(cid, value) if name else value

    return " ".join((part(record.get("a"), True), part(record.get("relation"), False),
                     part(record.get("b"), True)))


def remove_link(cid: str, lid: str) -> dict:
    with locks.campaign_lock(cid):
        _require_wellformed(cid, {"links"})
        links = doc.read(cid)["links"]
        if lid not in links:
            raise RefusedError(404, "not_found", "no such link")
        with _journal_link(cid, lid, f"{_link_label(cid, links[lid])} — removed"):
            doc.drop_link(cid, lid)
        return {"ok": True}


def merged_sources(cid: str, ref: str) -> list[str]:
    """The records merged directly into `ref`. Strict: a continuity.json whose
    aliases cannot be read raises `ContinuityError`, because "none" would be a
    guess -- and the guess a delete would act on."""
    if set(doc.malformed(cid)) & {"file", "aliases"}:
        raise doc.ContinuityError("continuity.json's aliases cannot be read")
    aliases = doc.read(cid)["aliases"]
    return sorted(src for src, record in aliases.items()
                  if isinstance(record, dict) and record.get("to") == ref)


def forget_ref(cid: str, ref: str, name: str = "") -> list[str]:
    """Remove every alias and link that names `ref`, journalling each removal.

    Called inside a record's DELETE, under the same hold: thread, commitment and
    event ids are slugs that become free on delete, and a recreated record of
    the same name would otherwise inherit the dead one's merges and links.
    `name` is the deleted record's display name, which `describe` can no longer
    read once the record is gone.

    Only the sections that can name `ref` must be readable: an event can be the
    end of a link but never part of a merge, so a malformed ``aliases`` section
    does not stop an event's links from being removed."""
    def label_of(other: str) -> str:
        return name if other == ref and name else describe(cid, other)

    with locks.campaign_lock(cid):
        try:
            prefix, _ = canon.split_ref(ref)
        except ValueError:
            prefix = ""
        needed = {"file", "links"} | ({"aliases"} if prefix in effective.ALIASABLE else set())
        if set(doc.malformed(cid)) & needed:
            raise doc.ContinuityError("continuity.json cannot be read")
        data = doc.read(cid)
        removed: list[str] = []
        for src, record in sorted(data["aliases"].items()):
            to = record.get("to") if isinstance(record, dict) else None
            if src != ref and to != ref:
                continue
            label = (f"{label_of(src)} → merged into "
                     f"{label_of(to) if isinstance(to, str) and to else '?'}"
                     " — removed with deleted record")
            with _journal_alias(cid, src, label):
                doc.drop_alias(cid, src)
            removed.append(src)
        for lid, record in sorted(data["links"].items()):
            if not isinstance(record, dict) or ref not in (record.get("a"), record.get("b")):
                continue
            parts = [label_of(record[k]) if isinstance(record.get(k), str) else "?"
                     for k in ("a", "b")]
            rel = record.get("relation") if isinstance(record.get("relation"), str) else "?"
            with _journal_link(cid, lid,
                               f"{parts[0]} {rel} {parts[1]} — removed with deleted record"):
                doc.drop_link(cid, lid)
            removed.append(lid)
        return removed
