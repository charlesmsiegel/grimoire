"""Bounded, stable identities for individual contributions in a combined scene turn."""

from hashlib import sha256


def contribution_id(tid: str, index: int) -> str:
    """Stable per-post identity within 032's 104-character message-id bound."""
    if index == 0:
        return tid
    candidate = f"{tid}.pc{index + 1}"
    return candidate if len(candidate) <= 104 else "pc-" + sha256(candidate.encode()).hexdigest()
