"""Scene reads: the parsed transcript, frontmatter-only metadata, enumeration,
and the history fields (`location_history`, `time_history`) other stores follow.

`read_scene` needs the seated players to decide which messages are user-side,
so this module imports `appearances/cast.py` — the read-only half of
`appearances`, which touches no scene state. That cut is what lets the import
sit at module scope in one direction only (`appearances/transitions.py` is the
half that comes back the other way, into `write.py`).
"""

from __future__ import annotations

from ..appearances import cast
from ..frontmatter import parse_frontmatter, parse_frontmatter_head
from ..paths import safe_id
from . import paths, serialize


def list_scenes(cid: str) -> list[dict]:
    paths._require_campaign(cid)
    out: list[dict] = []
    d = paths._scenes_dir(cid)
    if d.exists():
        for p in d.glob("*.md"):
            if not safe_id(p.stem):   # enumeration agrees with the resolvers
                continue
            meta = parse_frontmatter_head(p)  # never reads the transcript body
            history = histories(meta)["times"]
            out.append({
                "id": p.stem,
                "title": meta.get("title", p.stem),
                "model": meta.get("model", ""),
                "created": meta.get("created", ""),
                "updated": meta.get("updated", ""),
                "date": history[0] if history else "",
                "pcless": meta.get("pcless") == "true",
                # Absorbed and accepted into the chronicle -- `mark_absorbed`
                # writes it. Read with the same tolerance as
                # `routes.scenes._already_absorbed`, which is what actually
                # refuses a second absorb: this file is hand-editable, and a
                # rail that called a scene unfinished while the absorb guard
                # called it done would be worse than either answer alone.
                "done": str(meta.get("done", "")).lower() == "true",
            })
    out.sort(key=lambda m: m["updated"], reverse=True)
    return out


def is_pcless(cid: str, sid: str) -> bool:
    """A scene deliberately without a player character (director-driven)."""
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        return False
    return parse_frontmatter_head(p).get("pcless") == "true"


def read_scene(cid: str, sid: str) -> dict:
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    players = frozenset(cast.player_names(cid, sid))
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {"meta": {"id": sid, **meta}, "messages": serialize._parse_messages(body, players)}


def read_scene_window(cid: str, sid: str, limit: int, before: int | None = None) -> dict:
    """`read_scene` narrowed to a window of the transcript's TAIL.

    The window is the `limit` messages ending just before index `before`
    (`None` ⇒ the end of the transcript), which is how a reader pages
    backwards: ask for the tail, then ask again with the offset the last page
    reported until `has_older` goes false.

    `offset` is the absolute index of `messages[0]` — the same index
    `edit_message`/`split_reply` take, so a client that has only page 2 in hand
    addresses a message exactly as one holding the whole transcript does.

    `has_user_message` answers a question no window can answer for itself:
    whether the transcript contains a player turn ANYWHERE. It is what reroll
    eligibility turns on — the regenerate route refuses an all-assistant
    transcript as an opening post — and an offscreen scene is all-assistant no
    matter how long it grows, so "there are older pages" is not a substitute.

    The file is still parsed whole (a scene is one flat markdown script; there
    is no on-disk pagination and the write path rebuilds the full message list
    regardless). What windowing buys is the client's render path, which is
    where a several-hundred-post scene actually hurts.
    """
    scene = read_scene(cid, sid)
    messages = scene["messages"]
    total = len(messages)
    end = total if before is None else max(0, min(before, total))
    start = max(0, end - max(1, limit))
    return {"meta": scene["meta"], "messages": messages[start:end],
            "offset": start, "total": total, "has_older": start > 0,
            "has_user_message": any(m["role"] == "user" for m in messages)}


def read_scene_meta(cid: str, sid: str) -> dict:
    """A scene's frontmatter without parsing its transcript. For bulk scans
    (response-preset usage) where the messages are irrelevant and reading them
    for every scene in the library is pure waste."""
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    return {"id": sid, **parse_frontmatter_head(p)}


def get_dismissed(cid: str, sid: str) -> list[str]:
    """Suggestion ids the user dismissed for this scene. Missing scene ⇒ none."""
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        return []
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return [x for x in meta.get("dismissed", "").split(",") if x]


def trailing_transitions(messages: list[dict]) -> int:
    """How many messages at the tail are scene-transition lines.

    Reroll steps OVER these: a join/leave/location/time line is a record of
    something the player did, not model output, so it survives a reroll of the
    reply beneath it (whereas a manual dice roll blocks reroll outright — its
    entry lives on in rolls.json and the transcript line must stay in lockstep).
    """
    n = 0
    while n < len(messages) and messages[-1 - n].get("speaker") == serialize.TRANSITION_SPEAKER:
        n += 1
    return n


def histories(meta: dict) -> dict:
    """A scene's two histories out of its frontmatter: `{"locations", "times"}`.

    Both are stored as one comma-joined line and both are read the same way, so
    they are split in one place -- four copies of `[x for x in
    raw.split(",") if x]` is four places for the empty-string entry a bare
    `"".split(",")` produces to be forgotten.

    Takes a `meta` for `rolling_summary_fields`' reason: a caller scoring a
    transcript it has ALREADY read must measure the histories against THAT
    snapshot, or a move that landed between the two reads is counted against a
    transcript that does not contain the post announcing it.
    """
    return {"locations": [x for x in meta.get("location_history", "").split(",") if x],
            "times": [x for x in meta.get("time_history", "").split(",") if x]}


def get_location_history(cid: str, sid: str) -> list[str]:
    """Ordered campaign-location ids this scene has been at; last is current. Missing ⇒ []."""
    return _history(cid, sid, "locations")


def get_time_history(cid: str, sid: str) -> list[str]:
    """Ordered scene moments (native datetime strings); last is current. Missing ⇒ []."""
    return _history(cid, sid, "times")


def _history(cid: str, sid: str, which: str) -> list[str]:
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        return []
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return histories(meta)[which]


def rolling_summary_fields(meta: dict) -> dict:
    """The rolling-summary fields out of a scene's frontmatter (#85).

    `{"summary", "at", "digest", "facts"}` — the prose, how many leading
    messages it was folded from, `rolling_summary.covered_digest` over those
    messages as they stood then, and `rolling_summary.facts_digest` over the
    scene facts the prompt was given.

    Takes a `meta` rather than a `(cid, sid)` so a caller holding a scene it has
    ALREADY read can ask without reading the file again, and review caught why
    that matters: reading twice pairs one snapshot's transcript with another
    snapshot's metadata, so a commit landing between the two produces `at: 11`
    against a 10-message transcript and reports a perfectly current summary as
    stale. One read, one answer.

    `at` is parsed defensively because frontmatter is hand-editable and this sits
    on the play path: a non-numeric value means "we have covered nothing", which
    costs one re-fold, where raising would 500 a panel refresh.
    """
    return {"summary": meta.get("rolling_summary", ""),
            "at": _count(meta.get("rolling_at", "")),
            "digest": meta.get("rolling_digest", ""),
            "facts": meta.get("rolling_facts", "")}


def _count(raw: str) -> int:
    """A non-negative count out of hand-editable frontmatter; junk reads as 0.

    Shared by the two watermarks that live in scene frontmatter
    (`rolling_summary_fields`, `scene_break_fields`) because they answer the
    same question about the same file and must answer it the same way: a
    negative `at` would make "posts since" larger than the transcript, and a
    non-numeric one must cost a re-fold rather than a 500 on the play path."""
    raw = str(raw)
    return int(raw) if raw.isdigit() else 0


def get_rolling_summary(cid: str, sid: str) -> dict:
    """`rolling_summary_fields` for a scene read fresh off disk.

    A scene that has never been summarized, and one that is not there at all,
    both report the empty state: the same posture every other reader in this
    module takes, and the reason the caller needs no separate "does this scene
    have one" question.
    """
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        return {"summary": "", "at": 0, "digest": "", "facts": ""}
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return rolling_summary_fields(meta)


def scene_break_fields(meta: dict) -> dict:
    """The scene-break watermark and proposal out of a scene's frontmatter (#84).

    `{"at", "locs", "times", "digest", "verdict", "reason", "title"}` — the
    transcript length, the location moves and the clock advances the last
    confirmation call already covered, `rolling_summary.covered_digest` over
    that prefix as it stood then, and what the call answered.

    The digest is what makes the three counts mean anything, and it is the same
    answer `rolling_summary_fields` reaches for one line above: the transcript
    is NOT append-only. A reroll replaces the trailing reply, `edit_message`
    rewrites one in place, and `delete_from` shortens it — so "we have already
    asked about the first thirty posts" is not a fact a count can carry. Without
    it a scene rewound from thirty posts to ten and played back up to
    twenty-five reports nothing new for fifteen posts of real story, and nothing
    in the record can notice.

    Takes a `meta` for `rolling_summary_fields`' reason, and it is the same
    reason: reading the file a second time pairs one snapshot's transcript with
    another snapshot's watermark, and a watermark from after a post that the
    transcript in hand does not have reports "nothing new" about a scene that
    has moved on.

    The three counts are parsed defensively, because frontmatter is
    hand-editable and this sits on the play path. A junk value reads as 0,
    which costs at most one extra question; raising would 500 a panel refresh.

    `verdict` is the tri-state the panel renders: `""` means nothing has been
    asked (or the answer was dismissed), `"yes"` and `"no"` are the two answers.
    A bare boolean could not tell the first case from the second.
    """
    return {"at": _count(meta.get("break_at", "")),
            "locs": _count(meta.get("break_locs", "")),
            "times": _count(meta.get("break_times", "")),
            "digest": meta.get("break_digest", ""),
            "verdict": meta.get("break_verdict", ""),
            "reason": meta.get("break_reason", ""),
            "title": meta.get("break_title", "")}


def get_scene_break(cid: str, sid: str) -> dict:
    """`scene_break_fields` for a scene read fresh off disk. A scene that has
    never been asked, and one that is not there at all, both report the empty
    state — `get_rolling_summary`'s posture, for its reason."""
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        return scene_break_fields({})
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return scene_break_fields(meta)


def get_suggested_date(cid: str, sid: str) -> str:
    """The creation-time date hint, if the scene still carries one. Missing ⇒ ""."""
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        return ""
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return meta.get("suggested_date", "")
