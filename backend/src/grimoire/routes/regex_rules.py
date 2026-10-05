"""The output-processing regex rules' HTTP surface (store/regex/).

One GET/PUT per level -- global, world, campaign and connection -- and the test
pane's `POST /regex/test`. Each GET answers `inherited` (what the level sits on,
in run order, `off` laid over it) next to its own `layer`, the shape the tracker
layers use, so an editor shows what a change would be changing without a second
request. A PUT replaces the level's whole file and is validated as a unit.

Nothing here is a detached run: the handlers are short, synchronous reads and
writes, and none reserves."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import store
from .common import _dump
from .models import RegexLayer, RegexTest

router = APIRouter()

_ROLES = ("model", "user")
_PHASES = ("display", "prompt", "store")


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
    """The rules a message in this scope would meet, in run order. A campaign
    scope is what a message there is run through, connection and all; any other
    scope is what it sits on plus its own rules."""
    layers = store.regex.layers
    if level == "campaign":
        return list(layers.effective(cid=key, connection=connection))
    own = [{"level": level, "rule": r, "off": False,
            "source": key if level == "connection" else ""}
           for r in layers.read_level(level, key)["rules"]]
    return layers.inherited(level, key) + own


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
            entries.append({"level": level, "rule": draft, "off": False,
                            "source": key if level == "connection" else ""})
    steps = store.regex.apply.trace(
        body.text, entries, role=body.role, phase=body.phase, depth=max(body.depth, 0))
    return {"steps": steps, "result": steps[-1]["text_after"] if steps else body.text}
