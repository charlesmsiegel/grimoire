"""Split LLM responses with XML character/narrator tags into segments."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

_TAG_PATTERN = re.compile(
    r"<(character)\s+ref=\"([^\"]+)\">([\s\S]*?)(?:</character>)"
    r"|<(narrator)>([\s\S]*?)(?:</narrator>)"
    r"|<(character)\s+ref=\"([^\"]+)\">([\s\S]*?)$"
    r"|<(narrator)>([\s\S]*?)$",
)


@dataclass(frozen=True)
class ResponseSegment:
    kind: Literal["character", "narrator"]
    ref: str | None
    body: str


def split_response(text: str) -> list[ResponseSegment]:
    if not text:
        return []

    segments: list[ResponseSegment] = []
    last_end = 0

    for m in _TAG_PATTERN.finditer(text):
        if m.start() > last_end:
            gap = text[last_end : m.start()].strip()
            if gap:
                segments.append(ResponseSegment(kind="narrator", ref=None, body=gap))

        if m.group(1):
            segments.append(ResponseSegment(kind="character", ref=m.group(2), body=m.group(3)))
        elif m.group(4):
            segments.append(ResponseSegment(kind="narrator", ref=None, body=m.group(5)))
        elif m.group(6):
            segments.append(ResponseSegment(kind="character", ref=m.group(7), body=m.group(8)))
        elif m.group(9):
            segments.append(ResponseSegment(kind="narrator", ref=None, body=m.group(10)))

        last_end = m.end()

    if last_end < len(text):
        trailing = text[last_end:].strip()
        if trailing:
            segments.append(ResponseSegment(kind="narrator", ref=None, body=trailing))

    if not segments and text.strip():
        return [ResponseSegment(kind="narrator", ref=None, body=text.strip())]

    segments = [s for s in segments if s.body.strip()]

    merged: list[ResponseSegment] = []
    for seg in segments:
        if merged and merged[-1].kind == seg.kind and merged[-1].ref == seg.ref:
            merged[-1] = ResponseSegment(
                kind=seg.kind,
                ref=seg.ref,
                body=merged[-1].body + "\n\n" + seg.body,
            )
        else:
            merged.append(seg)

    return merged
