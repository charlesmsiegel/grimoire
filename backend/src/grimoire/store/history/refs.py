"""One ref form at joins with 08's slash-form metadata."""

from __future__ import annotations


def norm(ref: str) -> str:
    return ref.strip().replace("/", ":", 1) if isinstance(ref, str) else ""
