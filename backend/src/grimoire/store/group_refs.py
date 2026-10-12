"""The group's raw actor refs are the authority for membership (spec 07).

Readers derive ordered, distinct actor refs; writers change the raw token list
so an unrelated edit cannot erase legacy text. Inherited views filter detached
actors, while an owned group names campaign actors as authored. This leaf has no
I/O, so copy guards and the future inverse can share identity rules without an
import cycle or a second membership store.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel

from . import entity_schema

ACTOR_KINDS = ("characters", "pcs")


class GroupMembershipContext(BaseModel):
    """One effective group plus diagnostics for an explicit first-copy review.

    Diagnostics are separate from frontmatter: a campaign's own copy needs none,
    and changing whether a World ref is unresolved never rewrites the World.
    Plain fields keep the Android Pydantic 1 and desktop Pydantic 2 contract.
    """

    layer: Literal["world", "campaign"]
    members: tuple[str, ...]
    leader: str | None
    filtered_members: tuple[str, ...]
    filtered_leader: str | None
    unresolved_members: tuple[str, ...]
    unresolved_leader: str | None
    # Every retained raw Leader token needing review, in raw order. Display
    # names one Leader (`unresolved_leader`, kept for older readers), but the
    # copy guard checks every valid Leader token (`group_slots`), so each one
    # it can refuse must be reportable and consentable (spec 07 7.7).
    unresolved_leaders: tuple[str, ...] = ()


class GroupIdentityConflict(RuntimeError):  # noqa: N818 - public 409 domain name specified by spec 07
    """Identity cannot be verified, or a retained foreign slot needs consent.

    RuntimeError deliberately avoids existing routes' ValueError-to-400 catches:
    this is a conflict the user can resolve, not malformed entity field syntax.
    """

    def __init__(self, message: str, *, slots: tuple[tuple[str, str], ...] = ()) -> None:
        super().__init__(message)
        self.slots = slots


def actor_ref(token: object) -> str | None:
    """A well-formed actor ref, without checking whether that actor exists."""
    if not isinstance(token, str):
        return None
    token = token.strip()
    kind, sep, aid = token.partition(":")
    if not sep or kind not in ACTOR_KINDS or not entity_schema.referenceable(aid):
        return None
    return token


def parse_members(meta: Mapping[str, object]) -> tuple[str, ...]:
    """Distinct well-formed members in first-seen order; legacy values are empty."""
    refs = (actor_ref(token) for token in entity_schema.parse_refs(meta.get("members")))
    return tuple(dict.fromkeys(ref for ref in refs if ref is not None))


def parse_leader(meta: Mapping[str, object]) -> str | None:
    """The first well-formed actor ref on a leniently read Leader line."""
    return next((ref for token in entity_schema.parse_refs(meta.get("leader"))
                 if (ref := actor_ref(token)) is not None), None)


def edited_line(raw: object, actor: str, change: Literal["join", "leave"]) -> str:
    """Change one validated actor slot while preserving unrelated raw tokens."""
    tokens = entity_schema.parse_refs(raw)
    if change == "join":
        if actor not in tokens:
            tokens.append(actor)
    else:
        tokens = [token for token in tokens if token != actor]
    return ", ".join(tokens)


def effective_refs(meta: Mapping[str, object], *, inherited: bool,
                   detached: set[str]) -> tuple[tuple[str, ...], str | None]:
    """Filter only inherited detached identities; owned refs remain authored."""
    members, leader = parse_members(meta), parse_leader(meta)
    if not inherited:
        return members, leader
    members = tuple(ref for ref in members if ref.replace(":", "/", 1) not in detached)
    if leader is not None and leader.replace(":", "/", 1) in detached:
        leader = None
    return members, leader


def leader_tokens(meta: Mapping[str, object]) -> tuple[str, ...]:
    """Every distinct well-formed Leader ref, dormant extras included, in order."""
    refs = (actor_ref(token) for token in entity_schema.parse_refs(meta.get("leader")))
    return tuple(dict.fromkeys(ref for ref in refs if ref is not None))


def group_slots(meta: Mapping[str, object]) -> frozenset[tuple[str, str]]:
    """All valid raw actor slots, including dormant/legacy multiple Leaders.

    Display chooses one Leader, but a first-copy guard must inspect every raw
    retained actor token: later clearing a token can expose the next one.
    """
    return frozenset((field, ref) for field in ("members", "leader")
                     for token in entity_schema.parse_refs(meta.get(field))
                     if (ref := actor_ref(token)) is not None)
