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

from . import atomic, locks, proposals, response_snapshots, rolls, turnstate
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
    atomic.write_text(_path(cid), json.dumps(data, ensure_ascii=False, indent=2))


def _scope(cid, sid, data):
    token = identity.ensure_identity(cid, sid)
    return data["scenes"].setdefault(token, {"responses": {}, "rounds": {}})


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
    return {**thinking,
        "role": "assistant",
        "speaker": record["speaker"],
        "content": content,
        "response_id": record["id"],
        "response_status": status,
        "response_can_reroll": bool(record.get("snapshot_ref") or record.get("snapshot")),
        "context_changed": False,
    }


def migrate(cid: str, sid: str) -> list[dict]:
    """Assign legacy posts IDs once, under the transcript's campaign lock."""
    with locks.campaign_lock(cid):
        data = _read(cid)
        scope = _scope(cid, sid, data)
        messages = read.read_scene(cid, sid)["messages"]
        changed = False
        for message in messages:
            if (
                message["role"] != "assistant"
                or message.get("speaker") in serialize.SYNTHETIC_SPEAKERS
                or message.get("response_id")
            ):
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
                "variants": [{"id": vid, "content": message["content"], "status": "complete"}],
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
    cid: str, sid: str, *, eligible, automatic, post, run_id, actor_ref=None, note="", turn=None
) -> dict:
    with locks.campaign_lock(cid):
        messages = read.read_scene(cid, sid)["messages"]
        observed_from = next((i for i in range(len(messages) - 1, -1, -1)
                              if messages[i].get("role") == "user"), len(messages))
        appearance_paths.begin_observing(cid, sid, [a["ref"] for a in eligible], observed_from)
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
            "turn": turn,
            "status": "pending",
            "pending_response": None,
            "watermark": len(messages),
            "transcript_hash": transcript_hash(messages),
        }
        scope["rounds"][round_id] = record
        _write(cid, data)
        return copy.deepcopy(record)


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
    cid: str, sid: str, round_id: str, actor_ref: str, speaker: str, snapshot: dict
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
) -> dict:
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


def editable(cid: str, sid: str, rid: str) -> int:
    messages = read.read_scene(cid, sid)["messages"]
    index = next((i for i, m in enumerate(messages) if m.get("response_id") == rid), None)
    if index is None:
        raise ResponseNotFound(rid)
    record = _scope(cid, sid, _read(cid))["responses"].get(rid, {})
    unknown_audit_boundary = any(r.get("scene") == sid for r in rolls.read(cid)) and not any(
        m.get("speaker") == serialize.ROLL_SPEAKER for m in messages
    )
    if (
        record.get("mechanically_locked")
        or unknown_audit_boundary
        or any(m.get("speaker") == serialize.ROLL_SPEAKER for m in messages[index:])
    ):
        raise ResponseConflict(
            "applied_mechanics",
            "A completed roll follows this response. Correct state or explicitly replay before changing this prose.",
        )
    return index


def _invalidate(cid, sid, index):
    data = _read(cid)
    scope = _scope(cid, sid, data)
    for round_record in scope["rounds"].values():
        if round_record["status"] in ("pending", "incomplete", "paused"):
            round_record["status"] = "superseded"
    _write(cid, data)
    proposals.supersede(cid, sid)
    turnstate.supersede(cid, sid, index)
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


def save_resume_prompt(cid: str, sid: str, rid: str, snapshot: dict) -> None:
    with locks.campaign_lock(cid):
        data = _read(cid)
        reference = response_snapshots.write(cid, rid, "resume", snapshot)
        record = _scope(cid, sid, data)["responses"][rid]
        record["resume_snapshot_ref"] = reference
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
