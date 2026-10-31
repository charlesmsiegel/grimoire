"""Logical resource addresses inside one V2 library.

An address is an app-generated slug path, not a display name or an OS path.
Only canonical spellings are accepted, so two different accepted strings can
never name the same resource on a case-insensitive or normalizing filesystem.
Validation is an allowlist: anything not explicitly permitted is rejected.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

MAX_ADDRESS_CHARS = 1024
MAX_SEGMENTS = 32
MAX_SEGMENT_CHARS = 128
MAX_SEGMENT_BYTES = 255
#: Root entries only a V1 (0.2.1) library has (``grimoire.library.V1_ROOT_MARKERS``,
#: duplicated because this kernel is pure). A V2 root holding one beside ``worlds``
#: would read as a V1 library and be refused, so nothing may create them.
V1_MARKER_ROOTS = frozenset({
    "llm_connections", "sampler_presets", "response_presets", ".world-staging",
    ".module-staging", ".char_macros_baked", "prompt_layout.json", "regex.json", "chores.json",
})
#: ``authority`` holds the durable audit, grants and sovereignty ledgers: trusted code only.
RESERVED_ROOTS = frozenset({"secrets", "authority"}) | V1_MARKER_ROOTS

_PUNCTUATION = frozenset("-_.")
# Default_Ignorable_Code_Point ranges (Unicode DerivedCoreProperties). Most are
# already excluded as control/format characters; the rest are letters or marks
# that render as nothing, so a human comparing two addresses could not see them.
_INVISIBLE = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160),
    (0x17B4, 0x17B5), (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E),
    (0x2060, 0x206F), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)
_WINDOWS_DEVICES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{n}" for n in range(10)}
    | {f"lpt{n}" for n in range(10)}
)


class InvalidResourcePath(ValueError):
    """An address failed validation; the message never echoes the input."""


def _canonical(segment: str) -> str:
    return unicodedata.normalize(
        "NFKC", unicodedata.normalize("NFKC", segment).casefold())


def _allowed_char(char: str) -> bool:
    point = ord(char)
    if any(low <= point <= high for low, high in _INVISIBLE):
        return False
    category = unicodedata.category(char)
    return category[0] in "LM" or category == "Nd" or char in _PUNCTUATION


def _check_segment(segment: object) -> None:
    # Exact types only: a str subclass could override the methods checked here.
    if type(segment) is not str or not 1 <= len(segment) <= MAX_SEGMENT_CHARS:
        raise InvalidResourcePath("address segment has an invalid length or type")
    if not all(_allowed_char(char) for char in segment):
        raise InvalidResourcePath("address segment contains a disallowed character")
    if unicodedata.category(segment[0])[0] == "M":
        raise InvalidResourcePath("address segment may not start with a combining mark")
    if segment != _canonical(segment):
        raise InvalidResourcePath("address segment is not in canonical form")
    if len(segment.encode("utf-8")) > MAX_SEGMENT_BYTES:
        raise InvalidResourcePath("address segment is too long in bytes")
    if segment.startswith(".") or segment.endswith("."):
        raise InvalidResourcePath("address segment may not start or end with '.'")
    if segment.split(".", 1)[0] in _WINDOWS_DEVICES:
        raise InvalidResourcePath("address segment is a reserved device name")


@dataclass(frozen=True, slots=True)
class ResourcePath:
    """A validated, canonical logical address; segments are its identity."""

    segments: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.segments) is not tuple or not 1 <= len(self.segments) <= MAX_SEGMENTS:
            raise InvalidResourcePath("address has an invalid number of segments")
        for segment in self.segments:
            _check_segment(segment)

    @classmethod
    def parse(cls, text: object) -> ResourcePath:
        if type(text) is not str:
            raise InvalidResourcePath("address must be a plain string")
        if len(text) > MAX_ADDRESS_CHARS:
            raise InvalidResourcePath("address is too long")
        if text.startswith("/"):
            raise InvalidResourcePath("address must be relative")
        return cls(tuple(text.split("/")))

    def __str__(self) -> str:
        return "/".join(self.segments)

    @property
    def reserved(self) -> bool:
        return self.segments[0] in RESERVED_ROOTS

    @property
    def ascii(self) -> bool:
        return str(self).isascii()

    def is_within(self, prefix: ResourcePath) -> bool:
        return self.segments[:len(prefix.segments)] == prefix.segments
