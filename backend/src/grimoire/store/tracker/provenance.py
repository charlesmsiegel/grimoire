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
from ..scenes import identity, read
from . import fields, records, walk


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
                     "response_id", "response_part", "excluded") if k in message}
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
            base = {"key": key, "fingerprint": _digest(body)}
    return expected, proven


def capture(cid: str, sid: str, key: str) -> dict | None:
    with locks.campaign_lock(cid):
        return certificates(cid, sid)[0].get(key)


def valid(cid: str, sid: str, key: str, certificate: dict | None) -> bool:
    if not isinstance(certificate, dict) or certificate.get("format") != 1:
        return False
    with locks.campaign_lock(cid):
        return certificates(cid, sid)[0].get(key) == certificate
