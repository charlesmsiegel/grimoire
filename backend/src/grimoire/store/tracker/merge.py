"""Folding the tracker model's reply into a snapshot, and a user's hand edit.

Pure: dicts in, dicts out, no store and no locks. The caller (the update
pipeline) holds whatever lock covers the write.

Two entry points, **deliberately opposite in how they treat a bad value**:

- `apply_reply` takes what a model said, and a model says things that do not
  fit -- a mood outside the options, a field nobody defined, a name that is
  not in the room. One bad entry must not cost the good ones next to it, and
  there is nobody to show an error to, so invalid entries are *dropped*.
- `apply_edit` takes what a person typed into the editor. They can be told,
  and a silently ignored edit looks like the app lost it, so an invalid edit
  *raises* `ValueError` and the whole request is refused.

Snapshot shape (`records.py` stores it verbatim)::

    {"characters:mara": {"present": True,
                         "fields": {"pose": {"value": "kneeling",
                                             "aware": "present"},
                                    "concealed": {"value": "a letter",
                                                  "aware": [],
                                                  "set_by": "user"}}}}

`aware` is `"present"` (everyone in the scene can see it) or a list of refs
that know it besides the owner, empty for a field only its owner knows.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable

from . import fields as field_defs

MAX_TEXT = 200
"""Characters kept of a text value, and of each list item. The tracker is a
sketch of the moment rather than prose, and its values are re-rendered into
every later prompt, so a model that writes a paragraph into `pose` is cut off
instead of being allowed to grow the prompt."""

_FENCE = re.compile(r"\A```[A-Za-z0-9_-]*\s*\n?(.*?)\n?\s*```\Z", re.DOTALL)


class TrackerReplyError(ValueError):
    """The reply held no usable JSON object."""


# --- parsing ------------------------------------------------------------------

def _balanced_end(text: str, start: int) -> int | None:
    """Index just past the `}` closing the `{` at `start`, or `None`. Braces
    inside a JSON string do not count, which is why this is not `find("}")`:
    a value like `"a } b"` would otherwise end the object early."""
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def _first_object(body: str) -> dict | None:
    """The first balanced `{...}` in `body` that parses to an object carrying
    `changes` or `awareness`. Prose may contain a stray balanced brace before
    the real object, so a span that does not parse, or is not a reply, is
    skipped.

    An *unbalanced* `{` ends the search instead: that is a reply cut off by the
    token limit, and descending into it would find the inner `{"Mara": {...}}`
    -- balanced, parseable, and not a reply -- and report a clean empty update
    where the changes were lost. The key requirement is the second line of the
    same defence."""
    pos = body.find("{")
    while pos != -1:
        end = _balanced_end(body, pos)
        if end is None:
            return None
        try:
            candidate = json.loads(body[pos:end])
        except ValueError:
            candidate = None
        if isinstance(candidate, dict) and ("changes" in candidate or "awareness" in candidate):
            return candidate
        pos = body.find("{", pos + 1)
    return None


def parse_reply(text: str) -> dict:
    """The model's reply as `{"changes": dict, "awareness": dict}`.

    Models wrap JSON in prose and code fences however the prompt asks them not
    to, so a fence is stripped and the first balanced `{...}` that parses is
    taken. A top level that is not an object (a bare list, a number) is an
    error rather than something to dig an object out of: `[{...}]` is a
    different contract, not a decorated one. So is an object carrying neither
    `changes` nor `awareness`, unless it is exactly `{}`."""
    body = (text or "").strip()
    fenced = _FENCE.match(body)
    if fenced:
        body = fenced.group(1).strip()
    try:
        whole = json.loads(body)
    except ValueError:
        whole = None
    else:
        if not isinstance(whole, dict):
            raise TrackerReplyError("the reply's top level is not an object")
    obj = whole if whole is not None else _first_object(body)
    if obj is None:
        raise TrackerReplyError("no JSON object in the reply")
    if obj and "changes" not in obj and "awareness" not in obj:
        # `{"Mara": {...}}`: the changes without their wrapper. Read as a
        # reply it has no changes and lands as a clean "nothing moved" -- the
        # update would report success over values it silently lost. Only an
        # empty object means "nothing changed" without saying `changes`.
        raise TrackerReplyError("the reply's object has no \"changes\" or \"awareness\"")
    changes, awareness = obj.get("changes"), obj.get("awareness")
    return {"changes": changes if isinstance(changes, dict) else {},
            "awareness": awareness if isinstance(awareness, dict) else {}}


# --- value validation ---------------------------------------------------------

def _line(text: str) -> str:
    """One line, at most `MAX_TEXT` characters: whitespace (newlines included)
    collapses to single spaces, because a value is rendered inline in a prompt
    and a chip."""
    return " ".join(text.split())[:MAX_TEXT].rstrip()


def _normalize(field: dict, value):
    """`value` as the field stores it, or `ValueError` saying why it cannot.

    Callers that drop (`apply_reply`) catch it; callers that refuse
    (`apply_edit`) let it through with the message."""
    kind = field.get("type")
    if kind == "list":
        if not isinstance(value, list):
            raise ValueError(f"{field['key']}: expected a list")
        if not all(isinstance(v, str) for v in value):
            # Dropped (reply) or refused (edit) as a whole: filtering would turn
            # `[{"state": "wet"}]` into `[]`, which is a valid clear of the list.
            raise ValueError(f"{field['key']}: list items must be text")
        return [v for v in (_line(v) for v in value) if v]
    if not isinstance(value, str):
        raise ValueError(f"{field['key']}: expected text")
    text = _line(value)
    if kind == "enum":
        # A model capitalizes ("Fear") as often as not; the option is what is
        # stored, so the lookup forgives case and nothing else.
        options = {o.lower(): o for o in field.get("options") or []}
        if text and text.lower() not in options:
            raise ValueError(f"{field['key']}: {text!r} is not one of the options")
        return options.get(text.lower(), "")
    return text


def _default_aware(field: dict):
    return "present" if field.get("aware") == "present" else []


def _writable(fields: list[dict]) -> dict[str, dict]:
    """Key -> definition for the fields a value may be written to. Switched-off
    fields are absent: their stored values stay (and are kept by the copy), but
    nothing new lands under a label the person turned off."""
    return {f["key"]: f for f in field_defs.active(fields)}


def _same_aware(a, b) -> bool:
    if isinstance(a, list) and isinstance(b, list):
        return set(a) == set(b)
    return a == b


def _entry(snapshot: dict, ref: str) -> dict:
    ent = snapshot.setdefault(ref, {"present": True, "fields": {}})
    ent.setdefault("fields", {})
    return ent


def _set_value(snapshot: dict, ref: str, field: dict, value, changed: list) -> bool:
    """Write `value` unless it is what is already there; True when it landed.

    A *changed* value starts again from the field's default awareness and with
    no `set_by`. The old awareness was about the old value: Winifred knowing
    Mara was calm says nothing about Mara being afraid, and carrying it over
    would leak the new secret to whoever knew the old one. The old `set_by` was
    a person's claim about the old value too; the model is now saying something
    different, and the player's pin is on what they wrote, not on the field."""
    fields_ = _entry(snapshot, ref)["fields"]
    cur = fields_.get(field["key"])
    if cur is None and value in ("", []):
        return False                  # clearing what was never set
    if cur is not None and cur.get("value") == value:
        return False
    fields_[field["key"]] = {"value": value, "aware": _default_aware(field)}
    changed.append([ref, field["key"], value])
    return True


# --- the model's reply --------------------------------------------------------

def apply_reply(prev: dict, reply: dict, fields: list[dict], roster: dict[str, str],
                present: set[str], match: Callable[[str, list[str]], str | None]
                ) -> tuple[dict, list[list]]:
    """`prev` with the model's `reply` laid over it: `(snapshot, changed)`,
    where `changed` is `[[ref, key, new_value], ...]` for every value that
    actually moved. `prev` is not touched.

    The snapshot carries forward: everyone in `present` has an entry, and
    anyone who had one and is not present keeps it with `present: False`
    (their last known state is what a later return starts from).

    Names in the reply resolve through `match` against the roster rather than
    by string equality, because the model writes "Winifred" for "Winifred
    Vance" exactly as a transcript label does, and `match_name` is the one rule
    in the app for when that is unambiguous -- a second rule here would
    disagree with the transcript about who a name means. Only `present` refs
    are writable: a character who left is not described by this post.

    Every invalid entry is dropped, never raised (see the module docstring)."""
    snap = copy.deepcopy(prev)
    for ref, ent in snap.items():
        ent["present"] = ref in present
    for ref in present:
        _entry(snap, ref)["present"] = True

    names = list(roster.values())
    by_name = {name: ref for ref, name in roster.items()}

    def resolve(name) -> str | None:
        if not isinstance(name, str):
            return None
        hit = match(name, names)
        ref = by_name.get(hit) if hit is not None else None
        return ref if ref in present else None

    defs = _writable(fields)
    changed: list[list] = []
    _apply_changes(snap, reply.get("changes") or {}, defs, resolve, changed)
    # After the changes, so a reply that sets a secret and says who knows it in
    # one breath is applied in the order it was meant.
    _apply_awareness(snap, reply.get("awareness") or {}, defs, resolve)
    return snap, changed


def _apply_changes(snap: dict, changes: dict, defs: dict[str, dict],
                   resolve: Callable, changed: list) -> None:
    for name, values in changes.items():
        ref = resolve(name)
        if ref is None or not isinstance(values, dict):
            continue
        for key, raw in values.items():
            field = defs.get(key)
            if field is None:
                continue
            try:
                value = _normalize(field, raw)
            except ValueError:
                continue
            _set_value(snap, ref, field, value, changed)


def _apply_awareness(snap: dict, awareness: dict, defs: dict[str, dict],
                     resolve: Callable) -> None:
    for target, who_list in awareness.items():
        if not isinstance(target, str) or not isinstance(who_list, list):
            continue
        owner_name, _, key = target.rpartition(".")
        owner = resolve(owner_name)
        stored = snap.get(owner, {}).get("fields", {}).get(key) if owner else None
        if stored is None or key not in defs or not isinstance(stored.get("aware"), list):
            continue            # nothing to widen: unset, or already everyone's
        for who in who_list:
            ref = resolve(who)
            if ref is not None and ref != owner and ref not in stored["aware"]:
                stored["aware"].append(ref)


def _widened(before: dict, fresh: dict):
    """What a reply added to who knows a field: `fresh`'s awareness less the
    `before` it was built from. The fresh result starts from the prior record,
    so its awareness is mostly the prior's -- and merging all of it back into
    a restored value would undo a person's narrowing (an awareness-only edit)
    the moment the record was re-run. A reply only ever appends to a list
    (`_apply_awareness`), so the difference is exactly its widening. With no
    `before` to compare, all of `fresh` counts."""
    new = fresh.get("aware")
    if "aware" not in before:
        return new
    old = before["aware"]
    if old == "present":
        return []                    # already everyone's: nothing to widen
    if new == "present":
        return "present"
    return [r for r in (new if isinstance(new, list) else [])
            if not isinstance(old, list) or r not in old]


def _merged_aware(a, b):
    """Two awareness specs as one: `"present"` (everyone) wins over a list;
    two lists are their union, `a`'s order first."""
    if a == "present" or b == "present":
        return "present"
    out = list(a) if isinstance(a, list) else []
    out += [r for r in (b if isinstance(b, list) else []) if r not in out]
    return out


def keep_user_values(snapshot: dict, own: dict | None, prior: dict,
                     changed: list[list], set_here: set[tuple[str, str]]
                     ) -> tuple[dict, list[list], list[list[str]]]:
    """`snapshot` (a fresh result for a post) with the hand-set values the
    post's OWN earlier record set AT this post put back:
    `(snapshot, changed, restored)`, `restored` naming every `[ref, field]`
    put back -- what the record still holds a person's word on, and so what
    the next re-run must restore again (`records.TOUCHED`).

    A re-run or a Retry rebuilds a record from the one before it, so without
    this every value a person typed into this record would be silently lost
    to it. Only `set_here` pairs -- the `(ref, field)`s in the record's own
    change list, and those a person touched there (`records.TOUCHED`), which
    covers an edit of who knows a value as well as of the value -- are
    restored: a record carries forward everything before
    it, `set_by` included, so a `"user"` value merely inherited from an
    earlier record is that record's to say, and restoring it here would
    revert a newer edit made there. A value the reply itself changed is the
    model's newer word and stands. A restored value keeps its `set_by`; its
    awareness is the stored one widened by whatever the reply widened for
    that field (`_widened` -- not the awareness it inherited, which would
    undo a narrowing). Listed in `changed` when it differs from what the post
    started from. A character the fresh snapshot does not hold (not present
    here, and not carried from the prior) is not conjured. `snapshot` is not
    touched."""
    snap = copy.deepcopy(snapshot)
    out = list(changed)
    restored_pairs: list[list[str]] = []
    moved = {(c[0], c[1]) for c in changed}
    for ref, ent in (own or {}).items():
        target = snap.get(ref)
        if target is None:
            continue
        for key, cur in ((ent or {}).get("fields") or {}).items():
            if not isinstance(cur, dict) or cur.get("set_by") != "user":
                continue
            if (ref, key) in moved or (ref, key) not in set_here:
                continue
            fields_ = target.setdefault("fields", {})
            fresh = fields_.get(key) or {}
            restored = copy.deepcopy(cur)
            before = ((prior.get(ref) or {}).get("fields") or {}).get(key) or {}
            if "aware" in fresh:
                restored["aware"] = _merged_aware(cur.get("aware", []),
                                                  _widened(before, fresh))
            fields_[key] = restored
            restored_pairs.append([ref, key])
            if before.get("value") != cur.get("value"):
                out.append([ref, key, cur.get("value")])
    return snap, out, restored_pairs


# --- a person's edit ----------------------------------------------------------

def _check_aware(aware, snapshot: dict, owner: str, key: str):
    if aware == "present":
        return "present"
    if (not isinstance(aware, list)
            or not all(isinstance(r, str) and r in snapshot for r in aware)):
        raise ValueError(f"{owner} {key}: aware must be \"present\" or a list of "
                         "characters in the scene")
    return list(dict.fromkeys(r for r in aware if r != owner))


def apply_edit(prev: dict, edits: dict, fields: list[dict]) -> tuple[dict, list[list]]:
    """`prev` with a person's hand edits applied: `(snapshot, changed)`.

    `edits` is `{ref: {key: {"value": ..., "aware": "present" | [refs]}}}`,
    either half optional. Same validation as `apply_reply`, but an invalid edit
    raises `ValueError` and nothing is applied: the editor can show the message,
    and dropping would make a typo look like it saved.

    Whatever a person changes -- the value or only who knows it -- is marked
    `"set_by": "user"`, which is what the later update passes read as "this was
    said by the player". An edit that changes nothing is a no-op and does not
    mark a field the person did not touch. `changed` lists value changes only;
    an awareness-only edit moves no value."""
    snap = copy.deepcopy(prev)
    defs = _writable(fields)
    changed: list[list] = []
    for ref, values in edits.items():
        if ref not in snap or not isinstance(values, dict):
            raise ValueError(f"unknown character {ref!r}")
        for key, edit in values.items():
            if key not in defs:
                raise ValueError(f"unknown or switched-off field {key!r}")
            _edit_one(snap, ref, defs[key], edit, changed)
    return snap, changed


def touched_by(before: dict, after: dict) -> list[list[str]]:
    """Every `[ref, field]` a person's edit wrote: where `after` (from
    `apply_edit`) holds a `"user"` value that differs from `before`'s. The
    value or only its awareness -- unlike `apply_edit`'s `changed`, which is
    the display's list and names value changes alone."""
    out = []
    for ref, ent in after.items():
        old = ((before.get(ref) or {}).get("fields") or {})
        for key, cur in ((ent or {}).get("fields") or {}).items():
            if isinstance(cur, dict) and cur.get("set_by") == "user" and old.get(key) != cur:
                out.append([ref, key])
    return out


def _edit_one(snap: dict, ref: str, field: dict, edit, changed: list) -> None:
    key = field["key"]
    if not isinstance(edit, dict) or not ({"value", "aware"} & set(edit)):
        raise ValueError(f"{ref} {key}: an edit needs a value or an awareness")
    stored = snap[ref].setdefault("fields", {}).get(key)
    if "value" in edit:
        value = _normalize(field, edit["value"])
    elif stored is None:
        raise ValueError(f"{ref} {key}: nothing is set to change the awareness of")
    else:
        value = stored["value"]
    new = stored is None or stored.get("value") != value
    if "aware" in edit:
        aware = _check_aware(edit["aware"], snap, ref, key)
    else:
        aware = _default_aware(field) if new else stored["aware"]
    if not new and _same_aware(stored.get("aware"), aware):
        return
    snap[ref]["fields"][key] = {"value": value, "aware": aware, "set_by": "user"}
    if new:
        changed.append([ref, key, value])
