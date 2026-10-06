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
from ..scenes import read as scenes_read
from . import candidates, canon, doc, effective, pending


class RefusedError(Exception):
    """A write this module will not make, with the HTTP shape to report it in."""

    def __init__(self, status: int, kind: str, detail: str, extra: dict | None = None):
        super().__init__(detail)
        self.status = status
        self.kind = kind
        self.detail = detail
        self.extra = extra or {}


_LEDGER_FILE = {"thread": "plot", "commitment": "commitments", "event": "events"}

#: How a journal label says a link's relation (§30: no store token reaches a
#: reader). A label is read as one sentence in the History rail -- "Mara's oath
#: is due by The coronation" -- so these are the sentence forms of §5.3's
#: Meaning column, where the Ledger and the Story Graph lead a line with the
#: phrase form ("Due by The coronation"). Held to `effective.RELATIONS` by
#: `test_continuity_wording.py`.
RELATION_WORDS: dict[str, str] = {
    "continues": "continues",
    "subthread_of": "is a subthread of",
    "pays_off": "pays off",
    "before": "is due before",
    "on": "is due on",
    "after": "is due after",
    "by": "is due by",
    "related_to": "is related to",
}


def _relation_words(relation) -> str:
    """`RELATION_WORDS`' entry, or "is linked to" for a relation it does not
    hold -- a hand-edited one, which a label still must not show raw."""
    if isinstance(relation, str) and relation in RELATION_WORDS:
        return RELATION_WORDS[relation]
    return "is linked to"


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


def _kind_word(ref) -> str:
    """The kind a ref names, as a word -- "record" for one that names none."""
    prefix = ref.split(":", 1)[0] if isinstance(ref, str) else ""
    return prefix if prefix in _LEDGER_FILE else "record"


def reader_name(cid: str, ref, ledgers: effective.Ledgers | None = None, *,
                start: bool = False) -> str:
    """A record's name in a journal label or a refusal: `describe`'s, or --
    where that could only answer with the ref -- the kind in words (§30: no
    store token reaches a reader). The server's counterpart of the review's
    `recordName`: a record whose ledger will not read "cannot be read right
    now" (it may well exist), one the ledger reads without is "a missing
    thread", and one that is there with no name is "an untitled thread".
    `describe` itself still answers with the ref, because the review's titles
    are compared against it to tell those apart. `start` capitalises a
    fallback that opens a sentence; a real title is never recased. Never
    raises."""
    try:
        title = describe(cid, ref, ledgers) if isinstance(ref, str) and ref else ""
        if title and title != ref:
            return title
        word = _kind_word(ref)
        if ledgers is None:
            ledgers = effective.Ledgers.load(cid)
        known = ledgers.exists(ref) if word != "record" else False
        article = "an" if word == "event" else "a"
        if known is None:
            text = f"{article} {word} that cannot be read right now"
        elif known:
            text = f"an untitled {word}"
        else:
            text = f"a missing {word}"
    except Exception:  # noqa: BLE001 -- a label must never be the thing that fails
        text = "a missing record"
    return text[:1].upper() + text[1:] if start else text


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
    """Refuse while a named kind's ledger will not read. The refusal names the
    records in words -- "threads", never "plot", the stem of the file that
    holds them (§30: no store token reaches a reader)."""
    unreadable = set(ledgers.unreadable)
    bad = [f"{p}s" for p in _LEDGER_FILE if p in prefixes and _LEDGER_FILE[p] in unreadable]
    if bad:
        words = bad[0] if len(bad) == 1 else f"{', '.join(bad[:-1])} and {bad[-1]}"
        raise RefusedError(409, "unreadable", f"{words} cannot be read right now")


def _require_exists(ledgers: effective.Ledgers, cid: str, *refs: str) -> None:
    for ref in refs:
        if ledgers.exists(ref) is False:
            raise RefusedError(404, "not_found", f"That {_kind_word(ref)} does not exist")


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


def _validated_alias(cid: str, ref: str, to: str, *, replace: bool,
                     accept_status_change: bool):
    """Every refusal a merge makes, before anything is written. Returns what
    the write then needs: the ledgers, the live view before it, both standings
    and the canonical the merge joins."""
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
                      f"{reader_name(cid, to, start=True)} is already merged into "
                      f"{reader_name(cid, ref)}")
    if ref in aliases and not replace:
        current = aliases[ref].get("to") if isinstance(aliases[ref], dict) else ""
        raise RefusedError(409, "alias_exists",
                      f"{reader_name(cid, ref, start=True)} is already merged elsewhere",
                      {"to": current if isinstance(current, str) else ""})
    before = effective.live_canon(cid, ledgers)
    canonical = before.get(to, to)
    mine, theirs = _standing(ledgers, ref), _standing(ledgers, canonical)
    standings = {"source": mine, "canonical": {"ref": canonical, **theirs}}
    if (effective.is_live(sp, mine["status"]) != effective.is_live(sp, theirs["status"])
            and not accept_status_change):
        raise RefusedError(409, "liveness_mismatch",
                      f"{reader_name(cid, ref, start=True)} is {mine['status']} but "
                      f"{reader_name(cid, canonical)} is {theirs['status']}",
                      standings)
    return before, standings


def validate_alias(cid: str, ref: str, to: str, *, replace: bool = False,
                   accept_status_change: bool = False) -> dict:
    """Every refusal `create_alias` would make for merging `ref` into `to`,
    writing nothing. Answers ``{"source": standing, "canonical": {ref,
    **standing}}`` -- a standing is ``{status, kind, due}`` -- which is what an
    apply validates the due copy against before its first write (§22 step 6)."""
    with locks.campaign_lock(cid):
        return _validated_alias(cid, ref, to, replace=replace,
                                accept_status_change=accept_status_change)[1]


def create_alias(cid: str, ref: str, to: str, *, replace: bool = False,
                 accept_status_change: bool = False, note: str = "",
                 source: str = "manual") -> dict:
    """Merge `ref` into `to`: `ref` stops being its own effective record."""
    with locks.campaign_lock(cid):
        before, standings = _validated_alias(cid, ref, to, replace=replace,
                                             accept_status_change=accept_status_change)
        mine, theirs = standings["source"], standings["canonical"]
        record = {"to": to, "created": paths.now_iso(), "source": source, "note": note}
        with _journal_alias(cid, ref,
                            f"{reader_name(cid, ref, start=True)} → merged into "
                            f"{reader_name(cid, to)}"):
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
            raise RefusedError(404, "not_found",
                               f"{reader_name(cid, ref, start=True)} is not merged")
        record = aliases[ref]
        to = record.get("to") if isinstance(record, dict) else ""
        with _journal_alias(cid, ref, f"{reader_name(cid, ref, start=True)} — unmerged from "
                                      f"{reader_name(cid, to)}"):
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
                               "That kind of link cannot join these two records.")
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
        with _journal_link(cid, lid, f"{reader_name(cid, ca, start=True)} "
                                     f"{_relation_words(relation)} {reader_name(cid, cb)}"):
            doc.put_link(cid, lid, record)
        return {"link": {"id": lid, **record}, "given": {"a": a, "b": b}}


def _link_label(cid: str, record) -> str:
    """``<a> <relation words> <b>`` for a stored link of any shape."""
    if not isinstance(record, dict):
        return "link"

    return " ".join((reader_name(cid, record.get("a"), start=True),
                     _relation_words(record.get("relation")),
                     reader_name(cid, record.get("b"))))


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


#: The staged edit kinds whose target can be an alias source, and the ref
#: prefix each one's target id takes.
_MERGEABLE_EDITS = {"plot": "thread", "commitment": "commitment"}


def merged_edit_targets(cid: str, edits: list) -> list[dict]:
    """Every plot/commitment edit in a review batch whose physical target is
    now a live alias source -- merged away after the review was staged.

    Slice C redirects a row naming an alias source when the review is STAGED.
    A review staged before the merge still names the source, and its `before`
    token was read off the source, so the save refuses it rather than
    redirecting: a beat written onto the canonical at save time is one
    `check_conflicts` never vouched for. Each entry is
    ``{index, id, label, source, canonical, canonical_title}``; `index` counts
    the submitted batch, as `check_conflicts`' does.

    A malformed continuity.json reads as no aliases (`doc.read`), so no alias
    is effective and this lists nothing -- the same view every effective reader
    and the staging redirect take of it."""
    ledgers = effective.Ledgers.load(cid)
    live = effective.live_canon(cid, ledgers)
    out: list[dict] = []
    for index, edit in enumerate(edits):
        if not isinstance(edit, dict):
            continue
        kind = edit.get("kind")
        prefix = _MERGEABLE_EDITS.get(kind) if isinstance(kind, str) else None
        target = edit.get("target")
        rid = target.get("id") if isinstance(target, dict) else None
        if prefix is None or not isinstance(rid, str) or not rid:
            continue
        source = f"{prefix}:{rid}"
        canonical = live.get(source)
        if canonical is None or canonical == source:
            continue
        label = edit.get("label")
        out.append({"index": index, "id": rid,
                    "label": label if isinstance(label, str) and label
                    else reader_name(cid, source, ledgers),
                    "source": source, "canonical": canonical,
                    "canonical_title": reader_name(cid, canonical, ledgers)})
    return out


def forget_ref(cid: str, ref: str, name: str = "") -> list[str]:
    """Remove every alias and link that names `ref`, journalling each removal.

    Called inside a record's DELETE, under the same hold: thread, commitment and
    event ids are slugs that become free on delete, and a recreated record of
    the same name would otherwise inherit the dead one's merges and links.
    `name` is the deleted record's display name, which `describe` can no longer
    read once the record is gone; every other end is named by `reader_name`, so
    an end that was already missing reads as "a missing event", never its ref.

    Only the sections that can name `ref` must be readable: an event can be the
    end of a link but never part of a merge, so a malformed ``aliases`` section
    does not stop an event's links from being removed."""
    def label_of(other, start: bool = False) -> str:
        return name if other == ref and name else reader_name(cid, other, start=start)

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
            label = (f"{label_of(src, start=True)} → merged into {label_of(to)}"
                     " — removed with deleted record")
            with _journal_alias(cid, src, label):
                doc.drop_alias(cid, src)
            removed.append(src)
        for lid, record in sorted(data["links"].items()):
            if not isinstance(record, dict) or ref not in (record.get("a"), record.get("b")):
                continue
            parts = [label_of(record.get("a"), start=True), label_of(record.get("b"))]
            rel = _relation_words(record.get("relation"))
            with _journal_link(cid, lid,
                               f"{parts[0]} {rel} {parts[1]} — removed with deleted record"):
                doc.drop_link(cid, lid)
            removed.append(lid)
        return removed


# ------------------------------------------------- acting on a cached finding
#
# A finding is a guess the reconcile sweep made against the records as they
# stood (`candidates`). Acting on one later has to prove those records still
# mean what the reader looked at (`check_candidate`, §22 steps 1-5), validate
# every part of the operation before the first write (`plan_apply`, step 6),
# and clean the cache up in an order whose failure costs nothing (`settle`).
# The status writes themselves are `routes/ledger.py`'s, so the route that
# applies a closure calls its helpers with the plan made here.

#: The verdicts that mean "no longer pending": hidden from the candidates read,
#: so a request naming one is told the finding has gone, not offered a resubmit.
_HIDDEN = frozenset({"gone", "satisfied", "suppressed", "settled"})

#: A dismissal's decisions (spec §21); ``keep_open`` only for lifecycle kinds.
DISMISS_DECISIONS = ("dismiss", "keep_open")

#: The apply body's keys and the type each takes (spec §21). Every one is
#: optional; a JSON null reads as absent.
_BODY_TYPES: dict[str, type] = {
    "op": str, "canonical": str, "from": str, "to": str, "relation": str,
    "status": str, "beat": str, "scene": str, "expect_fingerprint": str,
    "accept_status_change": bool, "copy_due": bool,
}

_GONE = "This finding is no longer pending."


class PartialSettleError(OSError):
    """An I/O failure after part of a settle landed. `landed` names the parts
    that did (``"suppression"``, ``"cache"``), so the route can report them and
    stamp the write token a non-2xx answer would otherwise leave unmoved."""

    def __init__(self, landed: list[str], detail: str = "the finding was only partly settled"):
        super().__init__(detail)
        self.landed = list(landed)


def _scene_titles(cid: str) -> dict[str, str] | None:
    """Scene id -> title for every scene that exists now, or None when the list
    cannot be read (nothing can then be said about evidence either way)."""
    try:
        rows = scenes_read.list_scenes(cid)
    except OSError:
        return None
    return {row["id"]: fieldtext.text(row.get("title")) or row["id"] for row in rows}


def _verdict(current: pending.Current, record: dict) -> str:
    try:
        return pending.verdict(current, record)
    except (AttributeError, TypeError, ValueError, UnicodeEncodeError):
        return "gone"           # what `pending.findings` reads such a record as


def _require_known(current: pending.Current, record: dict) -> None:
    """An ``unknown`` verdict as a refusal: the ledger named, when one is."""
    prefixes = {canon.split_ref(ref)[0] for ref in record["refs"]}
    _require_readable(current.ledgers, *prefixes)
    raise RefusedError(409, "unreadable", "these records cannot be read right now")


def check_candidate(cid: str, candidate_id: str, expect: str | None, *,
                    evidence: bool = True) -> dict:
    """Prove a cached finding still means what the reader looked at (§22).

    In order (Decision 16): continuity.json must be well formed, because the
    lenient read would take a malformed section as "no merges, no links, no
    dismissals" (409 ``malformed``); the finding must still be cached and
    pending (404 ``not_found`` for one hidden by its verdict, 409 for an
    unreadable ledger); and its CURRENT fingerprint must equal `expect` -- the
    cached one when None -- with every evidence scene still there (409
    ``stale_candidate``, carrying the current fingerprint and rows so the
    reader can look and resubmit against them, §12.9). `evidence=False` is
    dismiss's: setting a finding aside does not depend on the model's citation.

    Returns ``{"record", "current", "fingerprint", "scenes"}`` -- the current
    fingerprint, which is what a suppression is then keyed by.
    """
    with locks.campaign_lock(cid):
        _require_wellformed(cid, {"aliases", "links", "suppressions"})
        record = candidates.read(cid)["records"].get(candidate_id)
        if record is None:
            raise RefusedError(404, "not_found", _GONE)
        current = pending.Current.load(cid)
        verdict = _verdict(current, record)
        if verdict in _HIDDEN:
            raise RefusedError(404, "not_found", _GONE)
        if verdict == "unknown":
            _require_known(current, record)
        fp = pending.fingerprint(current, record["kind"], record["refs"]) or ""
        scenes = _scene_titles(cid)
        reason = None
        if fp != (record["fingerprint"] if expect is None else expect):
            reason = "records"
        elif evidence and scenes is not None and not pending.evidence_ok(
                record["proposal"], set(scenes)):
            reason = "evidence"
        if reason is not None:
            titles = scenes or {}
            rows = pending.rows(current, record["refs"],
                                scene_title=lambda sid: titles.get(sid, ""))
            raise RefusedError(409, "stale_candidate",
                               "Records have changed since this was found.",
                               {"current": {"fingerprint": fp, "records": rows},
                                "reason": reason})
        return {"record": record, "current": current, "fingerprint": fp,
                "scenes": scenes}


def _body(body) -> dict:
    """The apply body with every key type-checked; a null reads as absent."""
    if not isinstance(body, dict):
        raise RefusedError(400, "bad_body", "the apply body must be an object")
    out: dict = {}
    for key, kind in _BODY_TYPES.items():
        value = body.get(key)
        if value is None:
            continue
        if type(value) is not kind:
            raise RefusedError(400, "bad_body", f"{key!r} must be a {kind.__name__}")
        out[key] = value
    return out


def _beat(cid: str, body: dict, scenes: dict[str, str] | None) -> tuple[str, str]:
    """The optional beat and its scene, which come as a pair; a scene named
    must exist (a link's scene too)."""
    beat = body.get("beat", "").strip()
    scene = body.get("scene", "").strip()
    if beat and not scene:
        raise RefusedError(400, "bad_scene", "a beat needs the scene it happened in")
    if scene:
        if scenes is None:
            raise RefusedError(409, "unreadable", "the scene list cannot be read right now")
        if scene not in scenes:
            raise RefusedError(400, "bad_scene", f"no scene {scene!r}")
    return beat, scene


def _link_allowed(record: dict, a: str, b: str, relation: str) -> bool:
    """Decision 16's relation table for one candidate, read per kind: a temporal
    pair links the commitment to its event; a thread and a commitment by
    ``pays_off``; two threads by ``continues`` or ``subthread_of``; anything
    by ``related_to``. Both ends must be the candidate's two refs."""
    rule = effective.RELATIONS.get(relation)
    if rule is None or {a, b} != set(record["refs"]) or a == b:
        return False
    if canon.split_ref(a)[0] not in rule[0] or canon.split_ref(b)[0] not in rule[1]:
        return False
    if relation == "related_to":
        return True
    if pending.is_temporal(record):
        return relation in pending.TEMPORAL_RELATIONS
    if record["kind"] == "possible_relation":
        return relation == "pays_off"
    return relation in ("continues", "subthread_of")


def _plan_alias(cid: str, record: dict, body: dict) -> dict:
    if record["kind"] != "possible_duplicate":
        raise RefusedError(400, "bad_op", "only two records of one type can be merged")
    canonical = body.get("canonical", "")
    if canonical not in record["refs"]:
        raise RefusedError(400, "bad_canonical", "keep one of this finding's two records")
    source = next(ref for ref in record["refs"] if ref != canonical)
    accept = body.get("accept_status_change", False)
    standings = validate_alias(cid, source, canonical, accept_status_change=accept)
    plan: dict = {"alias": {"ref": source, "to": canonical, "accept": accept}}
    if body.get("copy_due"):
        due, theirs = standings["source"]["due"], standings["canonical"]
        if not due or theirs["due"]:
            raise RefusedError(400, "due_not_copyable",
                               "only a deadline the kept record lacks can be copied")
        plan["copy_due"] = due
        plan["target"] = canon.split_ref(theirs["ref"])[1]
    return plan


def _plan_link(record: dict, body: dict) -> dict:
    if record["kind"] not in candidates.PAIR_KINDS:
        raise RefusedError(400, "bad_op", "only a pair of records can be linked")
    a, b, relation = body.get("from", ""), body.get("to", ""), body.get("relation", "")
    if not _link_allowed(record, a, b, relation):
        raise RefusedError(400, "invalid_relation",
                           "That kind of link cannot join these records in that direction.")
    return {"link": {"a": a, "b": b, "relation": relation}}


def _plan_lifecycle(record: dict, op: str, body: dict) -> dict:
    expected = {"close": "possible_thread_closure",
                "resolve": "possible_commitment_resolution"}.get(op)
    if expected is None:                                   # keep_open
        if record["kind"] not in candidates.LIFECYCLE_KINDS:
            raise RefusedError(400, "bad_op", "only a closure can be kept open")
        return {"decision": "keep_open"}
    if record["kind"] != expected:
        raise RefusedError(400, "bad_op", f"{op!r} does not apply to this finding")
    status = body.get("status", "")
    if op == "close":
        if status not in ("", "closed"):
            raise RefusedError(400, "bad_status", "a thread closes as 'closed'")
        status = "closed"
    elif status not in commitments_store.RESOLVED:
        raise RefusedError(400, "bad_status",
                           "a commitment resolves as fulfilled, broken or expired")
    return {"status": status, "target": canon.split_ref(record["refs"][0])[1]}


def plan_apply(cid: str, checked: dict, body) -> dict:
    """Validate an apply body against a checked finding, writing nothing
    (Decision 16; §22 step 6: every part before the first write).

    The body is §21's flat dict -- a `BaseModel` cannot declare a field named
    ``from`` without `Field(alias=...)`, which the pydantic guard forbids
    (Decision 15) -- so each key is type-checked here (400 ``bad_body``). The
    plan is ``{"op", "beat", "scene"}`` plus, per op, ``"alias": {ref, to,
    accept}`` and optionally ``"copy_due"`` and the canonical's physical
    ``"target"``; ``"link": {a, b, relation}``; ``"status"`` and the physical
    ``"target"`` for a closure or resolution; ``"decision": "keep_open"``.

    A beat is written only by a closure or a resolution: offered with any other
    op it is refused rather than silently dropped (400 ``bad_op``). ``"scene"``
    is non-empty only for a link that names one or for a beat's scene -- the
    evidence scene a closure's `move_thread`/`move_commitment` is given with
    ``keep_later_scene``; otherwise the record's stored ``last_scene`` stays.
    """
    record = checked["record"]
    fields = _body(body)
    op = fields.get("op", "")
    if fields.get("beat", "").strip() and op not in ("close", "resolve"):
        raise RefusedError(400, "bad_op", "only a closure or a resolution can write a beat")
    beat, scene = _beat(cid, fields, checked.get("scenes"))
    if op == "alias":
        plan = _plan_alias(cid, record, fields)
    elif op == "link":
        plan = _plan_link(record, fields)
    elif op in ("close", "resolve", "keep_open"):
        plan = _plan_lifecycle(record, op, fields)
    else:
        raise RefusedError(400, "bad_op", f"unknown operation {op!r}")
    # The scene means something only as a link's provenance or a beat's scene;
    # anywhere else it is dropped, so the route can pass ``scene or None``.
    if op != "link" and not beat:
        scene = ""
    return {"op": op, "beat": beat, "scene": scene, **plan}


def settle(cid: str, candidate_id: str, record: dict, fingerprint: str,
           decision: str | None) -> list[str]:
    """Clean a finding out of the review: with a `decision`, first the
    suppression keyed by its CURRENT `fingerprint`, then the cache drop.

    That order is the point (Decision 16). A finding that is suppressed but
    still cached reads ``suppressed`` and is hidden, so a drop that then fails
    costs nothing; dropping first and failing to suppress would remove it
    unsuppressed and the next refresh would bring it back. Suppressions are not
    journalled (§12.8). Returns the parts that landed; an `OSError` after one
    did is re-raised as `PartialSettleError` naming them.
    """
    landed: list[str] = []
    with locks.campaign_lock(cid):
        try:
            if decision:
                doc.put_suppression(cid, fingerprint, {
                    "kind": record["kind"], "refs": list(record["refs"]),
                    "decision": decision, "created": paths.now_iso()})
                landed.append("suppression")
            if candidates.drop(cid, candidate_id) is not None:
                landed.append("cache")
        except OSError as e:
            if landed:
                raise PartialSettleError(landed) from e
            raise
    return landed


def dismiss(cid: str, candidate_id: str, decision: str,
            expect: str | None = None) -> dict:
    """Set a finding aside: ``dismiss``, or ``keep_open`` for a closure.

    The same malformed, hidden-verdict and stale checks as apply, but not the
    evidence one (Decision 17): a stale finding is refused unless `expect` is
    its current fingerprint, because suppressing a meaning nobody looked at
    would hide its replacement. A suppressions section that went malformed
    under the hold is 409 ``malformed``; with the suppression written first,
    nothing has landed by then.
    """
    with locks.campaign_lock(cid):
        checked = check_candidate(cid, candidate_id, expect, evidence=False)
        record = checked["record"]
        if decision not in DISMISS_DECISIONS or (
                decision == "keep_open" and record["kind"] not in candidates.LIFECYCLE_KINDS):
            raise RefusedError(400, "bad_decision",
                               f"{decision!r} is not a way to set this finding aside")
        try:
            settle(cid, candidate_id, record, checked["fingerprint"], decision)
        except doc.ContinuityError as e:
            raise RefusedError(409, "malformed", str(e)) from e
        return {"ok": True, "fingerprint": checked["fingerprint"]}


def restore_suppression(cid: str, fp: str) -> dict:
    """Undo a dismissal (§12.7): the finding comes back at the next refresh.
    One atomic write, so a failure lands nothing."""
    with locks.campaign_lock(cid):
        _require_wellformed(cid, {"suppressions"})
        if fp not in doc.read(cid)["suppressions"]:
            raise RefusedError(404, "not_found", "no such dismissal")
        doc.drop_suppression(cid, fp)
        return {"ok": True}
