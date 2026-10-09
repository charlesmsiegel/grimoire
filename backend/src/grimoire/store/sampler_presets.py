"""Sampler presets: named sets of sampling parameters, and where each applies.

A preset is `<home>/sampler_presets/<id>.json` -- `{name, params, notes,
source}` -- and supplies exactly the parameters it sets (`llm_sampling`'s rule,
and `response_presets`' before it). See
docs/superpowers/specs/2026-10-05-sampler-presets-design.md for the design and
for what its review changed.

Two things live here:

- the store (`list_presets`, `read_preset`, `create_preset`, `update_preset`,
  `delete_preset`);
- `from_sillytavern`, the import mapping.

Which preset a call runs with is the inference cascade's answer
(`store.inference.cascade.preset_for`: campaign route, global route, the
selection's own preset, none); the legacy cascade that lived here was retired
in inference slice C.

Global, like `llm_connections/`: nothing here is campaign-scoped, so nothing
here takes a campaign lock.
"""

from __future__ import annotations

import errno
import json
from pathlib import Path

from .. import llm_sampling
from . import atomic, config, frontmatter, inference_keys, routing
from .paths import home, natural_key, safe_id, slugify, uniquify

#: "No preset at this scope -- stop looking." Distinct from "" (no opinion,
#: keep walking). Prefixed with U+2063 for `response_presets.STYLE_CLEAR`'s
#: reason: `slugify` reserves nothing, so a preset genuinely named "None" slugs
#: to `none`, and a bare-"none" sentinel would clear instead of applying it.
PRESET_CLEAR = "⁣none"

#: Where a resolved answer came from. `campaign` and `global` are about the
#: ROUTE and follow it onto whichever connection serves it (`llm.ROUTE_SCOPES`);
#: `connection` stays with its connection; `override` is a reroll's own preset
#: and stays with the primary it was named for; `none` is provider defaults.
SCOPES = ("campaign", "global", "connection", "override", "none")

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
    return _shaped(pid, data)


def _shaped(pid: str, data: dict) -> dict:
    """A preset file's object as `read_preset` answers it."""
    params = data.get("params")
    name = data.get("name")
    return {"id": pid,
            "name": name if isinstance(name, str) and name.strip() else pid,
            "params": {k: v for k, v in params.items() if k in llm_sampling.CONTROLS}
            if isinstance(params, dict) else {},
            "notes": data.get("notes") if isinstance(data.get("notes"), str) else "",
            "source": data.get("source") if isinstance(data.get("source"), str) else ""}


def read_preset_strict(pid: str) -> dict | None:
    """`read_preset`, for a reader whose answer is WRITTEN down (retirement's
    derived presets, slice I): None only when no preset by that id can exist
    (an unsafe id) or none does (no file). A file that is there but holds no
    preset -- empty, not JSON, not an object, `params` that are not an
    object -- raises `frontmatter.RecordUnreadableError`, and the read's own
    `OSError` / `UnicodeDecodeError` pass through: a derived preset built
    from a base a sync client was holding would be saved without the base's
    samplers, for good.

    The shape is `read_preset`'s, params filtered the same way, so a plan
    read through either agrees on every preset both can read."""
    if not safe_id(pid):
        return None
    p = _path(pid)
    try:
        if not p.exists():
            return None
    except OSError as exc:
        # `read_preset`'s reason: a name too long for the filesystem is a
        # preset that does not exist, whichever way it is read.
        if getattr(exc, "errno", None) == errno.ENAMETOOLONG:
            return None
        raise
    text = p.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise frontmatter.RecordUnreadableError(
            f"sampler preset {pid} holds no preset ({exc})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("params", {}), dict):
        raise frontmatter.RecordUnreadableError(f"sampler preset {pid} holds no preset")
    return _shaped(pid, data)


def put_derived(pid: str, name: str, params: dict) -> None:
    """Write a derived reasoning preset (retirement, slice I ruling 4) at
    `pid`: written when absent, nothing when the file already holds this very
    preset (same name, same params), and `ValueError` when it holds anything
    else -- which `legacy_plan.derive`'s digest suffix makes unreachable,
    since it picks an id only when that id is free or holds this preset.

    `params` are written as given, not re-validated: they are the base
    preset's params as STORED plus the effort, and a value `validate` would
    refuse costs that one parameter on the wire (`read_preset`'s rule) --
    refusing it here would cost the whole derivation instead. Notes and
    source are empty, as the planner's in-memory copy has them, so two
    devices write the same bytes.

    In `config.format_hold`, as every preset write is (N19): the read, the
    comparison and the write are one hold. A file that cannot be read is
    never written over (`read_preset_strict`)."""
    if not safe_id(pid):
        raise ValueError(f"not a preset id: {pid!r}")
    with config.format_hold():
        current = read_preset_strict(pid)
        if current is not None:
            if (current["name"], current["params"]) == (name, params):
                return
            raise ValueError(f"sampler preset {pid} already holds another preset")
        _write(pid, name, dict(params), "", "")


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
    # In `config.format_hold`: a preset is a model setting, refused on a store
    # a newer build switched (`config.NewerFormatError`).
    with config.format_hold():
        pid = uniquify(slugify(name), lambda c: _path(c).exists())
        _write(pid, name, params, notes, source)
    return pid


def update_preset(pid: str, name: str, params: dict | None, notes: str = "") -> None:
    """Replace a preset's name, parameters and notes. `source` is kept: it says
    where the preset came FROM, which an edit does not change."""
    name, params, notes = _clean(name, params, notes)
    with config.format_hold():
        current = read_preset(pid)
        if current is None:
            raise PresetNotFoundError(pid)
        _write(pid, name, params, notes, current["source"])


def delete_preset(pid: str) -> None:
    """Remove a preset, clearing the global keys that named it first: route
    presets, and the preset part of a role, a fallback or a pin.

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
    if not safe_id(pid):
        raise PresetNotFoundError(pid)
    # Checked, read, cleared AND unlinked in one hold (cross-process): a role
    # written meanwhile is not merged over by a sweep computed before it, a
    # settings write that checks the preset still exists in this lock's hold
    # (`inference.settings._still_there`) cannot see it between the sweep and
    # the unlink and name it again, and another server's delete landing after
    # the existence check cannot make the unlink fail.
    with config.format_hold():
        try:
            # Inside a try for `read_preset`'s reason: an id longer than the
            # filesystem's NAME_MAX raises ENAMETOOLONG from the stat itself,
            # and a name the filesystem cannot hold is a preset that does not
            # exist.
            present = _path(pid).exists()
        except OSError:
            present = False
        if not present:
            raise PresetNotFoundError(pid)
        dangling = _dangling(config.read_config(), pid)
        if dangling:
            config.write_config(**dangling)
        _path(pid).unlink()


def _dangling(cfg: dict, pid: str) -> dict[str, str]:
    """Every global key naming preset `pid`, cleared: `{key: ""}`. The route
    presets (the legacy routes' and every new route's -- one spelling in both
    layouts), and the preset part of each generative role, each role's
    fallback and each route's pin (spec 11.3) -- the part alone: the provider
    and model it rode with are still a selection, and with no preset it
    samples as its provider's own."""
    named = {*routing.PRESET_CONFIG_KEYS,
             *(inference_keys.preset_key(r.key) for r in routing.ROUTES)}
    named.update(inference_keys.role_key(r, "preset") for r in inference_keys.GENERATIVE_ROLES)
    named.update(inference_keys.fallback_key(r, "preset")
                 for r in inference_keys.GENERATIVE_ROLES)
    named.update(inference_keys.pin_key(r.key, "preset") for r in routing.ROUTES)
    return {key: "" for key in sorted(named)
            if str(cfg.get(key, "") or "").strip() == pid}


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
        decoded = _stop_list(value)
        value = _without_macros(decoded, key, report)
        if value == []:
            # Neutral only when the FILE's list was empty. One emptied by
            # removing macros has had every entry reported as invalid already,
            # and calling it the off position as well would file one input
            # under two contradictory lists.
            if decoded == []:
                report["neutral"].append({"param": name, "from": key, "value": []})
            return
    if name == "max_tokens" and not include_max_tokens:
        report["skipped"].append({"param": name, "from": key, "value": value,
                                  "why": MAX_TOKENS_WHY})
        return
    # -1 is top-k's other "off" (llama.cpp, vLLM).
    if name in NEUTRAL and not isinstance(value, bool) and (
            value == NEUTRAL[name] or (name == "top_k" and value == -1)):
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

    Only the nine sampler parameters are mapped (`ST_KEYS`). A reasoning field
    is not: SillyTavern's names a provider's own levels, not grimoire's
    provider-neutral `reasoning_effort`, so it is listed as unmapped like any
    other key this import does not carry.

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
    shadowed: list[str] = []
    for name, keys in ST_KEYS.items():
        present = [k for k in keys if k in data]
        if present:
            used.add(present[0])
            shadowed += [f"{k} (unused: {present[0]} carries {name})" for k in present[1:]]
            _map_one(name, present[0], data[present[0]], include_max_tokens, params, report)
    shadow_keys = {entry.split(" ", 1)[0] for entry in shadowed}
    rest = sorted(k for k in data if k not in used and k not in shadow_keys)
    report["unmapped"] = [k for k in ORDER_KEYS if k in rest] + \
                         [k for k in rest if k not in ORDER_KEYS]
    # A second spelling of a parameter already taken is listed too, with what
    # took it: dropping it in silence is exactly what the report is for.
    report["unmapped"] += shadowed
    if any(k in rest for k in ORDER_KEYS):
        report["notes"].append(ORDER_NOTE)
    if not params:
        report["notes"].append("nothing in this file maps to a grimoire sampler "
                               "parameter; the preset was saved empty")
    return llm_sampling.validate(params), report
