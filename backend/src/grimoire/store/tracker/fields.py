"""The scene state tracker's field definitions, and how they layer.

The effective field set for a scene is built in order: the built-ins below
(code, not editable) -> the world's `tracker.json` -> the campaign's
`tracker.json` -> the scene's own layer. The world and campaign layers may
**add** a field, **change** properties of an inherited one, or **switch one
off**. The scene layer may only switch off or add a scene-only field -- it may
not redefine an inherited one, because changing an enum's options or a field's
type mid-scene would invalidate the snapshots already stored against it. A
switched-off field is kept in the list, marked `"off": True`, so values already
stored for it still have a label to render under; `active` drops them for the
callers (prompts, the update call) that must not see them.

A layer is validated against the list it sits on, so a campaign cannot add a key
its world already defined. Reading is the opposite: `read_layer` never raises
and treats anything it cannot trust as empty, because a hand-edited or
half-synced file must not take the play view down with it.

Writers: the world layer is not campaign-scoped, so it takes no campaign lock
and relies on `atomic.write_text`; the campaign and scene layers hold the
campaign lock like every other campaign-scoped write.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from .. import atomic, locks
from ..campaigns import read as campaigns_read
from ..scenes import identity as scenes_identity
from ..worlds import paths as worlds_paths
from . import paths

VISIBLE_MOODS = ("admiration", "amusement", "anger", "annoyance", "approval", "caring",
    "confusion", "curiosity", "desire", "disappointment", "disapproval", "disgust",
    "embarrassment", "excitement", "fear", "gratitude", "grief", "joy", "love",
    "nervousness", "neutral", "optimism", "pride", "realization", "relief", "remorse",
    "sadness", "surprise")
"""The 28 SillyTavern Character Expressions labels, so expression sprite packs
map onto `visible_mood` directly."""

DEFAULT_FIELDS = (
  {"key": "clothing", "label": "Clothing", "type": "text", "aware": "present",
   "hint": "What they are wearing right now, briefly. Change it only when the post shows clothing put on, taken off or damaged."},
  {"key": "position", "label": "Position", "type": "text", "aware": "present",
   "hint": "Where they are in the space, relative to people and things."},
  {"key": "pose", "label": "Pose", "type": "text", "aware": "present",
   "hint": "Body posture right now."},
  {"key": "holding", "label": "Holding", "type": "text", "aware": "present",
   "hint": "What is in their hands right now; empty when nothing."},
  {"key": "condition", "label": "Condition", "type": "list", "aware": "present",
   "hint": "Visible physical states, one short item each (soaked, bleeding, limping, flushed, exhausted). Remove an item when it stops being true."},
  {"key": "visible_mood", "label": "Visible mood", "type": "enum", "aware": "present",
   "options": list(VISIBLE_MOODS),
   "hint": "The demeanour others can read from face, voice and body right now."},
  {"key": "true_mood", "label": "True mood", "type": "text", "aware": "self",
   "hint": "What they actually feel, including toward others present."},
  {"key": "intent", "label": "Intent", "type": "text", "aware": "self",
   "hint": "What they are trying to get out of this scene right now."},
  {"key": "concealed", "label": "Concealed", "type": "text", "aware": "self",
   "hint": "Something on them or about them that others do not know."},
  {"key": "attention", "label": "Attention", "type": "text", "aware": "present",
   "hint": "Who or what they are focused on right now."},
)

TYPES = ("text", "list", "enum")
AWARE = ("present", "self")
_KEY = re.compile(r"\A[a-z][a-z0-9_]{0,31}\Z")
_PROPS = ("label", "type", "aware", "hint", "options")
"""What a field may carry besides its `key`, and so what `change` may touch."""


class FieldLayerError(ValueError):
    """A layer that cannot be stored. The message is shown to the person who
    wrote it, so it names the field and the rule."""


# --- validation ---------------------------------------------------------------

def _check_key(key) -> str:
    if not isinstance(key, str) or not _KEY.match(key):
        raise FieldLayerError(
            f"field key {key!r} must be lowercase letters, digits and underscores, "
            "starting with a letter, at most 32 characters")
    return key


def _check_label(key: str, label) -> str:
    if not isinstance(label, str) or not label.strip():
        raise FieldLayerError(f"field {key!r}: label must be a non-empty string")
    return label.strip()


def _check_type(key: str, value) -> str:
    if value not in TYPES:
        raise FieldLayerError(f"field {key!r}: type must be one of {', '.join(TYPES)}")
    return value


def _check_aware(key: str, value) -> str:
    if value not in AWARE:
        raise FieldLayerError(f"field {key!r}: aware must be one of {', '.join(AWARE)}")
    return value


def _check_hint(key: str, value) -> str:
    if not isinstance(value, str):
        raise FieldLayerError(f"field {key!r}: hint must be a string")
    return value


def _check_options(key: str, options) -> list[str]:
    if (not isinstance(options, list)
            or not all(isinstance(o, str) and o.strip() for o in options)):
        raise FieldLayerError(f"field {key!r}: options must be a list of non-empty strings")
    return [o.strip() for o in options]


_CHECKS = {"label": _check_label, "type": _check_type, "aware": _check_aware,
           "hint": _check_hint, "options": _check_options}


def _check_props(key: str, props: dict) -> dict:
    """The properties of one field (or of a partial change), normalized.

    Only the properties that are present are checked, so the same function
    serves a whole definition and a `change`."""
    return {name: check(key, props[name]) for name, check in _CHECKS.items() if name in props}


def _check_enum(key: str, field: dict) -> None:
    """`options` is required for an enum and forbidden otherwise."""
    if field.get("type") == "enum":
        if not field.get("options"):
            raise FieldLayerError(f"field {key!r}: an enum needs a non-empty options list")
    elif field.get("options") is not None:
        raise FieldLayerError(f"field {key!r}: only an enum may have options")


def _normalize_field(raw) -> dict:
    if not isinstance(raw, dict):
        raise FieldLayerError("each field must be an object")
    key = _check_key(raw.get("key"))
    for need in ("label", "type", "aware"):
        if need not in raw:
            raise FieldLayerError(f"field {key!r}: {need} is required")
    out = {"key": key, **_check_props(key, raw)}
    out.setdefault("hint", "")
    _check_enum(key, out)
    return out


def _validate_added(raw_fields, existing: dict) -> list[dict]:
    added, seen = [], set()
    for raw in raw_fields or []:
        field = _normalize_field(raw)
        if field["key"] in existing or field["key"] in seen:
            raise FieldLayerError(f"field {field['key']!r} already exists")
        seen.add(field["key"])
        added.append(field)
    return added


def _validate_change(raw_change, existing: dict, *, scene: bool) -> dict:
    raw_change = raw_change or {}
    if not isinstance(raw_change, dict):
        raise FieldLayerError("change must be an object of key -> properties")
    if scene and raw_change:
        raise FieldLayerError("a scene may switch fields off or add its own, "
                              "not change an inherited one")
    change: dict = {}
    for key, props in raw_change.items():
        _check_key(key)
        if not isinstance(props, dict):
            raise FieldLayerError(f"field {key!r}: a change must be an object")
        if "key" in props:
            raise FieldLayerError(f"field {key!r}: a field's key cannot be changed")
        unknown = set(props) - set(_PROPS)
        if unknown:
            raise FieldLayerError(f"field {key!r}: cannot change {min(unknown)!r}")
        clean = _check_props(key, props)
        if key in existing:
            _check_enum(key, _merge(existing[key], clean))
        change[key] = clean
    return change


def _validate_off(raw_off) -> list[str]:
    raw_off = raw_off or []
    if not isinstance(raw_off, list):
        raise FieldLayerError("off must be a list of field keys")
    off: list[str] = []
    for key in raw_off:
        _check_key(key)
        if key not in off:
            off.append(key)
    return off


def validate_layer(layer: dict, *, scene: bool = False,
                   base: list[dict] | tuple[dict, ...] | None = None) -> dict:
    """The layer normalized to `{"version": 1, "fields", "change", "off"}`, or
    `FieldLayerError`.

    `base` is the list the layer sits on (default: the built-ins). A layer may
    not add a key already in it, and a change is checked against the field it
    lands on, so "enum with no options" is caught whichever property was edited.
    A `change` or `off` naming a key the base lacks is accepted and later
    ignored: the layer above can lose a field it had changed (the world dropped
    it), and that must not make the layer below unsaveable.

    `scene=True` is the scene layer's rule: it may switch off or add, never
    redefine, because snapshots already stored would stop matching.
    """
    if not isinstance(layer, dict):
        raise FieldLayerError("a layer must be an object")
    if layer.get("version", 1) != 1:
        raise FieldLayerError("unsupported layer version")
    existing = {f["key"]: f for f in (DEFAULT_FIELDS if base is None else base)}
    return {"version": 1,
            "fields": _validate_added(layer.get("fields"), existing),
            "change": _validate_change(layer.get("change"), existing, scene=scene),
            "off": _validate_off(layer.get("off"))}


# --- layering -----------------------------------------------------------------

def _merge(field: dict, props: dict) -> dict:
    """`field` with `props` laid over it. Leaving `enum` drops the options it no
    longer has a use for, so a type change alone is a complete edit."""
    merged = {**copy.deepcopy(field), **copy.deepcopy(props)}
    if merged.get("type") != "enum":
        merged.pop("options", None)
    return merged


def apply_layer(base: list[dict], layer: dict) -> list[dict]:
    """`base` with `layer` applied: changes merged, switched-off fields kept but
    marked `"off": True` (stored values still need their labels), additions
    appended. Never mutates `base`.

    An addition whose key `base` already has is skipped rather than replacing it:
    the layer was valid when written, so this is a world that grew the key
    afterwards, and replacing would retype a field under snapshots already
    stored."""
    change, off = layer.get("change") or {}, set(layer.get("off") or [])
    out = []
    for original in base:
        field = _merge(original, change.get(original["key"], {}))
        if field["key"] in off:
            field["off"] = True
        out.append(field)
    have = {f["key"] for f in out}
    for field in layer.get("fields") or []:
        if field["key"] not in have:
            out.append(copy.deepcopy(field))
            have.add(field["key"])
    return out


def active(fields: list[dict]) -> list[dict]:
    """The fields not switched off: what prompts and the update call may see."""
    return [f for f in fields if not f.get("off")]


def digest(fields: list[dict]) -> str:
    """A short fingerprint of a field set, to tell whether it moved."""
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode("utf-8")).hexdigest()[:16]


# --- reading ------------------------------------------------------------------

def read_layer(path: Path, *, scene: bool = False) -> dict:
    """The stored layer at `path`, normalized; `{}` for a missing, unreadable,
    garbled or invalid file. Never raises: a hand-edited or half-synced file
    must not take the play view down, and an empty layer is the safe reading.

    `scene` applies the scene rule on the way in too, so a scene file hand-edited
    to carry a `change` cannot redefine a field."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return validate_layer(raw, scene=scene)
    except (OSError, ValueError):       # FieldLayerError and JSONDecodeError are ValueErrors
        return {}


def world_layer(wid: str) -> dict:
    try:
        return read_layer(paths.world_layer_path(wid))
    except worlds_paths.WorldNotFound:
        return {}


def world_fields(wid: str) -> list[dict]:
    return apply_layer([copy.deepcopy(f) for f in DEFAULT_FIELDS], world_layer(wid))


def campaign_layer(cid: str) -> dict:
    return read_layer(paths.campaign_layer_path(cid))


def campaign_fields(cid: str) -> list[dict]:
    wid = campaigns_read.read_campaign(cid)["meta"].get("world") or ""
    return apply_layer(world_fields(wid), campaign_layer(cid))


def scene_layer(cid: str, sid: str) -> dict:
    """The scene's layer; `{}` when the scene has no identity yet (nothing has
    ever been stored for it) or none was written."""
    ident = scenes_identity.scene_identity(cid, sid)
    if ident is None:
        return {}
    return read_layer(paths.scene_layer_path(cid, ident), scene=True)


def effective(cid: str, sid: str) -> list[dict]:
    """Every field in force for a scene, switched-off ones marked."""
    return apply_layer(campaign_fields(cid), scene_layer(cid, sid))


# --- writing ------------------------------------------------------------------

def _write(path: Path, layer: dict) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, json.dumps(layer, indent=2, ensure_ascii=False) + "\n")
    return layer


def write_world_layer(wid: str, layer: dict) -> dict:
    """Validate against the built-ins and store. No campaign lock: a world is not
    campaign-scoped, and `atomic.write_text` is what keeps the file whole."""
    path = paths.world_layer_path(wid)
    if not worlds_paths.world_exists(wid):
        # `_write` makes parent directories, which would otherwise conjure a
        # world directory out of an id nobody created.
        raise worlds_paths.WorldNotFound(wid)
    return _write(path, validate_layer(layer))


def write_campaign_layer(cid: str, layer: dict) -> dict:
    path = paths.campaign_layer_path(cid)
    with locks.campaign_lock(cid):
        wid = campaigns_read.read_campaign(cid)["meta"].get("world") or ""
        return _write(path, validate_layer(layer, base=world_fields(wid)))


def write_scene_layer(cid: str, sid: str, layer: dict) -> dict:
    """Store a scene's layer, minting the scene's identity if it has none (the
    layer lives under it). Raises `SceneNotFound` for a scene that is not there."""
    with locks.campaign_lock(cid):
        ident = scenes_identity.ensure_identity(cid, sid)
        clean = validate_layer(layer, scene=True, base=campaign_fields(cid))
        return _write(paths.scene_layer_path(cid, ident), clean)
