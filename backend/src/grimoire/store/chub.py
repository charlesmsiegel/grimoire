"""Read-only client for chub.ai's (undocumented) public API: character and
lorebook lookup, gallery listing. Never writes to disk. Endpoints were
reverse-engineered by live testing -- see
docs/superpowers/specs/2026-06-30-chub-download-design.md for the verified
shapes and the fragility risk that implies.
"""

from __future__ import annotations

import re

import certifi
import httpx

_TIMEOUT = 10.0
_UA = "Mozilla/5.0 (grimoire chub.ai import)"
_PATH_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
_URL_RE = re.compile(r"^https?://(?:www\.)?chub\.ai/characters/([\w.-]+/[\w.-]+)/?$")


class ChubParseError(Exception):
    pass


class ChubFetchError(Exception):
    pass


def parse_full_path(url_or_path: str) -> str | None:
    """Accept a chub.ai character page URL or a bare "creator/slug" path."""
    s = url_or_path.strip()
    if s.startswith(("http://", "https://")):
        s = s.split("?", 1)[0].split("#", 1)[0]
        m = _URL_RE.match(s)
        return m.group(1) if m else None
    return s if _PATH_RE.match(s) else None


def normalize_link(raw: str) -> str | None:
    """Accept any http(s) URL as-is (chub.ai or otherwise), or chub.ai's bare
    "creator/slug" shorthand -- expanded to a full chub.ai character URL so a
    linked/downloaded reference is always stored as one canonical URL shape.
    None if raw is neither a URL nor a recognized chub.ai shorthand."""
    s = raw.strip()
    if s.lower().startswith(("http://", "https://")):
        return s
    full_path = parse_full_path(s)
    return f"https://chub.ai/characters/{full_path}" if full_path else None


def _get_json(url: str) -> dict | None:
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    try:
        with httpx.Client(timeout=_TIMEOUT, verify=certifi.where(), headers=headers) as client:
            r = client.get(url)
            r.raise_for_status()
            return r.json()
    except Exception:  # noqa: BLE001 — best-effort; callers handle a None
        return None


def fetch_character_node(full_path: str) -> dict | None:
    data = _get_json(f"https://api.chub.ai/api/characters/{full_path}?full=true")
    return data.get("node") if data else None


def fetch_lorebook_node(lorebook_id: int) -> dict | None:
    data = _get_json(f"https://api.chub.ai/api/lorebooks/{lorebook_id}?full=true")
    return data.get("node") if data else None


def fetch_gallery_paths(project_id: int) -> list[str]:
    data = _get_json(f"https://gateway.chub.ai/api/gallery/project/{project_id}?limit=48&count=false")
    if not data:
        return []
    nodes = data.get("nodes") or []
    return [n["primary_image_path"] for n in nodes
            if isinstance(n, dict) and n.get("primary_image_path")]
