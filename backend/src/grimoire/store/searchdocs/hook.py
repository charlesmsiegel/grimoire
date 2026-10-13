"""The `searchdocs` hot rebuild for 05's sync (roadmap 08-C2c, spec section 9).

`cache_sync` owns the hook (`_SearchDocsHook`): it imports its hooks, so this
module may not import it back. The work is here, as plain functions over plain
data, and the class there folds what they return into its results -- the
overview hook's arrangement. The functions are not named `local` and `network`:
those are the protocol's, and the operation guard holds every `.network(...)`
call to the warm-up module.

**The rows name the documents.** Every `searchdocs.scene` row, and every
`vector:searchdocs.scene:<digest>` row `record_embedded` writes, sits on each
file the document read and carries `{"campaign", "identity"}`. So the documents
an edited file touches are exactly the identities on its rows, a world file
included -- its rows name each campaign whose documents read it. An identity
no live scene carries belongs to a deleted scene, or to one whose sid a new
scene took under a new identity; its rows rebuild and embed nothing.

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


def _instance(row: compiled.Materialized) -> tuple[str, str] | None:
    try:
        value = json.loads(row.instance) if row.instance else None
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    cid, identity = value.get("campaign"), value.get("identity")
    if not (isinstance(cid, str) and cid and isinstance(identity, str) and identity):
        return None
    return cid, identity


def _count(counts: dict[str, int], name: str, n: int = 1) -> None:
    counts[name] = counts.get(name, 0) + n


def rebuild(hot: Mapping[str, Sequence[compiled.Materialized]], *, space: dict | None,
            space_digest: str | None, embedding: bool, dry_run: bool,
            halted: Callable[[], object]) -> Rebuilt:
    """Rebuild the documents the hot rows name, and judge each vector row.
    No network. A dry run stores nothing and plans nothing to send."""
    out = Rebuilt()
    wanted: dict[str, dict[str, list[tuple[str, compiled.Materialized]]]] = {}
    for rel, rows in hot.items():
        for row in rows:
            named = _instance(row)
            if named is None:
                out.outcomes.append((rel, row.kind, "skipped"))
                continue
            cid, identity = named
            wanted.setdefault(cid, {}).setdefault(identity, []).append((rel, row))
    plan: dict[str, list[tuple[live.LiveDocument, list[tuple[str, str]]]]] = {}
    for cid in sorted(wanted):
        if halted():
            break
        _campaign(cid, wanted[cid], out, plan, space=space, space_digest=space_digest,
                  embedding=embedding, dry_run=dry_run)
    out.plan = plan or None
    return out


def _campaign(cid: str, by_identity: dict[str, list[tuple[str, compiled.Materialized]]],
              out: Rebuilt, plan: dict, *, space: dict | None, space_digest: str | None,
              embedding: bool, dry_run: bool) -> None:
    try:
        sids = live.identities(cid)
        only = frozenset(sids[i] for i in by_identity if i in sids)
        live_set, rendered = live.build(cid, only=only, store=not dry_run) if only \
            else (None, frozenset())
    except Exception as exc:  # noqa: BLE001 -- one campaign's failure is its rows' status
        for rows in by_identity.values():
            out.outcomes.extend((rel, row.kind, f"failed:{type(exc).__name__}") for rel, row in rows)
        return
    docs = {d.doc.meta.get("identity", ""): d for d in live_set.documents} if live_set else {}
    for identity, rows in by_identity.items():
        doc = docs.get(identity)
        for rel, row in rows:
            if doc is None:
                # A deleted scene, a recycled sid's old identity, or a head
                # that will not read: nothing of this document to rebuild.
                out.outcomes.append((rel, row.kind, "skipped"))
            elif row.kind == DOC_KIND:
                built = "built" if doc.doc.key in rendered else "hit"
                out.outcomes.append((rel, row.kind, "planned" if dry_run else built))
            else:
                _vector(cid, rel, row, doc, out, plan, space=space, space_digest=space_digest,
                        embedding=embedding, dry_run=dry_run)


def _vector(cid: str, rel: str, row: compiled.Materialized, doc: live.LiveDocument,
            out: Rebuilt, plan: dict, *, space: dict | None, space_digest: str | None,
            embedding: bool, dry_run: bool) -> None:
    """One vector row: re-embed only in the batch's space, only a text the
    cache lacks."""
    if space is None:
        _count(out.embedding, "embedding_off")
        out.outcomes.append((rel, row.kind, "skipped"))
        return
    if row.kind != VECTOR_PREFIX + (space_digest or ""):
        _count(out.embedding, "stale_space")
        out.outcomes.append((rel, row.kind, "skipped"))
        return
    if vectors.load(space["space"], [doc.doc.text]):
        if not dry_run:
            # Held already (09's turn warm, or another row): its rows move to
            # the document's current inputs.
            embedded.record_embedded(cid, [doc], space=space)
        _count(out.embedding, "held")
        out.outcomes.append((rel, row.kind, "hit"))
        return
    if not embedding:
        _count(out.embedding, "deferred")
        out.outcomes.append((rel, row.kind, "deferred"))
        return
    entries = plan.setdefault(cid, [])
    entry = next((e for e in entries if e[0].doc.key == doc.doc.key), None)
    if entry is None:
        entries.append((doc, [(rel, row.kind)]))
        if dry_run:
            _count(out.embedding, "would_send")
    else:
        entry[1].append((rel, row.kind))
    out.outcomes.append((rel, row.kind, "planned"))


def reembed(plan: Mapping[str, Sequence[tuple[live.LiveDocument, Sequence[tuple[str, str]]]]], *,
            space: dict, halted: Callable[[], object]) -> Reembedded:
    """Send the plan: one `vectors_for` a campaign, at most `REINDEX_LIMIT`
    documents. A document outside this run's window stays `lazy`. A failure
    other than `bad_response` -- the endpoint's, not one input's -- stops the
    campaigns after it, which are named with its kind."""
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
                                   client=_CLIENT, limit=REINDEX_LIMIT)
        if got.sent:
            _count(out.embedding, "requests")
            _count(out.embedding, "sent", got.sent)
        for doc, keys in entries:
            if doc.doc.key in got.vectors:
                outcome = "built"
            elif got.error:
                outcome = f"failed:{got.error}"
            else:
                outcome = "lazy"
            out.outcomes.extend((rel, kind, outcome) for rel, kind in keys)
        if got.error and got.error != "bad_response":
            out.failure = got.error
    return out
