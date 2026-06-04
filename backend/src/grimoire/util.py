"""Cross-module utilities.

Small helpers shared across services that don't fit any narrower module.
Keep this file narrow — anything domain-specific belongs in its own package.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

_DEFAULT_ID_HEX_WIDTH = 12
_SLUGIFY_ID_RE = re.compile(r"[^a-z0-9]+")
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def new_id(prefix: str, *, length: int = _DEFAULT_ID_HEX_WIDTH) -> str:
    """Return ``"{prefix}_{N hex chars}"`` from a fresh UUID4.

    ``length`` is the count of hex characters after the prefix (default 12).
    The state store uses 16 for delta/embedding rows; service-layer IDs use 12.
    """
    return f"{prefix}_{uuid.uuid4().hex[:length]}"


def now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(UTC).isoformat()


def parse_iso_datetime(value: Any) -> datetime | None:
    """Parse a stored ISO-8601 value to a ``datetime``; ``None`` if unparseable.

    Accepts an existing ``datetime`` (returned unchanged), an ISO string, or any
    falsy/empty value (-> ``None``). Malformed strings yield ``None`` rather than
    raising — callers that need a hard failure should parse explicitly.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def slugify_id(raw: str, *, fallback: str = "") -> str:
    """Lower-case ``raw`` and collapse non-alphanumeric runs into ``-``.

    Used for case-insensitive identifier matching (e.g. so callers can pass
    ``Alistair-Hyde-Smythe`` and find ``alistair-hyde-smythe``). Returns
    ``fallback`` if nothing usable remains.

    Distinct from :func:`grimoire.files.slug.slugify`, which also strips
    non-ASCII via NFKD normalization and enforces a max length — this helper
    is for ID lookup, not filename generation.
    """
    slug = _SLUGIFY_ID_RE.sub("-", raw.lower()).strip("-")
    return slug or fallback


def safe_json_loads(value: str | dict | list | None) -> Any:
    """Parse JSON string, or return already-parsed dicts/lists unchanged."""
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def safe_json_dumps(value: Any) -> str | None:
    """Serialize to JSON with deterministic key ordering, or None passthrough."""
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, default=str)


def json_equal(left: Any, right: Any) -> bool:
    """Structural equality of two JSON-serializable values (key order ignored)."""
    return safe_json_dumps(left) == safe_json_dumps(right)


def extract_json_object(text: str) -> dict | None:
    """Pull the first JSON object out of LLM output, tolerating ``` fences.

    Strips an optional Markdown code fence, then takes the substring from the
    first ``{`` to the last ``}``. Returns ``None`` if no object is found or it
    doesn't parse — callers treat that as "model didn't produce usable JSON".
    """
    text = text.strip()
    fence = _JSON_FENCE.search(text)
    if fence is not None:
        text = fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def canonicalize_character_ref(ref: str) -> str:
    """Collapse any recognized character-ref spelling to one canonical string.

    Characters are referenced in several equivalent spellings across the
    codebase — the canonical ``library:worlds/<world>/characters/<id>`` and
    ``campaign:emergent/character/<id>`` forms, plus shorthands emitted by the
    campaign creator and the scene reconciliation path:

    - ``emergent/character/<id>`` / ``emergent/<id>`` / ``campaign:emergent/<id>``
      → ``campaign:emergent/character/<id>``
    - ``library:worlds/<w>/character/<id>`` / ``worlds/<w>/characters/<id>`` /
      bare ``<w>/<id>`` → ``library:worlds/<w>/characters/<id>``
    - over-qualified ``<world>/worlds/<world>/characters/<id>`` (a world-stored
      PC ref double-prefixed with its world id) → ``library:worlds/<w>/characters/<id>``

    Normalizing both sides of a comparison lets identity checks (cast-change
    presence, ``is_pc``, PC-enter queueing) line up regardless of which spelling
    was stored (#464). Unrecognized refs are returned unchanged.
    """
    raw = ref.strip()
    if not raw:
        return ref
    # Emergent (campaign-local): every spelling carries the asset id as the
    # trailing path segment.
    if raw.startswith("campaign:emergent/") or raw.startswith("emergent/"):
        asset = raw.rstrip("/").rsplit("/", 1)[-1]
        return f"campaign:emergent/character/{asset}" if asset else ref
    # Library: pull (world, id) from the full, scheme-less, or singular spelling.
    body = raw.partition("library:")[2] if raw.startswith("library:") else raw
    parts = [p for p in body.split("/") if p]
    # Match ``worlds/<w>/characters/<id>`` at the tail of the path. Anchoring on
    # the tail (rather than parts[0]) also collapses an over-qualified ref like
    # ``<world>/worlds/<world>/characters/<id>`` to the canonical form.
    if len(parts) >= 4 and parts[-4] == "worlds" and parts[-2] in {"characters", "character"}:
        return f"library:worlds/{parts[-3]}/characters/{parts[-1]}"
    if len(parts) == 2 and ":" not in raw and parts[0] != "worlds":
        # Bare ``<world>/<id>`` shorthand the campaign creator can register.
        return f"library:worlds/{parts[0]}/characters/{parts[1]}"
    return ref


__all__ = [
    "canonicalize_character_ref",
    "extract_json_object",
    "json_equal",
    "new_id",
    "now_iso",
    "parse_iso_datetime",
    "safe_json_dumps",
    "safe_json_loads",
    "slugify_id",
]
