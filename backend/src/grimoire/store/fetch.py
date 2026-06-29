"""Best-effort image fetching shared by avatar download and asset localization.

Decodes embedded data-URIs, downloads remote images over HTTP(S) with an SSRF
guard and a size cap, and validates results by magic bytes. Never raises into a
caller's happy path — a miss returns None.
"""

from __future__ import annotations

import base64
import ipaddress
import socket
import ssl
from urllib.parse import urlparse

import certifi
import httpx

MAX_BYTES = 100 * 1024 * 1024
IMG_EXTS = ("png", "jpg", "jpeg", "gif", "webp")
_CT_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
           "image/gif": "gif", "image/webp": "webp"}
_MAX_REDIRECTS = 5
_UA = "Mozilla/5.0 (grimoire image fetch)"
# Trust certifi's CA bundle explicitly, independent of any ambient SSL_CERT_FILE.
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())


def sniff_ext(raw: bytes) -> str | None:
    """Identify an image by its magic bytes (some hosts mislabel content-type)."""
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:3] == b"\xff\xd8\xff":
        return "jpg"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return None


def decode_data_uri(uri: str) -> tuple[bytes, str] | None:
    """Decode a `data:image/...;base64,...` URI (no network)."""
    if not uri.startswith("data:"):
        return None
    header, _, b64 = uri.partition(",")
    if "base64" not in header:
        return None
    try:
        raw = base64.b64decode(b64, validate=False)
    except Exception:  # noqa: BLE001
        return None
    if not raw or len(raw) > MAX_BYTES:
        return None
    mime = header[len("data:"):].split(";")[0].strip().lower()
    ext = _CT_EXT.get(mime) or sniff_ext(raw)
    return (raw, ext) if ext else None


def host_is_blocked(host: str) -> bool:
    """True if the host resolves to (or is) a private/loopback/link-local/reserved address."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True  # unresolvable -> block
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            return True
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return True
    return False


def _http_get_bytes(url: str) -> tuple[bytes, str | None]:
    """Fetch bytes, validating each redirect hop and aborting early past the cap."""
    headers = {"User-Agent": _UA, "Accept": "image/*,*/*"}
    with httpx.Client(timeout=10.0, follow_redirects=False, verify=_SSL_CTX, headers=headers) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError("bad url")
            if host_is_blocked(parsed.hostname):
                raise ValueError("blocked host")
            with client.stream("GET", url) as r:
                if r.is_redirect:
                    loc = r.headers.get("location")
                    if not loc:
                        raise ValueError("redirect without location")
                    url = str(r.url.join(loc))
                    continue
                r.raise_for_status()
                cl = r.headers.get("content-length")
                if cl and cl.isdigit() and int(cl) > MAX_BYTES:
                    raise ValueError("too large")
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > MAX_BYTES:
                        raise ValueError("too large")
                return bytes(buf), r.headers.get("content-type")
        raise ValueError("too many redirects")


def download_url(url: str) -> tuple[bytes, str] | None:
    """Download an image; return (bytes, ext) or None on any failure / non-image."""
    try:
        content, ctype = _http_get_bytes(url)
    except Exception:  # noqa: BLE001 — best-effort; callers never fail on a miss
        return None
    if not content or len(content) > MAX_BYTES:
        return None
    sniff = sniff_ext(content)
    ct = (ctype or "").split(";")[0].strip().lower()
    if sniff is None and not ct.startswith("image/"):
        return None
    ext = sniff or _CT_EXT.get(ct) or url.rsplit(".", 1)[-1].lower()
    if ext not in IMG_EXTS:
        ext = "png"
    return content, ext
