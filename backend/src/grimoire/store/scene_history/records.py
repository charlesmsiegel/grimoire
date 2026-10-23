"""Content-addressed public checkpoints, never a source of narrative facts.

A post's token identifies one complete observation. An absent or damaged
checkpoint means unknown state; another token's state cannot fill that gap.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil

from .. import atomic, locks
from ..campaigns import paths


def token_for(identity: str, state: dict) -> str:
    return hashlib.sha256(json.dumps([identity, state], sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def directory(cid: str, identity: str):
    if not re.fullmatch(r"[0-9a-f]{32}", identity):
        raise ValueError("Invalid scene identity")
    return paths.campaign_root(cid) / "scene-history" / identity


def save_checkpoint(cid: str, identity: str, token: str, state: dict) -> None:
    if token != token_for(identity, state):
        raise ValueError("Checkpoint token mismatch")
    with locks.campaign_lock(cid):
        path = directory(cid, identity) / (token + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic.write_text(path, json.dumps({"format": 1, "state": state}))


def checkpoint(cid: str, identity: str, token: str | None) -> dict | None:
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
        return None
    try:
        body = json.loads(atomic.read_text(directory(cid, identity) / (token + ".json")))
        state = body["state"]
        if body["format"] == 1 and isinstance(state, dict) and token_for(identity, state) == token:
            return state
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def drop(cid: str, identity: str) -> None:
    with locks.campaign_lock(cid):
        path = directory(cid, identity)
        if path.exists():
            shutil.rmtree(path)


def clone_tokens(cid: str, source: str, target: str, tokens: set[str]) -> dict[str, str]:
    """Copy only requested retained observations, rekeyed to the new identity."""
    mapped = {}
    with locks.campaign_lock(cid):
        for token in tokens:
            state = checkpoint(cid, source, token)
            if state is not None:
                new = token_for(target, state)
                save_checkpoint(cid, target, new, state)
                mapped[token] = new
    return mapped
