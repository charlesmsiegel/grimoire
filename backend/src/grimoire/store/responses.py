"""Response ledger with transcript-owned identities and immutable prompt snapshots.

The transcript is authoritative for membership and current text. The ledger keeps
variants and pending round steps, keyed by scene identity so rename is free and
replacement cannot inherit a dead scene's work. Writes fail closed on corrupt
JSON. Ledger-before-transcript publication may leave an unselected variant after
a crash; it never removes the old response before its replacement exists.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid

from . import atomic, locks, proposals, response_snapshots, rolls
from .appearances import paths as appearance_paths
from .campaigns import paths as campaign_paths
from .paths import now_iso
from .scenes import identity, read, serialize, write


class ResponseNotFound(Exception):  # noqa: N818 - follows existing SceneNotFound store contract
    pass


class ResponseConflict(Exception):  # noqa: N818 - public store domain exception
    def __init__(self, kind, detail):
        self.kind, self.detail = kind, detail
        super().__init__(detail)


def _path(cid):
    return campaign_paths.campaign_root(cid) / "responses.json"


def _read(cid):
    try:
        data = json.loads(_path(cid).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"scenes": {}}
    if not isinstance(data, dict) or not isinstance(data.get("scenes"), dict):
        raise ValueError("invalid response ledger")
    return data


def _write(cid, data):
    # Compact, because this file is campaign-wide and rewritten WHOLE several
    # times a turn (open a round, prepare, save the variant, close the round),
    # each while holding the campaign lock every other writer queues on.
    # `indent` pads it with whitespace nobody reads and, before CPython 3.13
    # (so on the 3.11 floor and on the 3.12 Android embeds), is also what
    # sends `json.dumps` down the pure-Python encoder instead of the C one --
    # the dearer of the two; 3.13's C encoder indents too. Still
    # plain JSON, so a build that wrote it indented and one that writes it
    # compact read each other's files. `ensure_ascii=False` stays: escaping
    # the prose's non-ASCII punctuation would hand the bytes straight back.
    atomic.write_text(
        _path(cid), json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    )


def _scope(cid, sid, data):
    token = identity.ensure_identity(cid, sid)
    return data["scenes"].setdefault(token, {"responses": {}, "rounds": {}})


def actor_refs(cid: str, sid: str) -> dict[str, str]:
    """One read-side index for a scene window's response identities.

    A scene with no ledger (including a manual opener) needs no migration or
    write merely to render its transcript.
    """
    token = identity.scene_identity(cid, sid)
    if not token:
        return {}
    records = _read(cid)["scenes"].get(token, {}).get("responses", {})
    return {rid: record["actor_ref"] for rid, record in records.items()
            if record.get("actor_ref")}


def variants_by_response(
    cid: str, sid: str, *, token: str | None = None
) -> dict[str, tuple[str | None, list[str]]]:
    """Each response's active variant id and every variant id it has, for the
    scene tracker's walk (which keys a character post by its active variant and
    keeps every variant's record, so a swipe back finds its state).

    Read-only, so it never mints: `_scope` would `ensure_identity`, writing the
    scene file to answer a question about it. A scene with no identity has no
    ledger scope either, which is `{}`.

    Takes no lock, as `actor_refs` takes none: the ledger is one file written
    whole through `atomic`, so a single read already sees the active pointer
    and the variant list from one ledger state. A lock here would only add a
    wait -- up to the lock timeout -- in front of every prompt composed while
    something else holds the campaign (`context.assemble` reaches this through
    the tracker's walk). A caller that must decide against what it read holds
    the campaign lock itself, around this read and its write.

    `token` is for a caller that has already resolved the identity and would
    act on an empty answer. `scene_identity` is fail-soft -- an unreadable
    scene file reads as "no identity" -- so resolving it a second time here
    could turn a momentarily unreadable file into `{}`, and a prune would take
    that as "no response has any variant" and discard every one of them.
    """
    if token is None:
        token = identity.scene_identity(cid, sid)
    if not token:
        return {}
    records = _read(cid)["scenes"].get(token, {}).get("responses", {})
    return {rid: (record.get("active_variant"),
                  [v["id"] for v in record.get("variants", [])])
            for rid, record in records.items()}


def transcript_hash(messages):
    public = [{k: m[k] for k in ("role", "speaker", "content") if k in m} for m in messages]
    return hashlib.sha256(
        json.dumps(public, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _id():
    return uuid.uuid4().hex


def _message(record, content, status, variant=None):
    variant = variant or next((v for v in record["variants"] if v["id"] == record["active_variant"]), {})
    # Only an opaque pointer enters transcript metadata. The reasoning itself
    # stays in the response ledger and cannot become scene context or mechanics.
    thinking = {"response_thinking": variant["id"]} if variant.get("reasoning") else {}
    # Only when known, so a variant saved before provenance serializes as it did.
    served = {"connection": variant["connection"]} if variant.get("connection") else {}
    return {**thinking, **served,
        "role": "assistant",
        "speaker": record["speaker"],
        "content": content,
        "response_id": record["id"],
        "response_status": status,
        "response_can_reroll": bool(record.get("snapshot_ref") or record.get("snapshot")),
        "context_changed": False,
    }


def _unassigned(message: dict) -> bool:
    """A model-written post the ledger has no record for yet.

    One predicate for both halves of the migration -- what `migrate` assigns
    and what `needs_migration` looks for -- so the fast path cannot decide a
    scene is finished while `migrate` would still find work in it, nor keep a
    scene on the slow path forever over a line `migrate` will never touch (a
    synthetic speaker is nobody's reply).
    """
    return (
        message["role"] == "assistant"
        and message.get("speaker") not in serialize.SYNTHETIC_SPEAKERS
        and not message.get("response_id")
    )


def needs_migration(messages: list[dict]) -> bool:
    """Whether `migrate` would assign anything in this transcript."""
    return any(_unassigned(m) for m in messages)


def migrate_if_needed(cid: str, sid: str) -> dict:
    """`migrate` for a caller that wants its effect rather than its records,
    and the scene as it stands afterwards: the scene open and the reroll.

    Both used to call `migrate` unconditionally, which takes the campaign lock
    and parses the WHOLE campaign's ledger -- every scene's variants, not this
    one's -- to find, in the steady state, nothing at all: a post is assigned
    its response exactly once. That made opening a scene cost the campaign's
    entire response history, and made it queue behind a turn, which holds the
    lock across its own ledger rewrites. So the transcript is asked first,
    without the lock, and `migrate` runs only when it would do something.

    Double-checked, not merely checked: `migrate` re-reads the ledger and the
    transcript under the lock and decides again, so two opens racing here
    cannot assign one post twice. A post appended between this read and the
    caller's use of it is migrated on the next open -- which is what already
    happened to a post appended just after an open.

    A missing identity also takes the slow path. Nothing here needs one, but
    `migrate` is where an opened scene that predates identities has always
    been given its token (`_scope` -> `ensure_identity`), and runs and
    notifications key on that token; skipping it would leave such a scene
    identity-less until something else happened to ask.
    """
    scene = read.read_scene(cid, sid)
    if not needs_migration(scene["messages"]) and identity.scene_identity(cid, sid):
        return scene
    migrate(cid, sid)
    return read.read_scene(cid, sid)


def migrate(cid: str, sid: str) -> list[dict]:
    """Assign legacy posts IDs once, under the transcript's campaign lock."""
    with locks.campaign_lock(cid):
        data = _read(cid)
        scope = _scope(cid, sid, data)
        messages = read.read_scene(cid, sid)["messages"]
        changed = False
        for message in messages:
            if not _unassigned(message):
                continue
            rid, vid = _id(), _id()
            record = {
                "id": rid,
                "actor_ref": None,
                "speaker": message.get("speaker") or "Grimoire",
                "round_id": None,
                "snapshot": None,
                "active_variant": vid,
                "status": "complete",
                "created": now_iso(),
                # Its connection rides along, so a swipe away and back puts
                # the post's provenance back with its text.
                "variants": [{"id": vid, "content": message["content"], "status": "complete",
                              **({"connection": message["connection"]}
                                 if message.get("connection") else {})}],
            }
            scope["responses"][rid] = record
            message.update(
                {
                    k: v
                    for k, v in _message(record, message["content"], "complete").items()
                    if k.startswith("response_") or k == "context_changed"
                }
            )
            changed = True
        if changed:
            _write(cid, data)
            write.replace_messages(cid, sid, messages, preserve_turn_sizes=True)
        return [
            copy.deepcopy(scope["responses"][m["response_id"]])
            for m in messages
            if m.get("response_id") in scope["responses"]
        ]


def get(cid: str, sid: str, rid: str, *, private=False) -> dict:
    with locks.campaign_lock(cid):
        scope = _scope(cid, sid, _read(cid))
        record = scope["responses"].get(rid)
        if record is None:
            raise ResponseNotFound(rid)
        result = copy.deepcopy(record)
        matching = [m for m in read.read_scene(cid, sid)["messages"] if m.get("response_id") == rid]
        message = matching[0] if matching else None
        if message is not None:
            result.update(
                content="\n\n".join(m["content"] for m in matching),
                context_changed=message.get("context_changed", False),
            )
        else:
            result["content"] = ""
        result["can_reroll"] = bool(result.get("snapshot_ref") or result.get("snapshot"))
        if private:
            if result.get("snapshot_ref"):
                result["snapshot"] = response_snapshots.read(cid, result["snapshot_ref"])
            if result.get("resume_snapshot_ref"):
                result["resume_snapshot"] = response_snapshots.read(
                    cid, result["resume_snapshot_ref"]
                )
        if not private:
            result.pop("snapshot", None)
            result.pop("resume_snapshot", None)
        return result


def new_round(
    cid: str, sid: str, *, eligible, automatic, post, run_id, actor_ref=None, note="", turn=None,
    mode="directed", plan=(), auto_remaining=0, round_index=1, auto_total=0, present=None,
    typed_note: str = "",
) -> dict:
    """Open a round, superseding any unfinished one.

    `note` is what the prompt is steered by, which for an empty send, a replay
    turn or an automatic follow-on round is the `director_note.j2` template;
    `typed_note` is only what the player typed, so a variant's `made_by.note`
    never puts app wording in their mouth.

    `mode` and `plan` are the group-play order this round follows (the refs
    still to speak after `actor_ref`); `auto_remaining`, `round_index` and
    `auto_total` place it in an auto-continue chain. A round written before
    these keys existed reads them with `.get(key, default)` and acts as a
    lone Directed round.

    `present` is everyone in the scene, whose observation is anchored here
    (`begin_observing`) whether or not they may speak this round -- a
    character sitting out or filtered from `eligible` is still watching.
    Defaults to `eligible`."""
    with locks.campaign_lock(cid):
        messages = read.read_scene(cid, sid)["messages"]
        observed_from = next((i for i in range(len(messages) - 1, -1, -1)
                              if messages[i].get("role") == "user"), len(messages))
        watchers = eligible if present is None else present
        appearance_paths.begin_observing(cid, sid, [a["ref"] for a in watchers], observed_from)
        data = _read(cid)
        scope = _scope(cid, sid, data)
        for previous in scope["rounds"].values():
            if previous["status"] in ("pending", "incomplete", "paused"):
                previous["status"] = "superseded"
        round_id = _id()
        record = {
            "id": round_id,
            "eligible": eligible,
            "used": [],
            "automatic": automatic,
            "post": post,
            "run_id": run_id,
            "actor_ref": actor_ref,
            "note": note,
            "typed_note": typed_note,
            "turn": turn,
            "status": "pending",
            "pending_response": None,
            "watermark": len(messages),
            "transcript_hash": transcript_hash(messages),
            "mode": mode,
            "plan": list(plan),
            "auto_remaining": auto_remaining,
            "round_index": round_index,
            "auto_total": auto_total,
        }
        scope["rounds"][round_id] = record
        _write(cid, data)
        return copy.deepcopy(record)


def round_typed_note(cid: str, sid: str, round_id: str | None) -> str:
    """The note the player typed for a round, or "" -- for a round that has
    none, is missing, or for `None` (a migrated response has no round).

    Read-only and lock-free, for `actor_refs`' reasons: it resolves the
    identity without minting (`_scope` would write the scene file) and the
    ledger is one file written whole. A caller deciding against it holds the
    campaign lock itself."""
    if not round_id:
        return ""
    token = identity.scene_identity(cid, sid)
    if not token:
        return ""
    rounds = _read(cid)["scenes"].get(token, {}).get("rounds", {})
    return rounds.get(round_id, {}).get("typed_note", "")


def update_round(cid: str, sid: str, round_id: str, **fields) -> dict:
    with locks.campaign_lock(cid):
        data = _read(cid)
        scope = _scope(cid, sid, data)
        record = scope["rounds"][round_id]
        completed = fields.pop("completed_response", None)
        issue = fields.pop("control_issue", None)
        if completed:
            response = scope["responses"][completed]
            variant = next(v for v in response["variants"] if v["id"] == response["active_variant"])
            variant["issue"] = issue or variant.get("issue")
        record.update(fields)
        _write(cid, data)
        return copy.deepcopy(record)


def unfinished(cid: str, sid: str) -> dict | None:
    with locks.campaign_lock(cid):
        records = _scope(cid, sid, _read(cid))["rounds"].values()
        return next(
            (
                copy.deepcopy(r)
                for r in reversed(list(records))
                if r["status"] in ("pending", "incomplete", "paused")
            ),
            None,
        )


def prepare(
    cid: str,
    sid: str,
    round_id: str,
    actor_ref: str,
    speaker: str,
    snapshot: dict,
    settings: dict | None = None,
) -> dict:
    with locks.campaign_lock(cid):
        data = _read(cid)
        scope = _scope(cid, sid, data)
        record = {
            "id": _id(),
            "round_id": round_id,
            "post": scope["rounds"][round_id]["post"],
            "actor_ref": actor_ref,
            "speaker": speaker,
            "status": "pending",
            "active_variant": None,
            "variants": [],
            "created": now_iso(),
        }
        if settings is not None:
            record["settings"] = copy.deepcopy(settings)
        record["snapshot_ref"] = response_snapshots.write(cid, record["id"], "primary", snapshot)
        scope["responses"][record["id"]] = record
        scope["rounds"][round_id].update(pending_response=record["id"], actor_ref=actor_ref)
        _write(cid, data)
        return copy.deepcopy(record)


def save_variant(
    cid: str,
    sid: str,
    rid: str,
    content: str,
    status: str,
    *,
    handoff=None,
    issue=None,
    activate=True,
    part="",
    reasoning="",
    connection="",
    made_by: dict | None = None,
) -> dict:
    """Append a variant. `made_by` (the call that wrote it) is stored only when
    given: a variant from before it existed, or one whose provenance could not
    be built, has no key at all rather than an empty one."""
    with locks.campaign_lock(cid):
        data = _read(cid)
        record = _scope(cid, sid, data)["responses"][rid]
        messages = read.read_scene(cid, sid)["messages"]
        prefix = (
            [
                m["content"]
                for m in messages
                if m.get("response_id") == rid and m.get("response_part", "") != part
            ]
            if part
            else []
        )
        previous: dict = next((v for v in record["variants"] if v["id"] == record["active_variant"]), {})
        thinking_parts: dict[str, str] = dict(previous.get("reasoning_parts", {})) if part else {}
        thinking_parts[part] = reasoning
        variant = {
            "reasoning": "\n\n".join(s for s in thinking_parts.values() if s),
            "reasoning_parts": thinking_parts,
            "id": _id(),
            "content": "\n\n".join([*prefix, content]),
            "part": part,
            "part_content": content,
            "status": status,
            "handoff": handoff,
            "issue": issue,
            "created": now_iso(),
        }
        if connection:
            variant["connection"] = connection
        if made_by is not None:
            variant["made_by"] = copy.deepcopy(made_by)
        record["variants"].append(variant)
        if activate:
            record.update(active_variant=variant["id"], status=status)
        _write(cid, data)
        if activate and content.strip():
            messages = read.read_scene(cid, sid)["messages"]
            index = next(
                (
                    i
                    for i, m in enumerate(messages)
                    if m.get("response_id") == rid and m.get("response_part", "") == part
                ),
                None,
            )
            message = {**_message(record, content, status), "response_part": part}
            if index is None:
                write.append_reply(cid, sid, [message])
            else:
                messages[index] = message
                write.replace_messages(cid, sid, messages, preserve_turn_sizes=True)
            if status == "complete" and part:
                _complete_parts(cid, sid, rid)
        return copy.deepcopy(variant)


def _response_index(messages: list[dict], rid: str) -> int | None:
    return next((i for i, m in enumerate(messages) if m.get("response_id") == rid), None)


def _editable_reason(record: dict, messages: list[dict], scene_rolls: bool, rid: str) -> str | None:
    """Why this response's prose may not change, or `None` when it may.

    The one place the three checks live, so `editable` (which refuses) and
    `swipe_state` (which reports) cannot drift: the response was mechanically
    locked, the scene has an audit entry but no roll line left in the
    transcript to say where it applied (`scene_rolls`: a roll was logged for
    this scene), or a roll line sits at or after the response's first message.
    """
    index = _response_index(messages, rid)
    has_roll_line = any(m.get("speaker") == serialize.ROLL_SPEAKER for m in messages)
    if (
        record.get("mechanically_locked")
        or (scene_rolls and not has_roll_line)
        or (index is not None
            and any(m.get("speaker") == serialize.ROLL_SPEAKER for m in messages[index:]))
    ):
        return "applied_mechanics"
    return None


def editable(cid: str, sid: str, rid: str) -> int:
    messages = read.read_scene(cid, sid)["messages"]
    index = _response_index(messages, rid)
    if index is None:
        raise ResponseNotFound(rid)
    record = _scope(cid, sid, _read(cid))["responses"].get(rid, {})
    scene_rolls = any(r.get("scene") == sid for r in rolls.read(cid))
    if _editable_reason(record, messages, scene_rolls, rid) is not None:
        raise ResponseConflict(
            "applied_mechanics",
            "A completed roll follows this response. Correct state or explicitly replay before changing this prose.",
        )
    return index


def swipe_state(cid: str, sid: str, rid: str) -> dict:
    """What the swipe arrows need to know about a response, and nothing more.

    Lock-free and write-free, as `variants_by_response` is: the identity comes
    from `scene_identity` (`_scope` would mint one and write the scene file),
    the ledger is one file written whole so a single read is one state, and no
    snapshot is opened. `get` is none of those -- it waits behind the campaign
    lock, mints, and reads snapshots -- which is what a read made on every
    scene open and every landed turn cannot afford. A variant's content and
    reasoning stay out; the disclosure fetches them through `get`.

    `editable` is the answer `editable` would give, evaluated without raising,
    because the client cannot see the audit boundary. `round_open` is an
    unfinished round in the scene: `activate` supersedes it, which would
    destroy a paused roll or a Retry the player has not used. `edited` is the
    transcript's prose differing from the active variant's -- a hand edit
    (`scenes.edit_message`) keeps the response id and leaves the ledger alone
    -- and then `active` is None: the prose is no variant's, so it is not "n of
    m", its provenance is not that variant's, and a swipe would silently
    discard the edit. The comparison is `get`'s join, stripped. A response the
    ledger does not know, or that `delete` left a record of after removing it
    from the transcript, is `ResponseNotFound`.
    """
    messages = read.read_scene(cid, sid)["messages"]
    if _response_index(messages, rid) is None:
        raise ResponseNotFound(rid)
    token = identity.scene_identity(cid, sid)
    scope = _read(cid)["scenes"].get(token, {}) if token else {}
    record = scope.get("responses", {}).get(rid)
    if record is None:
        raise ResponseNotFound(rid)
    variants = record.get("variants", [])
    active = next((i for i, v in enumerate(variants) if v["id"] == record.get("active_variant")), None)
    prose = "\n\n".join(m["content"] for m in messages if m.get("response_id") == rid)
    edited = active is not None and prose.strip() != (variants[active].get("content") or "").strip()
    if edited:
        active = None
    scene_rolls = any(r.get("scene") == sid for r in rolls.read(cid))
    return {
        "active": active,
        "variants": [
            {"id": v["id"], "status": v["status"],
             **({"made_by": copy.deepcopy(v["made_by"])} if "made_by" in v else {})}
            for v in variants
        ],
        "settings": copy.deepcopy(record.get("settings")),
        "resume_settings": copy.deepcopy(record.get("resume_settings")),
        "can_reroll": bool(record.get("snapshot_ref") or record.get("snapshot")),
        "editable": _editable_reason(record, messages, scene_rolls, rid) is None,
        "round_open": any(r.get("status") in ("pending", "incomplete", "paused")
                          for r in scope.get("rounds", {}).values()),
        "edited": edited,
    }


def _invalidate(cid, sid, changed_at: int):
    """Supersede what a change at message index `changed_at` made stale.

    The rolling summary folded the first `at` messages, so a change at or after
    that index leaves both the prose and its digest true -- a swipe on the
    trailing response is the common case, and resetting there forced a re-fold
    from post 0. Only a change inside the fold throws it away. Rounds,
    proposals and the scene-break check reset either way.
    """
    data = _read(cid)
    scope = _scope(cid, sid, data)
    for round_record in scope["rounds"].values():
        if round_record["status"] in ("pending", "incomplete", "paused"):
            round_record["status"] = "superseded"
    _write(cid, data)
    proposals.supersede(cid, sid)
    covered = read.rolling_summary_fields(read.read_scene_meta(cid, sid))["at"]
    if changed_at < covered:
        write.set_rolling_summary(cid, sid, "", 0, "")
    write.set_scene_break(cid, sid, 0, 0, 0)


def delete(cid: str, sid: str, rid: str) -> None:
    with locks.campaign_lock(cid):
        index = editable(cid, sid, rid)
        messages = read.read_scene(cid, sid)["messages"]
        kept_indices = [i for i, m in enumerate(messages) if m.get("response_id") != rid]
        appearance_paths.remap_presence(
            cid, sid, {old: new for new, old in enumerate(kept_indices)}, len(kept_indices)
        )
        messages = [messages[i] for i in kept_indices]
        for message in messages[index:]:
            if message.get("response_id"):
                message["context_changed"] = True
        _invalidate(cid, sid, index)
        write.replace_messages(cid, sid, messages)


def activate(cid: str, sid: str, rid: str, vid: str) -> None:
    with locks.campaign_lock(cid):
        index = editable(cid, sid, rid)
        data = _read(cid)
        record = _scope(cid, sid, data)["responses"][rid]
        variant = next((v for v in record["variants"] if v["id"] == vid), None)
        if variant is None or variant["status"] != "complete":
            raise ResponseConflict(
                "variant_incomplete", "Only a completed variant can be selected."
            )
        messages = read.read_scene(cid, sid)["messages"]
        retained = [i for i, m in enumerate(messages) if i == index or m.get("response_id") != rid]
        appearance_paths.remap_presence(
            cid, sid, {old: new for new, old in enumerate(retained)}, len(retained)
        )
        messages = [messages[i] for i in retained]
        messages[index] = _message(record, variant["content"], "complete", variant)
        for message in messages[index + 1 :]:
            if message.get("response_id"):
                message["context_changed"] = True
        _invalidate(cid, sid, index)
        data = _read(cid)
        record = _scope(cid, sid, data)["responses"][rid]
        record.update(active_variant=vid, status="complete")
        _write(cid, data)
        write.replace_messages(cid, sid, messages)


def save_resume_prompt(
    cid: str, sid: str, rid: str, snapshot: dict, settings: dict | None = None
) -> None:
    with locks.campaign_lock(cid):
        data = _read(cid)
        reference = response_snapshots.write(cid, rid, "resume", snapshot)
        record = _scope(cid, sid, data)["responses"][rid]
        record["resume_snapshot_ref"] = reference
        if settings is not None:
            record["resume_settings"] = copy.deepcopy(settings)
        record.setdefault("resume_snapshot_refs", []).append(reference)
        _write(cid, data)


def publish_saved(cid: str, sid: str, rid: str) -> None:
    """Recover ledger-before-transcript publication without another model call."""
    with locks.campaign_lock(cid):
        record = _scope(cid, sid, _read(cid))["responses"][rid]
        messages = read.read_scene(cid, sid)["messages"]
        variant = next(v for v in record["variants"] if v["id"] == record["active_variant"])
        part = variant.get("part", "")
        index = next((i for i, m in enumerate(messages)
                      if m.get("response_id") == rid and m.get("response_part", "") == part), None)
        text = variant.get("part_content", variant["content"])
        if text.strip():
            message = {**_message(record, text, variant["status"]), "response_part": part}
            if index is None:
                write.append_reply(cid, sid, [message])
            elif messages[index] != message:
                # A retry may have published a partial version of this part.
                # Its presence alone does not mean the completed variant landed.
                messages[index] = message
                write.replace_messages(cid, sid, messages, preserve_turn_sizes=True)
        if variant["status"] == "complete":
            _complete_parts(cid, sid, rid)


def _complete_parts(cid, sid, rid):
    messages = read.read_scene(cid, sid)["messages"]
    changed = False
    for message in messages:
        if message.get("response_id") == rid and message.get("response_status") != "complete":
            message["response_status"] = "complete"
            changed = True
    if changed:
        write.replace_messages(cid, sid, messages, preserve_turn_sizes=True)


def mark_applied(cid: str, sid: str, round_id: str | None = None) -> None:
    """An applied boundary cannot be erased by cutting its displayed dice line."""
    with locks.campaign_lock(cid):
        data = _read(cid)
        scope = _scope(cid, sid, data)
        ids = {m.get("response_id") for m in read.read_scene(cid, sid)["messages"]}
        if round_id:
            ids.add(scope["rounds"][round_id].get("pending_response"))
        for rid in ids:
            if rid in scope["responses"]:
                scope["responses"][rid]["mechanically_locked"] = True
        _write(cid, data)


def drop_scene(cid: str, sid: str) -> None:
    """Remove the deleted identity's variants and known frozen prompt files."""
    with locks.campaign_lock(cid):
        data = _read(cid)
        token = identity.scene_identity(cid, sid)
        scope = data["scenes"].get(token)
        if not scope:
            return
        for record in scope["responses"].values():
            refs = {
                record.get("snapshot_ref"),
                record.get("resume_snapshot_ref"),
                *record.get("resume_snapshot_refs", []),
            }
            for reference in refs - {None}:
                response_snapshots.remove(cid, reference)
        data["scenes"].pop(token, None)
        _write(cid, data)
