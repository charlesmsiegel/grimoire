"""Variant-owned post sequences and their stable placement around synthetic posts.

Response IDs group posts; they are not post addresses. A saved take keeps every
part's identity, public-state token and preceding post anchor. Switching takes
restores that sequence without moving a later part before a retained transition.
"""

from __future__ import annotations


def snapshot(messages: list[dict], rid: str, part: str, new: dict) -> list[dict]:
    selected = [(i, m) for i, m in enumerate(messages)
                if part and m.get("response_id") == rid and m.get("response_part", "") != part]
    at = next((i for i, m in enumerate(messages)
               if m.get("response_id") == rid and m.get("response_part", "") == part), len(messages))
    selected.append((at, {**new, "response_part": part}))
    return [{**{k: message.get(k) for k in ("post_id", "scene_state_token", "legacy_unkeyed", "content", "response_part")},
             "after_post_id": messages[i - 1].get("post_id") if i else None}
            for i, message in sorted(selected, key=lambda item: item[0])]


def place(messages: list[dict], rid: str, at: int, restored: list[dict],
          saved: list[dict]) -> tuple[list[dict], dict[int, int]]:
    kept = [m for m in messages if m.get("response_id") != rid]
    position = at
    for ordinal, message in enumerate(restored):
        anchor = saved[ordinal].get("after_post_id")
        if ordinal:
            position = next((i + 1 for i, m in enumerate(kept)
                             if m.get("post_id") == anchor), position)
        kept.insert(position, message)
        position += 1
    positions = {id(message): i for i, message in enumerate(kept)}
    mapping = {i: positions[id(m)] for i, m in enumerate(messages) if id(m) in positions}
    mapping[at] = at
    return kept, mapping
