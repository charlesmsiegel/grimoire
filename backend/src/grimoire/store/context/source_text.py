"""Exact source spans through a section's one macro expansion (#131).

Templates annotate whole contributions; their temporary marks are removed BEFORE
macro expansion, so replacement values, random options and trimming see the
original prompt. Each replacement moves the source spans alongside the text.
Only the inspector keeps those slices; packing and messages receive plain text.

If a malformed macro crosses source boundaries, its result belongs to the source
where it started. Other spans start after that replacement. This preserves the
old prompt even for broken cards without inventing a second expansion.
"""

from __future__ import annotations

import bisect
import re
import uuid


class Capture:
    """One section render's source identities and character spans."""

    def __init__(self):
        self.prefix = f"\x00source:{uuid.uuid4().hex}:"
        self.pattern = re.compile(re.escape(self.prefix) + r"(\d+):(start|end)\x00")
        self.labels: list[dict] = []
        self.spans: list[tuple[int, int, int]] = []

    def mark(self, label: str, text: str, ref: str = "") -> str:
        if not text:
            return text
        index = len(self.labels)
        self.labels.append({"label": label or ref or "Unnamed source",
                            **({"ref": ref} if ref else {})})
        return f"{self.prefix}{index}:start\x00{text}{self.prefix}{index}:end\x00"

    def unmark(self, rendered: str) -> str:
        """Strip marks and the render's outer whitespace, retaining its spans.

        A template may repeat a marked string. Track occurrences rather than
        searching for body text, which can occur elsewhere or name two sources.
        """
        pieces = []
        starts: dict[int, list[int]] = {}
        spans = []
        last = length = 0
        for match in self.pattern.finditer(rendered):
            piece = rendered[last:match.start()]
            pieces.append(piece)
            length += len(piece)
            index = int(match[1])
            if match[2] == "start":
                starts.setdefault(index, []).append(length)
            elif starts.get(index):
                spans.append((starts[index].pop(), length, index))
            last = match.end()
        pieces.append(rendered[last:])
        text = "".join(pieces)
        left, right = len(text) - len(text.lstrip()), len(text.rstrip())
        self.spans = [(max(start, left) - left, min(end, right) - left, index)
                      for start, end, index in sorted(spans)
                      if max(start, left) < min(end, right)]
        return text.strip()

    def sub(self, pattern, repl, text: str, *, flags: int = 0) -> str:
        """The same re.sub, with one offset map shared by all source spans.

        A replacement crossing a boundary maps that boundary to its END:
        the source containing the macro's opening owns the replacement.
        Regex matches and callbacks see unmarked text, exactly as before.
        """
        changes = []

        def replace(match):
            value = repl(match)
            changes.append((match.start(), match.end(), len(value)))
            return value

        result = re.sub(pattern, replace, text, flags=flags)
        ends = [end for _start, end, _size in changes]
        shifts = [0]
        for start, end, size in changes:
            shifts.append(shifts[-1] + size - (end - start))

        def moved(position: int) -> int:
            at = bisect.bisect_right(ends, position)
            if at < len(changes) and changes[at][0] < position:
                start, _end, size = changes[at]
                return start + shifts[at] + size
            return position + shifts[at]

        self.spans = [(moved(start), moved(end), index) for start, end, index in self.spans]
        return result

    def finish(self, text: str) -> tuple[str, list[dict]]:
        """Clip expanded spans to the section's usual final outer strip."""
        left, right = len(text) - len(text.lstrip()), len(text.rstrip())
        sources = []
        occurrences: dict[str, int] = {}
        for start, end, index in self.spans:
            body = text[max(start, left):min(end, right)]
            if body:
                label = self.labels[index]
                identity = label.get("ref") or label["label"]
                occurrence = occurrences.get(identity, 0)
                occurrences[identity] = occurrence + 1
                sources.append({**label, "id": f"{identity}:{occurrence}", "text": body})
        return text.strip(), sources

    def extract(self, rendered: str) -> tuple[str, list[dict]]:
        """World info already expanded its bodies for shedding: just slice."""
        return self.finish(self.unmark(rendered))
