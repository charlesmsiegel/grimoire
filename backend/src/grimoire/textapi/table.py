"""The text API's local table (058): several player characters at one device, each with its own controller.

The owner names the other people playing at this device when the app starts (``local_players``): each is a human
principal of its own, and the app vouches for whichever of them a call names (``by``), as it vouches for the
owner. Nobody else is ever named. The owner hands a character of the chat to one of them, or takes a second
character themselves (one player, several PCs: ``seat``); each then
drafts for their own character (``draft``, ``drop_draft``), and ``submit`` posts every draft, in order, as one
turn the cast answers once (``SceneRunner.post_together``). A refused submission posts nothing and keeps the
drafts to correct.

Everyone at a local table reads the same screen, so the scene is the owner's view of it: a whisper one player's
character posts is on the screen the others read (the turn records who heard it). Drafts are held in memory by
the open app; a restart loses them (058 milestone 2).
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.core import DIRECTOR
from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.play.drafts import Draft, TurnDraft
from grimoire.play.together import Contribution

from .errors import ApiRefused
from .fields import field, parse_ref

#: Chats with drafts held at once: a local table plays a few chats, not hundreds.
MAX_DRAFTED_CHATS = 64


def _actor(args: Mapping[str, object]) -> InstanceRef:
    return parse_ref(InstanceRef.parse, args, "actor")


def _by(api, args: Mapping[str, object]) -> str:  # type: ignore[no-untyped-def]
    """Who at this device the call is for: the owner unless ``by`` names one of its local players."""
    by = field(args, "by", str, required=False, default=api.player)
    if by not in api.seats:
        raise ApiRefused("not_at_this_table", "by names someone this device was not started for")
    return by


def drafts_view(draft: TurnDraft | None, names: Mapping[InstanceRef, str]) -> list[dict]:
    return [] if draft is None else [
        {"actor": str(item.actor), "name": names.get(item.actor, item.actor.instance_id), "by": item.by,
         "text": item.text, "mode": item.mode} for item in draft.drafts]


def _drafted(api, chat: ChatRef, draft: TurnDraft) -> dict:  # type: ignore[no-untyped-def]
    """``draft`` held as the chat's drafts, and the scene that shows it. A chat whose scene cannot be read (one that
    does not exist, say) keeps the drafts it had, so a refused call holds nothing new against the cap."""
    before = api.drafts.get(chat)
    if draft.drafts:
        if chat not in api.drafts and len(api.drafts) >= MAX_DRAFTED_CHATS:
            raise ApiRefused("too_many_drafts", f"drafts are held for at most {MAX_DRAFTED_CHATS} chats at once")
        api.drafts[chat] = draft
    else:
        api.drafts.pop(chat, None)
    try:
        return api._scene(chat)
    except BaseException:
        if before is None:
            api.drafts.pop(chat, None)
        else:
            api.drafts[chat] = before
        raise


def seat(api, args: Mapping[str, object]) -> dict:  # type: ignore[no-untyped-def]
    """Hand a character of the chat to a player at this device (``player``: the owner, who may play several, or a
    local player), or back to the director (``"director"``)."""
    chat = api._chat(args)
    actor = _actor(args)
    player = field(args, "player", str)
    if player != DIRECTOR and player not in api.seats:
        raise ApiRefused("not_at_this_table", "a character is handed to a player at this device, or back to the "
                                              "director")
    api.runner.seat_player(chat, api.player_handle, actor, player)
    return api._scene(chat)


def draft(api, args: Mapping[str, object]) -> dict:  # type: ignore[no-untyped-def]
    """Put or correct ``by``'s draft for ``actor`` (``text``, ``mode``), or move it (``at``, 0 is first)."""
    chat = api._chat(args)
    actor, by = _actor(args), _by(api, args)
    text = field(args, "text", str, required=False)
    mode = field(args, "mode", str, required=False, default="auto")
    at = field(args, "at", int, required=False)
    current = api.drafts.get(chat, TurnDraft(chat))
    if text is None:  # a move only: the draft keeps its words
        if at is None:
            raise ApiRefused("invalid_field", "a draft takes text, a place (at), or both")
        return _drafted(api, chat, current.move(actor, by, at))
    return _drafted(api, chat, current.put(Draft(actor, by, text, mode), at=at))


def drop_draft(api, args: Mapping[str, object]) -> dict:  # type: ignore[no-untyped-def]
    chat = api._chat(args)
    actor, by = _actor(args), _by(api, args)
    return _drafted(api, chat, api.drafts.get(chat, TurnDraft(chat)).drop(actor, by))


def submit(api, args: Mapping[str, object]) -> dict:  # type: ignore[no-untyped-def]
    """Post the chat's drafts, in order, as one turn; they are cleared once it is written, and kept if refused."""
    chat = api._chat(args)
    current = api.drafts.get(chat)
    if current is None or not current.drafts:
        raise ApiRefused("no_drafts", "there is nothing drafted to submit")
    api.runner.post_together(chat, tuple(Contribution(api.seats[item.by], item.actor, item.text, item.mode)
                                         for item in current.drafts))
    api.drafts.pop(chat, None)
    return api._scene(chat)


OPS = {"seat": seat, "draft": draft, "drop_draft": drop_draft, "submit": submit}

__all__ = ["MAX_DRAFTED_CHATS", "OPS", "drafts_view"]
