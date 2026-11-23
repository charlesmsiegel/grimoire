"""What every Time op shares: the director's audience, request fields, campaign time as shown, names, and each
domain's refusal answered as its own reason."""

from __future__ import annotations

import re
from collections.abc import Mapping

from grimoire.app.adapter import AdapterContext, Handler
from grimoire.calendars import CalendarError, CalendarState
from grimoire.calendars import Moment as ClockMoment
from grimoire.campaignapi import parse
from grimoire.chats.codec import ChatCodecError
from grimoire.commitments import CommitmentError
from grimoire.core import DIRECTOR
from grimoire.core.authority import AuthorityError, controller_of
from grimoire.core.entity_codec import instance_from_payload
from grimoire.core.identity import (
    ChatRef,
    InstanceRef,
    ScopeRef,
    paired_instances,
    validate_segment,
)
from grimoire.downtime import DowntimeError
from grimoire.graph.model import GraphError
from grimoire.institutions import InstitutionError
from grimoire.offscreen import OffscreenError
from grimoire.store.errors import LedgerBroken, LedgerInvalid
from grimoire.textapi import ApiRefused

from ..campaign import hub_of

_REASON = re.compile(r"[a-z][a-z0-9_]{1,63}")
#: The longest span one pursuit, or one activity's required effort, may be from this page (a typing slip of extra
#: zeros is refused before the domain is asked; 055 and 074 allow more).
MAX_DAYS = 36600
MAX_MINUTES = MAX_DAYS * 1440

#: Every domain refusal a Time op can meet, answered with its own machine-readable reason, never a server error.
DOMAIN_ERRORS: tuple[type[Exception], ...] = (
    DowntimeError, OffscreenError, CommitmentError, CalendarError, ChatCodecError, InstitutionError, GraphError,
    AuthorityError)


def reason_of(error: BaseException, default: str) -> str:
    reason = getattr(error, "reason", None)
    if not (isinstance(reason, str) and _REASON.fullmatch(reason)):
        first = error.args[0] if getattr(error, "args", None) else None
        reason = first if isinstance(first, str) and _REASON.fullmatch(first) else default
    return reason


def refusal(error: BaseException, default: str = "time_refused") -> ApiRefused:
    if isinstance(error, (LedgerInvalid, LedgerBroken)):
        return ApiRefused("ledger_unreadable", f"a ledger this page reads cannot be trusted ({type(error).__name__}); "
                                               "nothing was written")
    if isinstance(error, AuthorityError) and not _REASON.fullmatch(str(getattr(error, "reason", "") or "")):
        return ApiRefused("not_authorized", str(error)[:300] or "not authorized")
    message = getattr(error, "message", None) if isinstance(error, OffscreenError) else None
    return ApiRefused(reason_of(error, default), (message or str(error) or type(error).__name__)[:300])


def guarded(handler: Handler) -> Handler:
    """``handler`` with every domain refusal answered as ``ApiRefused(reason)``. A stale write (``StaleWrite``) is
    left to the text API, which answers it ``changed`` as it does for every op."""

    def run(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
        try:
            return handler(ctx, args)
        except ApiRefused:
            raise
        except (LedgerInvalid, LedgerBroken) as error:
            raise refusal(error) from None
        except DOMAIN_ERRORS as error:
            raise refusal(error) from None

    run.__name__ = getattr(handler, "__name__", "time_op")
    return run


# -- request fields ---------------------------------------------------------------------------------------------
def director(args: Mapping[str, object], what: str = "time and commitments") -> None:
    audience = parse.field(args, "audience", str, required=False, default="table")
    if audience not in ("table", "director"):
        raise ApiRefused("invalid_field", "audience must be table or director")
    if audience != "director":
        raise ApiRefused("director_only", f"{what} are the director's")


def campaign(args: Mapping[str, object]) -> ScopeRef:
    value = args.get("campaign")
    if type(value) is not str:
        raise ApiRefused("invalid_field", "campaign must be a campaign id")
    try:
        validate_segment(value, "campaign")
    except ValueError:
        raise ApiRefused("invalid_field", "campaign is not a valid campaign id") from None
    return ScopeRef(value)


def chat(args: Mapping[str, object], scope: ScopeRef, *, required: bool = True) -> ChatRef | None:
    """The chat the request names, which must be one of the campaign's: its cast is whom the page lays out."""
    if args.get("chat") is None and not required:
        return None
    found = parse.chat_of(args)
    if found.campaign_id != scope.campaign_id:
        raise ApiRefused("invalid_field", "the chat is not one of this campaign's")
    return found


def text(args: Mapping[str, object], key: str, *, maximum: int = 400, empty: bool = False) -> str:
    value = args.get(key, "" if empty else None)
    if (type(value) is not str or len(value) > maximum or (not empty and not value.strip())
            or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
        raise ApiRefused("invalid_field", f"{key} must be one line of text of at most {maximum} characters")
    return value


def whole(args: Mapping[str, object], key: str, low: int, high: int, *, default: int | None = None) -> int:
    value = args.get(key, default)
    if type(value) is not int or not low <= value <= high:
        raise ApiRefused("invalid_field", f"{key} must be a whole number from {low} to {high}")
    return value


def segment(args: Mapping[str, object], key: str, *, maximum: int = 100, required: bool = True) -> str | None:
    value = args.get(key)
    if value is None and not required:
        return None
    if type(value) is not str or len(value) > maximum:
        raise ApiRefused("invalid_field", f"{key} must be an id of at most {maximum} characters")
    try:
        validate_segment(value, key)
    except ValueError:
        raise ApiRefused("invalid_field", f"{key} is not a valid id") from None
    return value


def expected(args: Mapping[str, object], what: str = "the campaign") -> str:
    value = args.get("expected")
    if type(value) is not str:
        raise ApiRefused("review_required", f"read {what} before changing it")
    return value


def actor(value: object, label: str, campaign_id: str) -> InstanceRef:
    return parse.actor_of(value, label, campaign_id)


# -- what is shown ----------------------------------------------------------------------------------------------
def moment(calendar: CalendarState | None, at: ClockMoment | None) -> dict | None:
    """A campaign-clock moment in the calendar's own notation (``None`` when there is none to show)."""
    if at is None:
        return None
    hours, minutes = divmod(at.minute, 60)
    date = calendar.calendar.format(at.fixed_day) if calendar is not None else None
    return {"date": date, "time": f"{hours:02d}:{minutes:02d}", "fixed_day": at.fixed_day, "minute": at.minute}


def clock_view(calendar: CalendarState | None) -> dict:
    if calendar is None:
        return {"started": False, "calendar": None, "now": None, "deadlines": []}
    deadlines = [{"deadline": item.deadline_id, "label": item.label, "kind": item.kind,
                  "due": moment(calendar, item.due), "flagged_by": item.triggered_by}
                 for item in sorted(calendar.deadlines, key=lambda item: (item.due, item.deadline_id))]
    return {"started": True, "calendar": {"provider": calendar.calendar.provider, "custom": calendar.calendar.custom_id},
            "now": moment(calendar, calendar.now), "deadlines": deadlines}


def flags(calendar: CalendarState | None, triggered) -> list[dict]:  # type: ignore[no-untyped-def]
    labels = {} if calendar is None else {item.deadline_id: item.label for item in calendar.deadlines}
    return [{"deadline": item, "label": labels.get(item)} for item in triggered]


class Names:
    """An actor's display name: the chat's cast name, else its instance's own local name. Presentation only: a
    reference is never looked up by name, and an actor whose name cannot be read shows its reference."""

    def __init__(self, ctx: AdapterContext, scene=None) -> None:  # type: ignore[no-untyped-def]
        self.store = ctx.store
        self.cast = {} if scene is None else dict(scene.names)
        self.cache: dict[InstanceRef, str | None] = {}

    def name(self, ref: InstanceRef) -> str | None:
        if self.cast.get(ref):
            return self.cast[ref]
        if ref not in self.cache:
            try:
                envelope = self.store.read(ref).envelope
                schema = envelope.extra.get("schema")
                found = instance_from_payload(ref, schema if isinstance(schema, str) else "", envelope.payload)
                self.cache[ref] = found.local_name
            except Exception:  # noqa: BLE001 -- a name is presentation only; a missing one shows the reference
                self.cache[ref] = None
        return self.cache[ref]

    def who(self, ref: InstanceRef) -> dict:
        return {"actor": str(ref), "name": self.name(ref), "kind": ref.kind}


def controls_of(scene, campaign_id: str) -> tuple:  # type: ignore[no-untyped-def]
    """The campaign's 003 control records, as the chat's scene read them (none without a chat)."""
    return () if scene is None else tuple(item for item in scene.controls if item.campaign_id == campaign_id)


def controller(campaign_id: str, ref: InstanceRef, controls) -> str | None:  # type: ignore[no-untyped-def]
    """An actor's present controller: ``director`` for none, ``None`` when 003's record is ambiguous."""
    try:
        found = controller_of(campaign_id, ref.instance_id, controls)
    except AuthorityError:
        return None
    return DIRECTOR if found is None else found


def scene_of(ctx: AdapterContext, found: ChatRef):  # type: ignore[no-untyped-def]
    return hub_of(ctx).runner.open(found)


def campaign_actors(ctx: AdapterContext, campaign_id: str) -> list[InstanceRef]:
    """The campaign's instances, each one actor (an id two kinds share is no one actor, IDENTITY.md)."""
    present = set(ctx.store.campaign_instances(campaign_id))
    return sorted(present - set(paired_instances(present)), key=str)


def section(build) -> dict:  # type: ignore[no-untyped-def]
    """One part of the overview: its answer, or the refusal that stopped it (the other parts still answer)."""
    try:
        return build()
    except ApiRefused as error:
        return {"error": {"reason": error.reason, "message": str(error)}}
    except (LedgerInvalid, LedgerBroken, *DOMAIN_ERRORS) as error:
        refused = refusal(error)
        return {"error": {"reason": refused.reason, "message": str(refused)}}
