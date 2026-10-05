"""Messages as a phase sees them: the one door callers outside the package use.

`view` is what every LLM reader of transcript text goes through (the `prompt`
phase) and what the scene read and the exports go through (`display`). It never
writes: the stored transcript stays raw, and turning a rule off restores every
later prompt and every screen at once. `store_phase` is the other direction --
the opt-in rewrite of text as it lands, which is the only phase that changes
what is stored.

Each message is run against the effective list for the connection that
produced it, so a list is built once per distinct `connection` value rather
than once per message. When no rule could apply in the phase for any of them,
the messages come back as plain copies without a pattern being run: a store
with no rule files must build exactly the prompt it built before rules existed.
"""

from __future__ import annotations

from . import apply, layers


def _live(entries: list[apply.Entry], phase: str) -> list[apply.Entry]:
    """The entries that could apply in `phase` to some message. Role and depth
    are still checked per message by `apply.run`; this only decides whether
    there is anything to run at all."""
    out = []
    for e in entries:
        rule = e["rule"]
        if not rule["enabled"] or e["off"]:
            continue
        if rule["rewrite_stored"] if phase == "store" else phase in rule["applies"]:
            out.append(e)
    return out


def view(messages: list[dict], *, cid: str | None, phase: str, offset: int = 0,
         total: int | None = None) -> list[dict]:
    """Copies of `messages` with `content` run through the `phase` rules.

    `messages` may be a window of a longer transcript: `offset` is the index of
    its first message in the whole, and `total` the whole's length, so depth --
    0 for the newest message -- counts over the full transcript rather than
    over the slice. A line no rule may touch (a roll, a transition, a director
    note) is copied unchanged."""
    out = [dict(m) for m in messages]
    lists: dict[str, list[apply.Entry]] = {}
    for m in out:
        conn = m.get("connection") or ""
        if conn not in lists:
            lists[conn] = _live(layers.effective(cid=cid, connection=conn), phase)
    if not any(lists.values()):
        return out
    n = total if total is not None else len(messages)
    for i, m in enumerate(out):
        entries = lists[m.get("connection") or ""]
        role = apply.role_of(m)
        if not entries or role is None or not isinstance(m.get("content"), str):
            continue
        m["content"] = apply.run(m["content"], entries, role=role, phase=phase,
                                 depth=n - 1 - (offset + i))
    return out


def annotate_shown(messages: list[dict], *, cid: str | None, offset: int = 0,
                   total: int | None = None) -> list[dict]:
    """Copies of `messages`, each carrying `shown` only where the display phase
    changed its text. `content` stays raw: it is what an edit starts from."""
    shown = view(messages, cid=cid, phase="display", offset=offset, total=total)
    out = []
    for m, s in zip(messages, shown, strict=True):
        copy = dict(m)
        if s.get("content") != m.get("content"):
            copy["shown"] = s["content"]
        out.append(copy)
    return out


def store_phase(text: str, *, cid: str, role: str,
                connection: str = "") -> tuple[str, list[str]]:
    """`text` through the rules that rewrite stored text, and the ids of the
    rules that changed it (a rule that matched nothing, or replaced a match
    with itself, is not one of them)."""
    entries = _live(layers.effective(cid=cid, connection=connection), "store")
    if not entries:
        return text, []
    fired: list[str] = []
    for step in apply.trace(text, entries, role=role, phase="store", depth=0):
        if step["applied"] and step["text_after"] != text:
            fired.append(step["rule_id"])
        text = step["text_after"]
    return text, fired
