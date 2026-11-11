"""Read the card document a PNG character card carries in a text chunk: bytes in, bytes out, no library.

A PNG card is an ordinary picture with the card's JSON, base64-encoded, in a ``tEXt`` chunk whose keyword is
``ccv3`` (Character Card V3) or the older ``chara`` (Character Card V2). ``read_png_card`` walks the chunks
and returns that JSON's bytes; ``ccv3`` is preferred when both are present. Pixels are never decoded.

The reader is strict, because the file comes from anywhere: the signature, the header chunk, every chunk's
length and CRC, the end chunk and the absence of anything after it are all checked, and every size is
bounded before it is used. Each refusal is a ``CardError`` whose ``reason`` is ``invalid_png`` (``detail``
says what: ``signature``, ``truncated``, ``header``, ``chunk_type``, ``crc``, ``no_image_data``, ``no_end``,
``trailing_data``, ``too_many_chunks``, ``text``, ``duplicate_card``, ``base64``), ``no_embedded_card`` (a
sound picture with no card in it) or ``too_large``. Messages name the fault, never the file's contents.
"""

from __future__ import annotations

import base64
import binascii
import re
import struct
import zlib
from collections.abc import Iterator
from dataclasses import dataclass

from grimoire.cards import CardError

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
#: the largest PNG card read (038's cap on one picture: the file is also the card's portrait)
MAX_PNG_BYTES = 32 * 1024 * 1024
#: more chunks than any picture has; a file of only empty chunks would otherwise hold millions
MAX_PNG_CHUNKS = 65536
#: the text-chunk keywords that hold a card, the preferred one first
CARD_KEYWORDS = ("ccv3", "chara")
_BASE64 = re.compile(rb"(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?")
_LETTERS = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")


@dataclass(frozen=True)
class EmbeddedCard:
    """The card document found in a PNG: the chunk ``keyword`` it was under and its decoded JSON ``data``."""

    keyword: str
    data: bytes


def _bad(message: str, detail: str) -> CardError:
    return CardError(f"this PNG cannot be read: {message}", "invalid_png", detail)


def png_chunks(data: bytes) -> Iterator[tuple[bytes, bytes]]:
    """Each chunk of a whole PNG file as ``(type, body)``, checked as the module docstring says."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("a PNG is bytes")
    data = bytes(data)
    if len(data) > MAX_PNG_BYTES:
        raise CardError(f"this PNG is larger than {MAX_PNG_BYTES} bytes", "too_large", "png")
    if not data.startswith(PNG_SIGNATURE):
        raise _bad("it does not start as a PNG file does", "signature")
    offset, count, ended, pictured = len(PNG_SIGNATURE), 0, False, False
    while offset < len(data):
        if ended:
            raise _bad("there is data after the end of the picture", "trailing_data")
        if len(data) - offset < 12:
            raise _bad("the file ends in the middle of a chunk", "truncated")
        length, kind = struct.unpack_from(">I4s", data, offset)
        if any(byte not in _LETTERS for byte in kind):
            raise _bad("a chunk has a type that is not four letters", "chunk_type")
        if length > len(data) - offset - 12:
            raise _bad("the file ends in the middle of a chunk", "truncated")
        body = data[offset + 8:offset + 8 + length]
        (stored,) = struct.unpack_from(">I", data, offset + 8 + length)
        if zlib.crc32(body, zlib.crc32(kind)) & 0xFFFFFFFF != stored:
            raise _bad("a chunk's checksum does not match its contents", "crc")
        count += 1
        if count > MAX_PNG_CHUNKS:
            raise _bad(f"it has more than {MAX_PNG_CHUNKS} chunks", "too_many_chunks")
        if count == 1:
            if kind != b"IHDR" or length != 13 or 0 in struct.unpack_from(">II", body):
                raise _bad("it does not begin with a picture header", "header")
        elif kind == b"IHDR":
            raise _bad("it has a second picture header", "header")
        if kind == b"IDAT":
            pictured = True
        if kind == b"IEND":
            if length:
                raise _bad("its end chunk is not empty", "no_end")
            if not pictured:
                raise _bad("it holds no picture data", "no_image_data")
            ended = True
        offset += 12 + length
        yield kind, body
    if count == 0:
        raise _bad("the file ends after its signature", "truncated")
    if not ended:
        raise _bad("the file ends before the picture does", "truncated")


def read_png_card(data: bytes, *, max_card_bytes: int) -> EmbeddedCard:
    """The card document in a PNG card, base64-decoded (at most ``max_card_bytes``). The whole file is
    checked before anything is returned, so a card is never read out of a damaged picture."""
    found: dict[str, bytes] = {}
    for kind, body in png_chunks(data):
        if kind != b"tEXt":
            continue
        keyword, separator, text = body.partition(b"\x00")
        if not separator or not 1 <= len(keyword) <= 79:
            raise _bad("a text chunk has no keyword", "text")
        name = keyword.decode("latin-1").lower()
        if name not in CARD_KEYWORDS:
            continue
        if name in found and found[name] != text:
            raise _bad(f"it holds two different cards under {name}", "duplicate_card")
        found[name] = text
    for name in CARD_KEYWORDS:
        if name not in found:
            continue
        text = found[name].strip(b" \t\r\n")
        if len(text) > (max_card_bytes + 2) // 3 * 4 + 4:
            raise CardError(f"the card in this PNG is larger than {max_card_bytes} bytes", "too_large", name)
        # The shape is checked here, not left to b64decode: before Python 3.13 it accepts padding past the end.
        if not _BASE64.fullmatch(text):
            raise _bad(f"the card under {name} is not base64", "base64")
        try:
            card = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError):
            raise _bad(f"the card under {name} is not base64", "base64") from None
        if len(card) > max_card_bytes:
            raise CardError(f"the card in this PNG is larger than {max_card_bytes} bytes", "too_large", name)
        return EmbeddedCard(name, card)
    raise CardError("this PNG holds no character card: it has no ccv3 or chara text chunk", "no_embedded_card", "png")


__all__ = ["CARD_KEYWORDS", "EmbeddedCard", "MAX_PNG_BYTES", "MAX_PNG_CHUNKS", "PNG_SIGNATURE", "png_chunks",
           "read_png_card"]
