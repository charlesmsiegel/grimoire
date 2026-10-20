"""ledger: knowledge responsibilities."""

from __future__ import annotations

from typing import NamedTuple

from fastapi import HTTPException

from ... import store
from ...store.scenes import identity as scenes_identity
from ..models import KnowledgeEdit, KnowledgePost, KnowledgeSave
from . import _knowledge_sources, _policy
from ._router import router as router


@router.get("/campaigns/{cid}/ledger/knowledge/scenes/{sid}")
def get_knowledge_scene(cid: str, sid: str):
    """What a statement about `sid` could name, as an actor's prompt would
    carry it: each keyed post with the fingerprint a grant sends back as
    `expect` (`shown` is the digest of the text listed here), every post a
    whole-scene grant would also expose -- keyless ones included -- with the
    title and chronicle summary and line, and the whole scene's `digest` and
    `shown`. Writes nothing."""
    _policy._campaign_or_404(cid)
    read = _knowledge_sources._scene_posts(cid, sid)
    posts, keyless = [], []
    for i, (m, view) in enumerate(zip(read.messages, read.shown, strict=True)):
        if store.scenes.serialize.is_director_note(m):
            continue
        text, hidden = str(view.get("content") or ""), store.scenes.serialize.is_excluded(m)
        fp = store.knowledge.shown_fingerprint(m, view, read.variants)
        row = {"index": i, "speaker": str(m.get("speaker") or ""), "text": text}
        if fp["key"]:
            posts.append({**fp, **row, "hidden": hidden})
        elif not hidden:
            keyless.append(row)
    return {"sid": sid, "digest": read.digest, "shown": _knowledge_sources._whole_shown(read),
            "title": read.title, "summary": str(read.record.get("summary") or ""),
            "one_line": str(read.record.get("one_line") or ""),
            "keyless": len(keyless), "keyless_posts": keyless, "posts": posts}


class _SceneView(NamedTuple):
    """One read of a scene for the list: its id, title, prompt-view messages,
    current fingerprints and what a whole-scene grant would expose."""
    sid: str
    title: str
    shown: list[dict]
    facts: store.knowledge.SceneFacts
    whole_shown: str


def _scene_view(cid: str, identity: str, ledger) -> _SceneView | str | None:
    """The scene a statement is about, None when it was deleted, or
    ``"unverifiable"`` when it could not be read just now (a synced folder
    mid-write, a damaged response ledger) -- which marks that one entry rather
    than failing the list. `ledger` is `responses.variants_by_scene`, read
    once per request, or an exception when that read failed."""
    try:
        sid = scenes_identity.find_by_identity(cid, identity)
    except scenes_identity.UnreadableError:
        return "unverifiable"
    if sid is None:
        return None
    if isinstance(ledger, Exception):
        return "unverifiable"
    try:
        read = _knowledge_sources._scene_posts(cid, sid, variants=ledger.get(identity, {}))
    except HTTPException:
        return "unverifiable"
    # The posts from `scene_facts`, the whole-scene digest from the same read
    # a subject is fingerprinted with (title and chronicle included).
    posts = store.knowledge_scene.scene_facts(read.messages, read.variants).posts
    return _SceneView(sid, read.title, read.shown,
                      store.knowledge.SceneFacts(posts=posts, digest=read.digest),
                      _knowledge_sources._whole_shown(read))


def _listed_posts(entry: dict, scene: _SceneView | None) -> list[dict]:
    """Each named post as the scene shows it now, with the fingerprint of that
    text -- what a re-stamp sends back as `expect`."""
    if entry["subject"]["kind"] != "posts":
        return []
    rows = []
    for p in entry["subject"]["posts"]:
        row = {"key": p["key"], "part": p["part"], "index": None,
               "speaker": "", "text": "", "missing": True}
        for i, m in enumerate(scene.shown if scene else ()):
            if (store.knowledge.post_key(m) == p["key"]
                    and store.knowledge.post_part(m) == p["part"]):
                variant, digest, _hidden = scene.facts.posts.get(  # type: ignore[union-attr]
                    (p["key"], p["part"]), ("", "", False))
                text = str(m.get("content") or "")
                row.update(index=i, speaker=str(m.get("speaker") or ""), text=text,
                           missing=False, variant=variant, digest=digest,
                           shown=store.knowledge.digest(text))
                break
        rows.append(row)
    return rows


def _stale_reason(entry: dict, held, posts: list[dict], now: str | None) -> str | None:
    """Why a statement no longer applies, or None. Structural loss applies to
    every statement; a fingerprint or a renamed audience only stops a grant,
    since an `unaware` never consults either."""
    if held == "unverifiable":
        # Not "gone": the scene could not be read just now, and the statement
        # may well be fine.
        return "unverifiable"
    if not isinstance(held, _SceneView):
        return "gone"
    # A grant needs every post it names; an `unaware` withholds whichever of
    # its posts remain, so it is gone only when none does.
    lost = (all(p["missing"] for p in posts) if entry["status"] == store.knowledge.UNAWARE
            else any(p["missing"] for p in posts))
    if posts and lost:
        return "gone"
    if entry["status"] not in store.knowledge.GRANTS:
        return None
    if entry["audience"] != store.knowledge.EVERYONE and now != entry["audience_name"]:
        return "audience"
    if store.knowledge.holds(entry, held.facts):
        return None
    hidden = entry["subject"]["kind"] == "posts" and any(
        (held.facts.posts.get((p["key"], p["part"])) or ("", "", False))[2]
        for p in entry["subject"]["posts"])
    return "hidden" if hidden else "changed"


@router.get("/campaigns/{cid}/ledger/knowledge")
def get_knowledge(cid: str):
    """Every statement, with what the ledger needs to show it: the scene it is
    about (null when deleted; `unverifiable` when the scan could not finish),
    the current text of each named post (prompt view), and why a grant no
    longer grants -- `stale` with `stale_reason` `changed`, `hidden`, `gone`
    (its scene or post was deleted), `unverifiable` (its scene could not be
    read just now) or `audience` (the audience was renamed or no longer
    resolves, §7.3/§7.5)."""
    _policy._campaign_or_404(cid)
    entries, intact = store.knowledge.read_checked(cid)
    who = store.knowledge_scene.audiences_for(cid)
    try:   # one parse of the campaign-wide ledger for every scene below
        ledger: dict | Exception = store.responses.variants_by_scene(cid)
    except (ValueError, OSError) as exc:
        ledger = exc
    scenes: dict[str, _SceneView | str | None] = {}
    out = []
    for kid, entry in sorted(entries.items(), key=lambda kv: kv[1].get("created", ""),
                             reverse=True):
        identity = entry["subject"]["scene"]
        if identity not in scenes:
            scenes[identity] = _scene_view(cid, identity, ledger)
        held = scenes[identity]
        scene = held if isinstance(held, _SceneView) else None
        posts = _listed_posts(entry, scene)
        now = _knowledge_sources._audience_now(who, entry["audience"])
        reason = _stale_reason(entry, held, posts, now)
        out.append({
            "id": kid, **entry,
            "scene": ({"sid": scene.sid, "title": scene.title, "digest": scene.facts.digest,
                       "shown": scene.whole_shown} if scene else None),
            "unverifiable": held == "unverifiable",
            "posts": posts,
            "stale": reason is not None, "stale_reason": reason,
            "audience_now": None if entry["audience"] == store.knowledge.EVERYONE else now,
        })
    return {"entries": out, "intact": intact}


@router.post("/campaigns/{cid}/ledger/knowledge")
def post_knowledge(cid: str, body: KnowledgeSave):
    """Write a statement by hand, or accept one from a capture
    (`source: "accepted"`). Both are authoritative (spec 11 §7.1)."""
    _policy._campaign_or_404(cid)
    if body.status not in store.knowledge.STATUSES:
        raise _knowledge_sources._bad("bad_status", "a status is knows, suspects or unaware")
    if body.source not in store.knowledge.SOURCES:
        raise _knowledge_sources._bad("bad_source", "a source is manual or accepted")
    with store.locks.campaign_lock(cid):
        try:
            # First: a ledger that cannot be written must refuse before the
            # subject below gives a legacy scene its identity.
            kid = store.knowledge.mint_id(cid)
        except store.knowledge.KnowledgeError as exc:
            raise _knowledge_sources._malformed() from exc
        name = _knowledge_sources._knowledge_audience(cid, body.audience, body.audience_name or None)
        subject = _knowledge_sources._knowledge_subject(cid, body.scene, body.posts, body.whole_scene, body.status,
                                     body.expect, body.expect_digest, body.expect_shown)
        entry = {"audience": body.audience, "audience_name": name, "status": body.status,
                 "subject": subject, "note": body.note, "source": body.source}
        _knowledge_sources._knowledge_write(cid, kid, entry, lambda: store.knowledge.add(cid, entry, kid=kid))
    return {"id": kid}


def _edited(cid: str, stored: dict, body: KnowledgeEdit) -> tuple[dict, bool]:
    """`stored` with the body's status, audience and note applied, and whether
    a new audience makes it a new statement for recency."""
    entry = dict(stored)
    if body.status is not None:
        if body.status not in store.knowledge.STATUSES:
            raise _knowledge_sources._bad("bad_status", "a status is knows, suspects or unaware")
        entry["status"] = body.status
    renew = False
    if body.audience is not None:
        entry["audience"] = body.audience
        entry["audience_name"] = _knowledge_sources._knowledge_audience(cid, body.audience, body.audience_name)
        renew = body.audience != stored["audience"]
    if body.note is not None:
        entry["note"] = body.note
    return entry, renew


def _restamped(cid: str, subject: dict, status: str, body: KnowledgeEdit) -> dict:
    """The stored subject's posts fingerprinted again, against the text the
    client was shown (`expect`): the ledger's "confirm against current text"."""
    try:
        sid = scenes_identity.find_by_identity(cid, subject["scene"])
    except scenes_identity.UnreadableError as exc:
        raise _knowledge_sources._busy() from exc
    if sid is None:
        raise _knowledge_sources._bad("gone", "the scene this is about was deleted", 409)
    posts = (None if subject["kind"] == "scene" else
             [KnowledgePost(key=p["key"], part=p["part"]) for p in subject["posts"]])
    return _knowledge_sources._knowledge_subject(cid, sid, posts, posts is None, status, body.expect,
                              body.expect_digest, body.expect_shown, gone_is=409)


@router.put("/campaigns/{cid}/ledger/knowledge/{kid}")
def put_knowledge(cid: str, kid: str, body: KnowledgeEdit):
    """Correct a statement. Only what the body names moves: the audience (its
    current name is recorded again), the status, the note, a new subject, or
    `restamp`. Without a new subject or a restamp the stored fingerprint is
    kept, so a note edit never confirms a stale grant -- and an `unaware`
    cannot become a grant that way at all, since a keyless part's `unaware`
    is stored about the whole scene and flipping it would be the whole-scene
    grant `unkeyed` refuses. A new audience or subject is a new statement for
    recency (§7.2), so it is dated now."""
    _policy._campaign_or_404(cid)
    with store.locks.campaign_lock(cid):
        try:
            stored = store.knowledge.get_strict(cid, kid)
        except store.knowledge.KnowledgeError as exc:
            raise _knowledge_sources._malformed() from exc
        if stored is None:
            raise HTTPException(status_code=404, detail="statement not found")
        entry, renew = _edited(cid, stored, body)
        grant = entry["status"] in store.knowledge.GRANTS
        if body.scene is not None:
            entry["subject"] = _knowledge_sources._knowledge_subject(cid, body.scene, body.posts, body.whole_scene,
                                                  entry["status"], body.expect, body.expect_digest,
                                                  body.expect_shown)
            renew = True
        elif grant and stored["status"] == store.knowledge.UNAWARE:
            # Neither a plain edit nor a restamp chooses text: an `unaware`
            # stored whole-scene for a keyless part would come out as the
            # whole-scene grant `unkeyed` refuses.
            raise _knowledge_sources._bad("bad_subject", "name the posts again to turn this into knowing or "
                                      "suspecting it")
        elif body.restamp:
            entry["subject"] = _restamped(cid, stored["subject"], entry["status"], body)
        if entry == stored and not renew:
            return {"ok": True}   # nothing moved: no write, no journal row, no stamp
        _knowledge_sources._knowledge_write(cid, kid, entry,
                         lambda: store.knowledge.update(cid, kid, entry, renew=renew))
    return {"ok": True}


@router.delete("/campaigns/{cid}/ledger/knowledge/{kid}")
def delete_knowledge(cid: str, kid: str):
    _policy._campaign_or_404(cid)
    with store.locks.campaign_lock(cid):
        try:
            stored = store.knowledge.get_strict(cid, kid)
        except store.knowledge.KnowledgeError as exc:
            raise _knowledge_sources._malformed() from exc
        if stored is None:
            raise HTTPException(status_code=404, detail="statement not found")
        _knowledge_sources._knowledge_write(cid, kid, stored, lambda: store.knowledge.remove(cid, kid))
    return {"ok": True}
