"""The `searchdocs` hot rebuild for 05's sync (roadmap 08-C2c, spec section 9).

`cache_sync` owns the hook (`_SearchDocsHook`): it imports its hooks, so this
module may not import it back. The work is here, as plain functions over plain
data, and the class there folds what they return into its results -- the
overview hook's arrangement. The functions are not named `local` and `network`:
those are the protocol's, and the operation guard holds every `.network(...)`
call to the warm-up module.

**The rows name the documents.** A document's rows (`searchdocs.scene`, and
the `vector:searchdocs.scene:<digest>` rows `record_embedded` writes) sit on
each file it read. On the scene's own file a row names the scene,
`{"campaign", "identity"}`; on a shared file -- a ledger, a card, a world
record -- it names the campaign alone, one row per campaign, so a long
campaign never pushes a shared file past 03's `MAX_HISTORY_ROWS` and out of
every hook's sight (`live.row_instance`). A scene row rebuilds that scene; a
campaign row rebuilds every live scene of the campaign, and a campaign vector
row re-embeds those whose own scene file carries the same kind's row under
their identity. An identity no live scene carries belongs to a deleted
scene, or to one whose sid a new scene took under a new identity; its rows
rebuild and embed nothing.

**What is re-embedded.** Only a document that was embedded (it has a vector
row) in the batch's space, under the same scene identity, whose text the cache
now lacks -- at most `REINDEX_LIMIT` a campaign a sync (one round trip). The
rest stays for 09's turn warm. A row in a space the role has moved away from
is `stale_space`, and embedding off is `embedding_off`: nothing is sent for
either, since re-embedding there would embed content never embedded there.

`rebuild` touches no network and holds no lock; `reembed` is the network half.
Outcomes are per row, `(rel, kind, outcome)`, in 05's report vocabulary, and
hold no document text.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from ... import embeddings
from .. import compiled, vectors
from . import embedded, live
from . import scene as scene_mod

#: Documents re-embedded per campaign per sync: one round trip. The rest stay
#: for 09's lazy turn warm. Structural; tune later.
REINDEX_LIMIT = embeddings.BATCH - 1
DOC_KIND = scene_mod.KIND
VECTOR_PREFIX = f"vector:{embedded.PROJECTION}:"

#: One process-wide sync client, for its connection pool -- the producers'
#: pattern; tests replace it.
_CLIENT = embeddings.EmbeddingsClient()


def owns(kind: str) -> bool:
    """`searchdocs.scene`, and a scene-document vector in any space: a row in
    another space is still this hook's to answer (`stale_space`)."""
    return kind == DOC_KIND or (kind.startswith(VECTOR_PREFIX) and len(kind) > len(VECTOR_PREFIX))


@dataclass
class Rebuilt:
    """What `rebuild` found: an outcome per row, the embedding counts, and
    the plan -- per campaign, each document to re-embed with the rows it
    answers -- or None when there is nothing to send."""

    outcomes: list[tuple[str, str, str]] = field(default_factory=list)
    embedding: dict[str, int] = field(default_factory=dict)
    plan: dict[str, list[tuple[live.LiveDocument, list[tuple[str, str]]]]] | None = None


@dataclass
class Reembedded:
    outcomes: list[tuple[str, str, str]] = field(default_factory=list)
    embedding: dict[str, int] = field(default_factory=dict)
    failure: str = ""


def _instance(row: compiled.Materialized) -> tuple[str, str | None] | None:
    """`(campaign, scene key)` for a scene's own row, `(campaign, None)` for
    a campaign row on a shared file; None for a row that names nothing. The
    key is `id:<identity>`, or `sid:<sid>` for a scene from before the
    identity backfill (`live.row_instance`)."""
    try:
        value = json.loads(row.instance) if row.instance else None
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    cid, identity, sid = value.get("campaign"), value.get("identity"), value.get("sid")
    if not (isinstance(cid, str) and cid):
        return None
    if isinstance(identity, str) and identity:
        return cid, f"id:{identity}"
    if isinstance(sid, str) and sid:
        return cid, f"sid:{sid}"
    if identity is None and sid is None:
        return cid, None
    return None


def _key(doc: live.LiveDocument) -> str:
    """The scene key a live document answers to: an identity-less scene only
    under its sid, so a recycled sid with an identity of its own answers to
    nothing a deleted scene left."""
    identity = doc.doc.meta.get("identity", "")
    return f"id:{identity}" if identity else f"sid:{doc.sid}"


def _count(counts: dict[str, int], name: str, n: int = 1) -> None:
    counts[name] = counts.get(name, 0) + n


@dataclass
class _Wanted:
    """One campaign's hot rows: those naming a scene, by scene key, and those
    naming the whole campaign (a shared file's)."""

    scenes: dict[str, list[tuple[str, compiled.Materialized]]] = field(default_factory=dict)
    campaign: list[tuple[str, compiled.Materialized]] = field(default_factory=list)


def rebuild(hot: Mapping[str, Sequence[compiled.Materialized]], *, space: dict | None,
            space_digest: str | None, embedding: bool, dry_run: bool,
            halted: Callable[[], object]) -> Rebuilt:
    """Rebuild the documents the hot rows name, and judge each vector row.
    No network. A dry run stores nothing and plans nothing to send."""
    out = Rebuilt()
    wanted: dict[str, _Wanted] = {}
    for rel, rows in hot.items():
        for row in rows:
            named = _instance(row)
            if named is None:
                out.outcomes.append((rel, row.kind, "skipped"))
                continue
            cid, key = named
            entry = wanted.setdefault(cid, _Wanted())
            if key is None:
                entry.campaign.append((rel, row))
            else:
                entry.scenes.setdefault(key, []).append((rel, row))
    plan: dict[str, list[tuple[live.LiveDocument, list[tuple[str, str]]]]] = {}
    for cid in sorted(wanted):
        if halted():
            break
        _campaign(cid, wanted[cid], out, plan, space=space, space_digest=space_digest,
                  embedding=embedding, dry_run=dry_run)
    out.plan = plan or None
    return out


def _campaign(cid: str, wanted: _Wanted, out: Rebuilt, plan: dict, *, space: dict | None,
              space_digest: str | None, embedding: bool, dry_run: bool) -> None:
    rows_here = [*(r for rows in wanted.scenes.values() for r in rows), *wanted.campaign]
    try:
        sids = live.identities(cid)
        # A campaign row is every live scene of the campaign; a scene row is
        # that scene, while it is still live under that key.
        if wanted.campaign:
            only = None
        else:
            only = frozenset(sids[k[3:]] if k.startswith("id:") else k[4:]
                             for k in wanted.scenes if not k.startswith("id:") or k[3:] in sids)
        live_set, rendered = live.build(cid, only=only, store=not dry_run) \
            if only is None or only else (None, frozenset())
        docs = {_key(d): d for d in live_set.documents} if live_set else {}
        embedded_ids = _embedded(cid, docs, wanted.campaign)
    except Exception as exc:  # noqa: BLE001 -- one campaign's failure is its rows' status
        out.outcomes.extend((rel, row.kind, f"failed:{type(exc).__name__}") for rel, row in rows_here)
        return
    for key, rows in wanted.scenes.items():
        doc = docs.get(key)
        for rel, row in rows:
            if doc is None:
                # A deleted scene, a recycled sid's old identity, or a head
                # that will not read: nothing of this document to rebuild.
                out.outcomes.append((rel, row.kind, "skipped"))
            elif row.kind == DOC_KIND:
                built = "built" if doc.doc.key in rendered else "hit"
                out.outcomes.append((rel, row.kind, "planned" if dry_run else built))
            else:
                _vector(cid, rel, row.kind, doc, out, plan, space=space,
                        space_digest=space_digest, embedding=embedding, dry_run=dry_run)
    _campaign_rows(cid, wanted.campaign, docs, rendered, embedded_ids, out, plan, space=space,
                   space_digest=space_digest, embedding=embedding, dry_run=dry_run)


def _campaign_rows(cid: str, rows: Sequence[tuple[str, compiled.Materialized]],
                   docs: Mapping[str, live.LiveDocument], rendered: frozenset[str],
                   embedded_ids: Mapping[str, set[str]], out: Rebuilt, plan: dict, *,
                   space: dict | None, space_digest: str | None, embedding: bool,
                   dry_run: bool) -> None:
    """A shared file's rows, which name the campaign: its document row is
    `built` when any of the campaign's documents changed; its vector row
    stands for every document whose own scene file says it was embedded in
    THIS row's space."""
    for rel, row in rows:
        if row.kind == DOC_KIND:
            if not docs:
                out.outcomes.append((rel, row.kind, "skipped"))
            elif dry_run:
                out.outcomes.append((rel, row.kind, "planned"))
            else:
                changed = any(d.doc.key in rendered for d in docs.values())
                out.outcomes.append((rel, row.kind, "built" if changed else "hit"))
            continue
        mine = [docs[i] for i in embedded_ids.get(row.kind, ()) if i in docs]
        if not mine:
            out.outcomes.append((rel, row.kind, "skipped"))
        for doc in mine:
            _vector(cid, rel, row.kind, doc, out, plan, space=space, space_digest=space_digest,
                    embedding=embedding, dry_run=dry_run)


def _embedded(cid: str, docs: Mapping[str, live.LiveDocument],
              campaign_rows: Sequence[tuple[str, compiled.Materialized]]) -> dict[str, set[str]]:
    """For each vector kind on a campaign row, the identities whose own scene
    file carries that kind's row naming them: this hook reading its own
    kinds' rows by path, for the live scene paths it already holds."""
    kinds = {row.kind for _rel, row in campaign_rows if row.kind != DOC_KIND}
    if not kinds or not docs:
        return {}
    files = {live.scene_file(cid, d): key for key, d in docs.items()}
    found: dict = {}
    with compiled.batch() as request:
        try:
            # One read per scene file: 03's history budget bounds a READ, so
            # one read over a long campaign's scene files would answer them
            # all empty. A scene file carries only its own scene's rows.
            for path in files:
                found.update(request.materialized([path]))
        finally:
            request.disabled = True                       # a read: nothing to flush
    out: dict[str, set[str]] = {}
    for path, rows in found.items():
        for row in rows:
            named = _instance(row) if row.kind in kinds else None
            key = named[1] if named is not None else None
            if key is not None and key == files.get(path):
                out.setdefault(row.kind, set()).add(key)
    return out


def _vector(cid: str, rel: str, kind: str, doc: live.LiveDocument, out: Rebuilt, plan: dict, *,
            space: dict | None, space_digest: str | None, embedding: bool,
            dry_run: bool) -> None:
    """One document under one vector row: re-embed only in the batch's
    space, only a text the cache lacks."""
    if space is None:
        _count(out.embedding, "embedding_off")
        out.outcomes.append((rel, kind, "skipped"))
        return
    if kind != VECTOR_PREFIX + (space_digest or ""):
        _count(out.embedding, "stale_space")
        out.outcomes.append((rel, kind, "skipped"))
        return
    if vectors.load(space["space"], [doc.doc.text]):
        if not dry_run:
            # Held already (09's turn warm, or another row): its rows move to
            # the document's current inputs.
            embedded.record_embedded(cid, [doc], space=space)
        _count(out.embedding, "held")
        out.outcomes.append((rel, kind, "hit"))
        return
    if not embedding:
        _count(out.embedding, "deferred")
        out.outcomes.append((rel, kind, "deferred"))
        return
    entries = plan.setdefault(cid, [])
    entry = next((e for e in entries if e[0].doc.key == doc.doc.key), None)
    if entry is None:
        entries.append((doc, [(rel, kind)]))
        if dry_run:
            _count(out.embedding, "would_send")
    else:
        entry[1].append((rel, kind))
    out.outcomes.append((rel, kind, "planned"))


def reembed(plan: Mapping[str, Sequence[tuple[live.LiveDocument, Sequence[tuple[str, str]]]]], *,
            space: dict, halted: Callable[[], object],
            root_moved: Callable[[], bool]) -> Reembedded:
    """Send the plan: one `vectors_for` a campaign, at most `REINDEX_LIMIT`
    documents. A document outside this run's window stays `lazy`; a failure
    names only the documents its request carried. A failure other than
    `bad_response` -- the endpoint's, not one input's -- stops the campaigns
    after it, which are named with its kind. A reply that lands after the
    store root moved is never saved: its rows stay `planned` (`unsynced`)."""
    out = Reembedded()
    for cid in sorted(plan):
        entries = plan[cid]
        if out.failure or halted():
            _count(out.embedding, "not_sent", len(entries))
            if out.failure:
                out.outcomes.extend((rel, kind, f"failed:{out.failure}")
                                    for _doc, keys in entries for rel, kind in keys)
            continue
        got = embedded.vectors_for(cid, [doc for doc, _keys in entries], space=space,
                                   client=_CLIENT, limit=REINDEX_LIMIT,
                                   keep=lambda: not root_moved())
        if got.sent:
            _count(out.embedding, "requests")
            _count(out.embedding, "sent", got.sent)
        if got.dropped:
            continue                                      # the root moved: left `planned`
        for doc, keys in entries:
            if doc.doc.key in got.vectors:
                outcome = "built"
            elif got.error and doc.doc.text in got.tried:
                outcome = f"failed:{got.error}"
            else:
                outcome = "lazy"
            out.outcomes.extend((rel, kind, outcome) for rel, kind in keys)
        if got.error and got.error != "bad_response":
            out.failure = got.error
    return out
