"""056's source catalogs on the Prep page: ``prep.catalogs``.

A catalog (056, ``grimoire.catalogs``) is an ordinary source-owned definition listing pinned member records
(characters, lore, gear, creatures), its pinned dependencies and its exclusions. A campaign does not record that it
uses a catalog: it opts in only through its chats' explicit bindings (docs/CATALOGS.md). So what Prep shows is
read, never inferred:

- each catalog in the library at its source's head, composed by 056's own ``compose_catalogs`` (a catalog that does
  not compose is shown with 056's refusal reason, never as empty), with its own exclusions and dependencies;
- each of the campaign's chats' bindings, with its source-qualified ref and the revision it is pinned to, and which
  catalogs make that exact revision available (and which exclude the entity from discovery: availability is not
  access, so the binding still holds);
- the context cost: for each NPC the chat's cast plays, the deterministic token estimate (``bytes / 3``) of the
  items its current context pack takes from each bound record, through the runner's own ``inspect_context`` (the
  pack a turn would build now). A record that is only available in a catalog costs nothing: membership never
  enters a prompt.

The view is the director's: it names every binding and every NPC's context. Changing a binding is not done here:
056 adds no write of its own, and the existing reviewed path is the record's own Library page (109's Attach, an
etag-guarded write a person confirms); detaching a working binding has no screen.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.app.adapter import AdapterContext
from grimoire.catalogs import CatalogError, compose_catalogs
from grimoire.catalogs.model import catalog_from_record
from grimoire.chats.codec import ChatCodecError
from grimoire.core import DIRECTOR
from grimoire.core.identity import ChatRef
from grimoire.core.index import MissingDefinition
from grimoire.effective import chat_snapshot
from grimoire.scene import SceneRefused
from grimoire.store import RecordNotFound, StoredDefinition, StoreError

from ..campaign import hub_of
from .common import campaign_of, director

#: At most this many catalogs are composed for one answer, and at most this many of a campaign's chats are read
#: (newest first); ``more_catalogs`` and ``more_chats`` say when there were more.
MAX_CATALOGS = 32
MAX_CHATS = 12
#: At most this many of a catalog's available records are listed by name (each opens its Library page).
MAX_RECORDS = 50
#: The kinds of record a catalog is said to hold, in the words the page uses; any other kind is shown as it is.
KINDS = {"character": "character", "lorebook": "lore", "lore": "lore", "item": "gear", "creature": "bestiary",
         "scenario": "scenario", "persona": "persona"}
CHANGE = ("Changing a binding is not done on this page: attach a catalog record to a chat from its Library page "
          "(reviewed, and refused if the chat changed since it was shown). Detaching a working binding has no screen "
          "yet. Package distribution (103) and following a catalog's head (079) are not available.")


def _catalogs(view) -> tuple[list[dict], list[dict], bool]:  # type: ignore[no-untyped-def]
    """Every catalog at its source's head, composed one by one: (shown catalogs, their entries, more)."""
    heads = sorted({definition.ref.unpinned() for definition in view.index.definitions()
                    if definition.ref.kind == "catalog"}, key=str)
    shown: list[dict] = []
    entries: list[dict] = []
    for unpinned in heads[:MAX_CATALOGS]:
        try:
            stored = view.index.resolve(unpinned)
        except MissingDefinition as error:
            shown.append({"catalog": str(unpinned), "name": None, "problem": error.reason, "excludes": [],
                          "depends_on": [], "members": 0, "available": 0, "kinds": {}, "records": [],
                          "more_records": False})
            continue
        item: dict = {"catalog": str(stored.ref), "name": getattr(stored, "name", None) or None, "problem": None,
                "excludes": [], "depends_on": [], "members": 0, "available": 0, "kinds": {}, "records": [],
                "more_records": False, "etag": getattr(stored, "etag", None)}
        try:
            if not isinstance(stored, StoredDefinition):
                raise CatalogError("invalid_record", stored.ref)
            node = catalog_from_record(stored)
            item.update(excludes=[str(ref) for ref in node.exclusions],
                        depends_on=[str(ref) for ref in node.dependencies],
                        members=len(node.members))
            composed = compose_catalogs(view, (stored.ref,))
        except CatalogError as error:
            item["problem"] = error.reason
            shown.append(item)
            continue
        kinds: dict[str, int] = {}
        for entry in composed.entries:
            kind = KINDS.get(entry.ref.kind, entry.ref.kind)
            kinds[kind] = kinds.get(kind, 0) + 1
        listed = sorted(composed.entries, key=lambda entry: str(entry.ref))
        item.update(available=len(composed.entries), kinds=dict(sorted(kinds.items())),
                    basis=[{"catalog": str(origin.ref), "etag": origin.etag} for origin in composed.basis],
                    records=[{"ref": str(entry.ref), "name": entry.name or None, "revision": entry.ref.revision,
                              "kind": KINDS.get(entry.ref.kind, entry.ref.kind)} for entry in listed[:MAX_RECORDS]],
                    more_records=len(listed) > MAX_RECORDS)
        entries.append({"catalog": item["catalog"], "exclusions": {ref.entity_key for ref in node.exclusions},
                        "entries": {entry.ref: entry for entry in composed.entries}})
        shown.append(item)
    return shown, entries, len(heads) > MAX_CATALOGS


def _costs(ctx: AdapterContext, chat, scene) -> list[dict]:  # type: ignore[no-untyped-def]
    """Each NPC's current context pack, by the record each item came from (the runner's own estimate)."""
    found = []
    for actor in scene.cast:
        if scene.controller(actor) != DIRECTOR:
            continue
        name = scene.names.get(actor)
        try:
            context = ctx.api.runner.inspect_context(chat, actor)
        except SceneRefused as error:
            found.append({"actor": str(actor), "name": name, "problem": error.reason, "tokens": None, "by_source": {}})
            continue
        except (ValueError, StoreError, LookupError) as error:
            found.append({"actor": str(actor), "name": name, "problem": getattr(error, "reason", None) or "unreadable",
                          "tokens": None, "by_source": {}})
            continue
        by_source: dict[str, int] = {}
        for item in context["items"]:
            source = item["provenance"].get("source")
            if source is not None:
                by_source[source] = by_source.get(source, 0) + int(item["tokens"])
        budget = context["budget"]
        found.append({"actor": str(actor), "name": name, "problem": None, "tokens": budget["tokens"],
                      "max_tokens": budget["max_tokens"], "measurement": budget["measurement"],
                      "by_source": by_source})
    return found


def _bindings(captured, catalogs: list[dict], costs: list[dict]) -> list[dict]:  # type: ignore[no-untyped-def]
    found = []
    for resolution in captured.snapshot.sources():
        ref, resolved = resolution.source.ref, resolution.resolved
        available_in, excluded_by = [], []
        for catalog in catalogs:
            entry = catalog["entries"].get(resolved) if resolved is not None else None
            if entry is not None:
                available_in.append({"catalog": catalog["catalog"],
                                     "declared_by": [str(origin.ref) for origin in entry.origins]})
            if ref.entity_key in catalog["exclusions"]:
                excluded_by.append(catalog["catalog"])
        cost = [{"actor": item["actor"], "name": item["name"], "tokens": item["by_source"].get(str(resolved), 0)}
                for item in costs if item["problem"] is None and resolved is not None]
        found.append({"role": resolution.source.role, "binding": str(ref), "pinned": ref.pinned,
                      "resolved": None if resolved is None else str(resolved),
                      "revision": None if resolved is None else resolved.revision,
                      "kind": KINDS.get(ref.kind, ref.kind),
                      "broken": None if resolution.broken is None else resolution.broken.reason,
                      "available_in": available_in, "excluded_by": excluded_by, "cost": cost})
    return found


def prep_catalogs(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The library's catalogs and what the campaign's chats bind from them, with each binding's context cost."""
    director(args, "the campaign's catalog bindings")
    campaign = campaign_of(args)
    view = ctx.store.load()
    catalogs, composed, more_catalogs = _catalogs(view)
    listed = ctx.api.listed_chats(campaign.campaign_id)
    chats = []
    hub = hub_of(ctx)
    for row in listed[:MAX_CHATS]:
        chat_text = row.get("chat")
        entry: dict = {"chat": chat_text, "label": row.get("label"), "name": (row.get("launch") or {}).get("name"),
                 "problem": None, "bindings": [], "costs": []}
        try:
            chat = ChatRef.parse(chat_text)
            captured = chat_snapshot(view, chat)
            scene = hub.runner.open(chat)
            costs = _costs(ctx, chat, scene)
            entry.update(bindings=_bindings(captured, composed, costs), costs=[
                {key: value for key, value in item.items() if key != "by_source"} for item in costs])
        except SceneRefused as error:
            entry["problem"] = error.reason
        except (RecordNotFound, ChatCodecError, StoreError, ValueError, TypeError, LookupError) as error:
            entry["problem"] = getattr(error, "reason", None) or type(error).__name__
        chats.append(entry)
    return {"campaign": campaign.campaign_id, "catalogs": catalogs, "more_catalogs": more_catalogs,
            "chats": chats, "more_chats": len(listed) > MAX_CHATS, "change": CHANGE,
            "estimate": "Token counts are the app's deterministic estimate (UTF-8 bytes / 3) of what each NPC's "
                        "context pack takes from a record now, not a provider's count."}


__all__ = ["CHANGE", "MAX_CATALOGS", "MAX_CHATS", "MAX_RECORDS", "prep_catalogs"]
