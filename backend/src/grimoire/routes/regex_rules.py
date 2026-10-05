"""The output-processing regex rules' HTTP surface (store/regex/).

One GET/PUT per level -- global, world, campaign and connection -- the test
pane's `POST /regex/test`, and the SillyTavern import (a preview that writes
nothing, then a commit of the rows the user kept). Each GET answers `inherited`
(what the level sits on, in run order, `off` laid over it) next to its own
`layer`, the shape the tracker layers use, so an editor shows what a change
would be changing without a second request. A PUT replaces the level's whole
file and is validated as a unit.

Nothing here is a detached run: the handlers are short, synchronous reads and
writes, and none reserves."""

from __future__ import annotations

import contextlib

from fastapi import APIRouter, HTTPException

from .. import store
from .common import _dump
from .models import RegexImport, RegexImportPreview, RegexLayer, RegexTest

router = APIRouter()

_ROLES = ("model", "user")
_PHASES = ("display", "prompt", "store")
_RUN_ORDER = {"connection": 0, "global": 1, "world": 2, "campaign": 3}


def _invalid(exc: store.regex.rules.RuleError) -> HTTPException:
    return HTTPException(status_code=400, detail={
        "kind": "invalid_rule", "index": exc.index, "field": exc.field, "detail": str(exc)})


def _require(level: str, key: str) -> None:
    """404 for a world, campaign or connection that is not there."""
    if level == "world" and not store.worlds.world_exists(key):
        raise HTTPException(status_code=404, detail="world not found")
    if level == "campaign" and not store.campaigns.campaign_exists(key):
        raise HTTPException(status_code=404, detail="campaign not found")
    if level == "connection":
        try:
            store.llm_connections.read_connection_raw(key)
        except (store.llm_connections.ConnectionNotFound, ValueError) as exc:
            raise HTTPException(status_code=404, detail="connection not found") from exc


def _warnings(rules: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for rule in rules:
        found = store.regex.rules.warnings(rule)
        if found:
            out[rule["id"]] = found
    return out


def _body(level: str, key: str) -> dict:
    layers = store.regex.layers
    layer = layers.read_level(level, key)
    return {"layer": layer, "inherited": layers.inherited(level, key),
            "warnings": _warnings(layer["rules"])}


def _get(level: str, key: str) -> dict:
    _require(level, key)
    return _body(level, key)


def _put(level: str, key: str, body: RegexLayer) -> dict:
    _require(level, key)
    try:
        store.regex.layers.write_level(level, key, _dump(body))
    except store.regex.rules.RuleError as exc:
        raise _invalid(exc) from exc
    except (store.worlds.WorldNotFound, store.campaigns.CampaignNotFound,
            store.llm_connections.ConnectionNotFound) as exc:
        # Deleted between the check and the write, in another tab.
        raise HTTPException(status_code=404, detail="not found") from exc
    return _body(level, key)


@router.get("/regex")
def get_regex():
    return _get("global", "")


@router.put("/regex")
def put_regex(body: RegexLayer):
    return _put("global", "", body)


@router.get("/worlds/{wid}/regex")
def get_world_regex(wid: str):
    return _get("world", wid)


@router.put("/worlds/{wid}/regex")
def put_world_regex(wid: str, body: RegexLayer):
    return _put("world", wid, body)


@router.get("/campaigns/{cid}/regex")
def get_campaign_regex(cid: str):
    return _get("campaign", cid)


@router.put("/campaigns/{cid}/regex")
def put_campaign_regex(cid: str, body: RegexLayer):
    return _put("campaign", cid, body)


@router.get("/llm-connections/{conn_id}/regex")
def get_connection_regex(conn_id: str):
    return _get("connection", conn_id)


@router.put("/llm-connections/{conn_id}/regex")
def put_connection_regex(conn_id: str, body: RegexLayer):
    return _put("connection", conn_id, body)


# --- the test pane -----------------------------------------------------------

def _scope_key(scope: dict) -> tuple[str, str]:
    """`(level, key)` for a test scope, or a 400 / 404 for one that names nothing."""
    kind = scope.get("kind")
    field = {"global": None, "world": "wid", "campaign": "cid", "connection": "id"}
    if kind not in field:
        raise HTTPException(status_code=400, detail="scope.kind must be global, world, "
                                                    "campaign or connection")
    key = ""
    if field[kind]:
        given = scope.get(field[kind])
        if not isinstance(given, str) or not given:
            raise HTTPException(status_code=400, detail=f"scope.{field[kind]} is required")
        key = given
    _require(kind, key)
    return kind, key


def _entries(level: str, key: str, connection: str) -> list[dict]:
    """The rules a message produced under `connection` would meet, in run order,
    cut at the scope's level -- the chain a real message gets (one connection's
    rules, never every connection's). A connection scope is its own rules, whatever
    `connection` says."""
    layers = store.regex.layers
    if level == "campaign":
        return list(layers.effective(cid=key, connection=connection))
    if level == "connection":
        return list(layers.effective(cid=None, connection=key))
    return list(layers.effective(cid=None, connection=connection,
                                 world=key if level == "world" else ""))


@router.post("/regex/test")
def post_regex_test(body: RegexTest):
    if body.role not in _ROLES:
        raise HTTPException(status_code=400, detail="role must be model or user")
    if body.phase not in _PHASES:
        raise HTTPException(status_code=400, detail="phase must be display, prompt or store")
    level, key = _scope_key(body.scope)
    entries = _entries(level, key, body.connection)
    if body.draft is not None:
        try:
            # Normalising mints an id for a draft that has none.
            draft = store.regex.rules.normalise(body.draft)
        except store.regex.rules.RuleError as exc:
            raise _invalid(exc) from exc
        for i, entry in enumerate(entries):
            if entry["rule"]["id"] == draft["id"]:
                entries[i] = {**entry, "rule": draft}
                break
        else:
            # A new rule runs after its own level's saved rules, which for a
            # connection is before the global ones the list also carries.
            at = max((i + 1 for i, e in enumerate(entries)
                      if _RUN_ORDER[e["level"]] <= _RUN_ORDER[level]), default=0)
            entries.insert(at, {"level": level, "rule": draft, "off": False,
                                "source": key if level == "connection" else ""})
    steps = store.regex.apply.trace(
        body.text, entries, role=body.role, phase=body.phase, depth=max(body.depth, 0))
    return {"steps": steps, "result": steps[-1]["text_after"] if steps else body.text}


# --- SillyTavern import ------------------------------------------------------

@router.post("/regex/import/preview")
def post_regex_import_preview(body: RegexImportPreview):
    try:
        rows = store.regex.st_import.preview(body.data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"rows": rows}


def _level_lock(level: str, key: str):
    """The campaign lock for a campaign level, held across the read and the
    write so an append cannot lose a PUT that lands in between; the other
    levels' writers take none (`store/locks.py` says why)."""
    if level == "campaign":
        return store.locks.campaign_lock(key)
    return contextlib.nullcontext()


@router.post("/regex/import")
def post_regex_import(body: RegexImport):
    """Append the kept rows to a level, in order, after its own rules. Every row
    gets a fresh id: SillyTavern's are not ours, and a second import of the same
    file must not collide with the first."""
    level, key = _scope_key(body.scope)
    if not body.rows:
        raise HTTPException(status_code=400, detail="rows: nothing to import")
    layers = store.regex.layers
    with _level_lock(level, key):
        doc = layers.read_level(level, key)
        used = {r["id"] for r in doc["rules"]}
        used |= {e["rule"]["id"] for e in layers.inherited(level, key)}
        added: list[dict] = []
        for i, raw in enumerate(body.rows):
            try:
                rule = store.regex.rules.normalise(
                    {k: v for k, v in raw.items() if k != "id"}, index=i)
            except store.regex.rules.RuleError as exc:
                raise _invalid(exc) from exc
            while rule["id"] in used:
                rule["id"] = store.regex.rules.mint_id()
            used.add(rule["id"])
            added.append(rule)
        try:
            layers.write_level(level, key, {**doc, "rules": doc["rules"] + added})
        except store.regex.rules.RuleError as exc:
            raise _invalid(exc) from exc
        except (store.worlds.WorldNotFound, store.campaigns.CampaignNotFound,
                store.llm_connections.ConnectionNotFound) as exc:
            raise HTTPException(status_code=404, detail="not found") from exc
    return {**_body(level, key), "added": [r["id"] for r in added]}
