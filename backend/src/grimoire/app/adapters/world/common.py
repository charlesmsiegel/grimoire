"""What every World op shares: the director's audience, request fields, and each domain's refusals as reasons."""

from __future__ import annotations

import re
from collections.abc import Mapping

from grimoire.app.adapter import AdapterContext, Handler
from grimoire.calendars import CalendarError
from grimoire.calendarstore import CalendarLibrary
from grimoire.campaignapi import parse
from grimoire.chats.codec import ChatCodecError
from grimoire.core.authority import AuthorityError
from grimoire.core.identity import (
    ChatRef,
    InstanceRef,
    ScopeRef,
    SourceRef,
    validate_segment,
)
from grimoire.core.index import MissingDefinition
from grimoire.core.knowledge import KnowledgeError
from grimoire.evidencestore.library import EvidenceWriteError
from grimoire.graph.model import GraphError
from grimoire.institutions import InstitutionError
from grimoire.social_hypotheses import HypothesisError
from grimoire.store.errors import LedgerBroken, LedgerInvalid, StoreError
from grimoire.textapi import ApiRefused
from grimoire.topology import TopologyError

_REASON = re.compile(r"[a-z][a-z0-9_]{1,63}")

#: Every domain refusal a World op can meet, answered with its own machine-readable reason (never a server
#: error). ``ValueError`` last: the rumor values (063) refuse with a bare ``ValueError("invalid_time")``.
DOMAIN_ERRORS: tuple[type[Exception], ...] = (
    TopologyError, CalendarError, ChatCodecError, KnowledgeError, AuthorityError, EvidenceWriteError,
    InstitutionError, HypothesisError, GraphError, MissingDefinition)


def reason_of(error: BaseException, default: str) -> str:
    reason = getattr(error, "reason", None)
    if not (isinstance(reason, str) and _REASON.fullmatch(reason)):
        first = error.args[0] if getattr(error, "args", None) else None
        reason = first if isinstance(first, str) and _REASON.fullmatch(first) else default
    return reason


def guarded(handler: Handler, default: str) -> Handler:
    """``handler`` with every domain refusal answered as ``ApiRefused(reason)``. A stale write (``StaleWrite``) is
    left to the text API, which answers it ``changed`` as it does for every op."""

    def run(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
        try:
            return handler(ctx, args)
        except ApiRefused:
            raise
        except (LedgerInvalid, LedgerBroken) as error:
            raise ApiRefused("ledger_unreadable", f"a ledger this page reads cannot be trusted ({type(error).__name__}); "
                                                  "nothing was written") from None
        except DOMAIN_ERRORS as error:
            raise ApiRefused(reason_of(error, default), str(error) or type(error).__name__) from None
        except ValueError as error:  # a domain value's own refusal (063's rumor times, a malformed record)
            raise ApiRefused(reason_of(error, default), str(error) or type(error).__name__) from None

    run.__name__ = getattr(handler, "__name__", "world_op")
    return run


def director(args: Mapping[str, object]) -> None:
    """Everything on the World page is director-facing: the table is refused, and told whose it is."""
    audience = parse.field(args, "audience", str, required=False, default="table")
    if audience not in ("table", "director"):
        raise ApiRefused("invalid_field", "audience must be table or director")
    if audience != "director":
        raise ApiRefused("director_only", "the world's places, rumors, factions and minds are the director's")


def campaign(args: Mapping[str, object]) -> str:
    value = args.get("campaign")
    if type(value) is not str:
        raise ApiRefused("invalid_field", "campaign must be a campaign id")
    try:
        validate_segment(value, "campaign")
    except ValueError:
        raise ApiRefused("invalid_field", "campaign is not a valid campaign id") from None
    return value


def chat(args: Mapping[str, object]) -> ChatRef:
    return parse.chat_of(args)


def number(value: object, message: str) -> float:
    """A JSON number (never a boolean), as the domain checks its own range; ``invalid_field`` otherwise."""
    if type(value) is int or type(value) is float:
        return value
    raise ApiRefused("invalid_field", message)


def present(ctx: AdapterContext, campaign_id: str) -> str:
    """``campaign_id`` when its campaign exists in the library; ``missing_campaign`` otherwise."""
    if ctx.store.campaign_presence(campaign_id) != "present":
        raise ApiRefused("missing_campaign", "this library has no such campaign")
    return campaign_id


def segment(args: Mapping[str, object], key: str) -> str:
    value = args.get(key)
    if type(value) is not str:
        raise ApiRefused("invalid_field", f"{key} must be an id")
    try:
        validate_segment(value, key)
    except ValueError:
        raise ApiRefused("invalid_field", f"{key} is not a valid id") from None
    if len(value) > 80:
        raise ApiRefused("invalid_field", f"{key} is at most 80 characters")
    return value


def text(value: object, key: str, *, maximum: int = 400) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise ApiRefused("invalid_field", f"{key} must be text of at most {maximum} characters")
    return value.strip()


def pinned(value: object, key: str) -> SourceRef:
    if type(value) is not str:
        raise ApiRefused("invalid_field", f"{key} must be a reference")
    try:
        ref = SourceRef.parse(value)
    except (ValueError, TypeError):
        raise ApiRefused("invalid_field", f"{key} is not a valid reference") from None
    if not ref.pinned:
        raise ApiRefused("invalid_field", f"{key} must name an exact revision")
    return ref


def actor(value: object, key: str, campaign_id: str) -> InstanceRef:
    return parse.actor_of(value, key, campaign_id)


def actors(value: object, key: str, campaign_id: str, *, maximum: int) -> tuple[InstanceRef, ...]:
    if type(value) is not list or len(value) > maximum:
        raise ApiRefused("invalid_field", f"{key} must be a list of at most {maximum} characters")
    found = tuple(actor(item, key, campaign_id) for item in value)
    if len(set(found)) != len(found):
        raise ApiRefused("invalid_field", f"{key} names a character twice")
    return found


def expected(args: Mapping[str, object]) -> str:
    value = args.get("expected")
    if type(value) is not str or not value:
        raise ApiRefused("review_required", "read it before changing it")
    return value


def moment_view(calendar, day: int, minute: int) -> dict:  # type: ignore[no-untyped-def]
    """A point on the campaign's 055 calendar, as the Chronicle shows it (a bare day when there is no calendar)."""
    hours, minutes = divmod(minute, 60)
    date = calendar.format(day) if calendar is not None else f"day {day}"
    return {"date": date, "time": f"{hours:02d}:{minutes:02d}", "fixed_day": day, "minute": minute}


def calendar_state(ctx: AdapterContext, campaign_id: str):  # type: ignore[no-untyped-def]
    return CalendarLibrary(ctx.store, ctx.handle).read(ScopeRef(campaign_id)).state


def instance_name(ctx: AdapterContext, ref: InstanceRef, names: Mapping[InstanceRef, str]) -> str | None:
    """A campaign character's display name: the chat's (022's) where the chat has it, else its own record's."""
    if ref in names:
        return names[ref]
    try:
        envelope = ctx.store.read(ref).envelope
    except (StoreError, ValueError):
        return None
    name = getattr(envelope, "name", None)
    if isinstance(name, str) and name.strip():
        return name
    local = envelope.payload.get("local_name") if isinstance(envelope.payload, Mapping) else None
    return local if isinstance(local, str) and local.strip() else None


__all__ = ["DOMAIN_ERRORS", "actor", "actors", "calendar_state", "campaign", "chat", "director", "expected", "guarded",
           "instance_name", "moment_view", "number", "pinned", "present", "reason_of", "segment", "text"]
