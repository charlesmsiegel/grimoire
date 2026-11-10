"""Sprint 113's adapter ops: campaign situation, authority and canon. Called as ``campaign.<op>``.

Only Sprint 113 edits this module (or a ``campaign`` subpackage that replaces it, keeping ``ADAPTER``). Each op is
``handler(ctx, args) -> dict`` over an existing domain service; see ``grimoire.app.adapter`` and docs/APP_SHELL.md.

Every op but ``chats`` is ``grimoire.campaignapi.CampaignApi``'s op of the same name (docs/CAMPAIGN_HUB.md),
over the app's open library, acting as its owner with the app's own trusted handle (``ctx.handle``). The hub
keeps no record: its writes go through 035's runner to 029, 040 and 021. 029 and 040 record their adjudication
under the app's own adjudication key (``app:adjudicator``, never the owner's), which the hub's 029/040 libraries
take from the library's authority as 035 requires; that key never writes anything but adjudication and obstacles.

``chats`` lists the library's chats by campaign (from the core ``chats`` op), so the hub page can be reached
from a campaign id (the ``campaign`` route, ``/<campaign id>``).
"""

from __future__ import annotations

from collections.abc import Mapping
from weakref import WeakKeyDictionary

from grimoire.app.adapter import Adapter, AdapterContext
from grimoire.campaignapi import CampaignApi
from grimoire.core.identity import ChatRef, validate_segment
from grimoire.textapi import ApiRefused

#: The hub's ops, served as ``campaign.<op>`` and passed on unchanged.
HUB_OPS = ("hub", "knowledge", "label", "confirm", "decide", "propose_effect", "endorse_effect", "approve_effect",
           "recover_effects")
#: The most chats one ``chats`` answer lists (the core ``chats`` op's own largest page).
MAX_CHATS = 1000

_HUBS: WeakKeyDictionary = WeakKeyDictionary()


def hub_of(ctx: AdapterContext) -> CampaignApi:
    """The one campaign hub over this app's open library (built on first use, then kept with its text API)."""
    hub = _HUBS.get(ctx.api)
    if hub is None:
        hub = _HUBS[ctx.api] = CampaignApi(ctx.store, ctx.authority, owner=ctx.owner, handle=ctx.handle)
    return hub


def _op(name: str):  # type: ignore[no-untyped-def]
    def handler(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
        return hub_of(ctx).call(name, args)

    handler.__name__ = f"campaign_{name}"
    return handler


def _chats(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The library's recent chats grouped by campaign; with ``campaign``, that campaign's alone."""
    wanted = args.get("campaign")
    if wanted is not None:
        if type(wanted) is not str:
            raise ApiRefused("invalid_field", "campaign must be a campaign id")
        try:
            validate_segment(wanted, "campaign")
        except ValueError:
            raise ApiRefused("invalid_field", "campaign is not a valid campaign id") from None
    listed = ctx.core("chats", {"filter": "recent", "limit": MAX_CHATS})
    campaigns: dict[str, list[dict]] = {}
    for chat in listed["chats"]:
        try:
            campaign = ChatRef.parse(chat["chat"]).campaign_id
        except (ValueError, TypeError, KeyError):
            continue
        if wanted is not None and campaign != wanted:
            continue
        campaigns.setdefault(campaign, []).append({"chat": chat["chat"], "label": chat.get("label"),
                                                    "name": (chat.get("launch") or {}).get("name"),
                                                    "problem": chat.get("problem")})
    return {"campaigns": [{"campaign": name, "chats": found} for name, found in sorted(campaigns.items())],
            "complete": len(listed["chats"]) < MAX_CHATS}


ADAPTER = Adapter("campaign", owner="113", ops={"chats": _chats, **{name: _op(name) for name in HUB_OPS}})

__all__ = ["ADAPTER", "HUB_OPS", "hub_of"]
