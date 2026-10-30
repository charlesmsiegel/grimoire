"""Pure text rules shared by the V0.2.1 interpreters: no I/O, no clocks.

Where V1 0.2.1 has a rule (frontmatter, ids, hashes), this is a port of that
rule, because the inventory must read a file the way V1 itself read it.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from urllib.parse import unquote, urlsplit

# ---- V1 text decoding and hashing ----------------------------------------------------


class Undecodable(ValueError):
    """Bytes that are not UTF-8: V1's ``read_text(encoding="utf-8")`` raised on them."""


def decode(data: bytes) -> str:
    """Text as V1 read it: strict UTF-8 with universal newlines (a BOM is kept)."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise Undecodable(str(error)) from None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def v1_hash(text: str) -> str:
    """Every content hash V1 records: sha256 of the newline-normalized text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(data: bytes) -> tuple[object, str | None]:
    """(value, None), or (None, a finding code) when V1 could not have read it."""
    try:
        text = decode(data)
    except Undecodable:
        return None, "invalid_utf8"
    try:
        return json.loads(text), None
    except (ValueError, RecursionError):  # a BOM, a 5000-digit int and deep nesting included
        return None, "malformed_json"


# ---- frontmatter (port of V1 store/frontmatter.py) -----------------------------------

_CLOSING_FENCE = re.compile(r"^---\r?$", re.MULTILINE)


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """V1's ``parse_frontmatter``: string scalars only; ``{}`` when unfenced."""
    if text.startswith("---\r\n"):
        rest = text[5:]
    elif text.startswith("---\n"):
        rest = text[4:]
    else:
        return {}, text
    fence = _CLOSING_FENCE.search(rest)
    if fence is None:
        return {}, text
    block = rest[:fence.start()]
    after = rest[fence.end():]
    for _ in range(2):
        if after.startswith("\r\n"):
            after = after[2:]
        elif after.startswith("\n"):
            after = after[1:]
    meta: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        meta[key.strip()] = _unquote(value)
    return meta, after


def split_list(value: str) -> list[str]:
    """A comma-joined frontmatter list, as V1 splits it."""
    return [part.strip() for part in value.split(",") if part.strip()]


# ---- V1 identifiers --------------------------------------------------------------------

def safe_id(value: object) -> bool:
    """V1's ``safe_id``: what its listings admit as a world/record/version id."""
    return (isinstance(value, str) and value not in ("", ".", "..")
            and not any(c in value for c in "/\\:") and not value.endswith((".", " ")))


def valid_image_name(name: object) -> bool:
    """V1's placement-name rule (``image_refs._valid_name``)."""
    return (isinstance(name, str) and safe_id(name) and "." not in name
            and not any(c in name for c in "*?[]\0"))


IMAGE_ID = re.compile(r"px1-[0-9a-f]{64}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
HEX32 = re.compile(r"[0-9a-f]{32}\Z")
#: Outfit and expression slugs (V1 expression_library.contracts).
SLUG = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


# ---- path hazards ----------------------------------------------------------------------

def collision_groups(paths: Iterable[str]) -> tuple[list[list[str]], list[list[str]]]:
    """(normalization groups, case groups): distinct paths that some filesystem
    or sync client would treat as one. Reported, never merged."""
    by_nfc: dict[str, set[str]] = {}
    by_fold: dict[str, set[str]] = {}
    for path in paths:
        by_nfc.setdefault(unicodedata.normalize("NFC", path), set()).add(path)
        by_fold.setdefault(unicodedata.normalize("NFC", path).casefold(), set()).add(path)
    normal = sorted(sorted(group) for group in by_nfc.values() if len(group) > 1)
    nfc_groups = {frozenset(group) for group in normal}
    case = sorted(sorted(group) for group in by_fold.values()
                  if len(group) > 1 and frozenset(group) not in nfc_groups)
    return normal, case


def name_key(name: str) -> str:
    """How a person would say two display names are 'the same name'."""
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


# ---- Markdown image references and URLs ------------------------------------------------

#: CommonMark inline image: ``![alt](destination "optional title")``.
_IMAGE = re.compile(r"!\[[^\]\n]*\]\(\s*(?:<([^>\n]*)>|([^\s)]+))(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'))?\s*\)")


def image_urls(text: str) -> list[str]:
    return [m.group(1) if m.group(1) is not None else m.group(2) for m in _IMAGE.finditer(text)]


def redact_url(url: str) -> str:
    """An external URL without credentials, query or fragment (they can hold tokens)."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return "<unparseable url>"
    return f"{parts.scheme}://{host}{port}{parts.path}"


def is_external(url: str) -> bool:
    return url.lower().startswith(("http://", "https://", "//"))


def url_path_segments(url: str) -> list[str]:
    """The local route's path segments, raw (percent-encoding kept)."""
    try:
        path = urlsplit(url).path
    except ValueError:
        return []
    return [segment for segment in path.split("/") if segment]


def decoded_names(raw: str) -> list[str]:
    """A route's image name, percent-decoded first and raw second, as V1's exporter tries."""
    decoded = unquote(raw)
    return [decoded] if decoded == raw else [decoded, raw]


# ---- safe display ----------------------------------------------------------------------

_SHOWN_CATEGORIES = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}


def escape(text: str) -> str:
    r"""`text` safe for a terminal: controls, format/bidi characters and
    surrogate escapes become ``\uXXXX``; backslashes are doubled."""
    out = []
    for char in text:
        if char == "\\":
            out.append("\\\\")
        elif unicodedata.category(char) in _SHOWN_CATEGORIES:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    return "".join(out)
