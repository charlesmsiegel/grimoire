"""What `store/knowledge.py` matches against, read from the store.

`knowledge` decides from arguments: a scene's current fingerprints
(`SceneFacts`) and who an audience reaches now (`Audiences`). Building either
needs a scene read, the response ledger and the group rosters, and every one
of those modules leads back to ``overlay``, which calls `knowledge`'s delete
hook -- so the reading lives here, one level up, and nothing below ``overlay``
imports it. Read-only: it writes nothing and takes no campaign lock (a caller
deciding against what it read holds the lock itself).
"""

from __future__ import annotations

import hashlib
import json

from . import chronicle, knowledge, membership, overlay, responses
from .scenes import identity as scenes_identity
from .scenes import read as scenes_read
from .scenes import serialize as scenes_serialize


def scene_digest(messages: list[dict], title: str = "", record: dict | None = None) -> str:
    """What a whole-scene statement is fingerprinted with: the transcript
    hash, and the scene's title and chronicle summary and line. A whole-scene
    grant is the one thing that makes the summary and title visible to an
    actor (spec 11 §4.3), so a later absorb, a rename or a corrected line is
    text the user never confirmed, and stops the grant like an edit does."""
    rec = record if isinstance(record, dict) else {}
    parts = [responses.transcript_hash(messages), str(title or ""),
             str(rec.get("summary") or ""), str(rec.get("one_line") or "")]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()


def whole_scene(cid: str, sid: str, meta: dict) -> tuple[str, dict | None]:
    """The title and chronicle record a scene digest covers."""
    return str(meta.get("title") or ""), chronicle.get_record(cid, sid)


def scene_facts(messages: list[dict], variants, *, title: str = "",
                record: dict | None = None) -> knowledge.SceneFacts:
    """Each keyed post's ``(variant, digest, hidden)`` and the scene digest.

    A key two posts share is `knowledge.AMBIGUOUS`, which no fingerprint
    matches. The variant is the response's ACTIVE one, which a roll
    continuation moves too: a grant on the part before a roll stops holding
    when the continuation lands, though its text did not change. That fails
    closed, and the ledger's re-stamp confirms it again.
    """
    posts: dict[tuple[str, str], tuple[str, str, bool]] = {}
    seen: set[tuple[str, str]] = set()
    for m in messages:
        fp = knowledge.fingerprint(m, variants)
        if not fp["key"]:
            continue
        slot = (fp["key"], fp["part"])
        posts[slot] = (knowledge.AMBIGUOUS if slot in seen else
                       (fp["variant"], fp["digest"], scenes_serialize.is_excluded(m)))
        seen.add(slot)
    return knowledge.SceneFacts(posts=posts, digest=scene_digest(messages, title, record))


def current(cid: str, sid: str) -> tuple[str | None, list[dict], knowledge.SceneFacts]:
    """One read of a scene for a caller that has nothing in hand: its
    identity (None for a scene with none yet), its messages and its facts."""
    token = scenes_identity.scene_identity(cid, sid)
    scene = scenes_read.read_scene(cid, sid)
    messages = scene["messages"]
    variants = responses.variants_by_response(cid, sid, token=token) if token else {}
    title, record = whole_scene(cid, sid, scene["meta"])
    return token, messages, scene_facts(messages, variants, title=title, record=record)


def audiences_for(cid: str) -> knowledge.Audiences:
    """Who each audience reaches in this campaign now: every character's
    display name, and every group's name and affiliated actors through 07's
    supported door (`membership.campaign_index` and `affiliated`, never
    `parse_members` off an entity read)."""
    index = membership.campaign_index(cid)
    names = {f"characters:{row['id']}": str(row.get("name") or "")
             for row in overlay.list_characters(cid)}
    groups = {ref: (roster.name, membership.affiliated(index, ref))
              for ref, roster in index.groups.items()}
    return knowledge.Audiences(names=names, groups=groups)
