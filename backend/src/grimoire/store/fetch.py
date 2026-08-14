"""Best-effort image fetching shared by avatar download and asset localization.

Decodes embedded data-URIs, downloads remote images over HTTP(S) with an SSRF
guard and a size cap, and validates results by magic bytes. Never raises into a
caller's happy path — a miss returns None.

The SSRF guard resolves each hop's host exactly once and connects to a literal
address that resolution validated, so the name is never looked up a second time
on the way to the socket — see `resolve_allowed` and `_pinned_request`.
"""

from __future__ import annotations

import base64
import ipaddress
import socket
import ssl

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
    # Bytes first, the URI's label second (#321). The mime written into a
    # data-URI is a claim like any filename, and a JPEG embedded as
    # `data:image/png;base64,...` -- which card exporters do produce -- would
    # otherwise be stored as `.png` and declared `image/png` by everything that
    # reads the stored suffix afterwards. The label still answers for a format
    # `sniff_ext` does not know, which is no worse than it was.
    ext = sniff_ext(raw) or _CT_EXT.get(mime)
    return (raw, ext) if ext else None


def resolve_allowed(host: str) -> list[str] | None:
    """Resolve `host` once and return its addresses, or None if any is non-public.

    Returning the addresses is the point: the caller connects to one of *these*
    rather than handing the hostname back to the socket layer, which would
    resolve it a second time. A DNS-rebinding attacker who controls the
    authoritative server can answer the two lookups differently — public for
    the check, loopback/link-local for the connect — so a check that only
    returns a verdict leaves the connection unguarded.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return None  # unresolvable -> block
    addrs: list[str] = []
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            return None  # e.g. a scoped literal we can't reason about -> block
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return None
        text = str(addr)
        if text not in addrs:
            addrs.append(text)
    return addrs or None


def host_is_blocked(host: str) -> bool:
    """True if the host resolves to (or is) a private/loopback/link-local/reserved address."""
    return resolve_allowed(host) is None


def _pinned_request(client: httpx.Client, u: httpx.URL, addr: str) -> httpx.Request:
    """A GET aimed at `addr` but still addressed to the URL's own hostname.

    The literal address goes in the URL, so nothing re-resolves the name;
    `Host` and the TLS SNI/certificate hostname stay the real one, so
    certificate verification still means what it says. httpx brackets an IPv6
    literal itself when it rewrites the host.

    The A-label (`raw_host`), never `u.host`: ssl re-encodes a unicode
    server_hostname with the stdlib's IDNA-2003 codec, which disagrees with the
    IDNA-2008 label httpx already put in `Host` and we already resolved — for
    `faß.example` that is `fass.example` against `xn--fa-hia.example`, i.e.
    verifying the certificate of a different site than the one we asked for.

    This holds through a proxy too — the literal is what gets CONNECTed, so the
    proxy doesn't re-resolve either. A proxy that allowlists hostnames refuses
    an IP CONNECT; that fails closed (the caller sees the usual None), which is
    the right way round for a guard.
    """
    return client.build_request(
        "GET",
        u.copy_with(host=addr),
        headers={"Host": u.netloc.decode("ascii")},
        extensions={"sni_hostname": u.raw_host.decode("ascii")},
    )


def _send_pinned(client: httpx.Client, u: httpx.URL, addrs: list[str]) -> httpx.Response:
    """Send to the first validated address that accepts a connection."""
    last: Exception | None = None
    for addr in addrs:
        try:
            return client.send(_pinned_request(client, u, addr), stream=True)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            last = exc  # a host with several records: try the next validated one
    raise last or ValueError("no usable address")


def _http_get_bytes(url: str, *, transport: httpx.BaseTransport | None = None) -> tuple[bytes, str | None]:
    """Fetch bytes, validating each redirect hop and aborting early past the cap.

    Every hop resolves its host exactly once and connects to an address that
    resolution validated — see `resolve_allowed`. `transport` is a test seam.

    One parser decides what the host is. Asking `urlparse` what to validate and
    then letting httpx decide what to request is its own bypass: the two
    disagree about IDN hosts, so the name checked would not be the name fetched.
    """
    headers = {"User-Agent": _UA, "Accept": "image/*,*/*"}
    # No keep-alive: the pool keys connections by origin, which is now the
    # pinned address, so a redirect to a different hostname on the same IP
    # would otherwise reuse a connection whose TLS handshake named the old one.
    limits = httpx.Limits(max_keepalive_connections=0)
    with httpx.Client(timeout=10.0, follow_redirects=False, verify=_SSL_CTX,
                      headers=headers, limits=limits, transport=transport) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            u = httpx.URL(url)
            if u.scheme not in ("http", "https") or not u.raw_host:
                raise ValueError("bad url")
            addrs = resolve_allowed(u.raw_host.decode("ascii"))
            if addrs is None:
                raise ValueError("blocked host")
            r = _send_pinned(client, u, addrs)
            try:
                if r.is_redirect:
                    loc = r.headers.get("location")
                    if not loc:
                        raise ValueError("redirect without location")
                    # Joined against the hostname URL, not the pinned one, so a
                    # relative Location keeps the hostname instead of the IP.
                    url = str(u.join(loc))
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
            finally:
                r.close()
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


def download_bytes(url: str) -> bytes | None:
    """Download raw bytes with the same SSRF guard and size cap as
    download_url, but no image-type requirement -- for fetching arbitrary
    files (e.g. a JSON character card) that aren't images."""
    try:
        content, _ctype = _http_get_bytes(url)
    except Exception:  # noqa: BLE001 — best-effort; callers never fail on a miss
        return None
    if not content or len(content) > MAX_BYTES:
        return None
    return content
