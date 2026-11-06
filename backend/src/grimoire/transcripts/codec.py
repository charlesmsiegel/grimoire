"""``grimoire.transcript/0`` payloads: a page of posts as stored JSON, and back. Pure.

The envelope (``grimoire.scope-record/1``) owns the page's address and schema id, so a payload that
repeats them is refused (``identity_in_payload``). Unknown fields inside a post, speaker or variant are
refused with a reason; unknown top-level fields are kept in ``extras``. An actor of another campaign is
refused (``foreign_actor``): a page names only its own chat's individuals.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.core.identity import InstanceRef, TranscriptRef
from grimoire.core.json_value import thaw

from .model import (
    TRANSCRIPT_SCHEMA, Post, Speaker, TranscriptError, TranscriptPage, Variant,
)

IDENTITY_KEYS = frozenset({"schema", "address", "campaign_id", "chat_id", "ref", "seq"})
PAGE_FIELDS = frozenset({"origin", "header", "posts"})
POST_FIELDS = frozenset({"id", "speaker", "variants", "active", "hidden", "audience", "source", "extras"})
SPEAKER_FIELDS = frozenset({"role", "actor", "label"})
VARIANT_FIELDS = frozenset({"text", "at", "source", "extras"})


def _object(value: object, label: str, fields: frozenset[str], required: frozenset[str]) -> Mapping:
    if not isinstance(value, Mapping):
        raise TranscriptError(f"{label} must be a JSON object")
    unknown = sorted(str(key) for key in value if key not in fields)
    if unknown:
        raise TranscriptError(f"{label} has unknown fields {unknown}", "unknown_field")
    missing = sorted(required - set(value))
    if missing:
        raise TranscriptError(f"{label} is missing {missing}", "missing_field")
    return value


def _list(value: object, label: str) -> list:
    if not isinstance(value, (list, tuple)):
        raise TranscriptError(f"{label} must be a list")
    return list(value)


def _speaker(value: object, ref: TranscriptRef, label: str) -> Speaker:
    data = _object(value, label, SPEAKER_FIELDS, frozenset({"role"}))
    actor = data.get("actor")
    if actor is not None:
        try:
            actor = InstanceRef.parse(actor)
        except ValueError:
            raise TranscriptError(f"{label}.actor is not an instance reference") from None
        if actor.campaign_id != ref.campaign_id:
            raise TranscriptError(f"{label}.actor belongs to another campaign", "foreign_actor")
    return Speaker(data["role"], actor, data.get("label"))


def _variant(value: object, label: str) -> Variant:
    data = _object(value, label, VARIANT_FIELDS, frozenset({"text"}))
    return Variant(data["text"], data.get("at"), data.get("source", {}), data.get("extras", {}))


def _post(value: object, ref: TranscriptRef, label: str) -> Post:
    data = _object(value, label, POST_FIELDS, frozenset({"id", "speaker", "variants", "active"}))
    variants = tuple(_variant(v, f"{label}.variants[{i}]") for i, v in enumerate(_list(data["variants"],
                                                                                        f"{label}.variants")))
    return Post(data["id"], _speaker(data["speaker"], ref, f"{label}.speaker"), variants, data["active"],
                data.get("hidden", False), data.get("audience", "unknown"), data.get("source", {}),
                data.get("extras", {}))


def page_from_payload(ref: TranscriptRef, schema: object, payload: object) -> TranscriptPage:
    """Decode one stored page; ``TranscriptError`` (``unsupported_schema``, ``identity_in_payload`` ...)."""
    if schema != TRANSCRIPT_SCHEMA:
        raise TranscriptError(f"unsupported transcript schema {schema!r}", "unsupported_schema")
    if not isinstance(payload, Mapping):
        raise TranscriptError("a transcript page payload must be a JSON object")
    clash = sorted(str(key) for key in payload if key in IDENTITY_KEYS)
    if clash:
        raise TranscriptError(f"the envelope owns {clash}", "identity_in_payload")
    if "posts" not in payload:
        raise TranscriptError("a transcript page needs posts", "missing_field")
    posts = tuple(_post(p, ref, f"posts[{i}]") for i, p in enumerate(_list(payload["posts"], "posts")))
    extras = {key: value for key, value in payload.items() if key not in PAGE_FIELDS}
    return TranscriptPage(ref, posts, payload.get("origin", {}), payload.get("header"), extras)


def _variant_payload(variant: Variant) -> dict:
    out: dict = {"text": variant.text}
    if variant.at is not None:
        out["at"] = variant.at
    if variant.source:
        out["source"] = thaw(variant.source)
    if variant.extras:
        out["extras"] = thaw(variant.extras)
    return out


def _post_payload(post: Post) -> dict:
    speaker: dict = {"role": post.speaker.role}
    if post.speaker.actor is not None:
        speaker["actor"] = str(post.speaker.actor)
    if post.speaker.label is not None:
        speaker["label"] = post.speaker.label
    out: dict = {"id": post.post_id, "speaker": speaker, "variants": [_variant_payload(v) for v in post.variants],
                 "active": post.active, "audience": post.audience}
    if post.hidden:
        out["hidden"] = True
    if post.source:
        out["source"] = thaw(post.source)
    if post.extras:
        out["extras"] = thaw(post.extras)
    return out


def page_to_payload(page: TranscriptPage) -> dict:
    """The JSON a page is stored as (the envelope adds its address and ``TRANSCRIPT_SCHEMA``)."""
    for post in page.posts:
        if post.speaker.actor is not None and post.speaker.actor.campaign_id != page.ref.campaign_id:
            raise TranscriptError(f"{post.post_id}: the actor belongs to another campaign", "foreign_actor")
    clash = sorted(set(page.extras) & (IDENTITY_KEYS | PAGE_FIELDS))
    if clash:
        raise TranscriptError(f"page extras may not use {clash}", "identity_in_payload")
    out: dict = dict(thaw(page.extras))  # type: ignore[arg-type]
    out["posts"] = [_post_payload(p) for p in page.posts]
    if page.origin:
        out["origin"] = thaw(page.origin)
    if page.header is not None:
        out["header"] = thaw(page.header)
    return out
