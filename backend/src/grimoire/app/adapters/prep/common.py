"""What the Prep ops share (115): reading the campaign, the chat and the view from a request.

A chat named by a Prep op must be of the campaign the op names, so an address that mixes two campaigns is refused
rather than read as one of them. ``audience`` is ``table`` unless it says ``director``.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.core.identity import ChatRef, ScopeRef, validate_segment
from grimoire.textapi import ApiRefused

AUDIENCES = ("table", "director")


def audience_of(args: Mapping[str, object]) -> str:
    value = args.get("audience")
    if value is None:
        return "table"
    if value not in AUDIENCES:
        raise ApiRefused("invalid_field", "audience must be table or director")
    return value  # type: ignore[return-value]


def director(args: Mapping[str, object], what: str) -> None:
    """Refuse ``director_only`` unless the request is the director's view."""
    if audience_of(args) != "director":
        raise ApiRefused("director_only", f"{what} is the director's")


def campaign_of(args: Mapping[str, object]) -> ScopeRef:
    value = args.get("campaign")
    if type(value) is not str:
        raise ApiRefused("invalid_field", "campaign must be a campaign id")
    try:
        validate_segment(value, "campaign")
    except ValueError:
        raise ApiRefused("invalid_field", "campaign is not a valid campaign id") from None
    return ScopeRef(value)


def chat_of(args: Mapping[str, object], campaign: ScopeRef | None = None) -> ChatRef:
    value = args.get("chat")
    if type(value) is not str:
        raise ApiRefused("invalid_field", "chat must be a chat reference")
    try:
        chat = ChatRef.parse(value)
    except (ValueError, TypeError):
        raise ApiRefused("invalid_field", "chat is not a chat reference") from None
    if campaign is not None and chat.campaign_id != campaign.campaign_id:
        raise ApiRefused("invalid_field", "that chat is not of this campaign")
    return chat


def segment(args: Mapping[str, object], key: str) -> str:
    value = args.get(key)
    if type(value) is not str:
        raise ApiRefused("invalid_field", f"{key} must be an id")
    try:
        validate_segment(value, key)
    except ValueError:
        raise ApiRefused("invalid_field", f"{key} is not a valid id: lower case letters, digits, '.', '_' and '-'") from None
    return value


__all__ = ["AUDIENCES", "audience_of", "campaign_of", "chat_of", "director", "segment"]
