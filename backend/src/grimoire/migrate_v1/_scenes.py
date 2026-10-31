"""Scene transcripts, parsed exactly as V0.2.1's ``scenes/serialize.py`` parses them.

Pure. The role of a post is not stored in V1: it is derived from the speaker
label and the scene's player cast, which the caller supplies (`players`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import _text

RESERVED_LABELS = {"You": "user", "Grimoire": "assistant"}
RESPONSE_METADATA = ("response_thinking", "response_part", "response_id", "response_status",
                     "response_can_reroll", "context_changed", "post_id", "scene_state_token",
                     "legacy_unkeyed", "connection", "excluded")
#: V1's synthetic speakers (dice rolls, transitions, notes) carry this invisible prefix.
SYNTHETIC_PREFIX = "⁣"
_MARKER = re.compile(r"^\*\*([^*\n]{1,64}?)(?: \(([^)\n]+)\))?:\*\*[ ]?", re.MULTILINE)
_METADATA = re.compile(r"<!-- grimoire-response (\{.*?\}) -->\s*")
_LEGACY_NAME = re.compile(r"\d{4}-\d{2}-\d{2}-.+\Z")
SCENE_KEYS = frozenset({"title", "model", "created", "updated", "identity", "pcless", "suggested_date",
                        "focus", "location_history", "time_history", "done", "one_line", "summary",
                        "turn_sizes", "greeting", "branch_of", "branch_group", "group_play", "dismissed"})
SCENE_KEY_PREFIXES = ("rolling_", "break_")


@dataclass
class Post:
    index: int
    speaker: str | None
    role: str
    synthetic: bool
    post_id: str | None
    content: str
    unknown_metadata: list[str] = field(default_factory=list)


def sid_grammar(sid: str) -> tuple[str, int | None]:
    """(`modern` | `legacy_date` | `invalid`, scene number) for a scene file stem."""
    parts = sid.split("--")
    if len(parts) in (2, 3) and parts[0].isdigit() and parts[-1]:
        try:
            return "modern", int(parts[0])
        except ValueError:  # a Unicode digit V1's int() would refuse
            return "invalid", None
    if _LEGACY_NAME.match(sid):
        return "legacy_date", None
    return "invalid", None


def match_name(label: str, names: list[str]) -> str | None:
    """The cast name `label` refers to, if unambiguous (V1 ``match_name``)."""
    low = label.strip().lower()
    if not low:
        return None
    exact = [n for n in names if n.lower() == low]
    if exact:
        return exact[0] if len(exact) == 1 else None
    prefixed = [n for n in names
                if n.lower().startswith(low) and not n.lower()[len(low)].isalnum()]
    return prefixed[0] if len(prefixed) == 1 else None


def _markers(body: str) -> list[re.Match]:
    return [m for m in _MARKER.finditer(body)
            if m.start() == 0 or body[max(0, m.start() - 2):m.start()] == "\n\n"]


def parse_posts(body: str, players: list[str]) -> tuple[list[Post], str]:
    """The posts of a transcript body, and any preamble text V1 drops."""
    matches = _markers(body)
    preamble = body[:matches[0].start()] if matches else body
    posts = []
    for index, m in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        base, sub = m.group(1), m.group(2)
        if base in RESERVED_LABELS:
            speaker, role = sub, RESERVED_LABELS[base]
        else:
            speaker = f"{base} ({sub})" if sub else base
            role = "user" if match_name(speaker, players) else "assistant"
        content = body[m.end():end].strip()
        metadata: dict = {}
        unknown: list[str] = []
        found = _METADATA.match(content)
        if found:
            try:
                candidate = json.loads(found.group(1))
                metadata = {k: v for k, v in candidate.items() if k in RESPONSE_METADATA}
                unknown = sorted(k for k in candidate if k not in RESPONSE_METADATA)
                content = content[found.end():]
            except (ValueError, AttributeError, RecursionError):
                metadata, unknown = {}, []
        post_id = metadata.get("post_id")
        posts.append(Post(index, speaker, role,
                          bool(speaker) and speaker.startswith(SYNTHETIC_PREFIX),
                          post_id if isinstance(post_id, str) else None, content, unknown))
    return posts, preamble


def scene_unknown_fields(meta: dict) -> list[str]:
    return sorted(k for k in meta if k not in SCENE_KEYS and not k.startswith(SCENE_KEY_PREFIXES))


def post_row(post: Post) -> dict:
    return {"index": post.index, "speaker": post.speaker, "role": post.role, "synthetic": post.synthetic,
            "post_id": post.post_id, "content_sha256": _text.sha256(post.content.encode("utf-8")),
            "content_length": len(post.content), "unknown_metadata": post.unknown_metadata}
