"""Network-free file stages and the async history entry (09-S1/S3).

08 alone reads the current SearchDocuments. Eligibility is evaluated once before
any scoring; admitted lexical candidates are never truncated to a future
semantic pool. Only selected usable scenes pay for prompt transcript expansion.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import replace
from functools import partial

import anyio

from .. import chronicle, entities, overlay, tokens
from ..scenes import read as scenes_read
from ..searchdocs import live
from . import budget, coverage, expand, lexical, model, refs, settings, structural
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


def retrieve_preview(
    cid: str,
    sid: str,
    query: model.Query,
    ceiling: int,
    *,
    summary_omissions: Mapping[str, str] | None = None,
) -> model.Evidence:
    validate(query)
    cfg = settings.read()
    cap = max(ceiling, 0)
    if cfg is None:
        return model.Evidence(ceiling=cap)
    depth = min(cfg.depth, query.max_scenes) if query.max_scenes is not None else cfg.depth
    if not depth or not cap:
        return model.Evidence(ceiling=cap)
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
    ordered = merge_mod.order(candidates)
    try:
        chron = chronicle.read_chronicle(cid)
        chron = chron if isinstance(chron, dict) else {}
    except Exception:  # noqa: BLE001 -- excerpts can still be evidence
        chron = {}
    by_sid = {d.sid: d for d in docs}
    omissions = summary_omissions or {}
    selected = merge_mod.select(
        ordered,
        depth,
        lambda c: _item(
            cid,
            c,
            by_sid[c.scene.sid],
            query,
            chron,
            cat,
            max(budget.MIN_ITEM_TOKENS, cap // depth) * 4,
            omissions.get(c.scene.sid, ""),
        ),
    )
    fitted = budget.fit(selected, cap, tokens.count_tokens)
    cv = coverage.verdict(ordered, depth, len(docs))
    digest = hashlib.sha256(
        json.dumps(
            [query.texts, query.terms, query.subjects, sorted(query.scopes), query.perspective],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return model.Evidence(tuple(fitted), tuple(ordered), cv, cap, digest)


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
) -> model.Evidence:
    """Complete file stages outside campaign holds (09-S3).

    The monotonic deadline reserves the later semantic/rerank phase. File reads
    cannot be interrupted midway and their evidence survives an elapsed deadline;
    S4/S5 must check the remaining time before starting any network work. Clients
    are supplied by the app, never constructed here. Preview and turns share the
    same eligibility, selection, expansion and omission-before-depth rules.
    """
    validate(query)
    if query.tier_limit == 3:
        if origin == "turn":
            raise ValueError("tier 3 history is never available on a turn")
        raise NotImplementedError("explicit source history belongs to 09-S6")
    evidence = await anyio.to_thread.run_sync(partial(
        retrieve_preview, cid, sid, query, ceiling,
        summary_omissions=summary_omissions,
    ))
    semantic = "skipped:deadline" if time.monotonic() >= deadline else "off"
    return replace(evidence, coverage=replace(evidence.coverage, semantic=semantic))
