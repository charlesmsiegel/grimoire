"""The app's page registry (108): how a Wave 7 sprint puts a screen in the shell without editing the shell.

A page is a folder ``static/pages/<id>/`` holding a ``page.json`` manifest and one ES module (its ``entry``).
The server reads every manifest when it starts, refuses to start on a bad one, and hands the list to the shell
(``GET /shell/pages``); the shell puts each page in its navigation slot and imports its module only when its
route is first opened. So a sprint adds a folder of its own, and nobody edits a shared route table.

A manifest (contract 1)::

    {"contract": 1, "id": "chats", "route": "chats", "title": "Chats", "slot": "play", "order": 10,
     "owner": "111", "entry": "page.js", "requires": ["chats", "open"], "unavailable": null}

``requires`` names the ops (core or ``adapter.op``) the page cannot work without: a page whose op is missing is
listed as unavailable with those ops named, never as a page whose buttons fail. ``unavailable`` lets a page
say so itself, with its named blocker ("waits on 046 milestone 2"). See docs/APP_SHELL.md.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

#: The manifest format this shell reads.
CONTRACT = 1
#: The navigation slots, in the order the shell shows them. ``hidden`` pages are reached by deep link only.
SLOTS = ("home", "play", "library", "campaign", "media", "settings", "hidden")
#: A page id and a route: lower case, ``-`` between words.
SLUG = re.compile(r"[a-z][a-z0-9-]{0,39}")
#: The entry module's file name, inside the page's own folder.
ENTRY = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\.js")
KEYS = frozenset({"contract", "id", "route", "title", "slot", "order", "owner", "entry", "requires", "unavailable"})
MAX_TITLE = 40
MAX_BLOCKER = 200


class PageManifestError(ValueError):
    """A page's manifest is not one this shell can show; the app does not start until it is fixed."""


@dataclass(frozen=True)
class Page:
    id: str
    route: str
    title: str
    slot: str
    order: int
    owner: str
    entry: str
    requires: tuple[str, ...] = ()
    unavailable: str | None = None

    @property
    def module(self) -> str:
        """The address the shell imports the page's module from."""
        return f"/static/pages/{self.id}/{self.entry}"

    def to_json(self, ops: Iterable[str]) -> dict:
        """The shell's view: ``missing`` names each required op this app does not serve."""
        served = set(ops)
        return {"id": self.id, "route": self.route, "title": self.title, "slot": self.slot, "order": self.order,
                "owner": self.owner, "module": self.module, "requires": list(self.requires),
                "missing": [op for op in self.requires if op not in served], "unavailable": self.unavailable}


def _text(value: object, key: str, where: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or not value.isprintable():
        raise PageManifestError(f"{where}: {key} must be printable text of 1 to {limit} characters")
    return value


def parse_manifest(folder_name: str, data: object) -> Page:
    where = f"pages/{folder_name}/page.json"
    if not isinstance(data, dict):
        raise PageManifestError(f"{where}: a manifest is a JSON object")
    unknown = set(data) - KEYS
    if unknown:
        raise PageManifestError(f"{where}: unknown keys {sorted(unknown)}")
    if type(data.get("contract")) is not int or data["contract"] != CONTRACT:
        raise PageManifestError(f"{where}: contract must be {CONTRACT}")
    page_id, route = data.get("id"), data.get("route")
    if not isinstance(page_id, str) or not SLUG.fullmatch(page_id) or page_id != folder_name:
        raise PageManifestError(f"{where}: id must match {SLUG.pattern} and name its own folder")
    if not isinstance(route, str) or not SLUG.fullmatch(route):
        raise PageManifestError(f"{where}: route must match {SLUG.pattern}")
    if data.get("slot") not in SLOTS:
        raise PageManifestError(f"{where}: slot must be one of {', '.join(SLOTS)}")
    order = data.get("order")
    if type(order) is not int or not 0 <= order <= 1000:
        raise PageManifestError(f"{where}: order must be a whole number from 0 to 1000")
    entry = data.get("entry")
    if not isinstance(entry, str) or not ENTRY.fullmatch(entry):
        raise PageManifestError(f"{where}: entry must be a .js file name matching {ENTRY.pattern}")
    requires = data.get("requires", [])
    if not isinstance(requires, list) or not all(isinstance(op, str) and op for op in requires):
        raise PageManifestError(f"{where}: requires must be a list of op names")
    unavailable = data.get("unavailable")
    if unavailable is not None:
        unavailable = _text(unavailable, "unavailable", where, MAX_BLOCKER)
    return Page(id=page_id, route=route, title=_text(data.get("title"), "title", where, MAX_TITLE),
                slot=data["slot"], order=order, owner=_text(data.get("owner"), "owner", where, 16), entry=entry,
                requires=tuple(requires), unavailable=unavailable)


def load_pages(static: Path) -> tuple[Page, ...]:
    """Every page under ``static/pages``, in slot order, then ``order``, then id. Refuses (``PageManifestError``)
    a bad manifest, a missing entry module, a folder with no manifest, or two pages on one route."""
    root = static / "pages"
    if not root.is_dir():
        return ()
    pages: list[Page] = []
    routes: dict[str, str] = {}
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or folder.name.startswith((".", "_")):
            continue
        manifest = folder / "page.json"
        if not manifest.is_file():
            raise PageManifestError(f"pages/{folder.name}: has no page.json")
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as error:
            raise PageManifestError(f"pages/{folder.name}/page.json: not readable JSON ({error})") from None
        page = parse_manifest(folder.name, data)
        if not (folder / page.entry).is_file():
            raise PageManifestError(f"pages/{folder.name}: its entry {page.entry} is missing")
        if page.route in routes:
            raise PageManifestError(f"pages/{folder.name}: route {page.route!r} is already pages/{routes[page.route]}'s")
        routes[page.route] = page.id
        pages.append(page)
    return tuple(sorted(pages, key=lambda page: (SLOTS.index(page.slot), page.order, page.id)))


__all__ = ["CONTRACT", "SLOTS", "Page", "PageManifestError", "load_pages", "parse_manifest"]
