"""The tracker update call's messages, and the material a newcomer brings.

Prompt text lives in templates/tracker/; this file gathers what the user
message names -- the active fields, each present character's current values with
who may know them, the characters seen for the first time, the posts -- and
hands it over. Rendering is pure; `newcomer` is the one function here that reads
the store, and every read it makes fails soft to an empty string, because a
card that cannot be read must not stop a post from being tracked.
"""

from __future__ import annotations

from ... import prompts
from .. import cards, characters, overlay, pcs, playstate
from ..appearances import versions as appearances_versions
from ..campaigns import paths as campaigns_paths
from . import fields as field_defs

_UNREADABLE = (characters.CharacterNotFound, characters.VersionNotFound,
               pcs.PCNotFound, pcs.PCVersionNotFound, cards.CardParseError,
               OSError, ValueError, TypeError)
"""What a missing, half-written or hand-damaged card or state file raises."""


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _card(cid: str, actor_id: str) -> dict:
    vid = appearances_versions.locked_version(cid, "characters", actor_id)
    if vid is None:
        raise characters.VersionNotFound(actor_id)
    card = characters.read_card(overlay.char_root(cid, actor_id), actor_id, vid)
    return cards.card_data(card)    # a hand-edited card may have no usable `data`


def _persona(cid: str, actor_id: str) -> dict:
    vid = appearances_versions.locked_version(cid, "pcs", actor_id)
    if vid is None:
        raise pcs.PCVersionNotFound(actor_id)
    return pcs.read_persona(overlay.pc_root(cid, actor_id), actor_id, vid)


def newcomer(cid: str, ref: str) -> dict:
    """`{"ref", "name", "description", "state"}` for a character the tracker has
    not recorded yet. Anything unreadable is an empty string."""
    kind, _, actor_id = ref.partition(":")
    name = description = state = ""
    try:
        if kind == "characters":
            data = _card(cid, actor_id)
            name = _text(data.get("name"))
            description = _text(data.get("description"))
        elif kind == "pcs":
            persona = _persona(cid, actor_id)
            name = _text(persona.get("name"))
            description = "\n\n".join(
                t for t in (_text(persona.get("summary")), _text(persona.get("description"))) if t)
    except _UNREADABLE:     # a missing or damaged card is an empty description
        pass
    try:
        if kind in ("characters", "pcs"):
            st = playstate.read_state(campaigns_paths.campaign_root(cid), actor_id, kind)
            state = _text((st or {}).get("current_state"))
    except _UNREADABLE:     # as above
        pass
    return {"ref": ref, "name": name or actor_id, "description": description, "state": state}


def _text_of(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value if str(v).strip())
    return str(value).strip() if value is not None else ""


def _characters(fields: list[dict], prior: dict, roster: dict[str, str],
                present: set[str], new_refs: set[str]) -> list[dict]:
    """One block per present character: those already in the snapshot first, in
    its order, then any others (a newcomer has no entry yet)."""
    refs = [r for r in prior if r in present]
    refs += sorted((r for r in present if r not in prior), key=lambda r: roster.get(r, r))
    blocks = []
    for ref in refs:
        stored = (prior.get(ref) or {}).get("fields") or {}
        values = []
        for f in fields:
            cur = stored.get(f["key"]) or {}
            text = _text_of(cur.get("value"))
            if not text:
                continue
            aware = cur.get("aware", [])
            values.append({
                "key": f["key"], "text": text,
                "user_set": cur.get("set_by") == "user",
                "private": isinstance(aware, list),
                "known_to": ([roster.get(a, a) for a in aware]
                             if isinstance(aware, list) else []),
            })
        blocks.append({"name": roster.get(ref, ref), "new": ref in new_refs,
                       "entries": values})
    return blocks


def _label(field: dict) -> str:
    """The field's label when it says something its key does not, else "".

    A custom field's label is what the person wrote it to mean -- `grudge`
    could be anything, "Old debt owed to Winifred" cannot -- so the model is
    shown it. A built-in's label only restates its key ("Visible mood" for
    `visible_mood`), and repeating it would be noise on every update."""
    label = field.get("label")
    if not isinstance(label, str) or not label.strip():
        return ""
    label = " ".join(label.split())
    return "" if label.lower().replace(" ", "_") == field["key"] else label


def build_messages(fields: list[dict], prior: dict, roster: dict[str, str],
                   present: set[str], newcomers: list[dict],
                   context_posts: list[dict], post: dict) -> list[dict]:
    """The system/user pair for one update. `post` and `context_posts` items are
    `{"speaker", "content"}`; switched-off fields are not shown."""
    active = field_defs.active(fields)
    shown = [{"key": f["key"], "label": _label(f), "type": f["type"], "aware": f.get("aware"),
              "options": f.get("options") or [], "hint": f.get("hint", "")}
             for f in active]
    new_refs = {n["ref"] for n in newcomers}
    return [{"role": "system", "content": prompts.render("tracker/update_system.j2")},
            {"role": "user", "content": prompts.render(
                "tracker/update_user.j2", fields=shown,
                characters=_characters(active, prior, roster, present, new_refs),
                newcomers=newcomers, context_posts=context_posts, post=post)}]
