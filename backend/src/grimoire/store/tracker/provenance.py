"""Evidence for historical presentation, independent of recoverable index flags.

A certificate names the exact transcript prefix, field definitions and selected
predecessor snapshot used by an update. Verification walks forward once, so an
unproven base poisons its descendants and a newly completed intermediate record
changes their frontier. Reading old data never manufactures evidence for it.
"""

from __future__ import annotations

import hashlib
import json

from .. import locks, responses
from ..scene_history import capture as history_capture
from ..scene_history import records as history_records
from ..scenes import identity, read
from . import expression_fields, fields, records, walk


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":")).encode()).hexdigest()


def certificates(cid: str, sid: str) -> tuple[dict[str, dict | None], dict[str, dict]]:
    """Expected proofs and proven snapshots, in one linear transcript walk.

    Callers hold the campaign lock when pairing this with publication. Index
    status chooses the same base as the ordinary tracker; validity never trusts
    its recoverable flags as proof of freshness.
    """
    ident = identity.scene_identity(cid, sid)
    messages = read.read_scene(cid, sid)["messages"]
    keys, owners = walk._scan(messages, responses.variants_by_response(cid, sid))
    hidden = walk._hidden_keys(messages, owners)
    entries = records.read_index(cid, ident) if ident else {}
    definition = fields.digest(fields.effective(cid, sid))
    prefixes = []
    digest = hashlib.sha256()
    for message in messages:
        canonical = {k: message[k] for k in ("post_id", "role", "speaker", "content",
                     "response_id", "response_part", "scene_state_token", "excluded") if k in message}
        digest.update(_digest(canonical).encode())
        prefixes.append(digest.hexdigest())
    expected: dict[str, dict | None] = {}
    proven: dict[str, dict] = {}
    base = None
    sound = True
    for at, key in keys:
        proof = ({"format": 1, "scene_identity": ident,
                  "post_id": messages[at].get("post_id"), "key": key,
                  "prefix": prefixes[at], "fields": definition, "predecessor": base}
                 if sound and ident and messages[at].get("post_id") and key not in hidden else None)
        expected[key] = proof
        body = records.read_snapshot(cid, ident, key) if ident else None
        if body is not None and entries.get(key, {}).get("status") == "ok" and key not in hidden:
            sound = proof is not None and body.get("provenance") == proof
            if sound:
                proven[key] = body["snapshot"]
            # Fingerprint includes the snapshot itself, not only index flags:
            # manual changes remain observable even after index reconstruction.
            base = {"key": key, "fingerprint": _digest(expression_fields.narrative_body(body))}
    return expected, proven


def capture(cid: str, sid: str, key: str) -> dict | None:
    with locks.campaign_lock(cid):
        proof = certificates(cid, sid)[0].get(key)
        if proof is None:
            return None
        messages = read.read_scene(cid, sid)["messages"]
        at = walk.index_of(cid, sid, key)
        if at is None:
            return None
        checkpoint = history_records.checkpoint(cid, proof["scene_identity"],
                                                 messages[at].get("scene_state_token")) or {}
        base_key, prior = walk.state_before(cid, sid, at)
        present = walk.present_at(cid, sid, at)
        # The ordinary tracker may bootstrap from today's card/play state.
        # VN can certify that only when it matches evidence recorded with the
        # post. Missing historical inputs remain unproven, not retrofitted.
        for ref in present - prior.keys():
            if checkpoint.get("input_digests", {}).get(ref) != history_capture.input_digest(cid, ref):
                return None
        if base_key and present & prior.keys():
            base_at = walk.index_of(cid, sid, base_key)
            base_state = history_records.checkpoint(cid, proof["scene_identity"],
                messages[base_at].get("scene_state_token")) if base_at is not None else None
            versions = {a["ref"]: a["version_id"] for a in (base_state or {}).get("actors", [])}
            current = {a["ref"]: a["version_id"] for a in checkpoint.get("actors", [])}
            if any(versions.get(ref) != current.get(ref) or ref not in current for ref in present & prior.keys()):
                return None
        return proof


def valid(cid: str, sid: str, key: str, certificate: dict | None) -> bool:
    if not isinstance(certificate, dict) or certificate.get("format") != 1:
        return False
    with locks.campaign_lock(cid):
        return capture(cid, sid, key) == certificate
