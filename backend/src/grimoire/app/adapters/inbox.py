"""The inbox: everything that waits on the person, as one derived list. Called as ``inbox.list``.

Home, the rail, the chat tray, Devices and sync and the campaign hub all read this one op, so none of them keeps
a list of its own (docs/INBOX.md). It stores nothing: every call gathers the open items again from the services
that own them, and each item names the existing op, with the exact arguments, that decides it:

- a **fork** (035's turns, read through the text API's scene runner as ``media.forks`` reads them): ``media.choose``;
- a keep-both **conflict** (``media.conflicts``, with ``media.devices`` for which device can be dialed):
  ``media.resolve`` with the etags as read;
- an **action** whose labelling awaits the player's confirmation and a change awaiting the player's own key
  (**approval**), both from ``campaign.hub`` asked as the **table**, never as the director: ``campaign.confirm``
  and ``campaign.endorse_effect`` with the fingerprint shown;
- a **reply** that was cut off or gave no line (the open turn as the text API's table view shows it): ``generate``
  and ``pass`` naming the turn.

So deciding in the inbox is the same write, with the same refusals, as deciding where the thing lives. There is
no write here. Nothing the director alone may read is in an item: campaign items are built from the table's hub
and turn text from the text API's table view only.

The work is bounded: at most ``limit`` of the chats played last are opened, each once, and a chat's hub is read
only when its turns show a labelling or a change still waiting (``complete`` is false when there are more chats,
when a service refused, or when the list was cut at ``MAX_ITEMS``). A service that refuses is named in
``problems`` while the rest is still listed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from grimoire.app.adapter import Adapter, AdapterContext
from grimoire.chats import ChatCodecError
from grimoire.core.identity import ChatRef
from grimoire.devicetag import tag_of
from grimoire.scene import SceneRefused
from grimoire.store import StoreError
from grimoire.textapi import ApiRefused, parse_ref
from grimoire.textapi.views import fork_view, set_aside_view, turn_view

from .campaign import hub_of
from .media import media_of

#: The kinds of item, in the order the inbox lists them, each with the word the interface shows for it.
KINDS = {"fork": "Fork", "conflict": "Conflict", "action": "Action", "approval": "Your approval",
         "reply": "Cut-off reply"}
_RANK = {kind: rank for rank, kind in enumerate(KINDS)}
#: The chats one call opens when ``limit`` is not given, and the most it may be asked to open.
DEFAULT_CHATS = 24
MAX_CHATS = 100
#: The most items one answer lists.
MAX_ITEMS = 200
#: The most characters of a post shown as an item's title.
TITLE_CHARS = 140

_UNREADABLE = (SceneRefused, StoreError, ChatCodecError, OSError, ValueError)


def _clip(text: str, limit: int = TITLE_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _count(number: int, word: str) -> str:
    return f"{number} {word}{'' if number == 1 else 's'}"


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def _chat_title(entry: Mapping[str, Any]) -> str:
    """What a chat is called, as the Chats list names it: its label, else what it was started from, else its ref."""
    launch = entry.get("launch") or {}
    return entry.get("label") or launch.get("name") or ("Premise chat" if launch.get("kind") == "premise"
                                                         else entry["chat"])


def _campaign_title(entry: Mapping[str, Any]) -> str | None:
    scope = entry.get("scope") or {}
    return scope.get("title") or None


def _where(*parts: str | None) -> str:
    return " · ".join(part for part in parts if part)


def _route(route: str, path: list[str] | None = None, query: dict | None = None, label: str | None = None) -> dict:
    """Where the item happened: a page's route, its path segments and its query, as ``ctx.href`` takes them."""
    return {"route": route, "path": path or [], "query": query or {}, "label": label or "Open where it happened"}


def _action(action_id: str, label: str, op: str, args: dict, done: str, *, confirm: dict | None = None,
            kind: str = "", quick: bool = False) -> dict:
    """One way to decide an item: the existing ``op`` and its exact ``args``. ``confirm`` is what is asked first
    (the same question the op's own screen asks), ``done`` what the item reads once it is decided. ``quick``
    says the action may be offered where the item is not shown in full (a chat's tray): never for a choice
    whose versions, operations or factors must be read first."""
    return {"id": action_id, "label": label, "op": op, "args": args, "confirm": confirm, "done": done, "kind": kind,
            "quick": quick}


# -- sync: forks and conflicts ---------------------------------------------------------------------------

def fork_item(chat: str, title: str, campaign: str | None, fork: dict, set_aside: list[dict],
              numbers: Mapping[str, int], devices: Mapping[str | None, str]) -> dict:
    """A chat two devices played on: one action for each branch that can still go on. ``devices`` names the
    device behind a branch's tag (this device, or one it knows); any other is said by its tag."""
    after = numbers.get(fork["after"]) if fork["after"] is not None else None
    branches = [branch for branch in fork["branches"] if not branch["set_aside"]]
    told, actions = [], []

    def named(branch: Mapping[str, Any]) -> str:
        return devices.get(branch["device"]) or f"device {branch['device']}"

    for branch in branches:
        device = named(branch)
        told.append(f"{device} has {_count(len(branch['turns']), 'turn')}")
        actions.append(_action(
            f"choose:{branch['head']}", f"Go on with {device}'s", "media.choose", {"chat": chat, "turn": branch["head"]},
            f"Going on with the branch from {device}", quick=True,
            confirm={"message": f"Go on with the branch from {device}? The other branch's turns are set aside: "
                                "no one hears them.", "label": "Go on with it", "danger": False}))
    return {
        "id": f"fork:{chat}", "kind": "fork", "label": KINDS["fork"],
        "title": f"{title} went two ways" + (f" after turn {after}" if after is not None else ""),
        "where": {"text": _where(campaign, title), "chat": chat, "campaign": ChatRef.parse(chat).campaign_id,
                  "devices": sorted({named(branch) for branch in fork["branches"]})},
        "detail": "Both devices played on. " + _sentence("; ".join(told)) + ". The branch you do not choose is "
                  "set aside and heard by no one.",
        "notes": [f"{_count(len(set_aside), 'turn')} {'was' if len(set_aside) == 1 else 'were'} set aside by an "
                  "earlier choice."] if set_aside else [],
        "actions": actions, "route": _route("chat", [chat]),
        "fork": {"chat": chat, "fork": fork, "set_aside": set_aside},
    }


def conflict_actions(entry: Mapping[str, Any], dialed: frozenset[str]) -> tuple[list[dict], list[str]]:
    """What may be kept of one keep-both conflict, and what cannot be yet and why.

    A conflict is ended with one device at a time, and both must be reachable. With several other versions
    standing, the text API keeps this device's only on the other device, so it is not offered here; a device's
    deletion waits until every other device's version is resolved (it is refused as stale before that)."""
    mine, theirs, sides = entry["mine"], entry["theirs"], entry["peers"]
    path = entry["path"]
    etags = [item["etag"] for item in theirs]
    actions: list[dict] = []
    notes: list[str] = []
    if not sides:
        return [], ["Which device this is with cannot be read here, so it cannot be resolved yet."]

    def keep(peer: str, value: str, label: str, what: str, kind: str = "") -> dict:
        return _action(
            f"resolve:{peer}:{value}", label, "media.resolve",
            {"peer": peer, "path": path, "keep": value, "mine": mine["etag"], "theirs": etags},
            f"Kept {what}",
            confirm={"message": f"Keep {what} of {path}? The rest of this conflict with {peer} is set aside here "
                                f"and on {peer}, and listed after.", "label": "Keep it", "danger": False}, kind=kind)

    for side in sides:
        peer = side["peer"]
        if peer not in dialed:
            notes.append(f"Both devices must be reachable: give {peer}'s address under Pair a device, on Devices "
                         "and sync, to resolve it.")
            continue
        waits = side["deleted"] and len(theirs) > 0
        if not (len(theirs) > 1 or waits):
            gone = mine["deleted"]
            with_peer = f" (with {peer})" if len(sides) > 1 else ""
            actions.append(keep(peer, "mine", ("Keep this device's deletion" if gone else "Keep this device's") + with_peer,
                                "this device's " + ("deletion" if gone else "version")))
        for number, tag in enumerate(side["versions"], start=1):
            label = f"Keep {peer}'s" + (f" (version {number})" if len(side["versions"]) > 1 else "")
            actions.append(keep(peer, tag, label, f"{peer}'s version"))
        if side["deleted"] and not theirs:
            actions.append(keep(peer, "theirs", f"Accept {peer}'s deletion", f"{peer}'s deletion", kind="danger"))
        if len(theirs) > 1 and not waits:
            notes.append(f"Several other versions stand, so this device's can be kept only on {peer}. Keeping one "
                         "of theirs here lists this device's after, with the rest.")
        if side["deleted"] and theirs:
            notes.append(f"{peer} deleted it. Resolve the other devices' versions first; then its deletion can be "
                         "kept or set aside.")
    return actions, notes


def conflict_item(entry: Mapping[str, Any], dialed: frozenset[str], names: Mapping[str, str],
                  chats: Mapping[str, str], campaigns: Mapping[str, str | None]) -> dict:
    """One file a sync left in a keep-both conflict, with every version as ``media.conflicts`` shows it."""
    path, kind, campaign = entry["path"], entry["kind"], entry["campaign"]
    versions = [entry["mine"], *entry["theirs"]]
    chat = next((item["preview"]["chat"] for item in versions
                 if item.get("preview") and item["preview"].get("chat")), None)
    route = _route("sync")
    if kind == "placement":
        subject = str(entry["subject"])
        title = f"{names.get(subject, subject)}'s {entry['slot']} picture changed on more than one device"
        route = _route("art", query={"subject": subject})
    elif kind == "turn":
        number = next((item["preview"]["turn"]["number"] for item in versions
                       if item.get("preview") and item["preview"].get("turn")), None)
        where = chats.get(chat, chat) if chat else "a chat"
        title = (f"Turn {number}" if number is not None else "A turn") + f" of {where} differs between devices"
        if chat:
            route = _route("chat", [chat])
    else:
        title = f"A {kind} record changed on more than one device"
    actions, notes = conflict_actions(entry, dialed)
    devices = [side["peer"] for side in entry["peers"]]
    return {
        "id": f"conflict:{path}", "kind": "conflict", "label": KINDS["conflict"], "title": title,
        "where": {"text": _where(campaigns.get(campaign) or str(campaign),
                                 "with " + ", ".join(devices) if devices else None),
                  "chat": chat, "campaign": campaign, "devices": devices},
        "detail": "Both versions are kept until you choose. Keep one: the rest of the conflict with that device is "
                  "set aside on both devices, and listed after.",
        "notes": notes, "actions": actions, "route": route, "conflict": dict(entry),
    }


# -- the campaign, as the table sees it -------------------------------------------------------------------

def campaign_items(hub: Mapping[str, Any], chat: str, title: str, campaign: str | None, post: str | None,
                   numbers: Mapping[str, int]) -> list[dict]:
    """What a chat's campaign waits on the player for, from ``campaign.hub`` as the **table** reads it."""
    campaign_id = ChatRef.parse(chat).campaign_id
    can = {item["op"]: item for item in hub["actions"]}
    names = {member["actor"]: member["name"] for member in hub["cast"]}
    hub_route = _route("campaign", [campaign_id], {"chat": chat})

    def where(turn: str | None, number: int | None = None) -> dict:
        number = numbers.get(turn) if number is None and turn is not None else number
        return {"text": _where(campaign, title, f"turn {number}" if number is not None else None), "chat": chat,
                "campaign": campaign_id, "devices": []}

    def offered(op: str, what: str, action: dict) -> tuple[list[dict], list[str]]:
        """The action, where the hub says its op is available now; else why it is not."""
        found = can.get(op)
        if found is not None and found.get("available"):
            return [action], []
        return [], [f"{what} is not available now" + (f": {found['reason']}." if found and found.get("reason") else ".")]

    items = []
    action = hub.get("open_action")
    label = action and action.get("label")
    if action and label and label["state"] == "proposed" and not action.get("framed") and not action.get("outcome"):
        actor = action["actor"]
        who = (actor and (actor.get("name") or actor.get("ref"))) or "Someone"
        against = label.get("against")
        goal = None
        if against:
            goal = next((item["goal"] for item in hub["obstacles"] if item["obstacle"] == against["obstacle"]),
                        against["obstacle"])
        said = f"Labelled {label['label']}" + (f", against “{goal}”" if goal else "") \
            + (f" by {against['approach']}" if against and against.get("approach") else "") + "."
        actions, notes = offered("confirm", "Confirming the labelling", _action(
            "confirm", "Confirm the labelling", "campaign.confirm", {"chat": chat, "audience": "table"},
            "Labelling confirmed", quick=not (label["factors"] or label["stakes"])))
        items.append({
            "id": f"label:{chat}:{action['turn']}", "kind": "action", "label": KINDS["action"],
            "title": _clip(post) if post else f"{who} is acting", "where": where(action["turn"]),
            "detail": " ".join(part for part in (said, label.get("reason")) if part) + " Confirming frames it for "
                      "the director to decide.",
            "notes": notes, "actions": actions, "route": hub_route,
            "action": {"turn": action["turn"], "actor": actor, "label": label},
        })
    for turn in hub["turns"]:
        for effect in turn["effects"]:
            if effect["state"] not in ("proposed", "approved"):
                continue
            if not any(key["principal"] == hub["player"] and not key["given"] for key in effect["endorsers"]):
                continue
            args = {"chat": chat, "audience": "table", "turn": turn["turn"], "number": effect["number"],
                    "fingerprint": effect["fingerprint"]}
            actions, notes = offered("endorse_effect", "Endorsing this change", _action(
                "endorse", "Endorse as shown", "campaign.endorse_effect", args, "Your key is given",
                confirm={"message": f"Give your key to effect {effect['number']}, exactly as shown: "
                                    f"{effect['summary']}", "label": "Endorse", "danger": False}))
            items.append({
                "id": f"effect:{chat}:{turn['turn']}:{effect['number']}", "kind": "approval",
                "label": KINDS["approval"], "title": _clip(effect["summary"]),
                "where": where(turn["turn"], turn["number"]),
                "detail": "It becomes world truth once you endorse it and the director approves. Your key covers "
                          "exactly what is shown here.",
                "notes": notes, "actions": actions, "route": hub_route,
                "effect": {"turn": turn["turn"], "number": turn["number"], "effect": effect, "names": names,
                           "player": hub["player"]},
            })
    return items


# -- a reply that did not arrive --------------------------------------------------------------------------

def reply_item(chat: str, title: str, campaign: str | None, turn: Mapping[str, Any], interrupted: bool,
               generates: bool) -> dict | None:
    """The open turn's reply was cut off, or its newest take ended with no line (as the chat page reads it)."""
    offered = turn["offered"]
    settled = [take for take in offered if take["state"] != "running"]
    newest = max(settled, key=lambda take: take["take"], default=None)
    running = any(take["state"] == "running" for take in offered) and not interrupted
    failed = newest is not None and not newest["lines"] and bool(newest["calls"])
    if not interrupted and (running or not failed):
        return None
    actions = []
    if generates:
        actions.append(_action("again", "Ask again", "generate", {"chat": chat},
                               "Asked again. The reply waits in the chat.", quick=True))
    actions.append(_action("pass", "Pass the turn", "pass", {"chat": chat, "turn": turn["turn"]}, "Passed", quick=True))
    return {
        "id": f"reply:{chat}:{turn['turn']}", "kind": "reply", "label": KINDS["reply"],
        "title": "A reply stopped before it finished" if interrupted else "The reply did not arrive",
        "where": {"text": _where(campaign, title, f"turn {turn['number']}"), "chat": chat,
                  "campaign": ChatRef.parse(chat).campaign_id, "devices": []},
        "detail": ("A reply was cut off before it finished." if interrupted
                   else "Every call for the newest reply ended without a line.")
                  + (" Ask again to replace it, or pass the turn." if generates else " Pass the turn, or set up a model "
                     "to ask again."),
        "notes": [], "actions": actions, "route": _route("chat", [chat]),
    }


# -- the op ----------------------------------------------------------------------------------------------

def _table_waits(scene: Any) -> bool:
    """Whether the chat's turns show a labelling to confirm or a change still waiting on a key: the only things
    ``campaign_items`` reads the hub for, so a chat with neither is not asked for its hub at all."""
    pending = scene.pending
    if pending is not None and pending.labels and pending.labels[-1].state == "proposed":
        return True
    return any(effect.state in ("proposed", "approved") for record in scene.records for effect in record.effects)


def _limit(args: Mapping[str, object]) -> int:
    limit = args.get("limit", DEFAULT_CHATS)
    if type(limit) is not int or not 1 <= limit <= MAX_CHATS:
        raise ApiRefused("invalid_field", f"limit must be 1 to {MAX_CHATS}")
    return limit


def _list(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Everything open that waits on the person, in the order to work down it. With ``chat``, that chat's own
    items only (what its tray shows): no other chat is opened and no sync conflict is listed."""
    limit = _limit(args)
    only = parse_ref(ChatRef.parse, args, "chat", required=False)
    problems: list[dict] = []
    more_chats = False
    if only is None:
        listed = ctx.core("chats", {"filter": "recent", "limit": min(limit + 1, 1000)})["chats"]
        more_chats = len(listed) > limit
        entries = listed[:limit]
    else:
        entries = ctx.api.chat_entries([only])
        if not entries:
            raise ApiRefused("no_chat", f"no chat {only}")
    # which devices this one knows, to name a branch's device and to say which can be dialed for a conflict
    media = media_of(ctx.api)
    dialed: frozenset[str] = frozenset()
    devices: dict[str | None, str] = {None: "this device"}
    try:
        known = media.call("sync.devices", {})
    except ApiRefused as error:
        known = None
        problems.append({"source": "sync", "code": error.reason})
    else:
        dialed = frozenset(peer["peer"] for peer in known["peers"] if peer["paired"] and peer["address"])
        devices.update({tag_of(peer["peer"]): peer["peer"] for peer in known["peers"]})
        if known["device"]:
            devices[tag_of(known["device"])] = "this device"
    names: dict[str, str] = {}
    chats = {entry["chat"]: _chat_title(entry) for entry in entries}
    campaigns: dict[str, str | None] = {}
    found: list[tuple[int, int, dict]] = []  # (the kind's rank, the chat's place by recency, the item)
    for place, entry in enumerate(entries):
        chat = entry["chat"]
        try:
            ref = ChatRef.parse(chat)
        except (ValueError, TypeError):
            continue
        title, campaign = chats[chat], _campaign_title(entry)
        campaigns.setdefault(ref.campaign_id, campaign or title)  # a card chat's campaign has no title of its own
        try:
            scene = ctx.runner.open(ref)
        except _UNREADABLE as error:
            problems.append({"source": "chat", "chat": chat, "code": getattr(error, "reason", type(error).__name__)})
            continue
        names.update({str(actor): name for actor, name in scene.names.items()})
        numbers = {stored.record.turn_id: stored.record.number for stored in scene.turns.turns}
        items = []
        fork = fork_view(scene.turns.fork)
        if fork is not None:
            items.append(fork_item(chat, title, campaign, fork,
                                   [set_aside_view(stored) for stored in scene.turns.set_aside], numbers, devices))
        pending = scene.pending
        shown = None
        if pending is not None:
            stored = next((item for item in scene.turns.turns if item.record.turn_id == pending.turn_id), None)
            shown = None if stored is None else turn_view(stored, scene.names)
        if shown is not None:
            reply = reply_item(chat, title, campaign, shown, scene.interrupted, ctx.runtime is not None)
            if reply is not None:
                items.append(reply)
        if _table_waits(scene):  # the hub is the campaign's reader: asked only where a turn says something waits
            try:
                hub = hub_of(ctx).call("hub", {"chat": chat, "audience": "table"})
            except ApiRefused as error:
                problems.append({"source": "campaign", "chat": chat, "code": error.reason})
            else:
                post = shown["post"]["text"] if shown is not None and shown["post"] else None
                items += campaign_items(hub, chat, title, campaign, post, numbers)
        found += [(_RANK[item["kind"]], place, item) for item in items]
    if only is None and known is not None:
        try:
            listed_conflicts = media.call("sync.conflicts", {})
        except ApiRefused as error:
            problems.append({"source": "sync", "code": error.reason})
        else:
            for campaign_id in listed_conflicts["unknown"]:
                problems.append({"source": "sync", "campaign": campaign_id, "code": "conflicts_unknown"})
            found += [(_RANK["conflict"], len(entries), conflict_item(entry, dialed, names, chats, campaigns))
                      for entry in listed_conflicts["conflicts"]]
    found.sort(key=lambda entry: (entry[0], entry[1], entry[2]["id"]))
    items = [item for _rank, _place, item in found]
    more_items = len(items) > MAX_ITEMS
    items = items[:MAX_ITEMS]
    for order, item in enumerate(items):
        item["order"] = order
    return {"items": items, "count": len(items), "complete": not (more_chats or more_items or problems),
            "problems": problems, "scope": "library" if only is None else "chat",
            "inspected": {"chats": len(entries), "limit": limit, "more_chats": more_chats, "more_items": more_items}}


ADAPTER = Adapter("inbox", owner="108", ops={"list": _list})

__all__ = ["ADAPTER", "DEFAULT_CHATS", "KINDS", "MAX_CHATS", "MAX_ITEMS", "campaign_items", "conflict_actions",
           "conflict_item", "fork_item", "reply_item"]
