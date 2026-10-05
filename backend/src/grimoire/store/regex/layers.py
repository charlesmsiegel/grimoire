"""The regex rule files, one per level, and how they stack.

Four levels, each a `{"rules": [...], "off": [...]}` JSON file:

  connection  <home>/llm_connections/<id>.regex.json   (goes with its connection)
  global      <home>/regex.json
  world       <world_root>/regex.json
  campaign    <campaign_root>/regex.json

Run order is connection -> global -> world -> campaign: a connection's rules
correct one model's habits, so every later and more general rule sees text that
is already clean. `off` lives at the world (it may name global and connection
ids) and at the campaign (global, world and connection ids); the global and
connection files sit under nothing, so they have none. Switching an inherited
rule off is the only way to change it -- the tracker layer's rule
(`store/tracker/fields.py`), which this reader follows: it never raises, and an
entry it cannot trust costs only itself, logged.

A layer is validated against what it sits on: its ids may not collide with an
inherited one (an `off` entry would name two rules), and its `off` may name only
inherited ids. Reading does not re-check `off` -- the lower level can lose a
rule the upper one had switched off, and that must not make the file unreadable.

Writers: world, global and connection take no campaign lock (they are not
campaign-scoped; `atomic.write_text` keeps the file whole). The campaign file is
validated against the world's ids and written inside one `campaign_lock(cid)`
hold, so a concurrent campaign write cannot land between the two.
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

from .. import atomic, llm_connections, locks
from .. import paths as store_paths
from ..campaigns import paths as campaigns_paths
from ..campaigns import read as campaigns_read
from ..worlds import paths as worlds_paths
from . import apply, rules

log = logging.getLogger(__name__)

LEVELS = ("global", "world", "campaign", "connection")

Entry = apply.Entry

_EMPTY: dict = {"rules": [], "off": []}

# Parsed and normalised files, keyed by what identifies a version of one. Run
# order is hot (a display frame asks for the whole stack), and a rule list
# should not be re-read and re-validated each time. Cleared when full rather
# than evicted: a library has a handful of these files, and the cap is only a
# bound on a long-lived process that has visited many campaigns.
_CACHE_MAX = 256
_cache: dict[tuple[str, int, int], dict] = {}


# --- where the files live -----------------------------------------------------

def _check_level(level: str) -> None:
    if level not in LEVELS:
        raise ValueError(f"unknown regex level {level!r}")


def path(level: str, key: str = "") -> Path:
    """The file for `level`. `key` is the world id, campaign id or connection id.

    Raises `WorldNotFound`, `CampaignNotFound` or `ConnectionNotFound` for a key
    that does not name a child of its directory: the resolvers are what refuse
    `..` and separators, so none of these is joined by hand."""
    _check_level(level)
    if level == "global":
        return store_paths.home() / "regex.json"
    if level == "world":
        return worlds_paths.world_root(key) / "regex.json"
    if level == "campaign":
        return campaigns_paths.campaign_root(key) / "regex.json"
    if not store_paths.safe_id(key):
        raise llm_connections.ConnectionNotFound(key)
    return llm_connections.regex_path(key)


def world_of(cid: str) -> str:
    """The id of the world `cid` belongs to, or "" for a campaign that is not there."""
    try:
        return campaigns_read.read_campaign(cid)["meta"].get("world", "")
    except campaigns_paths.CampaignNotFound:
        return ""


def _connection_exists(cid: str) -> bool:
    try:
        llm_connections.read_connection_raw(cid)
    except llm_connections.ConnectionNotFound:
        return False
    return True


# --- reading ------------------------------------------------------------------

def _salvaged(raw, file: Path) -> dict:
    """`raw` as a normalised doc. A rule that fails costs only itself, and so
    does a repeat of an id already seen; a file whose shape is wrong as a whole
    raises `ValueError`, and `_parse` reads it as empty."""
    if not isinstance(raw, dict):
        raise ValueError("a rule file must be an object")
    raw_rules = raw.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ValueError("rules must be a list")
    kept: list[dict] = []
    seen: set[str] = set()
    for i, item in enumerate(raw_rules):
        try:
            rule = rules.normalise(item, index=i)
        except rules.RuleError as exc:
            log.warning("regex: dropping rule %d of %s -- %s", i, file, exc)
            continue
        if rule["id"] in seen:
            log.warning("regex: dropping rule %d of %s -- id %s is already used",
                        i, file, rule["id"])
            continue
        seen.add(rule["id"])
        kept.append(rule)
    off = raw.get("off", [])
    if not isinstance(off, list):
        off = []
    return {"rules": kept,
            "off": list(dict.fromkeys(o for o in off if isinstance(o, str)))}


def _parse(file: Path) -> dict:
    try:
        return _salvaged(json.loads(file.read_text(encoding="utf-8")), file)
    except (OSError, ValueError) as exc:  # JSONDecodeError and UnicodeDecodeError too
        log.error("regex: ignoring the rule file at %s -- %s", file, exc)
        return {"rules": [], "off": []}


def _load(level: str, key: str) -> dict:
    """The doc for one level, shared with the cache: callers must not mutate it.
    Never raises; a level whose key names nothing reads empty."""
    try:
        file = path(level, key)
        st = file.stat()
    except (worlds_paths.WorldNotFound, campaigns_paths.CampaignNotFound,
            llm_connections.ConnectionNotFound, OSError):
        return _EMPTY
    ident = (str(file), st.st_mtime_ns, st.st_size)
    doc = _cache.get(ident)
    if doc is None:
        doc = _parse(file)
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[ident] = doc
    return doc


def read_level(level: str, key: str = "") -> dict:
    """`{"rules": [...], "off": [...]}` for one level -- a copy, so it can be
    edited and handed to `write_level`. Never raises: a missing, unparseable or
    half-synced file reads empty (the unparseable one logged, once per version
    of the file), and a single bad rule is dropped and logged while the rest
    load."""
    _check_level(level)
    return copy.deepcopy(_load(level, key))


# --- validation and writing ---------------------------------------------------

def validate_doc(doc: dict, *, level: str, inherited_ids: set[str]) -> dict:
    """`doc` normalised, or `rules.RuleError` (`.index` is the rule's place,
    `.field` the key at fault). `inherited_ids` is every id the level sits on."""
    _check_level(level)
    if not isinstance(doc, dict):
        raise rules.RuleError("a rule file must be an object")
    raw_rules = doc.get("rules", [])
    if not isinstance(raw_rules, list):
        raise rules.RuleError("rules: must be a list", field="rules")
    clean: list[dict] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_rules):
        rule = rules.normalise(raw, index=i)
        if rule["id"] in seen:
            raise rules.RuleError(f"id: {rule['id']!r} is used twice", index=i, field="id")
        if rule["id"] in inherited_ids:
            raise rules.RuleError(f"id: {rule['id']!r} is already used by an inherited rule",
                                  index=i, field="id")
        seen.add(rule["id"])
        clean.append(rule)
    raw_off = doc.get("off", [])
    if not isinstance(raw_off, list) or not all(isinstance(o, str) for o in raw_off):
        raise rules.RuleError("off: must be a list of rule ids", field="off")
    off = list(dict.fromkeys(raw_off))
    if off and level in ("global", "connection"):
        raise rules.RuleError(f"off: a {level} file sits under nothing to switch off",
                              field="off")
    unknown = [o for o in off if o not in inherited_ids]
    if unknown:
        raise rules.RuleError(f"off: {unknown[0]!r} is not an inherited rule", field="off")
    return {"rules": clean, "off": off}


def _store(level: str, key: str, clean: dict) -> dict:
    file = path(level, key)
    file.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(file, json.dumps(clean, indent=2, ensure_ascii=False) + "\n")
    # The stat key would catch the change anyway; this is for a filesystem whose
    # clock is too coarse to tell two writes of one size apart.
    for ident in [i for i in _cache if i[0] == str(file)]:
        _cache.pop(ident, None)
    return clean


def write_level(level: str, key: str, doc: dict) -> dict:
    """Validate `doc` against what the level inherits, store it, and return the
    normalised doc. Raises `rules.RuleError`."""
    _check_level(level)
    if level == "campaign":
        return write_campaign(key, doc)
    if level == "connection" and not _connection_exists(key):
        raise llm_connections.ConnectionNotFound(key)
    ids = {e["rule"]["id"] for e in _stack(level, key, set())}
    return _store(level, key, validate_doc(doc, level=level, inherited_ids=ids))


def write_campaign(cid: str, doc: dict) -> dict:
    """`write_level("campaign", cid, doc)`. Reads the world's side of the stack,
    validates against it and writes, all under the campaign lock."""
    with locks.campaign_lock(cid):
        ids = {e["rule"]["id"] for e in _stack("campaign", cid, set())}
        return _store("campaign", cid, validate_doc(doc, level="campaign", inherited_ids=ids))


# --- layering -----------------------------------------------------------------

def _entries(level: str, key: str, off: set[str], *, source: str = "") -> list[Entry]:
    return [{"level": level, "rule": r, "off": r["id"] in off, "source": source}
            for r in _load(level, key)["rules"]]


def _connection_entries(off: set[str]) -> list[Entry]:
    out: list[Entry] = []
    for conn in llm_connections.list_connections():
        out += _entries("connection", conn["id"], off, source=conn["id"])
    return out


def _stack(level: str, key: str, off: set[str]) -> list[Entry]:
    """What `level` sits on, in run order, with `off` laid over it."""
    if level in ("global", "connection"):
        return []
    out = _connection_entries(off) + _entries("global", "", off)
    if level == "campaign":
        wid = world_of(key)
        if wid:
            out += _entries("world", wid, off)
    return out


def inherited(level: str, key: str = "") -> list[Entry]:
    """Every rule `level` sits on, in run order, `off` set from the level's own
    `off` list. A world sees every connection's rules and the global ones; a
    campaign also sees its world's. The global and connection levels sit on
    nothing."""
    _check_level(level)
    off = set(_load(level, key)["off"]) if level in ("world", "campaign") else set()
    return _stack(level, key, off)


def effective(*, cid: str | None, connection: str = "") -> list[Entry]:
    """The rules for one message, in run order: the producing connection's, then
    global, world, campaign. An entry the world or campaign switched off is
    kept, marked `off`, so the editor can show it struck through. A missing or
    deleted connection contributes nothing. Entries share their rules with the
    cache: read them, do not edit them."""
    wid = world_of(cid) if cid else ""
    world_off = set(_load("world", wid)["off"]) if wid else set()
    campaign_off = set(_load("campaign", cid)["off"]) if cid else set()
    # The world may switch off connection and global rules, the campaign those
    # and the world's.
    under_world = world_off | campaign_off
    out: list[Entry] = []
    if connection and _connection_exists(connection):
        out += _entries("connection", connection, under_world, source=connection)
    out += _entries("global", "", under_world)
    if wid:
        out += _entries("world", wid, campaign_off)
    if cid:
        out += _entries("campaign", cid, set())
    return out
