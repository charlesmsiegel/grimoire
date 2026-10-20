"""Describe the exact packed prompt, its sources, drops and token counter."""

from __future__ import annotations

from copy import deepcopy

from .... import content_parts
from ... import tokens
from .. import pack
from . import _render


def _history_rows(p: dict, count, notes: frozenset[int] = frozenset(),
                  notes_trimmed: int = 0) -> list[dict]:
    """The history's inspector rows: the conversation, then -- when the kept
    history carries image references (#377) -- an Images row.

    The Images row is a SPLIT of the history's cost, never an addition beside
    it: `pack.message_cost` already charges each reference `IMAGE_TOKENS`, and
    `_breakdown`'s total is built from that, so the conversation row reports
    the text alone and the two rows sum to what the packer charged. Kept as a
    row of its own, in the `history` tier, so the budget bar draws the pictures
    in the Conversation bucket rather than leaving them out of a prompt whose
    whole size it claims to show. A carrier has no text and adds no blank line
    to the joined conversation.

    `notes` are the packed-history indices of author's-note messages, which
    the Author's notes row reports instead (`_notes_row`): the conversation
    row neither shows nor counts them, and its `trimmed` counts only the
    conversation messages the trim took (`notes_trimmed` were notes).
    """
    kept = [m for i, m in enumerate(p["history"]) if i not in notes]
    texts = [content_parts.text_of(m["content"]) for m in kept
             if not m.get(content_parts.CARRIER)]
    refs = [r for m in kept for r in content_parts.image_refs(m["content"])]
    rows = []
    hist = "\n\n".join(texts)
    if hist:
        # Displayed joined (one readable block), accounted per message with the
        # same per-message framing allowance the packer charges.
        rows.append({"id": "history", "label": "Conversation history", "text": hist,
                     "tier": pack.HISTORY, "dropped": False, "pinned": False,
                     "trimmed": p["history_trimmed"] - notes_trimmed,
                     "tokens": sum(pack.message_cost(content_parts.text_of(m["content"]), count)
                                   for m in kept if not m.get(content_parts.CARRIER))})
    if refs:
        rows.append({"id": "history_images", "label": f"Images ({len(refs)})",
                     "text": "\n".join(f"{r.get('alt', '')} — {r.get('url', '')}" for r in refs),
                     "tier": pack.HISTORY, "dropped": False, "pinned": False, "trimmed": 0,
                     "tokens": (len(refs) * pack.IMAGE_TOKENS
                                + sum(pack.MESSAGE_OVERHEAD for m in kept
                                      if m.get(content_parts.CARRIER)))})
    return rows


#: #128: why each source in a cast section is in the prompt. Every source in one
#: of these sections is a character, and the section names the fact that put it
#: there -- seated in this scene's cast, or on the off-scene directory's tier 2
#: or 3 -- so the reason is read off the section, never inferred per entry. It
#: rides the inspector row only: `_source_row` is what reaches JSON, and no
#: prompt text is built from it.
_CAST_REASONS = {
    "character_descriptions": {"type": "cast"},
    "off_scene_cast_active": {"type": "off_scene", "tier": "active"},
    "off_scene_cast_known": {"type": "off_scene", "tier": "known"},
}


def _source_row(section: dict) -> dict:
    """#131: only frozen text and labels reach JSON; cuts stay inspectable."""
    sources = section.get("sources")
    if not sources:
        return {}
    shed = set(section.get("shed_refs") or ())
    current = sources if section.get("dropped") else section.get("source_slices", sources)
    kept = {s["id"]: s for s in current}
    reason = _CAST_REASONS.get(section.get("id", ""))
    return {"sources": [{**(s if s.get("ref") in shed else kept[s["id"]]),
                         "shed": s.get("ref") in shed,
                         **({"reason": deepcopy(reason)} if reason else {})}
                        for s in sources if s.get("ref") in shed or s["id"] in kept]}


def _lore_row(section: dict) -> dict:
    """The entry-level keys of a World info or Recalled lore row: each entry
    with whether the packer shed it, the names its reasons need, and (World
    info) what was held back. Explicit keys, so the section's `shed` -- a
    function -- can never reach a row, a capture or JSON."""
    detail = section.get("lore")
    if not detail:
        return {}
    shed = set(section.get("shed_refs") or ())
    row = {"entries": [{**e, "reason": deepcopy(e["reason"]), "shed": e["ref"] in shed}
                       for e in detail["entries"]],
           "names": dict(detail["names"])}
    if "held_back" in detail:
        row["held_back"] = deepcopy(detail["held_back"])
    return row


def _history_row(section: dict) -> dict:
    """JSON-safe evidence only: the callable shed hook never reaches a capture."""
    detail = section.get("history")
    if detail is None:
        return {}
    shed = set(section.get("shed_refs") or ())
    return {"history": {
        "items": [{key: deepcopy(item[key]) for key in (
            "scene", "identity", "key", "title", "date", "location", "tier", "admitted_by",
            "rounds", "ranks", "excerpts", "evidence_ids", "posts", "summary", "tokens")} | {
                "shed": item["key"] in shed} for item in detail["items"]],
        "coverage": deepcopy(detail["coverage"]), "ceiling": detail["ceiling"],
        "budget_from": list(detail["budget_from"]), "query_digest": detail["query_digest"],
        **_unselected(detail),
        # 11's classification: each item's state and names, and each part's
        # class and basis. Inspector data only; no basis reaches a message.
        **({"epistemic": deepcopy(detail["epistemic"])} if "epistemic" in detail else {}),
        # Roadmap 10's trace, only when the planner was in play: model-written
        # questions and terms are shown here and never reach the prompt.
        **({"plan": deepcopy(detail["plan"])} if "plan" in detail else {}),
    }}


#: How many candidates that found no place in the section the inspector lists.
#: Display only: the admitted list can run to the widening window, and every
#: entry rides each prompt capture, so the row keeps the strongest few in merge
#: order and counts the rest.
HISTORY_UNSELECTED_SHOWN = 20


def _unselected(detail: dict) -> dict:
    """What retrieval admitted and the section does not carry -- past depth, or
    selected and then not fitted -- with its signals, so the reader sees what
    was found as well as what was chosen. Absent on hand-built detail."""
    if "candidates" not in detail:
        return {}
    chosen = {item["key"] for item in detail["items"]}
    rest = [c for c in detail["candidates"] if c["key"] not in chosen]
    return {"unselected": len(rest), "candidates": [
        {key: deepcopy(c[key]) for key in ("scene", "key", "tier", "admitted_by", "rounds", "ranks")}
        for c in rest[:HISTORY_UNSELECTED_SHOWN]]}


def _notes_row(notes: list[dict], p: dict, count) -> tuple[dict, frozenset[int]]:
    """The Author's notes row (play controls V), and the packed-history indices
    of the notes it reports.

    `notes` are `_assemble`'s positions (indices into the history BEFORE
    packing) plus the notes this turn's cadence skipped. The packer only trims
    from the front, so a note at index `i` is at `i - history_trimmed` when it
    survived, and was trimmed when `i` fell inside the cut. Each note is listed
    as applied (with the text that went out), trimmed, or skipped with its
    cadence, which is how the reader sees why a configured note is absent.
    Tier `history`: the notes are sent inside the conversation and the packer
    trims them with it, so a lock-in label would describe a protection they do
    not have.
    """
    cut = p["history_trimmed"]
    kept: set[int] = set()
    entries: list[dict] = []
    blocks: list[str] = []
    labels = {"campaign": "Campaign", "scene": "Scene"}
    for n in notes:
        label = labels.get(n["level"]) or f"Character: {n['name']}"
        entry = {"level": n["level"], "name": n["name"], "depth": n["depth"],
                 "every": n["every"], "status": n["status"], "tokens": 0}
        if n["status"] == "skipped":
            blocks.append(f"[{label} · skipped (every {n['every']})]")
        elif n["index"] < cut:
            entry["status"] = "trimmed"
            blocks.append(f"[{label} · depth {n['depth']} · trimmed]")
        else:
            at = n["index"] - cut
            kept.add(at)
            text = content_parts.text_of(p["history"][at]["content"])
            entry["tokens"] = pack.message_cost(text, count)
            blocks.append(f"[{label} · depth {n['depth']}]\n{text}")
        entries.append(entry)
    row = {"id": "authors_note", "label": "Author's notes", "tier": pack.HISTORY,
           "dropped": False, "pinned": False,
           "trimmed": sum(1 for e in entries if e["status"] == "trimmed"),
           "tokens": sum(e["tokens"] for e in entries), "text": "\n\n".join(blocks),
           "notes": entries}
    return row, frozenset(kept)


def _conversation_rows(notes: list[dict], p: dict, count) -> list[dict]:
    """The rows for what was sent inside the conversation: the history's
    (`_history_rows`), and the Author's notes row (`_notes_row`) right after
    the conversation row -- but only when a note is configured, so a campaign
    with none describes its turns exactly as it did before notes existed."""
    if not notes:
        return _history_rows(p, count)
    note_row, note_index = _notes_row(notes, p, count)
    history_rows = _history_rows(p, count, note_index, p.get("notes_trimmed", 0))
    at = next((n + 1 for n, r in enumerate(history_rows) if r["id"] == "history"), 0)
    return [*history_rows[:at], note_row, *history_rows[at:]]


def _breakdown(a: dict, p: dict, extra: list[tuple[str, str]] | None = None,
               count=None) -> dict:
    """The inspector's view of a turn, from an assemble/pack pair the caller
    already has: every section the packer produced, in prompt order, each with
    its `tier`, its `tokens`, and whether the packer `dropped` it — then the
    (possibly trimmed) history, the post-history block, and any `extra`
    messages the caller appends after the system one, plus the totals.

    Takes the pair rather than `(cid, sid)` so the caller that is about to SEND
    these messages can describe exactly them (#157). Recomposing from the store
    would describe a different prompt: `_assemble` re-expands `{{random}}` and
    `{{roll}}` on every pass.

    Dropped sections stay in the list with their text: the inspector's job is to
    show what was cut, and a drop the user cannot see is the silent truncation
    this replaced.

    The rows are an INVENTORY, grouped sections -> history -> post-history ->
    appended, and deliberately not a transcript of the wire order: a director
    note and the opener's prompt are both sent above post_history and reported
    below it. Every row is something that went out and its tokens are counted
    once; which message index it occupied is not a question this panel answers.

    `total_tokens` is what the request actually costs, measured the way the
    packer measures it — the COMPOSED system message, plus each history entry
    counted as the separate message it is sent as, plus the `extra` messages
    `_packed` reserved. It is deliberately not the sum of the section rows:
    those are a per-row breakdown, and token counts do not add up across
    strings that get joined (the blank lines between sections are real tokens,
    and the tiktoken-less heuristic rounds each string on its own). Summing the
    rows instead would let the inspector report a total that disagrees with the
    request it is describing.

    `count` is the tokenizer, `tokens.count_tokens` by default; `_prepare`
    passes the memoized one it packed with (`_token_memo`).
    """
    extra = extra or []
    if count is None:
        count = tokens.count_tokens
    rows = [{"id": s["id"], "label": s["label"], "text": s["text"], "tier": s["tier"],
             "dropped": s["dropped"], "trimmed": 0, "pinned": bool(s.get("pinned")),
             "tokens": count(s["text"]), **_source_row(s), **_lore_row(s), **_history_row(s)}
            for s in p["sections"]]

    # Every history message, notes included: they are sent, so they cost.
    hist_tokens = sum(pack.message_cost(m["content"], count) for m in p["history"])
    rows += _conversation_rows(a.get("notes") or [], p, count)
    if a["post_history"]:
        rows.append({"id": "post_history", "label": "Post-history instructions",
                     "text": a["post_history"], "pinned": False,
                     "tier": pack.LOCK_IN, "dropped": False, "trimmed": 0,
                     "tokens": count(a["post_history"])})
    # `lock-in`, and not merely as a label: `_packed` reserved these, so the
    # packer could not drop them even had it wanted to. Reporting them under any
    # droppable tier would describe a choice the packer never had.
    #: Appended rows are numbered rather than named after their label: two of
    #: them can carry the same one (an opener sends a prompt and shape rules
    #: every time), and the inspector keys its rows on `id`.
    extra_tokens = [count(text) for _label, text in extra]
    rows += [{"id": f"appended_{n}", "label": label, "text": text, "tier": pack.LOCK_IN,
              "dropped": False, "trimmed": 0, "pinned": False, "tokens": cost}
             for n, ((label, text), cost) in enumerate(zip(extra, extra_tokens, strict=True))]

    kept = [s["text"] for s in p["sections"] if not s["dropped"]]
    total = (count(_render._compose_system(kept)) + hist_tokens
             + count(a["post_history"]) + sum(extra_tokens))
    # Trimmed history messages are gone from `rows` entirely -- they are not a
    # section that can be shown struck through -- so their cost has to be added
    # here, or a pack that fit by trimming history alone reports nothing
    # dropped and the inspector stays silent about the cut it just made.
    out = {"sections": rows, "total_tokens": total,
           "dropped_tokens": (sum(r["tokens"] for r in rows if r["dropped"])
                              + p["history_trimmed_tokens"]
                              + p.get("images_dropped_tokens", 0)),
           "budget_tokens": p["budget"]}
    # Status is debug metadata, never a rendered row or token charge. Keep it
    # with the original pass so frozen views do not warn about today's files.
    if a.get("prompt_field_statuses") is not None:
        out["prompt_field_statuses"] = dict(a["prompt_field_statuses"])
    # Which counter made these numbers, taken from the pass that made them
    # (`_token_memo`) -- `tokens.counting` turns it into the inspector's label.
    # Absent when `count` is a bare function that cannot say.
    which = getattr(count, "counted_with", None)
    if which is not None and which():
        out["counted_with"] = which()
    return out
