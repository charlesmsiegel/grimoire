"""A snapshot as one reader may see it. Pure, like `merge`.

The tracker records what each character knows about the others, so the same
snapshot reads differently depending on who is asking: a character's own state
is theirs entirely; someone else's shows only what that character's body and
voice give away (`aware: "present"`) plus what they were told (a list naming
the viewer). The narrator is the one reader who sees everything, and is told
which values are private so it can play them as unspoken.
"""

from __future__ import annotations

from . import fields as field_defs

NARRATOR = "grimoire"
"""The viewer name for the narrator (`None` means the same)."""


def _text(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value if str(v).strip())
    return str(value).strip() if value is not None else ""


def _values(entry: dict, fields: list[dict], all_: bool, viewer) -> list[dict]:
    """The entry's non-empty values, in field order: every one when `all_`,
    else those `viewer` may have (`present`-aware, or a list naming them). A stored value whose field is gone or
    switched off has no definition to label it under and is not shown."""
    out = []
    stored = entry.get("fields") or {}
    for field in field_defs.active(fields):
        cur = stored.get(field["key"])
        if not cur:
            continue
        text = _text(cur.get("value"))
        aware = cur.get("aware", [])        # missing reads as the safe one
        if text and (all_ or aware == "present"
                     or (isinstance(aware, list) and viewer in aware)):
            out.append({"label": field["label"], "text": text,
                        "private": isinstance(aware, list)})
    return out


LEFT = " (left the scene)"
"""Appended to a departed character's name (`include_departed`)."""


def lines_for(snapshot: dict, fields: list[dict], viewer: str | None,
              roster: dict[str, str], *, include_departed: bool = False) -> list[dict]:
    """`[{"name", "own", "values": [{"label", "text", "private"}]}]` for the
    characters present in `snapshot`, as `viewer` may see them.

    `viewer` is a character ref, or `None`/`"grimoire"` for the narrator. The
    viewer's own line comes first and carries every value; the narrator's
    lines carry every value too, none `own`. A ref the roster does not name is
    skipped -- there is nothing to call it.

    `include_departed` also gives a line to each character the snapshot holds
    as no longer present, named with `LEFT`: a reader of the whole scene
    (absorb) needs the last state of someone who walked out, where a prompt
    playing the scene on describes only who is in it."""
    narrator = viewer is None or viewer == NARRATOR
    lines = []
    for ref, entry in snapshot.items():
        here = bool(entry.get("present"))
        if not (here or include_departed) or ref not in roster:
            continue
        own = not narrator and ref == viewer
        lines.append({"name": roster[ref] + ("" if here else LEFT), "own": own,
                      "values": _values(entry, fields, narrator or own, viewer)})
    lines.sort(key=lambda line: not line["own"])    # stable: the rest keep order
    return lines
