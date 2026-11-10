"""Chat launch and scene-play ops for the app (Sprint 111): Start Chat, adding a card, line requests,
corrections and missing-source repair, over the services that own them.

Like the rest of the text API these ops are trusted application code acting for the library owner. They
write only through 052's ``QuickStart`` (which composes 107's ``ChatLibrary`` and 035's ``SceneRunner``) and
035's ``SceneRunner`` itself, and keep no record of their own. Every op that changes a chat returns the
scene as ``open`` does, so a client renders one shape. See docs/TEXT_API.md.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from grimoire.core.identity import ChatRef, SourceRef
from grimoire.quickstart import Repair, Selection

from .errors import ApiRefused

if TYPE_CHECKING:  # pragma: no cover
    from .api import TextApi

#: What Start Chat starts from (052's selection kinds), and the definition kind each one's ``ref`` names.
START_KINDS = {"character": "character", "scenario": "scenario", "bundle": "character", "premise": None}
#: The kinds a launch picker lists. A persona is a ``character`` definition played by the player.
PICKER_KINDS = ("character", "scenario", "lorebook")
#: What a card added while playing may be (052's ``add_card``).
ADD_ROLES = ("character", "lorebook", "scenario")
#: 035's line requests a client may make on named lines (016): ``pause`` is its own op.
LINE_REQUESTS = ("skip", "redirect", "rephrase")
#: 008's typed input modes the composer offers. ``director`` is routed but refused ``not_in_this_slice``
#: by 035 (a player gives no director instructions yet), so it is not offered.
POST_MODES = ("auto", "speech", "action")
MAX_DIRECTION = 2_000
MAX_LINES = 200
MAX_LABEL = 200


def _text(args: Mapping[str, object], key: str, *, required: bool = True, limit: int | None = None) -> str | None:
    value = args.get(key)
    if value is None and not required:
        return None
    if type(value) is not str or not value:
        raise ApiRefused("invalid_field", f"{key} must be nonempty text")
    if limit is not None and len(value) > limit:
        raise ApiRefused("invalid_field", f"{key} is at most {limit} characters")
    return value


def _source(args: Mapping[str, object], key: str, *, required: bool = True, kind: str | None = None) -> SourceRef | None:
    text = _text(args, key, required=required)
    if text is None:
        return None
    try:
        ref = SourceRef.parse(text)
    except (ValueError, TypeError):
        raise ApiRefused("invalid_field", f"{key} is not a definition reference") from None
    if kind is not None and ref.kind != kind:
        raise ApiRefused("invalid_field", f"{key} must be a {kind} definition")
    return ref


def _opening_view(opening) -> dict:  # type: ignore[no-untyped-def]
    return {"id": opening.opening_id, "text": opening.text, "group_only": opening.group_only}


def _repair_view(repair: Repair) -> dict:
    return {"field": repair.field, "ref": repair.ref, "reason": repair.reason, "role": repair.role,
            "choices": list(repair.choices)}


class ChatPlayOps:
    """111's ops over one ``TextApi``: it supplies the store, the owner's handle and the services, and
    serializes every call. ``handlers()`` is what ``TextApi`` registers."""

    def __init__(self, api: TextApi) -> None:
        self.api = api

    def handlers(self) -> dict[str, Callable[[Mapping[str, object]], dict]]:
        return {"start_sources": self._start_sources, "start_options": self._start_options, "start": self._start,
                "add_card": self._add_card, "withdraw": self._withdraw, "strike": self._strike,
                "repairs": self._repairs, "detach": self._detach}

    # -- the launch picker ---------------------------------------------------------------------------------
    def _start_sources(self, args: Mapping[str, object]) -> dict:
        """Every character, scenario and lorebook the library could bind now, by head, with its name, for the
        Start Chat picker. A character is also what a persona is. Reads only; a source the library excluded
        or a definition it dropped is not listed, and nothing is matched by name."""
        view = self.api.store.load()
        found: dict[str, list[dict]] = {kind: [] for kind in PICKER_KINDS}
        for source_id in sorted(view.sources):
            try:
                heads = view.sources[source_id].manifest.head_refs()
            except ValueError:
                continue  # 010 refused this manifest; load() reports it
            for ref, revision in sorted(heads.items(), key=lambda item: str(item[0])):
                if ref.kind not in found or view.index.check(ref) is not None:
                    continue
                try:
                    name = str(view.index.resolve(ref).name)
                except Exception:  # noqa: BLE001 -- a head the index cannot resolve is not offered
                    continue
                found[ref.kind].append({"ref": str(ref), "name": name, "revision": revision})
        for items in found.values():
            items.sort(key=lambda item: (item["name"].casefold(), item["ref"]))
        return {"characters": found["character"], "scenarios": found["scenario"], "lorebooks": found["lorebook"]}

    def _selection(self, args: Mapping[str, object]) -> Selection:
        kind = _text(args, "kind")
        if kind not in START_KINDS:
            raise ApiRefused("invalid_selection", "kind must be character, scenario, bundle or premise")
        if kind == "premise":
            for key in ("ref", "lorebook"):
                if args.get(key) is not None:
                    raise ApiRefused("invalid_selection", "a premise names no source")
            premise = args.get("premise")
            if type(premise) is not str:
                raise ApiRefused("invalid_selection", "a premise is text")
            return Selection.from_premise(premise)  # 052 checks it is nonblank and not too long
        if args.get("premise") is not None:
            raise ApiRefused("invalid_selection", "only a premise selection carries premise text")
        ref = _source(args, "ref", kind=START_KINDS[kind])
        assert ref is not None
        if kind == "bundle":
            return Selection.bundle(ref, _source(args, "lorebook", required=False, kind="lorebook"))
        if args.get("lorebook") is not None:
            raise ApiRefused("invalid_selection", "only a bundle names a lorebook")
        return Selection.character(ref) if kind == "character" else Selection.scenario(ref)

    def _start_options(self, args: Mapping[str, object]) -> dict:
        """052's ``options``: the pinned sources a selection would bind, its openings, the default one, the
        card's own lorebook, and the chats already started from it (for Continue). Writes nothing."""
        options = self.api.quick.options(self._selection(args))
        return {"sources": [str(ref) for ref in options.sources],
                "openings": [_opening_view(opening) for opening in options.openings],
                "default_opening": options.default_opening,
                "lorebook": None if options.lorebook is None else str(options.lorebook),
                "related": self.api.chat_entries(options.related)}

    def _start(self, args: Mapping[str, object]) -> dict:
        """052's ``start``: a new world-less chat played as ``persona`` (required: 052 refuses ``no_persona``),
        opened with ``opening`` or the default. Every call is a new chat; Continue is ``continue``."""
        selection = self._selection(args)
        persona = _source(args, "persona", required=False, kind="character")
        opening = _text(args, "opening", required=False)
        label = _text(args, "label", required=False, limit=MAX_LABEL)
        started = self.api.quick.start(selection, self.api.player_handle, persona=persona, opening=opening,
                                       label=label)
        return self._scene(started.chat, unfinished=started.unfinished)

    # -- while playing -------------------------------------------------------------------------------------
    def _add_card(self, args: Mapping[str, object]) -> dict:
        """052's ``add_card``: a character, lorebook or scenario added to the chat, pinned. A new character
        witnessed nothing and hears only what is said from now on."""
        chat = self.api.chat_arg(args)
        role = args.get("role", "character")
        if role not in ADD_ROLES:
            raise ApiRefused("invalid_field", "role must be character, lorebook or scenario")
        ref = _source(args, "ref", kind=str(role))
        assert ref is not None
        started = self.api.quick.add_card(chat, ref, self.api.player_handle, role=str(role))
        return self._scene(chat, unfinished=started.unfinished)

    def _withdraw(self, args: Mapping[str, object]) -> dict:
        """035's line request: ``skip``, ``redirect`` or ``rephrase`` the named ``lines`` (by the ``line`` ids
        the scene shows). ``direction`` is recorded on the take for a redirect or rephrase."""
        chat = self.api.chat_arg(args)
        kind = args.get("kind")
        if kind not in LINE_REQUESTS:
            raise ApiRefused("invalid_field", "kind must be skip, redirect or rephrase")
        lines = args.get("lines")
        if (not isinstance(lines, list) or not lines or len(lines) > MAX_LINES
                or not all(type(item) is str and item for item in lines)):
            raise ApiRefused("invalid_field", f"lines must be 1 to {MAX_LINES} line ids")
        direction = _text(args, "direction", required=False, limit=MAX_DIRECTION)
        if kind == "skip" and direction is not None:
            raise ApiRefused("invalid_field", "a skip takes no direction")
        self.api.runner.request(chat, self.api.player_handle, str(kind), tuple(dict.fromkeys(lines)),
                                direction=direction)
        return self._scene(chat)

    def _strike(self, args: Mapping[str, object]) -> dict:
        """035's correction: strike one shown line of a kept take, so no one hears it from now on."""
        chat = self.api.chat_arg(args)
        line = _text(args, "line")
        assert line is not None
        self.api.runner.strike(chat, self.api.player_handle, line)
        return self._scene(chat)

    # -- a source gone ------------------------------------------------------------------------------------
    def _repairs(self, args: Mapping[str, object]) -> dict:
        """052's ``repairs``: every source the chat binds that does not resolve now, with its choices."""
        chat = self.api.chat_arg(args)
        return {"chat": str(chat), "repairs": [_repair_view(repair) for repair in self.api.quick.repairs(chat)]}

    def _detach(self, args: Mapping[str, object]) -> dict:
        """052's ``detach_broken``: the ``detach`` choice for one broken binding or world, named by the
        ``field`` and ``ref`` ``repairs`` gave. The chat plays on without it; its actor and turns stay."""
        chat = self.api.chat_arg(args)
        field = _text(args, "field")
        ref = _text(args, "ref")
        found = next((repair for repair in self.api.quick.repairs(chat)
                      if repair.field == field and repair.ref == ref), None)
        if found is None:
            raise ApiRefused("not_broken", "that source resolves now, or was already detached")
        self.api.quick.detach_broken(chat, found, self.api.player_handle)
        return self._scene(chat)

    def _scene(self, chat: ChatRef, *, unfinished: str | None = None) -> dict:
        scene = self.api.scene_view(chat)
        scene["unfinished"] = unfinished
        return scene


__all__ = ["ADD_ROLES", "LINE_REQUESTS", "PICKER_KINDS", "POST_MODES", "START_KINDS", "ChatPlayOps"]
