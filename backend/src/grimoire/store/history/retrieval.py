"""Shared file stages and bounded async semantic history (09-S1/S3/S4).

08 alone reads the current SearchDocuments. Eligibility is evaluated once before
any scoring; admitted lexical candidates are never truncated to the bounded
semantic pool. Only selected usable scenes pay for prompt transcript expansion.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from functools import partial

import anyio

from .. import chronicle, embed_space, entities, history_rerank, overlay, tokens
from ..campaigns import paths as campaign_paths
from ..scenes import read as scenes_read
from ..searchdocs import live
from . import (
    budget,
    coverage,
    expand,
    lexical,
    model,
    refs,
    semantic,
    settings,
    structural,
    threads,
)
from . import merging as merge_mod
from . import query as query_mod

log = logging.getLogger(__name__)


def validate(query: model.Query) -> None:
    model.actor_of(query.perspective)
    if not query.scopes <= model.RELATIONS:
        raise ValueError("unknown history relation scope")
    if query.tier_limit not in (1, 2, 3):
        raise ValueError("unknown history tier")
    if query.max_scenes is not None and query.max_scenes < 0:
        raise ValueError("max_scenes must not be negative")


def _eligible(cid: str, sid: str) -> tuple[live.LiveDocument, ...]:
    try:
        rows = scenes_read.list_scenes(cid)
    except Exception:  # noqa: BLE001 -- 08's independent tolerant listing can still answer
        rows = []
    group = next((r.get("branch_group") for r in rows if r["id"] == sid), None)
    documents = live.scene_documents(cid)
    if not group:
        current = next((d for d in documents.documents if d.sid == sid), None)
        if current is not None:
            group = current.doc.meta.get("branch_group") or current.doc.meta.get("identity")
    return tuple(
        d
        for d in documents.documents
        if d.sid < sid
        and not d.closed_by
        and not (group and (d.doc.meta.get("branch_group") or d.doc.meta.get("identity")) == group)
    )


def _summary(document: live.LiveDocument, chron: dict) -> str:
    record = chron.get(document.sid)
    if isinstance(record, dict) and isinstance(record.get("summary"), str):
        return record["summary"]
    # 08 has already applied its intactness policy for a rolling summary.
    # Its labelled line is the bounded prose this document actually indexed.
    if document.doc.meta.get("summary_source") == "rolling":
        return next(
            (
                line.partition(": ")[2]
                for line in document.doc.text.splitlines()
                if line.startswith("Running summary: ")
            ),
            "",
        )
    return next(
        (
            line.partition(": ")[2]
            for line in document.doc.text.splitlines()
            if line.startswith("Summary: ")
        ),
        "",
    )


def _item(
    cid: str,
    c: model.Candidate,
    doc: live.LiveDocument,
    query: model.Query,
    chron: dict,
    cat: query_mod.Catalog,
    max_bytes: int,
    omission: str,
) -> model.EvidenceItem | None:
    excerpts = expand.excerpts(cid, c.scene, query, max_bytes)
    summary = _summary(doc, chron)
    if not excerpts and (not summary or omission):
        return None
    meta = doc.doc.meta
    location = ""
    for location_ref in reversed(meta.get("locations", [])):
        ref = refs.norm(location_ref)
        if not cat.allowed(ref):
            continue
        try:
            record = overlay.read_entity(cid, "locations", ref.partition(":")[2])
        except Exception:  # noqa: BLE001 -- an unreadable location stays unnamed
            continue
        if entities.normalize_secrecy(record["meta"].get("secrecy")) != entities.GM_ONLY:
            location = str(record["meta"].get("name") or "")
            break
    date = str((meta.get("moments") or {}).get("first") or meta.get("chronicle_date") or "")
    return model.EvidenceItem(
        c.scene,
        str(meta.get("title") or c.scene.sid),
        date,
        location,
        summary,
        excerpts,
        summary_omission=omission,
    )


@dataclass(frozen=True)
class _Selection:
    query: model.Query
    cfg: settings.Settings
    cap: int
    depth: int
    cat: query_mod.Catalog
    docs: tuple[live.LiveDocument, ...]
    ordered: tuple[model.Candidate, ...]
    sources_complete: bool = True
    source_views: Mapping[str, tuple[query_mod.Catalog, model.Query]] | None = None
    #: Structural matches that rank but do not admit (plain co-presence past
    #: STRUCTURAL_PLAIN_CAP), in structural order. Never evidence on their own,
    #: but part of the structural list the semantic pool is cut from (5.4), so
    #: semantic can admit one.
    held_back: tuple[model.Candidate, ...] = ()


def _prepare(cid, sid, query, ceiling, *, documents=None, structural_catalog=None, others=()):
    validate(query)
    cfg = settings.read()
    cap = max(ceiling, 0)
    if cfg is None:
        return None
    depth = min(cfg.depth, query.max_scenes) if query.max_scenes is not None else cfg.depth
    if not depth or not cap:
        return None
    try:
        scene = scenes_read.read_scene(cid, sid)
        posts = len(scene["messages"])
    except Exception:  # noqa: BLE001 -- passed queries may inspect another eligible boundary
        posts = 0
    cat = query_mod.scoped(query_mod.catalog(cid, sid, posts), query.perspective)
    # Directly constructed queries pass the same exclusion boundary as build:
    # a planner's subject cannot reintroduce a protected record or its terms.
    terms = cat.filter_terms(query.terms)
    query = replace(
        query,
        terms=terms,
        subjects=query_mod.subjects(cid, cat, cat.clean("\n".join(query.texts)), query.subjects),
    )
    weights = cat.weights(terms)
    docs = _eligible(cid, sid) if documents is None else documents
    structural_candidates = structural.candidates(
        cid, sid, query, docs, catalog=cat if structural_catalog is None else structural_catalog)
    by_key = {c.scene.key: c for c in structural_candidates}
    ranked = [c for c in structural_candidates if c.admitted_by]
    structural_ranks = {c.scene.key: rank for rank, c in enumerate(ranked, 1)}
    scores = lexical.score(terms, [d.doc for d in docs], weights=weights,
                           others=[d.doc for d in others])
    lexical_ranked = sorted(
        zip(docs, scores, strict=True), key=lambda pair: pair[0].sid, reverse=True
    )
    lexical_ranked.sort(key=lambda pair: pair[1].score, reverse=True)
    lexical_ranks = {
        d.sid: rank
        for rank, (d, s) in enumerate(
            ((d, s) for d, s in lexical_ranked if lexical.admits(s, weights)), 1
        )
    }
    candidates = []
    for doc, score in zip(docs, scores, strict=True):
        scene_ref = _source_ref(doc)
        previous = by_key.get(scene_ref.key)
        admits = (previous.admitted_by if previous else frozenset()) | (
            frozenset({"lexical"}) if doc.sid in lexical_ranks else frozenset()
        )
        if not admits:
            continue
        signals = model.Signals(
            structural=previous.rounds[0].structural if previous else None,
            lexical=score if score.matched else None,
        )
        ranks = {}
        if scene_ref.key in structural_ranks:
            ranks[(0, "structural")] = structural_ranks[scene_ref.key]
        if doc.sid in lexical_ranks:
            ranks[(0, "lexical")] = lexical_ranks[doc.sid]
        candidates.append(model.Candidate(scene_ref, (signals,), ranks, 1, admits))
    return _Selection(query, cfg, cap, depth, cat, docs, tuple(merge_mod.order(candidates)),
                      held_back=tuple(c for c in structural_candidates if not c.admitted_by))


def _source_ref(doc):
    return model.SceneRef(doc.sid, doc.doc.meta.get("identity") or None,
                          doc.doc.meta.get("_history_campaign", ""))


def _shared_ref(anchor, source, ref):
    """Overlay ancestry is proof; the spelling of a slug is never proof."""
    kind, _, rid = ref.partition(":")
    if kind not in overlay.INHERITED_KINDS:
        return False
    try:
        root = (overlay.actor_root if kind in {"characters", "pcs"} else overlay.entity_root)
        if root(anchor, kind, rid) == root(source, kind, rid):
            return True
        world = overlay.wroot_of(anchor)
        if world != overlay.wroot_of(source):
            return False
        return all(ref.replace(":", "/", 1) not in overlay.detached(c)
                   and overlay.world_identity_matches_owned(c, kind, rid, world)
                   for c in (anchor, source))
    except Exception:  # noqa: BLE001 -- an unproven origin cannot confer a structural reason
        return False


def _source_docs(cid, sid, sources):
    """Every requested scope's eligible documents, read before any is scored:
    tier 3's lexical corpus is all of them (6.2). A complete anchor says
    nothing about another campaign, and an unknown file stays unknown even when
    nothing was admitted."""
    found, complete = {}, True
    for source in dict.fromkeys(sources):
        try:
            documents = live.scene_documents(source)
        except campaign_paths.CampaignNotFound:
            continue
        except Exception:  # noqa: BLE001 -- named sources degrade independently
            complete = False
            continue
        complete = complete and not documents.skipped and not documents.degraded
        found[source] = _eligible(source, sid) if source == cid else tuple(
            d for d in documents.documents if not d.closed_by)
    return found, complete


def _source_catalog(cid, sid, source, anchor_cat):
    """The structural view of `source`: its own, or -- another campaign --
    only the refs overlay ancestry proves it shares with the anchor."""
    source_cat = query_mod.catalog(source, sid if source == cid else "")
    if source == cid:
        return source_cat
    shared = {r for r in anchor_cat.names if source_cat.allowed(r)
              and anchor_cat.allowed(r) and _shared_ref(cid, source, r)}
    return replace(source_cat,
        names={r: source_cat.names[r] for r in shared},
        present=anchor_cat.present.intersection(shared),
        location=anchor_cat.location if anchor_cat.location in shared else "")


def _qualify(source, docs):
    """Each document under a campaign-qualified key, and the map from its
    source-local scene key to the qualified `SceneRef`."""
    tagged_docs, qualified = [], {}
    for d in docs:
        original = d.doc.key
        tagged = replace(d, doc=replace(d.doc, key="campaign:"+source+"/"+original,
            meta={**d.doc.meta, "_history_campaign": source,
                  "_history_document_key": original}))
        tagged_docs.append(tagged)
        qualified[model.SceneRef(tagged.sid, tagged.doc.meta.get("identity") or None).key] = \
            _source_ref(tagged)
    return tagged_docs, qualified


def _prepare_sources(cid, sid, query, ceiling, sources):
    anchor_cat = query_mod.catalog(cid, sid)
    found, complete = _source_docs(cid, sid, sources)
    all_docs, candidates, held_back, views = [], [], [], {}
    base = None
    for source, docs in found.items():
        prepared = _prepare(source, sid if source == cid else "", query, ceiling,
                            documents=docs,
                            structural_catalog=_source_catalog(cid, sid, source, anchor_cat),
                            others=[d for s, ds in found.items() if s != source for d in ds])
        if prepared is None:
            return None
        base = prepared
        views[source] = (prepared.cat, prepared.query)
        tagged, qualified = _qualify(source, docs)
        all_docs.extend(tagged)
        candidates.extend(replace(c, scene=qualified[c.scene.key], tier=3)
                          for c in prepared.ordered)
        held_back.extend(replace(c, scene=qualified[c.scene.key], tier=3)
                         for c in prepared.held_back)
    if base is None:
        base = _prepare(cid, sid, query, ceiling, documents=())
    if base is None:
        return None
    # Held-back matches across every source, in structural order, newest first
    # on a tie: the structural list the semantic pool is cut from (5.4).
    held_back.sort(key=lambda c: (c.scene.campaign, c.scene.sid), reverse=True)
    held_back.sort(key=structural._rank)
    return replace(base, query=query, cat=anchor_cat, docs=tuple(all_docs),
                   ordered=tuple(merge_mod.order(_across_sources(candidates))),
                   sources_complete=complete, source_views=views, held_back=tuple(held_back))


def _across_sources(candidates):
    """Renumber each signal's ranks over every named source at once.

    Each source was ranked on its own, so every source has a rank 1 and RRF
    would read a weak match first in one campaign as the equal of a strong one
    first in another. A signal's list is rebuilt from its own raw value --
    lexical score, structural relation and seed counts -- newest first on a
    tie, as within one campaign. Every source's lexical scores were computed
    over the one corpus of all of them (`lexical.score(others=)`), so they are
    one ordering of raw values, never a fused number."""
    def lexical_key(c):
        return -c.rounds[0].lexical.score

    lists = {"lexical": lexical_key, "structural": structural._rank}
    out = list(candidates)
    for signal, key in lists.items():
        ranked = [c for c in out if signal in c.admitted_by
                  and getattr(c.rounds[0], signal) is not None]
        ranked.sort(key=lambda c: (c.scene.campaign, c.scene.sid), reverse=True)
        ranked.sort(key=key)
        rank = {c.scene.key: n for n, c in enumerate(ranked, 1)}
        out = [replace(c, ranks={**c.ranks, (0, signal): rank[c.scene.key]})
               if c.scene.key in rank else c for c in out]
    return out


def _expander(cid, prepared, summary_omissions):
    query, cap, depth, cat, docs = (prepared.query, prepared.cap, prepared.depth,
                                   prepared.cat, prepared.docs)
    sources = prepared.source_views or {cid: (cat, query)}
    chronicles = {}
    for source in sources:
        try:
            chron = chronicle.read_chronicle(source)
            chronicles[source] = chron if isinstance(chron, dict) else {}
        except Exception:  # noqa: BLE001 -- excerpts can still be evidence
            chronicles[source] = {}
    by_key = {_source_ref(d).key: d for d in docs}
    omissions = summary_omissions or {}
    def item(c):
        source = c.scene.campaign or cid
        source_cat, source_query = sources[source]
        found = _item(
            source, c, by_key[c.scene.key], source_query, chronicles[source], source_cat,
            max(budget.MIN_ITEM_TOKENS, cap // depth) * 4,
            omissions.get(c.scene.key if c.scene.campaign else c.scene.sid, ""))
        # The existing authoritative item template owns formatting; qualify its
        # title rather than creating a second source header renderer.
        return replace(found, title=f"[{source}] {found.title}") if found and c.scene.campaign else found
    return item


def expander(cid: str, sid: str, query: model.Query, ceiling: int, *,
             summary_omissions: Mapping[str, str] | None = None):
    """`merge`'s bound expansion callback for a turn's rounds (10): a scene
    the merged order selects that neither round expanded is built here into
    a complete `EvidenceItem` -- the same headers, summary and prompt
    excerpts as retrieval's own (`_expander`), anchored on `query`'s terms.

    Lazy: nothing is read until the first scene needs it, and then only the
    catalog and the eligible documents, never a second scoring pass. A scene
    no longer eligible expands to None, which `select` skips."""
    validate(query)
    built: dict[str, object] = {}

    def item(candidate: model.Candidate) -> model.EvidenceItem | None:
        if "item" not in built:
            cfg = settings.read()
            if cfg is None or candidate.scene.campaign:
                return None
            depth = (min(cfg.depth, query.max_scenes) if query.max_scenes is not None
                     else cfg.depth) or 1
            try:
                posts = len(scenes_read.read_scene(cid, sid)["messages"])
            except Exception:  # noqa: BLE001 -- as `_prepare`: another boundary may answer
                posts = 0
            cat = query_mod.catalog(cid, sid, posts)
            docs = _eligible(cid, sid)
            selection = _Selection(replace(query, terms=cat.filter_terms(query.terms)), cfg,
                                   max(ceiling, 0), depth, cat, docs, ())
            built["keys"] = {_source_ref(d).key for d in docs}
            built["item"] = _expander(cid, selection, summary_omissions)
        if candidate.scene.key not in built["keys"]:  # type: ignore[operator]
            return None
        return built["item"](candidate)  # type: ignore[operator]
    return item


def _expanded(cid, prepared, ordered, summary_omissions):
    item = _expander(cid, prepared, summary_omissions)
    return {c.scene.key: item(c) for c in ordered}


def _finish(cid, prepared, ordered, cv, summary_omissions, held=None):
    # Rerank grades the actual prompt view, so its already-expanded units must
    # survive the call unchanged. Only a later refill pays for new expansion.
    held = {} if held is None else dict(held)
    expand_item = _expander(cid, prepared, summary_omissions)

    def item(c):
        if c.scene.key not in held:
            held[c.scene.key] = expand_item(c)
        return held[c.scene.key]
    selected = merge_mod.select(
        ordered,
        prepared.depth,
        item,
    )
    fitted = budget.fit(selected, prepared.cap, tokens.count_tokens, depth=prepared.depth)
    query = prepared.query
    digest = hashlib.sha256(
        json.dumps(
            [query.texts, query.terms, query.subjects, sorted(query.scopes), query.perspective],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return model.Evidence(tuple(fitted), tuple(ordered), cv, prepared.cap, digest,
                          prepared.depth)


def retrieve_preview(cid: str, sid: str, query: model.Query, ceiling: int, *,
                     summary_omissions: Mapping[str, str] | None = None) -> model.Evidence:
    prepared = _prepare(cid, sid, query, ceiling)
    if prepared is None:
        return model.Evidence(ceiling=max(ceiling, 0))
    cv = coverage.verdict(prepared.ordered, prepared.depth, len(prepared.docs))
    cv = replace(cv, rerank="skipped:preview" if prepared.cfg.rerank else "off")
    return _finish(cid, prepared, prepared.ordered, cv, summary_omissions)


def _semantic_complete(prepared, result) -> bool:
    """Every eligible scene's document has a valid cosine -- counted per
    scene, since scenes sharing a document key share its one vector."""
    return all(d.doc.key in result.signals for d in prepared.docs)


def _semantic_candidates(prepared, result, pool):
    # A held-back structural match keeps its relations if semantic admits it;
    # `order` drops it again if nothing does.
    candidates = {c.scene.key: c for c in prepared.held_back}
    candidates.update({c.scene.key: c for c in prepared.ordered})
    admitted = sorted((d for d in prepared.docs
                       if d.doc.key in result.signals
                       and result.signals[d.doc.key].cosine >= prepared.cfg.threshold),
                      key=lambda d: d.sid, reverse=True)
    admitted.sort(key=lambda d: result.signals[d.doc.key].cosine, reverse=True)
    # A vector is shared by document text, but a rank belongs to a scene: two
    # legacy scenes can project to one document key, and each keeps its rank.
    ranks = {_source_ref(d).key: rank for rank, d in enumerate(admitted, 1)}
    for doc in prepared.docs:
        signal = result.signals.get(doc.doc.key)
        if signal is None:
            continue
        ref = _source_ref(doc)
        previous = candidates.get(ref.key)
        if previous is None and ref.key not in ranks:
            continue
        previous = previous or model.Candidate(ref, (model.Signals(),), {},
                                               3 if ref.campaign else 1 if doc.doc.key in pool else 2, frozenset())
        candidates[ref.key] = replace(
            previous, rounds=(replace(previous.rounds[0], semantic=signal),),
            ranks={**previous.ranks, **({(0, "semantic"): ranks[ref.key]}
                                       if ref.key in ranks else {})},
            admitted_by=previous.admitted_by | (frozenset({"semantic"})
                                                if ref.key in ranks else frozenset()))
    return merge_mod.order(list(candidates.values()))


def _validate_scope(query: model.Query, origin: str, source_campaigns: tuple[str, ...]) -> None:
    """09-C1's programming errors: tier 3 is never a turn's, and only tier 3
    names sources -- explicitly."""
    validate(query)
    if query.tier_limit == 3:
        if query.perspective != model.NARRATOR:
            raise ValueError("tier 3 history is the narrator's alone")
        if origin == "turn":
            raise ValueError("tier 3 history is never available on a turn")
        if not source_campaigns:
            raise ValueError("tier 3 history requires explicit source_campaigns")
    elif source_campaigns:
        raise ValueError("source_campaigns requires non-turn tier 3 history")


def _tier_one(prepared, query, space, embed_client, deadline):
    """Tier 1's semantic pool, and the bounded tier-2 lookahead its one
    request may warm. The admitted union is retained whole; only the pool is
    cut, after RRF union, so weighted-name matches cannot open every vector.
    A live set that fits the pool bound is the pool: tier 1 can already afford
    every document, so there is nothing for tier 2 to widen into."""
    by_key = {_source_ref(d).key: d for d in prepared.docs}
    if len(prepared.docs) <= semantic.POOL_MAX:
        admitted = [c.scene.key for c in prepared.ordered]
        seen = set(admitted)
        rest = sorted((k for k in by_key if k not in seen),
                      key=lambda k: by_key[k].sid, reverse=True)
        pool = tuple(by_key[k] for k in (*admitted, *rest))
    else:
        # The admitted union in RRF order, then the structural matches that
        # rank without admitting: the structural list is the whole list (5.4).
        keys = dict.fromkeys(c.scene.key for c in (*prepared.ordered, *prepared.held_back))
        pool = tuple(by_key[k] for k in list(keys)[:semantic.POOL_MAX] if k in by_key)
    pool_keys = frozenset(d.doc.key for d in pool)
    before = coverage.verdict(prepared.ordered, prepared.depth, len(prepared.docs),
                              semantic_complete=False, sources_complete=prepared.sources_complete)
    lookahead: tuple[live.LiveDocument, ...] = ()
    if (query.tier_limit >= 2 and before.verdict in {"empty", "thin"}
            and space is not None and embed_client is not None
            and not embed_space.outage(space["space"]) and time.monotonic() < deadline):
        # Prepare widening's warm before the one request, but evaluate its
        # signals only if tier 1 remains thin/empty. A sufficient
        # lexical/structural union never pays for speculative vector reads.
        # Total live documents stays WIDEN_LIMIT.
        lookahead = tuple(sorted((d for d in prepared.docs if d.doc.key not in pool_keys),
                                 key=lambda d: d.sid, reverse=True)
                          [:semantic.WIDEN_LIMIT-len(pool)])
    return pool, lookahead


async def retrieve(
    cid: str,
    sid: str,
    query: model.Query,
    *,
    ceiling: int,
    embed_client,
    deadline: float,
    rerank=None,
    origin: str = "turn",
    summary_omissions: Mapping[str, str] | None = None,
    run_id: str = "",
    source_campaigns: tuple[str, ...] = (),
) -> model.Evidence:
    """Gather independent signals outside campaign holds (09-S3/S4).

    The monotonic deadline reserves the later semantic/rerank phase. File reads
    cannot be interrupted midway and their evidence survives an elapsed deadline;
    Semantic and S5 stages check remaining time before network work. Clients
    are supplied by the app, never constructed here. Preview and turns share the
    same eligibility, selection, expansion and omission-before-depth rules.
    """
    _validate_scope(query, origin, source_campaigns)
    prepare = (partial(_prepare_sources, cid, sid, query, ceiling, source_campaigns)
               if query.tier_limit == 3 else partial(_prepare, cid, sid, query, ceiling))
    prepared = await threads.run(prepare)
    if prepared is None:
        return model.Evidence(ceiling=max(ceiling, 0))
    # The space is resolved in a worker, on history's own threads (`threads`):
    # spelled out so the embed-space guard can read it back to the role.
    space = await anyio.to_thread.run_sync(embed_space.endpoint, limiter=threads.limiter())
    if not prepared.docs:
        # A first scene has no history to score: exhausted spends nothing,
        # not even the query embedding.
        cv = coverage.verdict((), prepared.depth, 0, sources_complete=prepared.sources_complete)
        cv = replace(cv, semantic="skipped:exhausted" if space is not None else "off",
                     tiers=(3,) if source_campaigns else (1,))
        return await threads.run(partial(
            _finish, cid, prepared, prepared.ordered, cv, summary_omissions))
    pool, lookahead = _tier_one(prepared, query, space, embed_client, deadline)
    pool_keys = frozenset(d.doc.key for d in pool)
    try:
        result = await semantic.score(prepared.query.texts, (*pool, *lookahead), pool=pool,
                                      space=space, embed_client=embed_client, deadline=deadline,
                                      campaign=cid, scene=sid, post=query.turn_index,
                                      run_id=run_id)
    except Exception:  # noqa: BLE001 -- an embed failure costs semantic only (11.3), never file evidence
        # A provider failure is filed by its meter and never arrives here; this
        # is the rest (a cache write, a refused argument). Ids only: no text.
        log.warning("History semantic stage failed for campaign %s scene %s", cid, sid)
        result = semantic.SemanticResult(status="failed:error")
    ordered = _semantic_candidates(prepared, result, pool_keys)
    cv = coverage.verdict(ordered, prepared.depth, len(prepared.docs),
                          semantic_complete=result.status != "ok"
                          or _semantic_complete(prepared, result),
                          sources_complete=prepared.sources_complete)
    tiers: tuple[int, ...] = (3,) if source_campaigns else (1,)
    if query.tier_limit >= 2 and result.status == "ok" and cv.verdict in {"empty", "thin"}:
        remaining = sorted((d for d in prepared.docs if d.doc.key not in result.evaluated),
                           key=lambda d: d.sid, reverse=True)
        remaining = remaining[:max(0, semantic.WIDEN_LIMIT-len(result.evaluated))]
        if remaining and time.monotonic() < deadline:
            tiers = (3,) if source_campaigns else (1, 2)
            try:
                result = await semantic.widen(result, remaining, space=space, deadline=deadline)
            except Exception:  # noqa: BLE001 -- tier 1's scores stand; widening is optional
                log.warning("History widening failed for campaign %s scene %s", cid, sid)
                result = replace(result, status="failed:error")
            ordered = _semantic_candidates(prepared, result, pool_keys)
            cv = coverage.verdict(ordered, prepared.depth, len(prepared.docs),
                                  semantic_complete=_semantic_complete(prepared, result),
                                  sources_complete=prepared.sources_complete)
    cv = replace(cv, semantic=result.status, tiers=tiers)
    held = None
    if rerank is not None:
        held = await threads.run(partial(
            _expanded, cid, prepared, ordered[:history_rerank.MAX_RANKED], summary_omissions))
        ordered, status = await rerank(prepared.query, tuple(ordered), held)
        cv = replace(cv, rerank=status)
    return await threads.run(partial(
        _finish, cid, prepared, ordered, cv, summary_omissions, held))
