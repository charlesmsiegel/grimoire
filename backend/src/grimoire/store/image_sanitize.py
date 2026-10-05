"""Lossless metadata sanitizer for stored images.

`sanitize(data)` rewrites an image *container* so that what a browser does not
draw -- text chunks, EXIF camera and GPS data, XMP, IPTC, comments, thumbnails
-- is gone, while every coded byte of the picture is copied through untouched.
Nothing is decoded or re-encoded. That is what lets the caller hash the result
and call the hash the image's identity: the same pixels with different notes
attached sanitize to the same bytes, and a private note cannot ride along into
another world's bundle or export (docs/superpowers/specs/
2026-10-05-content-addressed-image-store-design.md, section 1.1).

Orientation is the one piece of metadata that is kept, because browsers apply it
(`image_hash.ORIENTS`): a JPEG's EXIF, or a PNG `eXIf` chunk before the first
IDAT, is replaced by a minimal EXIF block holding only that tag (and dropped
outright when the orientation is 1, absent or unreadable). WebP EXIF is dropped
whole, as browsers do not apply it there.

What is kept is allowlisted, never the reverse: a PNG keeps `_PNG_KEEP`, a WebP
`_WEBP_KEEP` (and, inside each animation frame, only its frame data), and a
JPEG's JFIF header is rebuilt with its density but without the thumbnail it
may embed -- a second picture, which need not be this one.

Two properties the callers rely on:

- **Deterministic and idempotent.** The output depends only on the input's
  kept bytes, and `sanitize(sanitize(x)) == sanitize(x)`.
- **Never raises, never guesses.** A format it does not know, or a container that
  does not parse structurally (bad magic, truncated header, a PNG chunk whose
  CRC does not match), comes back as the very bytes received. Falling back to
  the original is the only safe answer; a half-parsed rewrite would be a guess.

Anything after the container's own end marker (PNG `IEND`, JPEG `EOI`, the GIF
trailer, the RIFF size) is dropped, since trailing data is the commonest place
for a second copy of what was just stripped to hide. Padding bytes in a RIFF
file are normalised to zero for the same reason.

Pillow is used only to read an orientation and to build the minimal EXIF; it is
never asked to save an image.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Iterator

from PIL import Image

from . import fetch

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
#: Chunks that change what is drawn. Every other chunk -- text, tIME, bKGD,
#: private ancillary ones -- is dropped; `eXIf` is handled separately.
_PNG_KEEP = frozenset({
    b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND",
    b"iCCP", b"gAMA", b"cHRM", b"sRGB", b"cICP", b"sBIT", b"pHYs",
    b"acTL", b"fcTL", b"fdAT",
})

_ORIENTATION = 0x0112
_EXIF_PREFIX = b"Exif\x00\x00"

#: GIF application extensions that carry playback, not notes.
_GIF_KEEP_APPS = (b"NETSCAPE2.0", b"ANIMEXTS1.0")

_VP8X_EXIF_XMP = 0x08 | 0x04
#: The WebP chunks that carry what is drawn. Every other one -- EXIF, XMP, and
#: any unknown chunk -- is dropped (and EXIF/XMP's VP8X flags cleared).
_WEBP_KEEP = frozenset({b"VP8 ", b"VP8L", b"VP8X", b"ALPH", b"ANIM", b"ANMF", b"ICCP"})
#: Inside an ANMF: a 16-byte frame header, then these (and unknown ones, dropped).
_ANMF_HEADER = 16
_WEBP_FRAME_KEEP = frozenset({b"ALPH", b"VP8 ", b"VP8L"})


class _UnparsedError(Exception):
    """The container is not structurally what its magic bytes claim."""


def sanitize(data: bytes) -> bytes:
    """Return `data` with non-rendering metadata stripped, or `data` itself
    when the format is unknown or the container does not parse. Never raises."""
    return sanitize_checked(data)[0]


def sanitize_checked(data: bytes) -> tuple[bytes, bool]:
    """`(bytes, sanitized)`: what `sanitize` returns, and whether it actually
    rewrote the container. False means `data` came back as received -- a format
    this module does not know, or one that did not parse -- so whatever
    metadata it carried is still in it, and the caller must not treat it as
    clean (the image store gives such bytes an identity of their own rather
    than one a clean copy of the same picture would share). Never raises."""
    try:
        kind = fetch.sniff_ext(data)
        if kind == "png":
            return _png(data), True
        if kind == "jpg":
            return _jpeg(data), True
        if kind == "webp":
            return _webp(data), True
        if kind == "gif":
            return _gif(data), True
    except Exception:  # noqa: BLE001 -- the contract is "never raises"
        pass
    return data, False


# --- orientation -------------------------------------------------------------

def _minimal_exif(blob: bytes) -> bytes | None:
    """An Orientation-only EXIF (with the `Exif\\0\\0` prefix) for the EXIF in
    `blob`, or None when the orientation is 1, absent, or unreadable."""
    try:
        exif = Image.Exif()
        exif.load(blob)
        orientation = exif.get(_ORIENTATION)
        if not isinstance(orientation, int) or not 2 <= orientation <= 8:
            return None
        keep = Image.Exif()
        keep[_ORIENTATION] = orientation
        out = keep.tobytes()
    except Exception:  # noqa: BLE001 -- unreadable EXIF is dropped, not fatal
        return None
    return out if out.startswith(_EXIF_PREFIX) else _EXIF_PREFIX + out


# --- PNG ---------------------------------------------------------------------

def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload)))


def _png(raw: bytes) -> bytes:
    n = len(raw)
    out = [_PNG_SIG]
    pos, first, seen_idat, seen_exif, ended = 8, True, False, False, False
    while pos < n and not ended:
        if pos + 12 > n:
            raise _UnparsedError("truncated chunk header")
        (length,) = struct.unpack(">I", raw[pos:pos + 4])
        end = pos + 12 + length
        if length > 0x7FFFFFFF or end > n:
            raise _UnparsedError("chunk overruns file")
        kind = raw[pos + 4:pos + 8]
        payload = raw[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", raw[end - 4:end])
        if zlib.crc32(kind + payload) != crc:
            raise _UnparsedError("bad CRC")
        if first and kind != b"IHDR":
            raise _UnparsedError("IHDR is not first")
        first = False
        if kind in _PNG_KEEP:
            out.append(raw[pos:end])
            seen_idat = seen_idat or kind == b"IDAT"
            ended = kind == b"IEND"
        elif kind == b"eXIf" and not seen_idat and not seen_exif:
            seen_exif = True
            minimal = _minimal_exif(payload)
            if minimal is not None:
                out.append(_png_chunk(b"eXIf", minimal[len(_EXIF_PREFIX):]))
        pos = end
    if not ended:
        raise _UnparsedError("no IEND")
    return b"".join(out)


# --- JPEG --------------------------------------------------------------------

_JFIF = b"JFIF\x00"
#: "JFIF\0", version (2), units (1), X and Y density (2 each): what is kept of
#: a JFIF APP0. Its thumbnail size and RGB thumbnail are not.
_JFIF_HEADER = len(_JFIF) + 7


def _jpeg_jfif_segment(payload: bytes) -> bytes:
    """A JFIF APP0 rebuilt with the same version, units and density and no
    thumbnail (0x0, no payload) -- an embedded thumbnail is a second picture,
    which need not be this one. b"" (dropped) when too short to have them."""
    if len(payload) < _JFIF_HEADER + 2:
        return b""
    body = payload[:_JFIF_HEADER] + b"\x00\x00"
    return b"\xff\xe0" + struct.pack(">H", len(body) + 2) + body


def _jpeg_keeps(marker: int, payload: bytes) -> bool:
    """Whether a segment other than the Exif APP1 and the JFIF APP0 survives
    (as it stands). A JFXX APP0 -- the JFIF thumbnail extension -- does not."""
    if marker == 0xE2:
        return payload.startswith(b"ICC_PROFILE\x00")
    if marker == 0xEE:
        return payload.startswith(b"Adobe")
    # other APPn (XMP and extended XMP, IPTC, vendor data) and COM go
    return not (0xE0 <= marker <= 0xEF or marker == 0xFE)


def _jpeg_exif_segment(payload: bytes) -> bytes:
    """The Orientation-only APP1 replacing an Exif one, or b"" to drop it."""
    minimal = _minimal_exif(payload)
    if minimal is None or len(minimal) + 2 > 0xFFFF:
        return b""
    return b"\xff\xe1" + struct.pack(">H", len(minimal) + 2) + minimal


def _jpeg_entropy_end(raw: bytes, pos: int) -> int:
    """The offset of the first real marker at or after `pos` (len(raw) when the
    data runs out): escaped 0xFF00, restart markers and fill bytes are data."""
    n = len(raw)
    while True:
        i = raw.find(b"\xff", pos)
        if i < 0 or i + 1 >= n:
            return n
        nxt = raw[i + 1]
        if nxt == 0xFF:
            pos = i + 1
        elif nxt == 0x00 or 0xD0 <= nxt <= 0xD7:
            pos = i + 2
        else:
            return i


def _jpeg_marker(raw: bytes, pos: int) -> tuple[int, int]:
    """(marker, offset of its 0xFF) at `pos`, skipping fill bytes."""
    n = len(raw)
    if pos + 1 >= n or raw[pos] != 0xFF:
        raise _UnparsedError("expected a marker")
    while pos + 1 < n and raw[pos + 1] == 0xFF:
        pos += 1
    if pos + 1 >= n:
        raise _UnparsedError("truncated marker")
    return raw[pos + 1], pos


def _jpeg_segment(raw: bytes, pos: int, marker: int) -> tuple[int, bytes]:
    """(offset past, payload of) the length-prefixed segment at `pos`."""
    if marker in (0x00, 0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
        raise _UnparsedError("unexpected standalone marker")
    if pos + 4 > len(raw):
        raise _UnparsedError("truncated segment header")
    (length,) = struct.unpack(">H", raw[pos + 2:pos + 4])
    end = pos + 2 + length
    if length < 2 or end > len(raw):
        raise _UnparsedError("segment overruns file")
    return end, raw[pos + 4:end]


def _jpeg_kept(marker: int, payload: bytes, segment: bytes, seen: set[int]) -> bytes:
    """What of one segment survives, given the rebuilt kinds already `seen`:
    the first Exif APP1 and the first JFIF APP0 are rebuilt (any later one
    goes), the rest kept or dropped whole by `_jpeg_keeps`."""
    if marker == 0xE1 and payload.startswith(_EXIF_PREFIX):
        rebuild = _jpeg_exif_segment
    elif marker == 0xE0 and payload.startswith(_JFIF):
        rebuild = _jpeg_jfif_segment
    else:
        return segment if _jpeg_keeps(marker, payload) else b""
    if marker in seen:
        return b""
    seen.add(marker)
    return rebuild(payload)


def _jpeg(raw: bytes) -> bytes:
    n = len(raw)
    out = [b"\xff\xd8"]
    pos, seen_sos = 2, False
    seen: set[int] = set()
    while not (seen_sos and pos >= n):  # ran out in entropy data: keep what is there
        marker, pos = _jpeg_marker(raw, pos)
        if marker == 0xD9:
            if not seen_sos:
                raise _UnparsedError("EOI before SOS")
            out.append(b"\xff\xd9")
            break
        end, payload = _jpeg_segment(raw, pos, marker)
        out.append(_jpeg_kept(marker, payload, raw[pos:end], seen))
        pos = end
        if marker == 0xDA:
            seen_sos = True
            data_end = _jpeg_entropy_end(raw, pos)
            out.append(raw[pos:data_end])
            pos = data_end
    if not seen_sos:
        raise _UnparsedError("no scan")
    return b"".join(out)


# --- WebP --------------------------------------------------------------------

def _riff_chunks(raw: bytes, pos: int, end: int) -> Iterator[tuple[bytes, bytes]]:
    """(fourcc, payload) for each chunk in ``raw[pos:end]``. A last chunk whose
    pad byte would fall past `end` is accepted, as libwebp accepts it."""
    while pos < end:
        if pos + 8 > end:
            raise _UnparsedError("truncated chunk header")
        fourcc = raw[pos:pos + 4]
        (size,) = struct.unpack("<I", raw[pos + 4:pos + 8])
        data_end = pos + 8 + size
        if data_end > end or (data_end + (size & 1) > end and data_end != end):
            raise _UnparsedError("chunk overruns its container")
        yield fourcc, raw[pos + 8:data_end]
        pos = min(data_end + (size & 1), end)


def _riff_chunk(fourcc: bytes, payload: bytes) -> bytes:
    """One chunk, its pad byte (if any) zero."""
    return fourcc + struct.pack("<I", len(payload)) + payload + b"\x00" * (len(payload) & 1)


def _webp_frame(payload: bytes) -> bytes:
    """An ANMF payload with only its frame data kept: the 16-byte frame header
    and the ALPH/VP8/VP8L sub-chunks, never the unknown ones a frame may carry."""
    if len(payload) < _ANMF_HEADER:
        raise _UnparsedError("short ANMF")
    kept = [_riff_chunk(k, p)
            for k, p in _riff_chunks(payload, _ANMF_HEADER, len(payload))
            if k in _WEBP_FRAME_KEEP]
    return payload[:_ANMF_HEADER] + b"".join(kept)


def _webp_payload(fourcc: bytes, payload: bytes) -> bytes:
    """A kept chunk's payload as written: VP8X without the EXIF/XMP flags of
    the chunks dropped, an ANMF with only its frame data, the rest as read."""
    if fourcc == b"VP8X":
        if len(payload) < 10:
            raise _UnparsedError("short VP8X")
        return bytes([payload[0] & ~_VP8X_EXIF_XMP & 0xFF]) + payload[1:]
    if fourcc == b"ANMF":
        return _webp_frame(payload)
    return payload


def _webp(raw: bytes) -> bytes:
    n = len(raw)
    if n < 20:
        raise _UnparsedError("too short")
    (riff_size,) = struct.unpack("<I", raw[4:8])
    end = 8 + riff_size
    if riff_size < 4 or end > n:
        raise _UnparsedError("RIFF size overruns file")
    # EXIF, XMP, and every chunk no browser draws, go.
    chunks = [_riff_chunk(fourcc, _webp_payload(fourcc, payload))
              for fourcc, payload in _riff_chunks(raw, 12, end) if fourcc in _WEBP_KEEP]
    if not chunks:
        raise _UnparsedError("no chunks")
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


# --- GIF ---------------------------------------------------------------------

def _gif_sub_blocks(raw: bytes, pos: int) -> int:
    """The offset just past the sub-block chain (and its terminator) at `pos`."""
    n = len(raw)
    while True:
        if pos >= n:
            raise _UnparsedError("truncated sub-blocks")
        size = raw[pos]
        pos += 1 + size
        if pos > n:
            raise _UnparsedError("sub-block overruns file")
        if size == 0:
            return pos


def _gif_extension(raw: bytes, pos: int) -> tuple[bool, int]:
    """(kept, offset past it) for the extension block at `pos`."""
    if pos + 2 > len(raw):
        raise _UnparsedError("truncated extension")
    label = raw[pos + 1]
    end = _gif_sub_blocks(raw, pos + 2)
    if label == 0xFE:
        return False, end
    if label == 0xFF:
        size = raw[pos + 2]
        return raw[pos + 3:pos + 3 + size] in _GIF_KEEP_APPS, end
    return True, end


def _gif_image_end(raw: bytes, pos: int) -> int:
    """The offset past the image descriptor, colour table and data at `pos`."""
    if pos + 10 > len(raw):
        raise _UnparsedError("truncated image descriptor")
    after = pos + 10
    if raw[pos + 9] & 0x80:
        after += 3 * (1 << ((raw[pos + 9] & 7) + 1))
    return _gif_sub_blocks(raw, after + 1)  # +1: LZW minimum code size


def _gif_start(raw: bytes) -> int:
    """The offset of the first block, past the header and global colour table."""
    if len(raw) < 13:
        raise _UnparsedError("too short")
    pos = 13
    if raw[10] & 0x80:
        pos += 3 * (1 << ((raw[10] & 7) + 1))
    if pos > len(raw):
        raise _UnparsedError("truncated colour table")
    return pos


def gif_loop_after_image(data: bytes) -> bool:
    """Whether a GIF carries a loop extension (NETSCAPE2.0 or ANIMEXTS1.0)
    after its first image descriptor.

    Read-only, and here because this is the module that walks GIF blocks. It
    exists for `image_hash`: Pillow reads a loop count only before frame 0,
    while a browser honours one wherever it sits, so such a file cannot be
    hashed by what Pillow reports. The walk goes as far as the blocks parse,
    and answers False for anything that is not a GIF. Never raises."""
    if not data.startswith(b"GIF8"):
        return False
    try:
        pos, seen_image, n = _gif_start(data), False, len(data)
        while pos < n:
            block = data[pos]
            if block == 0x2C:
                seen_image = True
                pos = _gif_image_end(data, pos)
            elif block == 0x21:
                if (seen_image and pos + 3 <= n and data[pos + 1] == 0xFF
                        and data[pos + 3:pos + 3 + data[pos + 2]] in _GIF_KEEP_APPS):
                    return True
                pos = _gif_extension(data, pos)[1]
            else:
                return False            # the trailer, or a block that is not one
    except _UnparsedError:
        pass
    return False


def _gif(raw: bytes) -> bytes:
    n = len(raw)
    pos = _gif_start(raw)
    out = [raw[:pos]]
    while pos < n:
        block = raw[pos]
        if block == 0x3B:
            out.append(b"\x3b")
            return b"".join(out)
        if block == 0x21:
            keep, end = _gif_extension(raw, pos)
        elif block == 0x2C:
            keep, end = True, _gif_image_end(raw, pos)
        else:
            raise _UnparsedError("unknown block")
        if keep:
            out.append(raw[pos:end])
        pos = end
    raise _UnparsedError("no trailer")
