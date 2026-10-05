"""Sampler presets: named sets of sampling parameters, and where each applies.

A preset is `<home>/sampler_presets/<id>.json` -- `{name, params, notes,
source}` -- and supplies exactly the parameters it sets (`llm_sampling`'s rule,
and `response_presets`' before it). See
docs/superpowers/specs/2026-10-05-sampler-presets-design.md for the design and
for what its review changed.

Three things live here, and only one of them touches the disk:

- the store (`list_presets`, `read_preset`, `create_preset`, `update_preset`,
  `delete_preset`);
- `resolve`, the PURE cascade -- campaign route, global route, the serving
  connection's own preset, none -- whose impure inputs (`campaign.md`,
  `config.md`, the connection) the caller hands over, the split
  `routing.resolve` and `response_presets.resolve` already use;
- `from_sillytavern`, the import mapping.

Global, like `llm_connections/`: nothing here is campaign-scoped, so nothing
here takes a campaign lock.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from .. import llm_sampling
from . import atomic, config, routing
from .paths import home, natural_key, safe_id, slugify, uniquify

#: "No preset at this scope -- stop looking." Distinct from "" (no opinion,
#: keep walking). Prefixed with U+2063 for `response_presets.STYLE_CLEAR`'s
#: reason: `slugify` reserves nothing, so a preset genuinely named "None" slugs
#: to `none`, and a bare-"none" sentinel would clear instead of applying it.
PRESET_CLEAR = "⁣none"

#: Where a resolved answer came from. `campaign` and `global` are about the
#: ROUTE and follow it onto whichever connection serves it (`llm.ROUTE_SCOPES`);
#: `connection` stays with its connection; `none` is provider defaults.
SCOPES = ("campaign", "global", "connection", "none")

NAME_MAX = 120
NOTES_MAX = 4000


class PresetNotFoundError(Exception):
    pass


def _dir() -> Path:
    return home() / "sampler_presets"


def _path(pid: str) -> Path:
    return _dir() / f"{pid}.json"


def read_preset(pid: str) -> dict | None:
    """One preset, or None for an unsafe id, a missing file, or one that is not
    a preset at all.

    `params` is returned as STORED, not re-validated: a hand edit that put one
    bad value in costs that one parameter (`llm_sampling.split` reports it as
    dropped) rather than the whole preset vanishing from every route it is on.
    Only a file with no usable shape at all reads as missing.
    """
    if not safe_id(pid):
        return None
    try:
        p = _path(pid)
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    params = data.get("params")
    name = data.get("name")
    return {"id": pid,
            "name": name if isinstance(name, str) and name.strip() else pid,
            "params": {k: v for k, v in params.items() if k in llm_sampling.NAMES}
            if isinstance(params, dict) else {},
            "notes": data.get("notes") if isinstance(data.get("notes"), str) else "",
            "source": data.get("source") if isinstance(data.get("source"), str) else ""}


def exists(pid: str) -> bool:
    return read_preset(pid) is not None


def list_presets() -> list[dict]:
    """Every readable preset, by name."""
    d = _dir()
    if not d.exists():
        return []
    out = [got for got in (read_preset(p.stem) for p in d.glob("*.json")) if got is not None]
    return sorted(out, key=lambda r: (natural_key(r["name"]), r["id"]))


def _clean(name: object, params: object, notes: object) -> tuple[str, dict, str]:
    """The three writable fields, checked. ValueError says which is wrong."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("a preset needs a name")
    if len(name.strip()) > NAME_MAX:
        raise ValueError(f"a preset name is at most {NAME_MAX} characters")
    if notes is None:
        notes = ""
    if not isinstance(notes, str) or len(notes) > NOTES_MAX:
        raise ValueError(f"notes must be text of at most {NOTES_MAX} characters")
    return name.strip(), llm_sampling.validate(params if params is not None else {}), notes


def _write(pid: str, name: str, params: dict, notes: str, source: str) -> None:
    _dir().mkdir(parents=True, exist_ok=True)
    payload = {"name": name, "params": params, "notes": notes, "source": source}
    atomic.write_text(_path(pid), json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def create_preset(name: str, params: dict | None = None, notes: str = "",
                  source: str = "") -> str:
    """Store a new preset and return its id. ValueError for a bad field."""
    name, params, notes = _clean(name, params, notes)
    pid = uniquify(slugify(name), lambda c: _path(c).exists())
    _write(pid, name, params, notes, source)
    return pid


def update_preset(pid: str, name: str, params: dict | None, notes: str = "") -> None:
    """Replace a preset's name, parameters and notes. `source` is kept: it says
    where the preset came FROM, which an edit does not change."""
    current = read_preset(pid)
    if current is None:
        raise PresetNotFoundError(pid)
    name, params, notes = _clean(name, params, notes)
    _write(pid, name, params, notes, current["source"])


def delete_preset(pid: str) -> None:
    """Remove a preset, clearing the global route keys that named it first.

    Deliberately NOT swept: campaign keys (every campaign.md, under every
    campaign's lock, for a reference resolution already walks past) and
    connections' `sampler_preset`. A connection file rewrite mints a new
    `rev`, which empties its cached model catalog and resets its health
    verdict -- deleting a preset must not do that to every connection that
    used it. A dangling reference there reads as "no opinion", like a dangling
    campaign one.

    Config cleared BEFORE the unlink, `llm_connections.delete_connection`'s
    ordering and reason: a failure between the two then leaves a preset
    nothing names, never a name with no preset.
    """
    if not safe_id(pid) or not _path(pid).exists():
        raise PresetNotFoundError(pid)
    cfg = config.read_config()
    dangling = {key: "" for key in routing.PRESET_CONFIG_KEYS if cfg.get(key) == pid}
    if dangling:
        config.write_config(**dangling)
    _path(pid).unlink()


# ---- resolution (pure) ----

def _opinion(meta: dict, key: str, known: Callable[[str], bool]) -> str:
    """What a scope says: a preset id, `PRESET_CLEAR`, or "" for no opinion --
    which an absent key, a blank one and a dangling id all are."""
    value = str(meta.get(key, "") or "").strip()
    if value == PRESET_CLEAR:
        return PRESET_CLEAR
    return value if value and known(value) else ""


def resolve(task: str, *, campaign_meta: dict, cfg: dict, conn: dict | None,
            known: Callable[[str], bool]) -> dict:
    """Which preset `task` runs with on `conn`: `{preset_id, scope}`.

    `preset_id` is "" for none -- provider defaults, whether because a scope
    cleared it (scope names that scope) or because nothing anywhere set one
    (scope `none`). A task no route claims skips straight to the connection.
    """
    got = routing.route(task)
    if got is not None:
        key = routing.preset_key(got.key)
        scopes = ([("campaign", campaign_meta)] if got.campaign_scoped else []) \
            + [("global", cfg)]
        for scope, meta in scopes:
            chosen = _opinion(meta, key, known)
            if chosen == PRESET_CLEAR:
                return {"preset_id": "", "scope": scope}
            if chosen:
                return {"preset_id": chosen, "scope": scope}
    own = str((conn or {}).get("sampler_preset", "") or "").strip()
    if own and known(own):
        return {"preset_id": own, "scope": "connection"}
    return {"preset_id": "", "scope": "none"}


def scope_values(scope: str, *, campaign_meta: dict, cfg: dict) -> dict[str, str]:
    """What THIS scope says per route, raw -- the picker's selected values."""
    own = campaign_meta if scope == "campaign" else cfg
    return {r.key: str(own.get(routing.preset_key(r.key), "") or "")
            for r in routing.routes_for(scope)}


def inherited(scope: str, route_key: str, *, campaign_meta: dict, cfg: dict,
              conn: dict | None, known: Callable[[str], bool]) -> dict:
    """What `route_key` would resolve to if THIS scope said nothing -- the only
    honest label for an "inherit" option (`routing.bundle`'s reasoning)."""
    key = routing.preset_key(route_key)
    task = routing.route_by_key(route_key).tasks[0]
    if scope == "campaign":
        campaign_meta = {k: v for k, v in campaign_meta.items() if k != key}
    else:
        cfg = {k: v for k, v in cfg.items() if k != key}
    return resolve(task, campaign_meta=campaign_meta, cfg=cfg, conn=conn, known=known)


def refused(scope: str, fields) -> list[str]:
    """The `preset_*` keys in `fields` this scope may not set."""
    allowed = {routing.preset_key(r.key) for r in routing.routes_for(scope)}
    return [f for f in fields if f not in allowed]


# ---- SillyTavern import ----

#: grimoire param -> the SillyTavern keys that carry it, first present wins.
#: Chat Completion presets use the OpenAI spellings; Text Completion and
#: KoboldAI presets use the short ones.
ST_KEYS: dict[str, tuple[str, ...]] = {
    "temperature": ("temperature", "temp"),
    "top_p": ("top_p",),
    "top_k": ("top_k",),
    "min_p": ("min_p",),
    "repetition_penalty": ("repetition_penalty", "rep_pen"),
    "frequency_penalty": ("frequency_penalty", "freq_pen"),
    "presence_penalty": ("presence_penalty", "presence_pen"),
    "max_tokens": ("openai_max_tokens", "max_tokens", "genamt", "amount_gen"),
    "stop": ("stop", "stopping_strings", "custom_stopping_strings"),
}

#: Each sampler's off position. ST writes every field into every file, so a
#: value here is ST saying "not in use" -- stored, it would make every import
#: report `top_k` dropped on a standard endpoint for a value that does nothing.
NEUTRAL: dict[str, object] = {"top_k": 0, "min_p": 0, "repetition_penalty": 1,
                              "frequency_penalty": 0, "presence_penalty": 0, "top_p": 1}

#: Unmapped keys that change what a MAPPED value means, listed first.
ORDER_KEYS = ("temperature_last", "sampler_order", "sampler_priority")
ORDER_NOTE = ("the order samplers run in is not carried over, and it changes what "
              "a min-p or temperature value does")


def _stop_list(value: object) -> object:
    """ST stores custom stop strings as a JSON-encoded list in a string."""
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            return [value] if value else []
        return decoded
    return value


def _without_macros(value: object, key: str, report: dict) -> object:
    """A stop list minus its `{{…}}` macro strings, each reported as invalid:
    SillyTavern expands those per chat, and stored as text they never match."""
    if not isinstance(value, list):
        return value
    kept = []
    for item in value:
        if isinstance(item, str) and "{{" in item and "}}" in item:
            report["invalid"].append({
                "key": key, "why": f"stop string {item!r} uses a SillyTavern "
                                   "macro, which grimoire does not expand"})
        else:
            kept.append(item)
    return kept


#: Why ST's response length is not imported unless asked for.
MAX_TOKENS_WHY = ("response length caps every call the preset reaches, absorb "
                  "included; import it only on purpose")


def _map_one(name: str, key: str, value: object, include_max_tokens: bool,
             params: dict, report: dict) -> None:
    """File one SillyTavern value under exactly one of the report's lists, and
    into `params` when it is stored."""
    if name == "stop":
        value = _without_macros(_stop_list(value), key, report)
        if value == []:
            report["neutral"].append({"param": name, "from": key, "value": []})
            return
    if name == "max_tokens" and not include_max_tokens:
        report["skipped"].append({"param": name, "from": key, "value": value,
                                  "why": MAX_TOKENS_WHY})
        return
    if name in NEUTRAL and not isinstance(value, bool) and value == NEUTRAL[name]:
        report["neutral"].append({"param": name, "from": key, "value": value})
        return
    try:
        checked = llm_sampling.validate({name: value})[name]
    except ValueError as exc:
        report["invalid"].append({"key": key, "why": str(exc)})
        return
    params[name] = checked
    report["mapped"].append({"param": name, "from": key, "value": checked})


def from_sillytavern(data: object, include_max_tokens: bool = False) -> tuple[dict, dict]:
    """A SillyTavern preset as `(params, report)`.

    `report` has five lists -- `mapped`, `neutral`, `skipped`, `invalid`,
    `unmapped` -- plus `notes`. ValueError for anything that is not a JSON
    object, which is the only thing that stops an import: a file that maps
    nothing still makes a valid (empty) preset, and the report says so.
    """
    if not isinstance(data, dict):
        raise ValueError("a SillyTavern preset is a JSON object")
    params: dict = {}
    report: dict = {"mapped": [], "neutral": [], "skipped": [], "invalid": [],
                    "unmapped": [], "notes": []}
    used: set[str] = set()
    for name, keys in ST_KEYS.items():
        present = [k for k in keys if k in data]
        used.update(present)
        if present:
            _map_one(name, present[0], data[present[0]], include_max_tokens, params, report)
    rest = sorted(k for k in data if k not in used)
    report["unmapped"] = [k for k in ORDER_KEYS if k in rest] + \
                         [k for k in rest if k not in ORDER_KEYS]
    if any(k in rest for k in ORDER_KEYS):
        report["notes"].append(ORDER_NOTE)
    if not params:
        report["notes"].append("nothing in this file maps to a grimoire sampler "
                               "parameter; the preset was saved empty")
    return llm_sampling.validate(params), report
