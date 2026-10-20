"""ledger: knowledge_sources responsibilities."""

from __future__ import annotations

import json
from typing import NamedTuple

from fastapi import HTTPException

from ... import store
from ...store.regex import view as regex_view
from ...store.scenes import identity as scenes_identity
from ..models import KnowledgeFingerprint, KnowledgePost
from . import _policy
from ._router import router as router

# --------------------------------------------------------------- the knowledge
#
# Roadmap 11's overrides (`store/knowledge.py`): the user's word on who knows,
# suspects or never heard what happened in a scene, which beats every derived
# classification. So the routes hold to one rule above the others: a GRANT is
# only ever stored about text the user was shown. The server computes every
# fingerprint it stores, and a grant carries what the client displayed
# (`expect`) as a precondition -- a swipe or an edit landing between the read
# and the save is 409 `moved`, never a grant of text nobody saw. An `unaware`
# needs no such care: withholding more is the safe direction.


def _busy() -> HTTPException:
    return HTTPException(status_code=409, detail={
        "kind": "busy",
        "detail": "the campaign's scenes could not all be read just now; try again"})


def _malformed() -> HTTPException:
    return HTTPException(status_code=409, detail={
        "kind": "malformed",
        "detail": "knowledge.json cannot be read; repair it before editing statements"})


def _bad(kind: str, detail: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"kind": kind, "detail": detail})


def _knowledge_audience(cid: str, audience: str, shown: str | None = None) -> str:
    """The audience's current display name, or 400 for one that is not a
    character or group of this campaign. Read through the door the matcher
    reads (`knowledge_scene.audiences_for`), so the name stored is the name a
    grant is later checked against."""
    if audience == store.knowledge.EVERYONE:
        return ""
    if not store.knowledge.valid_audience(audience):
        raise _bad("bad_audience", "an audience is a character, a group, or everyone")
    name = _audience_now(store.knowledge_scene.audiences_for(cid), audience)
    if not name:
        raise _bad("bad_audience", "that character or group is not in this campaign")
    if shown is not None and shown != name:
        # The form named someone else: renamed, or replaced under the same id.
        raise _bad("moved", f"that audience is now called {name}; look again", 409)
    return name


def _audience_now(who, audience: str) -> str | None:
    if audience == store.knowledge.EVERYONE:
        return "Everyone"
    if audience.startswith("characters:"):
        return who.names.get(audience) or None
    return (who.groups.get(audience) or (None,))[0] or None


class _ScenePosts(NamedTuple):
    """One read of a scene: its raw messages, response variants, the
    prompt-view text (what an actor's prompt would carry, so what a grant is
    agreeing to), the digest a whole-scene statement is fingerprinted with,
    and its title and chronicle record."""
    messages: list[dict]
    variants: dict
    shown: list[dict]
    digest: str
    title: str
    record: dict


def _scene_posts(cid: str, sid: str, variants: dict | None = None) -> _ScenePosts:
    """Read a scene for a statement about it; every read failure is a 409 the
    reader can act on, never a 500. `variants` is the scene's slice of a
    ledger the caller already read (`responses.variants_by_scene`)."""
    try:
        scene = store.scenes.read_scene(cid, sid)
        token = scenes_identity.scene_identity(cid, sid)
    except store.scenes.SceneNotFound as exc:
        raise HTTPException(status_code=404, detail="scene not found") from exc
    except UnicodeDecodeError as exc:
        raise _bad("malformed", "the scene file cannot be read; repair it and try again",
                   409) from exc
    except (scenes_identity.UnreadableError, OSError) as exc:
        raise _busy() from exc
    messages = scene["messages"]
    if variants is None:
        try:
            variants = (store.responses.variants_by_response(cid, sid, token=token)
                        if token else {})
        except ValueError as exc:   # responses.json is not a response ledger
            raise _bad("malformed", "responses.json cannot be read; repair it and try again",
                       409) from exc
        except OSError as exc:
            raise _busy() from exc
    try:
        title, record = store.knowledge_scene.whole_scene(cid, sid, scene["meta"])
    except ValueError as exc:   # chronicle.json will not parse
        raise _bad("malformed", "chronicle.json cannot be read; repair it and try again",
                   409) from exc
    except OSError as exc:
        raise _busy() from exc
    return _ScenePosts(messages, variants, regex_view.view(messages, cid=cid, phase="prompt"),
                       store.knowledge_scene.scene_digest(messages, title, record),
                       title or sid, record if isinstance(record, dict) else {})


def _visible(read: _ScenePosts) -> list[tuple[int, dict, dict]]:
    """The posts a whole-scene grant would expose: every one in context."""
    return [(i, m, v) for i, (m, v) in enumerate(zip(read.messages, read.shown, strict=True))
            if not store.scenes.serialize.is_excluded(m)
            and not store.scenes.serialize.is_director_note(m)]


def _whole_shown(read: _ScenePosts) -> str:
    """The digest of everything a whole-scene grant exposes, as shown: the
    title, the chronicle summary and line, and every post in context in its
    prompt view. A whole-scene grant carries it back as `expect_shown`."""
    return store.knowledge.digest(json.dumps(
        [read.title, str(read.record.get("summary") or ""), str(read.record.get("one_line") or ""),
         [[str(m.get("speaker") or ""), str(v.get("content") or "")]
          for _i, m, v in _visible(read)]], ensure_ascii=False))


def _subject_form(posts: list[KnowledgePost] | None, whole_scene: bool,
                  grant: bool) -> list[KnowledgePost] | None:
    """The posts a request names, None for the whole scene, or a refusal.

    A post with no key cannot be named by itself: a grant about it is 400
    `unkeyed` (as a whole-scene grant it would also cover the summary, which
    the user did not judge), and an `unaware` is stored about the whole
    scene, which only withholds more (spec 11 §7.4).
    """
    if posts is not None and whole_scene:
        raise _bad("bad_subject", "name posts or the whole scene, not both")
    if posts is not None and any(not p.key for p in posts):
        if grant:
            raise _bad("unkeyed", "a post with no key can only be marked unaware; knowing "
                                  "it would have to grant the whole scene, summary included")
        return None
    if posts is None and not whole_scene:
        raise _bad("bad_subject", "name the posts, or say it is the whole scene")
    if posts is not None and not posts:
        raise _bad("bad_subject", "name at least one post, or the whole scene")
    if posts is not None and len({(p.key, p.part) for p in posts}) != len(posts):
        raise _bad("bad_subject", "name each post once")
    return posts


def _fingerprinted(read: _ScenePosts, posts: list[KnowledgePost], grant: bool,
                   expect: list[KnowledgeFingerprint] | None, gone_is: int) -> list[dict]:
    """Each named post's stored fingerprint. A grant must be about a visible
    post that still reads as the client was shown it -- its raw text and the
    prompt-view text the form displayed."""
    shown = {(e.key, e.part): (e.variant, e.digest, e.shown) for e in expect or []}
    out = []
    for p in posts:
        found = [(m, v) for m, v in zip(read.messages, read.shown, strict=True)
                 if store.knowledge.post_key(m) == p.key and store.knowledge.post_part(m) == p.part]
        if len(found) != 1:
            raise (_bad("gone", "that post is no longer in the scene", 409) if gone_is == 409
                   else _bad("unknown_post", "the scene holds no single post with that key"))
        message, view = found[0]
        fp = store.knowledge.shown_fingerprint(message, view, read.variants)
        if grant and store.scenes.serialize.is_excluded(message):
            raise _bad("hidden", "a hidden post reaches no one, so it cannot be known")
        now = (fp["variant"], fp["digest"], fp.pop("shown"))
        if grant and shown.get((p.key, p.part)) != now:
            raise _bad("moved", "that post changed since it was shown; look again", 409)
        out.append(fp)
    return out


def _knowledge_subject(cid: str, sid: str, posts: list[KnowledgePost] | None,
                       whole_scene: bool, status: str,
                       expect: list[KnowledgeFingerprint] | None, expect_digest: str,
                       expect_shown: str = "", *, gone_is: int = 400) -> dict:
    """The subject a statement about `sid` stores, fingerprinted from the scene
    as it reads under the caller's hold. Everything is validated before the
    scene is given an identity, so a refused request writes nothing."""
    grant = status in store.knowledge.GRANTS
    named = _subject_form(posts, whole_scene, grant)
    read = _scene_posts(cid, sid)
    if named is None:
        if grant and (expect_digest != read.digest or expect_shown != _whole_shown(read)):
            raise _bad("moved", "the scene changed since it was shown; look again", 409)
        subject: dict = {"kind": "scene", "digest": read.digest}
    else:
        subject = {"kind": "posts",
                   "posts": _fingerprinted(read, named, grant, expect, gone_is)}
    try:
        subject["scene"] = scenes_identity.ensure_identity(cid, sid)
    except scenes_identity.UnreadableError as exc:
        raise _busy() from exc
    return subject


def _knowledge_write(cid: str, kid: str, entry: dict, write) -> None:
    who = entry.get("audience_name") or "Everyone"
    with store.undo.journalled(cid, {"w": "knowledge", "id": kid}, kind="knowledge",
                               ref={"kind": "knowledge", "id": kid}, field="status",
                               label=_policy._label(who, "knowledge")):
        try:
            write()
        except store.knowledge.KnowledgeError as exc:
            raise _malformed() from exc
