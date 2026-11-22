"""Draft-aware context preview (085): what one NPC's context would likely hold if a draft were posted now.

A player writes a draft and asks, before posting, which of an NPC's memories and which lore the draft is likely
to bring into that NPC's context. ``preview_context`` answers on request only, never per keystroke, and spends
nothing: no provider is called, nothing is written, and the draft is not kept.

How it is built, so the answer is the runner's own and not a second retrieval model:

- **The draft is read by ``post``'s own reading.** ``SceneRunner._unsaved_post``, which ``post`` itself saves
  from, after ``post``'s player and table-request checks: play paused, the persona present and on stage, 035's
  ``read_post`` (008 routes it, 003 checks the player controls the persona, 032 says who would hear it), then
  the last turn closed. A draft ``post`` would refuse is refused here with ``post``'s reason and words
  (``DraftRefused``) and nothing is packed; a table request, which ``post`` would run, is ``table_request``; a
  draft that retries the open turn's own post is that saved turn, as ``post`` would return it.
- **Packed twice through generation's own assembly.** The selected NPC's context is assembled now, from the
  saved turns (the inspector's answer), and again with the draft as a new, unsaved turn after them, so the draft
  is in what the NPC heard and triggers its lore exactly as a posted line would. An NPC that would not hear the
  draft (a whisper to someone else, a withheld note) gets no second pack: its context does not change, and the
  answer says so without saying who would hear it.
- **What changes, and what it likely recalls.** ``changes`` lists the packed lines that appear and disappear
  with the draft. ``recalls`` explains them: a lore entry the draft triggers (exact: it was not a candidate
  without the draft), and a memory whose own words the draft shares (an estimate: memory is ranked by recency
  and by who is present, never by the draft, so sharing words says only that the NPC will hold both in view).
  A recalled item the budget sheds is listed first, with its reason, and counted in ``shed_recalls`` however
  many are listed: the warning the preview exists for, that the draft points at context the NPC will not get. Its line comes from one more pack of the same candidates with no
  budget limit, the NPC's own authorized candidates only.

It is an **estimate**: the turn's own prompt is rebuilt from saved state when it is generated, so anything that
changes first (another post, a kept line, new knowledge, edited settings or sources) changes it, and token counts
are the pack's estimate, not provider usage. Shown lines follow the inspector's safe view (``context_view``).
See docs/INFERENCE_UI.md#draft-preview-085.
"""

from __future__ import annotations

from grimoire.context import MAX_TOKENS, Budget, ContextPack, Origin, words
from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.scene import SceneRefused, TurnRecord

from .context_view import context_view, origin_view
from .runner import SceneRunner, _table_command
from .scene import _refuse

#: The lanes whose content a scene's text can change or point at: what a draft can recall.
RECALL_LANES = ("memory", "lore")
#: Recalls listed at most; ``more_recalls`` counts the rest.
MAX_RECALLS = 50
#: Words too common to say what a draft is about. English only: other languages are matched on every word of three
#: letters or more, which shows more memories as shared, never fewer, and never changes what is packed.
COMMON = frozenset("""
a about after again all also am an and any are as at be been before being both but by can could did do does
doing down each for from further had has have having he her here hers him his how i if in into is it its just
me more most my no nor not now of off on once only or other our ours out over own same she should so some such
than that the their theirs them then there these they this those through to too under until up very was we
were what when where which while who whom why will with would you your yours yes okay oh well let lets get got
go going come came say said tell told know see saw one two
""".split())


class DraftRefused(SceneRefused):
    """The draft cannot be posted as it is, so nothing is previewed: ``post``'s own reason and player-facing message."""


def _topic(text: str) -> set[str]:
    return {word for word in words(text) if len(word) >= 3 and word not in COMMON}


def _content(lane: str, line: str) -> str:
    """A memory line without its lead (``You were told: ...``): the lead's words are the pack's, not the NPC's."""
    if lane == "memory" and ": " in line:
        return line.split(": ", 1)[1]
    return line


def _candidates(pack: ContextPack) -> set[tuple[str, Origin]]:
    """Every candidate a pack weighed: ``pack`` includes or sheds each one, so this is the whole set."""
    return {(item.lane, item.origin) for item in pack.items} | {(item.lane, item.origin) for item in pack.omitted}


def _read_draft(runner: SceneRunner, scene, chat: ChatRef, by, draft: str, mode: str) -> tuple[TurnRecord, bool]:  # type: ignore[no-untyped-def]
    """The draft as ``post`` would read it, and whether it is a new turn: ``post``'s own reading, nothing written.

    A table request is refused (``post`` would run it, not save a line). A draft that retries the open turn's
    own post is that saved turn: ``post`` would return it, so the context it gives is the current one."""
    principal = runner._principal(scene, by)
    if _table_command(scene, draft, mode) is not None:
        raise _refuse("table_request", "this draft is a table request (pause, resume, skip or redirect), not a post")
    record, saved = runner._unsaved_post(scene, chat, principal, draft, mode)
    return (saved, False) if saved is not None else (record, True)


def _line_view(item, actor: InstanceRef) -> dict:  # type: ignore[no-untyped-def]
    return {"line": item.line, "lane": item.lane, "tokens": item.tokens, "provenance": origin_view(item.origin, actor)}


def _changes(now: ContextPack, after: ContextPack, actor: InstanceRef) -> dict:
    before = {(item.lane, item.origin) for item in now.items}
    kept = {(item.lane, item.origin) for item in after.items}
    shed = {(item.lane, item.origin): item.reason for item in after.omitted}
    return {"added": [_line_view(item, actor) for item in after.items if (item.lane, item.origin) not in before],
            "removed": [{**_line_view(item, actor), "reason": shed.get((item.lane, item.origin))}
                        for item in now.items if (item.lane, item.origin) not in kept]}


def _recalls(draft: str, now: ContextPack, after: ContextPack, wide: ContextPack, actor: InstanceRef) -> list[dict]:
    """What the draft likely brings to mind: those the budget sheds first (the warnings), then packed items, each
    in pack order."""
    asked = _topic(draft)
    triggered = {key for key in _candidates(after) - _candidates(now) if key[0] == "lore"}
    lines = {(item.lane, item.origin): item.line for item in wide.items}  # every candidate's line, unlimited
    found = []
    entries = [(item, False, item.reason) for item in after.omitted]
    entries += [(item, True, None) for item in after.items]
    for item, included, reason in entries:
        key = (item.lane, item.origin)
        if item.lane not in RECALL_LANES:
            continue
        line = item.line if included else lines.get(key)
        if line is None:
            continue  # cannot happen for a candidate of the same assembly; never guess a line
        shared = sorted(asked & _topic(_content(item.lane, line)))
        why = "triggered" if key in triggered else "shared_words" if shared else None
        if why is None:
            continue
        found.append({"line": line, "lane": item.lane, "tokens": item.tokens, "included": included,
                      "reason": reason, "why": why, "words": shared, "provenance": origin_view(item.origin, actor)})
    return found


def preview_context(runner: SceneRunner, chat: ChatRef, by, actor: InstanceRef, draft: str, *,  # type: ignore[no-untyped-def]
                    mode: str = "auto") -> dict:
    """``actor``'s likely context if ``by`` posted ``draft`` for the chat's player character now (module doc).

    Raises ``DraftRefused`` where ``post`` would refuse the draft, and the inspector's own ``SceneRefused`` where
    the NPC's context cannot be inspected. Writes nothing and calls no provider."""
    context, voice, ledger, entry = runner._inspectable(chat, actor)
    scene = context.scene
    try:
        record, new = _read_draft(runner, scene, chat, by, draft, mode)
    except SceneRefused as refused:  # the same reason and words: ``str`` of either reads ``reason: message``
        text, lead = str(refused), f"{refused.reason}: "
        raise DraftRefused(refused.reason, text[len(lead):] if text.startswith(lead) else "") from None
    records = scene.records
    now = runner._actor_assembly(context, actor, voice, records, ledger,
                                 recorded=entry.record if entry is not None else None)
    hears = actor in record.post.audience
    result = {"label": "Estimated preview with your draft", "estimated": True, "actor": str(actor),
              "draft": {"heard": hears, "kind": record.post.kind},
              "tokens": {"now": now["pack"].tokens, "with_draft": now["pack"].tokens},
              "changes": {"added": [], "removed": []}, "recalls": [], "shed_recalls": 0, "more_recalls": 0}
    if not hears:  # its context is the saved one: the draft never reaches it
        result["context"] = context_view(now["brief"], now["pack"], actor, label="Current generation context")
        return result
    turns = (*records, record) if new else records  # a retried post is already the last saved turn
    after = runner._actor_assembly(context, actor, voice, turns, ledger)
    wide = runner._actor_assembly(context, actor, voice, turns, ledger,
                                  budget=Budget(MAX_TOKENS, item_overhead=runner.budget.item_overhead))
    recalls = _recalls(draft, now["pack"], after["pack"], wide["pack"], actor)
    result.update(context=context_view(after["brief"], after["pack"], actor, label="Estimated context with your draft"),
                  tokens={"now": now["pack"].tokens, "with_draft": after["pack"].tokens},
                  changes=_changes(now["pack"], after["pack"], actor),
                  recalls=recalls[:MAX_RECALLS], more_recalls=max(0, len(recalls) - MAX_RECALLS),
                  shed_recalls=sum(not item["included"] for item in recalls))
    return result


__all__ = ["COMMON", "MAX_RECALLS", "RECALL_LANES", "DraftRefused", "preview_context"]
