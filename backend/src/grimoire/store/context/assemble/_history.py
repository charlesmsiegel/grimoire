"""Project prompt history, author notes and image references without persistence."""

from __future__ import annotations

import re

from .... import content_parts
from .. import authors_note, story


def _projected(cid: str, history: list[dict], notes: list[dict], *, images: int,
               expand) -> tuple[list[dict], list[dict]]:
    """The history as it is sent -- projected, author's `notes` injected at
    their depths, every message macro-expanded -- and the notes' positions in it.

    With `images` above 0, the newest `images` pictures stay as references
    (#377): projected as sentinels, expanded like any other text, then split
    into parts.
    """
    if images <= 0:
        projected, positions = authors_note.inject(history, notes)
        return ([{"role": m["role"], "content": expand(m["content"])} for m in projected],
                positions)
    image_table: dict[int, dict] = {}
    nonce = ""

    def project(messages: list[dict]) -> list[dict]:
        nonlocal nonce
        projected, table, nonce = story._project_history_refs(messages, images=images, cid=cid)
        image_table.update(table)
        return projected

    projected, positions = authors_note.inject(history, notes, project)
    return (_split_refs([{"role": m["role"], "content": expand(m["content"])} for m in projected],
                        image_table, nonce),
            positions)


def _parts_of(text: str, refs: dict[int, dict], sentinel) -> list:
    """`text` split on this composition's sentinels into text and reference
    parts. Removing the sentinels leaves exactly the `images=0` string, so the
    text parts concatenate to it (`content_parts`' rule)."""
    parts: list = []
    pos = 0
    for m in sentinel.finditer(text):
        if m.start() > pos:
            parts.append({"type": "text", "text": text[pos:m.start()]})
        ref = refs.get(int(m.group(1)))
        if ref is not None:
            parts.append(content_parts.ref(ref["url"], ref["alt"], ref["role"] != "user"))
        pos = m.end()
    if pos < len(text) or not parts:
        parts.append({"type": "text", "text": text[pos:]})
    return parts


def _split_refs(history: list[dict], refs: dict[int, dict], nonce: str) -> list[dict]:
    """Projected history with sentinels -> messages with image references, in
    USER messages only (#377).

    The providers that read images take them in user content alone, so a
    picture from an assistant post is CARRIED: moved to the start of the next
    user message, or -- when none follows in the window -- onto a carrier, a
    user message appended after the history that holds nothing else
    (`content_parts.CARRIER`). A message left with no reference is its plain
    string again, which is what keeps everything around it unchanged.
    """
    sentinel = re.compile("⟦img:" + re.escape(nonce) + r":(\d+)⟧")
    out: list[dict] = []
    carry: list = []
    for m in history:
        parts = _parts_of(m["content"], refs, sentinel)
        found = content_parts.image_refs(parts)
        if m["role"] == "user":
            parts = carry + parts
            carry = []
        else:
            carry += found
            parts = [p for p in parts if p not in found]
        out.append({**m, "content": content_parts.collapse(parts)})
    if carry:
        out.append({"role": "user", "content": carry, content_parts.CARRIER: True})
    return out
