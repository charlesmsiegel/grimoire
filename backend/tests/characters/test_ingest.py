"""Tests for the Character Card V2/V3 ingestor.

Covers the deterministic JSON / PNG / charx paths, the relationship and
faction extractor, the optional LLM enrichment hook, and the service
integration (avatar persistence, gallery, structural relationships
round-tripping through frontmatter).
"""

from __future__ import annotations

import base64
import json
import struct
import zipfile
import zlib
from io import BytesIO
from pathlib import Path

import pytest

from grimoire.characters import (
    CharactersService,
    enrich_with_llm,
    extract_relationships_deterministic,
    ingest_character_card_v2,
)
from grimoire.characters.errors import ImportError_
from grimoire.state_store import StateStore
from grimoire.types.characters import (
    CharacterData,
    CharacterImage,
    CharacterImageKind,
    CharacterRole,
    IngestedCharacterCard,
    IngestOptions,
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


async def _seed_setting(store: StateStore, setting_id: str) -> None:
    await store.write_library_file(
        library_id=f"settings/{setting_id}/setting/{setting_id}",
        frontmatter={"id": setting_id, "name": setting_id, "version": 1},
        body="",
        source="test:seed",
    )


def _v2_card_json(**overrides: object) -> dict:
    data = {
        "name": "vivienne",
        "description": (
            "A witty Toreador. Member of the Camarilla and sister of winifred. "
            "Lover of jazz, rival of Mithras. She speaks slowly and softly."
        ),
        "personality": "Charming and sardonic. Uses theatrical metaphors.",
        "scenario": "Late-Victorian London salons.",
        "first_mes": '"Darling, you look terrible."',
        "mes_example": "<START>\n{{char}}: Darling, sit down.\n{{char}}: Don't fuss.",
        "system_prompt": "Stay in voice.",
        "post_history_instructions": "Maintain the period register.",
        "alternate_greetings": ["Oh — back so soon?"],
        "creator": "test",
        "creator_notes": "Inspired by the Camarilla sourcebook.",
        "character_version": "1.2",
        "tags": ["vampire", "toreador"],
    }
    data.update(overrides)
    return {"spec": "chara_card_v2", "data": data}


def _png_with_card(card: dict) -> bytes:
    """Build a minimal PNG containing a `chara` tEXt chunk with `card`."""
    payload_b64 = base64.b64encode(json.dumps(card).encode("utf-8"))
    chunks: list[bytes] = []
    # IHDR for a 1x1 RGBA image (just enough to look like a valid PNG).
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    chunks.append(_make_chunk(b"IHDR", ihdr_data))
    # tEXt chunk: keyword \0 text (text contains the base64 payload).
    text_chunk_data = b"chara\x00" + payload_b64
    chunks.append(_make_chunk(b"tEXt", text_chunk_data))
    # IDAT with a single zlib-compressed empty scanline.
    idat = zlib.compress(b"\x00\xff\xff\xff\xff")
    chunks.append(_make_chunk(b"IDAT", idat))
    chunks.append(_make_chunk(b"IEND", b""))
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks)


def _make_chunk(kind: bytes, data: bytes) -> bytes:
    length = struct.pack(">I", len(data))
    crc = struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return length + kind + data + crc


def _charx_with_card(card: dict, *, avatar_bytes: bytes | None = None) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("card.json", json.dumps(card))
        if avatar_bytes is not None:
            zf.writestr("card.png", avatar_bytes)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# JSON envelope path
# --------------------------------------------------------------------------- #


def test_ingest_json_envelope_extracts_full_v2_fields() -> None:
    raw = json.dumps(_v2_card_json()).encode("utf-8")
    card = ingest_character_card_v2(raw)

    assert card.spec == "chara_card_v2"
    assert card.creator == "test"
    assert card.creator_notes.startswith("Inspired")
    assert card.character_version == "1.2"
    assert card.system_prompt == "Stay in voice."
    assert card.post_history_instructions == "Maintain the period register."
    assert card.alternate_greetings == ["Oh — back so soon?"]
    assert card.avatar_bytes is None

    data = card.data
    assert data.id == "vivienne"
    assert data.name == "vivienne"
    assert data.role == CharacterRole.MAJOR_NPC
    assert data.tags == ["vampire", "toreador"]
    assert data.description.startswith("A witty Toreador")
    assert data.voice.samples, "should pick up dialogue samples"
    assert data.image is not None
    assert data.image.base_prompt.startswith("vivienne")
    # alternate greetings + creator notes appear in the body
    assert "Alternate greetings" in data.body
    assert "Creator notes" in data.body


def test_ingest_rejects_missing_name() -> None:
    raw = json.dumps({"spec": "chara_card_v2", "data": {"description": "x"}}).encode("utf-8")
    with pytest.raises(ImportError_):
        ingest_character_card_v2(raw)


def test_ingest_unknown_spec_emits_warning() -> None:
    raw = json.dumps({"spec": "chara_card_v9", "data": {"name": "X"}}).encode("utf-8")
    card = ingest_character_card_v2(raw)
    assert any("unknown sillytavern spec" in w for w in card.warnings)


def test_ingest_no_samples_warns() -> None:
    raw = json.dumps(
        {"spec": "chara_card_v2", "data": {"name": "Y", "description": "no dialogue here"}}
    ).encode("utf-8")
    card = ingest_character_card_v2(raw)
    assert any("dialogue samples" in w for w in card.warnings)


def test_ingest_role_default_overridable() -> None:
    raw = json.dumps(_v2_card_json()).encode("utf-8")
    card = ingest_character_card_v2(
        raw, options=IngestOptions(role_default=CharacterRole.MINOR_NPC)
    )
    assert card.data.role == CharacterRole.MINOR_NPC


def test_ingest_derive_image_prompt_off() -> None:
    raw = json.dumps(_v2_card_json()).encode("utf-8")
    card = ingest_character_card_v2(raw, options=IngestOptions(derive_image_prompt=False))
    assert card.data.image is None


# --------------------------------------------------------------------------- #
# PNG embedded path
# --------------------------------------------------------------------------- #


def test_ingest_png_with_chara_chunk_extracts_card_and_avatar() -> None:
    png = _png_with_card(_v2_card_json())
    card = ingest_character_card_v2(png)

    assert card.data.name == "vivienne"
    assert card.avatar_bytes == png
    assert card.avatar_mime == "image/png"
    # Embedded avatar should appear in the gallery with a usage description.
    assert any(img.kind == CharacterImageKind.AVATAR for img in card.data.images)
    avatar = next(img for img in card.data.images if img.kind == CharacterImageKind.AVATAR)
    assert avatar.description, "embedded avatar must carry a usage description"
    assert avatar.source == "embedded_avatar"


def test_ingest_png_keep_avatar_off_drops_gallery_entry() -> None:
    png = _png_with_card(_v2_card_json())
    card = ingest_character_card_v2(png, options=IngestOptions(keep_embedded_avatar=False))
    assert card.avatar_bytes is None
    assert all(img.kind != CharacterImageKind.AVATAR for img in card.data.images)


def test_ingest_png_without_chara_chunk_raises() -> None:
    # Build a PNG with no tEXt chunks.
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    chunks = [_make_chunk(b"IHDR", ihdr_data), _make_chunk(b"IEND", b"")]
    bad_png = b"\x89PNG\r\n\x1a\n" + b"".join(chunks)
    with pytest.raises(ImportError_):
        ingest_character_card_v2(bad_png)


# --------------------------------------------------------------------------- #
# charx path
# --------------------------------------------------------------------------- #


def test_ingest_charx_with_avatar() -> None:
    avatar_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    archive = _charx_with_card(_v2_card_json(), avatar_bytes=avatar_png)
    card = ingest_character_card_v2(archive)

    assert card.data.name == "vivienne"
    assert card.avatar_bytes == avatar_png
    assert card.avatar_mime == "image/png"


def test_ingest_charx_missing_card_json_raises() -> None:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "no card")
    with pytest.raises(ImportError_):
        ingest_character_card_v2(buf.getvalue())


# --------------------------------------------------------------------------- #
# Deterministic relationship + faction extraction
# --------------------------------------------------------------------------- #


def test_extract_relationships_picks_up_explicit_phrases() -> None:
    text = (
        "vivienne is the sister of winifred and the wife of Edmund. "
        "She is married to Edmund. She is the mentor of Alistair. "
        "Member of the Camarilla. Leader of the Toreador. "
        "Rival of Mithras. Friend of winifred."
    )
    rels = extract_relationships_deterministic(text)
    kinds = {(r.kind, r.to_ref) for r in rels}
    assert ("sibling", "winifred") in kinds
    assert ("spouse", "edmund") in kinds
    assert ("mentor", "alistair") in kinds
    assert ("rival", "mithras") in kinds
    assert ("friend", "winifred") in kinds
    assert ("faction:member", "camarilla") in kinds
    assert ("faction:leader", "toreador") in kinds


def test_extract_relationships_known_lists_filter_noise() -> None:
    text = "Sister of winifred and ally of His Excellency."
    rels = extract_relationships_deterministic(
        text,
        known_characters={"winifred"},
    )
    # His Excellency is not a known character, so the ally match drops.
    kinds = {(r.kind, r.to_ref) for r in rels}
    assert ("sibling", "winifred") in kinds
    assert ("ally", "his-excellency") not in kinds


def test_extract_relationships_empty_text() -> None:
    assert extract_relationships_deterministic("") == []


def test_ingest_writes_structural_relationships() -> None:
    raw = json.dumps(_v2_card_json()).encode("utf-8")
    card = ingest_character_card_v2(raw)
    rels = {(r.kind, r.to_ref) for r in card.data.structural_relationships}
    assert ("sibling", "winifred") in rels
    assert ("rival", "mithras") in rels
    # "Member of the Camarilla" → faction:member
    assert ("faction:member", "camarilla") in rels


def test_ingest_relationships_disabled() -> None:
    raw = json.dumps(_v2_card_json()).encode("utf-8")
    card = ingest_character_card_v2(raw, options=IngestOptions(extract_relationships=False))
    assert card.data.structural_relationships == []


# --------------------------------------------------------------------------- #
# LLM enrichment
# --------------------------------------------------------------------------- #


async def test_enrich_with_llm_applies_patch() -> None:
    raw = json.dumps(_v2_card_json()).encode("utf-8")
    card = ingest_character_card_v2(raw)

    async def fake_llm(_: IngestedCharacterCard) -> dict:
        return {
            "voice_summary": "Refined: clipped, archaic, dry.",
            "voice_register": "archaic",
            "speech_patterns": ["clipped consonants"],
            "tags": ["camarilla"],
            "dos": ["address strangers as 'darling'"],
        }

    enriched = await enrich_with_llm(
        card,
        fake_llm,
        options=IngestOptions(enrich_with_llm=True),
    )
    assert enriched.data.voice.summary.startswith("Refined")
    assert enriched.data.voice.voice_register == "archaic"
    assert "clipped consonants" in enriched.data.voice.speech_patterns
    assert "camarilla" in enriched.data.tags
    assert "address strangers as 'darling'" in enriched.data.voice.dos
    assert "llm-enriched" in enriched.warnings


async def test_enrich_with_llm_skipped_when_disabled() -> None:
    raw = json.dumps(_v2_card_json()).encode("utf-8")
    card = ingest_character_card_v2(raw)

    async def must_not_run(_: IngestedCharacterCard) -> dict:
        raise AssertionError("LLM hook should not be invoked when enrich_with_llm is False")

    enriched = await enrich_with_llm(card, must_not_run, options=IngestOptions())
    assert enriched == card


async def test_enrich_with_llm_skips_empty_patch() -> None:
    raw = json.dumps(_v2_card_json()).encode("utf-8")
    card = ingest_character_card_v2(raw)

    async def empty(_: IngestedCharacterCard) -> dict:
        return {}

    enriched = await enrich_with_llm(card, empty, options=IngestOptions(enrich_with_llm=True))
    assert enriched.data == card.data


# --------------------------------------------------------------------------- #
# Service integration
# --------------------------------------------------------------------------- #


async def test_service_import_sillytavern_persists_relationships_and_avatar(
    characters: CharactersService, store: StateStore, tmp_path: Path
) -> None:
    await _seed_setting(store, "wod-london")
    png = _png_with_card(_v2_card_json())
    result = await characters.import_sillytavern(png, "wod-london")
    assert "vivienne" in result.created

    char = await characters.get("wod-london", "vivienne")
    # Structural relationships survive the round trip through frontmatter.
    rel_keys = {(r.kind, r.to_ref) for r in char.structural_relationships}
    assert ("sibling", "winifred") in rel_keys
    assert ("faction:member", "camarilla") in rel_keys
    # Avatar was written to disk and the gallery references it.
    assert char.images, "gallery should contain the embedded avatar"
    avatar = char.images[0]
    assert avatar.kind == CharacterImageKind.AVATAR
    assert avatar.source == "embedded_avatar"
    assert avatar.path
    avatar_path = store.data_root / avatar.path
    assert avatar_path.exists(), f"avatar should be persisted at {avatar_path}"


async def test_service_import_character_card_returns_full_ingest(
    characters: CharactersService, store: StateStore
) -> None:
    await _seed_setting(store, "wod-london")
    raw = json.dumps(_v2_card_json()).encode("utf-8")
    result, ingested = await characters.import_character_card(raw, "wod-london")
    assert result.created == ["vivienne"]
    assert ingested.spec == "chara_card_v2"
    assert ingested.creator_notes
    assert ingested.alternate_greetings


async def test_service_add_character_image_appends_to_gallery(
    characters: CharactersService, store: StateStore
) -> None:
    await _seed_setting(store, "wod-london")
    raw = json.dumps(_v2_card_json()).encode("utf-8")
    await characters.import_sillytavern(raw, "wod-london")

    expression = CharacterImage(
        path="angry.png",
        description="Bared fangs, eyes narrowed. Use for combat or hostile scenes.",
        kind=CharacterImageKind.EXPRESSION,
        tags=["angry"],
        source="generated",
        seed=42,
        prompt_used="vivienne, angry, fangs bared",
    )
    updated = await characters.add_character_image(
        "wod-london",
        "vivienne",
        expression,
        image_bytes=b"\x89PNG\r\n\x1a\nfake-bytes",
    )
    assert any(img.kind == CharacterImageKind.EXPRESSION for img in updated.images)
    angry = next(img for img in updated.images if img.kind == CharacterImageKind.EXPRESSION)
    assert "combat or hostile" in angry.description
    assert angry.seed == 42
    on_disk = store.data_root / angry.path
    assert on_disk.exists()


async def test_service_ingest_options_pass_through(
    characters: CharactersService, store: StateStore
) -> None:
    await _seed_setting(store, "wod-london")
    raw = json.dumps(_v2_card_json()).encode("utf-8")
    await characters.import_sillytavern(
        raw,
        "wod-london",
        options=IngestOptions(
            extract_relationships=False,
            derive_image_prompt=False,
            role_default=CharacterRole.MINOR_NPC,
        ),
    )
    char = await characters.get("wod-london", "vivienne")
    assert char.role == CharacterRole.MINOR_NPC
    assert char.structural_relationships == []
    assert char.image is None


async def test_service_ingest_with_llm_enrichment(library, mechanics, store: StateStore) -> None:
    await _seed_setting(store, "wod-london")

    async def fake_llm(_: IngestedCharacterCard) -> dict:
        return {"tags": ["enriched"], "voice_register": "archaic"}

    svc = CharactersService(library, mechanics, ingest_llm=fake_llm)
    raw = json.dumps(_v2_card_json()).encode("utf-8")
    await svc.import_sillytavern(raw, "wod-london", options=IngestOptions(enrich_with_llm=True))
    char = await svc.get("wod-london", "vivienne")
    assert "enriched" in char.tags
    assert char.voice.voice_register == "archaic"


# --------------------------------------------------------------------------- #
# CharacterData round-trip
# --------------------------------------------------------------------------- #


def test_character_data_image_round_trip_via_pydantic() -> None:
    img = CharacterImage(
        path="x.png",
        description="d",
        kind=CharacterImageKind.POSE,
        tags=["a", "b"],
        seed=7,
        prompt_used="prompt",
        source="generated",
    )
    payload = CharacterData(
        id="x",
        name="X",
        role=CharacterRole.MAJOR_NPC,
        images=[img],
    )
    dumped = payload.model_dump()
    reloaded = CharacterData.model_validate(dumped)
    assert reloaded.images == [img]
