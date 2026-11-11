"""The PNG card reader (``grimoire.cardstore.png``): the card document in a ``ccv3`` or ``chara`` text chunk.

Every picture and card is synthetic, built here with zlib and struct. The reader is strict: a file that is not
a whole, sound PNG is refused by name before any card is read out of it, and no input makes it raise anything
but ``CardError``.
"""

from __future__ import annotations

import base64
import json
import random
import struct

import pytest

from card_fixtures import V3
from png_fixtures import SIGNATURE, as_v2, chunk, embed, picture_chunks, png, png_card, text_chunk

from grimoire.cards import CardError, parse_card
from grimoire.cardstore.png import MAX_PNG_BYTES, MAX_PNG_CHUNKS, png_chunks, read_png_card

LIMIT = 2 * 1024 * 1024


def read(data: bytes):
    return read_png_card(data, max_card_bytes=LIMIT)


def refusal(data: bytes) -> tuple[str, str | None]:
    with pytest.raises(CardError) as caught:
        read(data)
    return caught.value.reason, caught.value.detail


def test_a_v3_card_is_read_from_its_ccv3_chunk_exactly():
    document = json.dumps(V3).encode("utf-8")
    found = read(png_card(document))
    assert (found.keyword, found.data) == ("ccv3", document)
    assert parse_card(json.loads(found.data)).name == "Mara Vell"


def test_an_older_card_is_read_from_its_chara_chunk():
    found = read(png_card(as_v2(V3), keyword="chara"))
    assert found.keyword == "chara"
    parsed = parse_card(json.loads(found.data))
    assert (parsed.format, parsed.name) == ("chara_card_v2", "Mara Vell")


@pytest.mark.parametrize("order", [("ccv3", "chara"), ("chara", "ccv3")])
def test_ccv3_is_preferred_when_both_are_present_whatever_their_order(order):
    documents = {"ccv3": V3, "chara": as_v2(V3)}
    data = png(*[text_chunk(key, embed(documents[key])) for key in order])
    found = read(data)
    assert found.keyword == "ccv3" and json.loads(found.data)["spec"] == "chara_card_v3"


def test_a_keyword_is_matched_whatever_its_case_and_other_text_is_passed_over():
    data = png(text_chunk("Comment", b"made for a test"), text_chunk("Chara", embed(as_v2(V3))),
               chunk(b"zTXt", b"chara\x00\x00junk"), chunk(b"iTXt", b"ccv3\x00\x00\x00\x00\x00junk"))
    assert read(data).keyword == "chara"


def test_base64_may_carry_surrounding_whitespace():
    assert read(png(text_chunk("ccv3", b"  " + embed(V3) + b"\n"))).keyword == "ccv3"


def test_the_same_card_twice_under_one_keyword_is_read_once_and_two_different_ones_are_refused():
    assert read(png(text_chunk("chara", embed(as_v2(V3))), text_chunk("chara", embed(as_v2(V3))))).keyword == "chara"
    other = as_v2(V3)
    other["data"]["name"] = "Someone Else"
    assert refusal(png(text_chunk("chara", embed(as_v2(V3))), text_chunk("chara", embed(other)))) == \
        ("invalid_png", "duplicate_card")


def test_a_sound_picture_with_no_card_is_named_as_that():
    assert refusal(png()) == ("no_embedded_card", "png")
    assert refusal(png(text_chunk("Comment", b"no card here"))) == ("no_embedded_card", "png")
    # a card in a compressed or international text chunk is not read: only tEXt is
    assert refusal(png(chunk(b"zTXt", b"chara\x00\x00junk"))) == ("no_embedded_card", "png")


@pytest.mark.parametrize("data, detail", [
    (b"\x89PNG\r\n\x1a\n", "truncated"),                                         # nothing after the signature
    (SIGNATURE + b"\x00\x00", "truncated"),                                        # half a chunk head
    (png_card(V3)[:-1], "truncated"),                                              # the last byte gone
    (png_card(V3)[:-12], "truncated"),                                             # no end chunk
    (png_card(V3)[:60], "truncated"),                                              # cut inside the card
    (SIGNATURE + struct.pack(">I", 0x7FFFFFFF) + b"IHDR" + b"\x00" * 30, "truncated"),  # a length past the file
    (SIGNATURE + struct.pack(">I", 0xFFFFFFFF) + b"IHDR" + b"\x00" * 30, "truncated"),
    (png_card(V3) + b"\x00", "trailing_data"),
    (png_card(V3) + chunk(b"tEXt", b"chara\x00AAAA"), "trailing_data"),
    (SIGNATURE + text_chunk("ccv3", embed(V3)) + b"".join(picture_chunks()), "header"),  # a card before the header
    (SIGNATURE + chunk(b"IHDR", b"\x00" * 12) + b"".join(picture_chunks()[1:]), "header"),
    (SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", 0, 2, 8, 0, 0, 0, 0)) + b"".join(picture_chunks()[1:]), "header"),
    (SIGNATURE + b"".join(picture_chunks()[:1] * 2 + picture_chunks()[1:]), "header"),   # two headers
    (SIGNATURE + picture_chunks()[0] + chunk(b"tE1t", b"x") + b"".join(picture_chunks()[1:]), "chunk_type"),
    (SIGNATURE + picture_chunks()[0] + chunk(b"IDAT", b"abc", crc=0) + picture_chunks()[2], "crc"),
    (SIGNATURE + picture_chunks()[0] + chunk(b"tEXt", b"ccv3\x00AAAA", crc=1) + b"".join(picture_chunks()[1:]), "crc"),
    (SIGNATURE + picture_chunks()[0] + picture_chunks()[2], "no_image_data"),
    (SIGNATURE + b"".join(picture_chunks()[:2]) + chunk(b"IEND", b"x"), "no_end"),
    (png(chunk(b"tEXt", b"no separator")), "text"),
    (png(chunk(b"tEXt", b"\x00text with no keyword")), "text"),
    (png(chunk(b"tEXt", b"k" * 80 + b"\x00text")), "text"),
    (png(text_chunk("ccv3", b"not base64!")), "base64"),
    (png(text_chunk("chara", b"QUJD=")), "base64"),
])
def test_a_damaged_png_is_refused_by_name(data, detail):
    assert refusal(data) == ("invalid_png", detail)


def test_a_file_that_is_no_png_is_refused():
    assert refusal(b"GIF89a not a png") == ("invalid_png", "signature")
    assert refusal(b"") == ("invalid_png", "signature")
    with pytest.raises(TypeError):
        read("a string")  # type: ignore[arg-type]


def test_a_damaged_card_chunk_is_refused_even_when_a_sound_one_is_preferred():
    # the whole file is checked first: a bad checksum anywhere refuses it, ccv3 or not
    data = png(text_chunk("ccv3", embed(V3)), chunk(b"tEXt", b"chara\x00AAAA", crc=7))
    assert refusal(data) == ("invalid_png", "crc")


def test_sizes_are_bounded_before_they_are_used():
    assert refusal(SIGNATURE + b"\x00" * (MAX_PNG_BYTES + 1 - len(SIGNATURE))) == ("too_large", "png")
    with pytest.raises(CardError) as caught:
        read_png_card(png_card(V3), max_card_bytes=64)
    assert (caught.value.reason, caught.value.detail) == ("too_large", "ccv3")
    # a text that fits the base64 bound and decodes to one byte too many
    with pytest.raises(CardError) as caught:
        read_png_card(png(text_chunk("chara", base64.b64encode(b"x" * 65))), max_card_bytes=64)
    assert caught.value.reason == "too_large"
    assert read_png_card(png(text_chunk("chara", base64.b64encode(b"x" * 64))), max_card_bytes=64).data == b"x" * 64
    header, data, end = picture_chunks()
    many = SIGNATURE + header + chunk(b"tIME") * MAX_PNG_CHUNKS + data + end
    assert refusal(many) == ("invalid_png", "too_many_chunks")


def test_a_refusal_never_echoes_the_card():
    other = as_v2(V3)
    other["data"]["name"] = "Someone Else"
    for data in (png_card(V3)[:-1], png(text_chunk("chara", embed(as_v2(V3))), text_chunk("chara", embed(other)))):
        with pytest.raises(CardError) as caught:
            read(data)
        assert "Mara" not in str(caught.value) and "Someone" not in str(caught.value)


def test_chunks_are_listed_in_order_for_a_sound_file():
    kinds = [kind for kind, _ in png_chunks(png_card(V3))]
    assert kinds == [b"IHDR", b"tEXt", b"IDAT", b"IEND"]


def test_no_mutation_of_a_png_card_makes_the_reader_do_anything_but_answer_or_refuse():
    """Fuzz-ish, seeded: flipped bytes, cuts, splices and repeats of a valid card. Whatever comes out is either
    a card that was really in the file or a ``CardError``; a flipped byte inside a chunk never passes its CRC."""
    rng = random.Random(1107)
    good = png_card(V3, chara=as_v2(V3))
    documents = {json.dumps(V3, ensure_ascii=False).encode("utf-8"), json.dumps(as_v2(V3), ensure_ascii=False).encode("utf-8")}
    answered = refused = 0
    for _ in range(1500):
        data = bytearray(good)
        how = rng.randrange(5)
        if how == 0:
            data[rng.randrange(len(data))] ^= 1 << rng.randrange(8)
        elif how == 1:
            del data[rng.randrange(len(data)):]
        elif how == 2:
            start = rng.randrange(len(data))
            del data[start:start + rng.randrange(1, 40)]
        elif how == 3:
            start = rng.randrange(len(data))
            data[start:start] = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 40)))
        else:
            start, end = sorted(rng.randrange(len(data)) for _ in range(2))
            data[end:end] = data[start:end]
        try:
            found = read(bytes(data))
        except CardError as error:
            refused += 1
            assert error.reason in ("invalid_png", "no_embedded_card", "too_large")
            continue
        answered += 1
        assert found.data in documents, how  # only a card whose chunk passed its checksum unchanged
    assert refused > 1000 and answered + refused == 1500


def test_random_bytes_after_a_signature_are_refused():
    rng = random.Random(42)
    for _ in range(300):
        data = SIGNATURE + bytes(rng.randrange(256) for _ in range(rng.randrange(0, 200)))
        with pytest.raises(CardError):
            read(data)
