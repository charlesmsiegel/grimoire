"""Shared file stages and bounded async semantic history (09-S1/S3/S4).

08 alone reads the current SearchDocuments. Eligibility is evaluated once before
any scoring; admitted lexical candidates are never truncated to the bounded
semantic pool. Only selected usable scenes pay for prompt transcript expansion.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from functools import partial

import anyio

from .. import chronicle, embed_space, entities, history_rerank, overlay, tokens
from ..scenes import read as scenes_read
from ..searchdocs import live
from . import budget, coverage, expand, lexical, model, refs, semantic, settings, structural
from . import merging as merge_mod
from . import query as query_mod


def validate(query: model.Query) -> None:
    if query.perspective != "narrator":
        raise ValueError("history supports the narrator perspective only")
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


def _prepare(cid, sid, query, ceiling):
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
    cat = query_mod.catalog(cid, sid, posts)
    # Directly constructed queries pass the same exclusion boundary as build:
    # a planner's subject cannot reintroduce a protected record or its terms.
    terms = cat.filter_terms(query.terms)
    query = replace(
        query,
        terms=terms,
        subjects=query_mod.subjects(cid, cat, cat.clean("\n".join(query.texts)), query.subjects),
    )
    weights = cat.weights(terms)
    docs = _eligible(cid, sid)
    structural_candidates = structural.candidates(cid, sid, query, docs, catalog=cat)
    by_key = {c.scene.key: c for c in structural_candidates}
    ranked = [c for c in structural_candidates if c.admitted_by]
    structural_ranks = {c.scene.key: rank for rank, c in enumerate(ranked, 1)}
    scores = lexical.score(terms, [d.doc for d in docs], weights=weights)
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
        scene_ref = model.SceneRef(doc.sid, doc.doc.meta.get("identity") or None)
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
    return _Selection(query, cfg, cap, depth, cat, docs, tuple(merge_mod.order(candidates)))


def _expander(cid, prepared, summary_omissions):
    query, cap, depth, cat, docs = (prepared.query, prepared.cap, prepared.depth,
                                   prepared.cat, prepared.docs)
    try:
        chron = chronicle.read_chronicle(cid)
        chron = chron if isinstance(chron, dict) else {}
    except Exception:  # noqa: BLE001 -- excerpts can still be evidence
        chron = {}
    by_sid = {d.sid: d for d in docs}
    omissions = summary_omissions or {}
    def item(c):
        return _item(
            cid, c, by_sid[c.scene.sid], query, chron, cat,
            max(budget.MIN_ITEM_TOKENS, cap // depth) * 4,
            omissions.get(c.scene.sid, ""),
        )
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
    fitted = budget.fit(selected, prepared.cap, tokens.count_tokens)
    query = prepared.query
    digest = hashlib.sha256(
        json.dumps(
            [query.texts, query.terms, query.subjects, sorted(query.scopes), query.perspective],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return model.Evidence(tuple(fitted), tuple(ordered), cv, prepared.cap, digest)


def retrieve_preview(cid: str, sid: str, query: model.Query, ceiling: int, *,
                     summary_omissions: Mapping[str, str] | None = None) -> model.Evidence:
    prepared = _prepare(cid, sid, query, ceiling)
    if prepared is None:
        return model.Evidence(ceiling=max(ceiling, 0))
    cv = coverage.verdict(prepared.ordered, prepared.depth, len(prepared.docs))
    cv = replace(cv, rerank="skipped:preview" if prepared.cfg.rerank else "off")
    return _finish(cid, prepared, prepared.ordered, cv, summary_omissions)


def _semantic_candidates(prepared, result, pool):
    candidates = {c.scene.key: c for c in prepared.ordered}
    admitted = sorted((d for d in prepared.docs
                       if d.doc.key in result.signals
                       and result.signals[d.doc.key].cosine >= prepared.cfg.threshold),
                      key=lambda d: d.sid, reverse=True)
    admitted.sort(key=lambda d: result.signals[d.doc.key].cosine, reverse=True)
    ranks = {d.doc.key: rank for rank, d in enumerate(admitted, 1)}
    for doc in prepared.docs:
        signal = result.signals.get(doc.doc.key)
        if signal is None:
            continue
        ref = model.SceneRef(doc.sid, doc.doc.meta.get("identity") or None)
        previous = candidates.get(ref.key)
        if previous is None and doc.doc.key not in ranks:
            continue
        previous = previous or model.Candidate(ref, (model.Signals(),), {},
                                               1 if doc.doc.key in pool else 2, frozenset())
        candidates[ref.key] = replace(
            previous, rounds=(replace(previous.rounds[0], semantic=signal),),
            ranks={**previous.ranks, **({(0, "semantic"): ranks[doc.doc.key]}
                                       if doc.doc.key in ranks else {})},
            admitted_by=previous.admitted_by | (frozenset({"semantic"})
                                                if doc.doc.key in ranks else frozenset()))
    return merge_mod.order(list(candidates.values()))


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
) -> model.Evidence:
    """Gather independent signals outside campaign holds (09-S3/S4).

    The monotonic deadline reserves the later semantic/rerank phase. File reads
    cannot be interrupted midway and their evidence survives an elapsed deadline;
    Semantic and S5 stages check remaining time before network work. Clients
    are supplied by the app, never constructed here. Preview and turns share the
    same eligibility, selection, expansion and omission-before-depth rules.
    """
    validate(query)
    if query.tier_limit == 3:
        if origin == "turn":
            raise ValueError("tier 3 history is never available on a turn")
        raise NotImplementedError("explicit source history belongs to 09-S6")
    prepared = await anyio.to_thread.run_sync(partial(_prepare, cid, sid, query, ceiling))
    if prepared is None:
        return model.Evidence(ceiling=max(ceiling, 0))
    space = await anyio.to_thread.run_sync(embed_space.endpoint)
    by_key = {model.SceneRef(d.sid, d.doc.meta.get("identity") or None).key: d
              for d in prepared.docs}
    # The admitted union is retained whole. Only the semantic evaluation pool
    # is cut, after RRF union, so weighted-name matches cannot open every vector.
    pool = tuple(by_key[c.scene.key] for c in prepared.ordered[:semantic.POOL_MAX])
    pool_keys = frozenset(d.doc.key for d in pool)
    before = coverage.verdict(prepared.ordered, prepared.depth, len(prepared.docs),
                              semantic_complete=False)
    lookahead = ()
    if (query.tier_limit >= 2 and before.verdict in {"empty", "thin"}
            and space is not None and embed_client is not None
            and not embed_space.outage(space["space"]) and time.monotonic() < deadline):
        lookahead = tuple(sorted((d for d in prepared.docs if d.doc.key not in pool_keys),
                                 key=lambda d: d.sid, reverse=True)
                          [:semantic.WIDEN_LIMIT-len(pool)])
    # Prepare widening's warm before the one request, but evaluate its signals
    # only if tier 1 remains thin/empty. A sufficient lexical/structural union
    # never pays for speculative vector reads. Total live documents stays 1000.
    result = await semantic.score(prepared.query.texts, (*pool, *lookahead), pool=pool, space=space,
                                  embed_client=embed_client, deadline=deadline,
                                  campaign=cid, scene=sid, post=query.turn_index, run_id=run_id)
    ordered = _semantic_candidates(prepared, result, pool_keys)
    cv = coverage.verdict(ordered, prepared.depth, len(prepared.docs),
                          semantic_complete=result.status != "ok"
                          or len(result.signals) >= len(prepared.docs))
    tiers: tuple[int, ...] = (1,)
    if query.tier_limit >= 2 and result.status == "ok" and cv.verdict in {"empty", "thin"}:
        remaining = sorted((d for d in prepared.docs if d.doc.key not in result.evaluated),
                           key=lambda d: d.sid, reverse=True)
        remaining = remaining[:max(0, semantic.WIDEN_LIMIT-len(result.evaluated))]
        if remaining and time.monotonic() < deadline:
            tiers = (1, 2)
            result = await semantic.widen(result, remaining, space=space, deadline=deadline)
            ordered = _semantic_candidates(prepared, result, pool_keys)
            cv = coverage.verdict(ordered, prepared.depth, len(prepared.docs),
                                  semantic_complete=len(result.signals) >= len(prepared.docs))
    cv = replace(cv, semantic=result.status, tiers=tiers)
    held = None
    if rerank is not None:
        held = await anyio.to_thread.run_sync(partial(
            _expanded, cid, prepared, ordered[:history_rerank.MAX_RANKED], summary_omissions))
        ordered, status = await rerank(prepared.query, tuple(ordered), held)
        cv = replace(cv, rerank=status)
    return await anyio.to_thread.run_sync(partial(
        _finish, cid, prepared, ordered, cv, summary_omissions, held))
