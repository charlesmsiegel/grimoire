"""tracker: transcript responsibilities."""

from __future__ import annotations

from ... import store
from ._router import router as router


def _speaker(m: dict, player: str) -> str:
    if m.get("role") == "user":
        return m.get("speaker") or player or "You"
    return m.get("speaker") or "Grimoire"


def _shown(cid: str, messages: list[dict], indices: list[int]) -> dict[int, str]:
    """The prompt view (`store/regex`) of the messages at `indices`, by index:
    the update reads what a turn prompt would show, depth counted over the
    whole transcript."""
    lo, hi = min(indices), max(indices)
    window = store.regex.view.view(messages[lo:hi + 1], cid=cid, phase="prompt",
                                   offset=lo, total=len(messages))
    return {i: window[i - lo].get("content", "") for i in indices}


def _locate(cid: str, sid: str, key: str) -> dict | None:
    """Where `key`'s post sits and what it says, or `None` once it is gone.

    A response's key is live while the response is on the transcript and the
    variant is still one of its variants -- active or not. The spec keeps every
    variant's record ("nothing is cancelled", and a swipe back must be free),
    so an update whose variant was swiped away while it waited still computes
    its record, at the response's position, from the variant's own text. Only a
    post that is GONE (cut, retconned, taken back after a failed turn) has
    nothing to record."""
    messages = store.scenes.read_scene(cid, sid)["messages"]
    walked = {k: i for i, k in store.tracker.walk.ordered_keys(cid, sid)}
    player = store.appearances.player_label(cid, sid)
    if key.startswith("p-"):
        index = walked.get(key)
        if index is None:
            return None
        m = messages[index]
        return {"index": index, "first": index, "post": index, "rid": "",
                "post_msg": {"speaker": _speaker(m, player),
                             "content": _shown(cid, messages, [index])[index]}}
    rid, vid = key[2:34], key[35:]
    parts = [i for i, m in enumerate(messages) if m.get("response_id") == rid]
    if not parts:
        return None
    record = store.responses.get(cid, sid, rid)
    variant = next((v for v in record.get("variants", []) if v.get("id") == vid), None)
    if variant is None:
        return None
    if walked.get(key) is not None:
        # The active variant: the transcript's text, every part of it.
        shown = _shown(cid, messages, parts)
        content = "\n\n".join(shown[i] for i in parts)
    else:
        # Viewed as the reply it would be, where the response stands.
        stand_in = {**messages[parts[-1]], "content": variant.get("content", ""),
                    "connection": variant.get("connection", "")}
        content = store.regex.view.view([stand_in], cid=cid, phase="prompt",
                                        offset=parts[-1], total=len(messages))[0]["content"]
    speaker = record.get("speaker") or _speaker(messages[parts[-1]], player)
    return {"index": parts[-1], "first": parts[0], "post": record.get("post"), "rid": rid,
            "post_msg": {"speaker": speaker, "content": content}}


def _context_posts(cid: str, sid: str, first: int) -> list[dict]:
    """The two non-synthetic messages before the post, oldest first. Rolls,
    transitions and director notes are not things a character did, and a post
    hidden from context is in no prompt."""
    messages = store.scenes.read_scene(cid, sid)["messages"]
    player = store.appearances.player_label(cid, sid)
    picked: list[int] = []
    for i in range(min(first, len(messages)) - 1, -1, -1):
        if (messages[i].get("speaker") in store.scenes.SYNTHETIC_SPEAKERS
                or store.scenes.is_excluded(messages[i])):
            continue
        picked.append(i)
        if len(picked) == 2:
            break
    if not picked:
        return []
    shown = _shown(cid, messages, picked)
    return [{"speaker": _speaker(messages[i], player), "content": shown[i]}
            for i in reversed(picked)]
