"""Character Card V2/V3 ingestor — deterministic, with optional LLM enrichment.

The module is the single entry point for taking a SillyTavern-shaped card
(JSON bytes, a ``.charx`` zip, or a PNG with an embedded ``chara`` tEXt
chunk) and projecting it onto Grimoire's ``CharacterData`` shape. The
parse itself is pure / regex-based — no I/O, no model calls — so it's
reproducible across runs and suitable for use in the deterministic golden
tests (spec 17 §L4).

The parser also surfaces:

* The full v2/v3 envelope (``creator_notes``, ``alternate_greetings``,
  ``character_book``, ``extensions``) so callers can display or persist
  them verbatim.
* An ``images`` gallery on the resulting ``CharacterData``. When the
  payload is a PNG, the PNG itself becomes the canonical portrait, with a
  description explaining when to use it. Generated images (from ImageGen)
  are appended to the same list by the service.
* Deterministic structural relationships and faction affiliations
  extracted from the prose (regex against a small set of explicit phrases,
  cross-referenced against the caller's known character/faction slugs to
  avoid spurious matches).

An optional ``enrich_with_llm`` pass refines the voice anchor and tags by
asking the configured LLM to read the description; this is the *only*
part of the pipeline that calls out to a model.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import struct
import zipfile
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

from grimoire.types.characters import (
    CharacterData,
    CharacterImage,
    CharacterImageKind,
    ImagePromptTemplate,
    IngestedCharacterCard,
    IngestOptions,
    StructuralRelationship,
    VoiceAnchor,
)

from .errors import ImportError_

# ----------------------------------------------------------------------- #
# Public API
# ----------------------------------------------------------------------- #

LLMEnrichCallable = Callable[[IngestedCharacterCard], Awaitable["LLMEnrichment"]]
"""Async hook used by :func:`enrich_with_llm`.

Implementations receive the ingested card and return an
:class:`LLMEnrichment` patch. Callers wire this through
:class:`grimoire.characters.service.CharactersService` rather than calling
the ingestor directly.
"""

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


class LLMEnrichment(dict):
    """Loose dict-typed enrichment patch returned by an LLM hook.

    Recognized keys: ``voice_summary``, ``voice_register``,
    ``speech_patterns`` (list[str]), ``dos`` (list[str]), ``donts``
    (list[str]), ``tags`` (list[str]), ``description`` (str), ``aliases``
    (list[str]). Unknown keys are ignored.
    """


def ingest_character_card_v2(
    payload: bytes,
    *,
    options: IngestOptions | None = None,
) -> IngestedCharacterCard:
    """Parse a Character Card V2/V3 payload deterministically.

    Accepts three shapes:

    1. **PNG bytes** — the canonical SillyTavern card format. The card
       JSON is base64-encoded inside a ``tEXt`` chunk named ``chara``
       (v2) or ``ccv3`` (v3 superset). The PNG itself is preserved as an
       embedded portrait when ``options.keep_embedded_avatar`` is true.
    2. **CHARX zip** — a ``.charx`` bundle with ``card.json`` at the
       root. Avatar bytes are pulled from ``card.png`` when present.
    3. **JSON bytes** — either the ``{spec, data}`` envelope or just the
       inner data object.

    Raises :class:`grimoire.characters.errors.ImportError_` when the
    payload is unintelligible. Warnings (recoverable issues like missing
    samples or unknown spec strings) are attached to the returned
    :class:`IngestedCharacterCard`.
    """
    opts = options or IngestOptions()
    warnings: list[str] = []
    avatar_bytes: bytes | None = None
    avatar_mime = ""

    if payload[:8] == _PNG_SIG:
        envelope, png_warnings = _extract_card_from_png(payload)
        warnings.extend(png_warnings)
        if opts.keep_embedded_avatar:
            avatar_bytes = payload
            avatar_mime = "image/png"
    elif payload[:2] == b"PK":
        envelope, avatar_bytes, avatar_mime, zip_warnings = _extract_card_from_charx(payload)
        warnings.extend(zip_warnings)
        if not opts.keep_embedded_avatar:
            avatar_bytes = None
            avatar_mime = ""
    else:
        envelope = _parse_json_payload(payload)

    spec, spec_version, data = _normalize_envelope(envelope)
    if spec and spec not in {"chara_card_v2", "chara_card_v3"}:
        warnings.append(f"unknown sillytavern spec {spec!r}; treating as v2-compatible")

    if not isinstance(data, dict) or not data.get("name"):
        raise ImportError_("character card missing 'name'")

    name = str(data.get("name") or "").strip() or "Unnamed"
    asset_id = _slugify(
        str(data.get("character_book_id") or data.get("char_id") or data.get("id") or name)
    )
    description = str(data.get("description") or "").strip()
    personality = str(data.get("personality") or "").strip()
    scenario = str(data.get("scenario") or "").strip()
    first_mes = str(data.get("first_mes") or "").strip()
    mes_examples = str(data.get("mes_example") or "").strip()
    system_prompt = str(data.get("system_prompt") or "").strip()
    post_history = str(data.get("post_history_instructions") or "").strip()
    creator = str(data.get("creator") or "").strip()
    creator_notes = str(data.get("creator_notes") or "").strip()
    character_version = str(data.get("character_version") or "").strip()
    alt_greetings = [
        str(g) for g in (data.get("alternate_greetings") or []) if isinstance(g, (str, int))
    ]
    character_book = data.get("character_book") or {}
    extensions = data.get("extensions") or {}
    tags_raw = data.get("tags") or []
    tags = [str(t) for t in tags_raw if isinstance(t, (str, int))]

    samples = _extract_dialogue_samples(mes_examples, first_mes)
    if not samples:
        warnings.append("no dialogue samples could be extracted from mes_example/first_mes")

    voice = VoiceAnchor(
        summary=personality or description.split(".", 1)[0].strip(),
        samples=samples,
        speech_patterns=_extract_speech_patterns(personality, description),
    )

    body = _compose_body(
        description=description,
        personality=personality,
        scenario=scenario,
        system_prompt=system_prompt,
        creator_notes=creator_notes,
        post_history=post_history,
        alt_greetings=alt_greetings,
    )

    image_template: ImagePromptTemplate | None = None
    if opts.derive_image_prompt:
        derived_prompt = _derive_image_prompt(name, description, personality, tags)
        if derived_prompt:
            image_template = ImagePromptTemplate(base_prompt=derived_prompt)

    images: list[CharacterImage] = []
    if avatar_bytes and opts.keep_embedded_avatar:
        images.append(
            CharacterImage(
                path=f"avatars/{asset_id}.png",
                description=(
                    "Embedded card portrait. Use for default character display "
                    "and as the reference style for generated variants."
                ),
                kind=CharacterImageKind.AVATAR,
                tags=["canonical", "portrait"],
                source="embedded_avatar",
                created_at=datetime.now(UTC),
            )
        )

    structural: list[StructuralRelationship] = []
    if opts.extract_relationships:
        structural = extract_relationships_deterministic(
            "\n".join(p for p in (description, personality, scenario) if p),
            known_characters=opts.setting_characters,
            known_factions=opts.setting_factions,
        )

    data_payload = CharacterData(
        id=asset_id,
        name=name,
        role=opts.role_default,
        aliases=[],
        tags=tags,
        voice=voice,
        image=image_template,
        images=images,
        structural_relationships=structural,
        description=description.split("\n", 1)[0].strip(),
        body=body,
    )

    return IngestedCharacterCard(
        data=data_payload,
        spec=spec or "",
        spec_version=spec_version,
        creator=creator,
        creator_notes=creator_notes,
        character_version=character_version,
        system_prompt=system_prompt,
        post_history_instructions=post_history,
        alternate_greetings=alt_greetings,
        character_book=character_book if isinstance(character_book, dict) else {},
        extensions=extensions if isinstance(extensions, dict) else {},
        avatar_bytes=avatar_bytes,
        avatar_mime=avatar_mime,
        warnings=warnings,
    )


async def enrich_with_llm(
    card: IngestedCharacterCard,
    llm: LLMEnrichCallable,
    *,
    options: IngestOptions | None = None,
) -> IngestedCharacterCard:
    """Optionally refine ``card`` by asking an LLM to read the description.

    The LLM callable is expected to return an :class:`LLMEnrichment` patch
    with any of the recognized keys (see the class docstring). Unknown
    keys are ignored. The patch is applied conservatively — empty strings
    and empty lists are skipped — so the deterministic parse stays the
    authority for anything the model can't improve on.
    """
    if not (options or IngestOptions()).enrich_with_llm:
        return card
    patch = await llm(card)
    if not patch:
        return card

    data = card.data
    voice = data.voice
    new_voice = voice.model_copy(
        update={
            "summary": _coalesce(patch.get("voice_summary"), voice.summary),
            "voice_register": _coalesce(patch.get("voice_register"), voice.voice_register),
            "speech_patterns": _merge_list(voice.speech_patterns, patch.get("speech_patterns")),
            "dos": _merge_list(voice.dos, patch.get("dos")),
            "donts": _merge_list(voice.donts, patch.get("donts")),
        }
    )
    new_data = data.model_copy(
        update={
            "voice": new_voice,
            "tags": _merge_list(data.tags, patch.get("tags")),
            "aliases": _merge_list(data.aliases, patch.get("aliases")),
            "description": _coalesce(patch.get("description"), data.description),
        }
    )
    return card.model_copy(update={"data": new_data, "warnings": [*card.warnings, "llm-enriched"]})


# ----------------------------------------------------------------------- #
# Relationship / faction extraction
# ----------------------------------------------------------------------- #


# Capture group: a capitalized name token. The leading verbs are matched
# case-insensitively (so a sentence-starting "Rival of X" parses the same
# as a mid-clause "rival of X"), but the captured *target* must still
# start with an uppercase letter to keep us from matching common nouns.
_NAME = r"([A-Z][\w'\- ]{1,40}?)"
_IF = re.IGNORECASE

_REL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"\b(?:son|daughter|child)\s+of\s+{_NAME}\b", _IF), "parent"),
    (re.compile(rf"\b(?:father|mother|parent)\s+(?:of|to)\s+{_NAME}\b", _IF), "child"),
    (re.compile(rf"\b(?:brother|sister|sibling|twin)\s+(?:of|to)\s+{_NAME}\b", _IF), "sibling"),
    (re.compile(rf"\b(?:wife|husband|spouse)\s+(?:of|to)\s+{_NAME}\b", _IF), "spouse"),
    (re.compile(rf"\bmarried\s+to\s+{_NAME}\b", _IF), "spouse"),
    (re.compile(rf"\b(?:mentor|teacher|master)\s+(?:of|to)\s+{_NAME}\b", _IF), "mentor"),
    (re.compile(rf"\b(?:apprentice|student|pupil)\s+(?:of|to)\s+{_NAME}\b", _IF), "apprentice"),
    (re.compile(rf"\b(?:lover|paramour)\s+(?:of|to)\s+{_NAME}\b", _IF), "lover"),
    (re.compile(rf"\b(?:ally|allies)\s+(?:of|with|to)\s+{_NAME}\b", _IF), "ally"),
    (re.compile(rf"\b(?:rival|nemesis)\s+(?:of|to)\s+{_NAME}\b", _IF), "rival"),
    (re.compile(rf"\b(?:enemy|foe)\s+(?:of|to)\s+{_NAME}\b", _IF), "enemy"),
    (re.compile(rf"\b(?:friend|companion)\s+(?:of|to)\s+{_NAME}\b", _IF), "friend"),
]

_FACTION_LEADER = r"(?:leader|head|chief|prince|primogen)"
_FACTION_MEMBER = r"(?:member|initiate|recruit)"

_FACTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"\b{_FACTION_MEMBER}\s+of\s+(?:the\s+)?{_NAME}\b", _IF), "faction:member"),
    (re.compile(rf"\b{_FACTION_LEADER}\s+of\s+(?:the\s+)?{_NAME}\b", _IF), "faction:leader"),
    (re.compile(rf"\bleads\s+(?:the\s+)?{_NAME}\b", _IF), "faction:leader"),
    (re.compile(rf"\b(?:loyal|sworn)\s+to\s+(?:the\s+)?{_NAME}\b", _IF), "faction:member"),
    (re.compile(rf"\bserves\s+(?:the\s+)?{_NAME}\b", _IF), "faction:member"),
    (re.compile(rf"\b(?:joined|joined the)\s+(?:the\s+)?{_NAME}\b", _IF), "faction:member"),
    (re.compile(rf"\b(?:opposes|opposing)\s+(?:the\s+)?{_NAME}\b", _IF), "faction:rival"),
]


def extract_relationships_deterministic(
    text: str,
    *,
    known_characters: Iterable[str] = (),
    known_factions: Iterable[str] = (),
) -> list[StructuralRelationship]:
    """Pull explicit relationship and faction phrases from ``text``.

    Looks only for unambiguous patterns ("son of X", "member of the Y") so
    the result is safe to write directly without confirmation. When
    ``known_characters`` / ``known_factions`` are provided, the extractor
    cross-references the captured names against those slugs and *only*
    keeps matches that resolve — this filters out spurious capitalized
    phrases like "His Excellency" or "London". When neither list is
    given, all matches are kept and the caller is responsible for
    confirmation.
    """
    if not text:
        return []
    char_slugs = {s for s in known_characters if s}
    faction_slugs = {s for s in known_factions if s}
    seen: set[tuple[str, str]] = set()
    out: list[StructuralRelationship] = []

    def _emit(captured: str, kind: str, ref_pool: set[str]) -> None:
        captured = captured.strip().strip(".,;:'\"")
        if not captured:
            return
        slug = _slugify(captured)
        if ref_pool and slug not in ref_pool:
            # Tolerate "the Tremere" by also retrying without the leading article.
            stripped = re.sub(r"^the[\s\-]+", "", slug)
            if stripped in ref_pool:
                slug = stripped
            else:
                return
        key = (slug, kind)
        if key in seen:
            return
        seen.add(key)
        out.append(StructuralRelationship(to_ref=slug, kind=kind, note=captured))

    for pattern, kind in _REL_PATTERNS:
        for match in pattern.finditer(text):
            _emit(match.group(1), kind, char_slugs)

    for pattern, kind in _FACTION_PATTERNS:
        for match in pattern.finditer(text):
            _emit(match.group(1), kind, faction_slugs)

    return out


# ----------------------------------------------------------------------- #
# PNG / charx extraction
# ----------------------------------------------------------------------- #


def _extract_card_from_png(payload: bytes) -> tuple[dict[str, Any], list[str]]:
    """Pull the ``chara`` (v2) or ``ccv3`` (v3) tEXt chunk out of a PNG.

    The PNG layout is ``signature | length(4) | type(4) | data | crc(4)``
    repeated. We walk the chunks and base64-decode the payload of the
    first matching tEXt entry, preferring ``ccv3`` over ``chara`` when
    both are present.
    """
    warnings: list[str] = []
    chunks = _iter_png_chunks(payload)
    v2_data: dict[str, Any] | None = None
    v3_data: dict[str, Any] | None = None
    for chunk_type, chunk_data in chunks:
        if chunk_type not in (b"tEXt", b"iTXt"):
            continue
        try:
            key, _, value = chunk_data.partition(b"\x00")
        except ValueError:
            continue
        key_str = key.decode("ascii", errors="replace")
        if chunk_type == b"iTXt":
            # iTXt: keyword\0 comp\0 method\0 lang\0 translated\0 text
            try:
                _comp_flag, _, rest = value.partition(b"\x00")
                _comp_method, _, rest = rest.partition(b"\x00")
                _lang, _, rest = rest.partition(b"\x00")
                _xlate, _, value = rest.partition(b"\x00")
            except ValueError:
                continue
        if key_str.lower() == "chara":
            decoded = _decode_b64_json(value)
            if decoded is not None:
                v2_data = decoded
        elif key_str.lower() == "ccv3":
            decoded = _decode_b64_json(value)
            if decoded is not None:
                v3_data = decoded
    chosen = v3_data or v2_data
    if chosen is None:
        raise ImportError_("PNG payload has no chara/ccv3 tEXt chunk")
    if v3_data is not None and v2_data is not None:
        warnings.append("PNG contained both chara (v2) and ccv3 (v3) chunks; ccv3 used")
    return chosen, warnings


def _iter_png_chunks(payload: bytes) -> list[tuple[bytes, bytes]]:
    pos = 8
    end = len(payload)
    out: list[tuple[bytes, bytes]] = []
    while pos + 12 <= end:
        (length,) = struct.unpack(">I", payload[pos : pos + 4])
        chunk_type = payload[pos + 4 : pos + 8]
        data_start = pos + 8
        data_end = data_start + length
        if data_end + 4 > end:
            break
        chunk_data = payload[data_start:data_end]
        out.append((chunk_type, chunk_data))
        pos = data_end + 4
        if chunk_type == b"IEND":
            break
    return out


def _decode_b64_json(value: bytes) -> dict[str, Any] | None:
    try:
        raw = base64.b64decode(value, validate=False)
    except (binascii.Error, ValueError):
        return None
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _extract_card_from_charx(
    payload: bytes,
) -> tuple[dict[str, Any], bytes | None, str, list[str]]:
    try:
        zf = zipfile.ZipFile(BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise ImportError_(f"charx is not a valid zip: {exc}") from exc
    names = set(zf.namelist())
    candidate: str | None = None
    for name in ("card.json", "character.json", "data.json"):
        if name in names:
            candidate = name
            break
    if candidate is None:
        raise ImportError_("charx bundle missing card.json/character.json/data.json")
    warnings = [f"charx: extracted from {candidate}"]
    with zf.open(candidate) as fh:
        envelope = _parse_json_payload(fh.read())

    avatar_bytes: bytes | None = None
    avatar_mime = ""
    for name in ("card.png", "avatar.png", "image.png"):
        if name in names:
            with zf.open(name) as fh:
                avatar_bytes = fh.read()
            avatar_mime = "image/png"
            break
    return envelope, avatar_bytes, avatar_mime, warnings


# ----------------------------------------------------------------------- #
# JSON / envelope helpers
# ----------------------------------------------------------------------- #


def _parse_json_payload(payload: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImportError_(f"card payload is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise ImportError_("card payload must be a JSON object")
    return decoded


def _normalize_envelope(envelope: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    if isinstance(envelope.get("data"), dict):
        return (
            str(envelope.get("spec") or ""),
            str(envelope.get("spec_version") or ""),
            envelope["data"],
        )
    return ("", "", envelope)


# ----------------------------------------------------------------------- #
# Body / voice helpers
# ----------------------------------------------------------------------- #


def _compose_body(
    *,
    description: str,
    personality: str,
    scenario: str,
    system_prompt: str,
    creator_notes: str,
    post_history: str,
    alt_greetings: list[str],
) -> str:
    parts: list[str] = []
    if description:
        parts.append("## Description\n\n" + description)
    if personality:
        parts.append("## Personality\n\n" + personality)
    if scenario:
        parts.append("## Scenario\n\n" + scenario)
    if system_prompt:
        parts.append("## System prompt\n\n" + system_prompt)
    if post_history:
        parts.append("## Post-history instructions\n\n" + post_history)
    if alt_greetings:
        greetings_md = "\n\n".join(f"- {g.strip()}" for g in alt_greetings if g.strip())
        if greetings_md:
            parts.append("## Alternate greetings\n\n" + greetings_md)
    if creator_notes:
        parts.append("## Creator notes\n\n" + creator_notes)
    return "\n\n".join(parts).strip()


def _extract_dialogue_samples(mes_examples: str, first_mes: str) -> list[str]:
    samples: list[str] = []
    for chunk in re.split(r"<START>", mes_examples or "", flags=re.IGNORECASE):
        for raw_line in chunk.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.lower().startswith("{{char}}:"):
                samples.append(line.split(":", 1)[1].strip())
            elif line.startswith('"') and line.endswith('"') and len(line) > 4:
                samples.append(line.strip('"'))
            if len(samples) >= 5:
                return samples
    if not samples and first_mes:
        for match in re.finditer(r'"([^"\n]{3,})"', first_mes):
            samples.append(match.group(1))
            if len(samples) >= 5:
                break
    return samples


def _extract_speech_patterns(personality: str, description: str) -> list[str]:
    """Pull obvious speech-pattern phrases like 'speaks slowly' from prose."""
    patterns: list[str] = []
    haystack = " ".join(p for p in (personality, description) if p)
    if not haystack:
        return patterns
    for match in re.finditer(
        r"\b(?:speaks?|talks?|uses?|says?)\s+([a-z][\w \-,]{2,40}?)(?=[\.;,]|$)",
        haystack,
        flags=re.IGNORECASE,
    ):
        snippet = match.group(0).strip().rstrip(".,;")
        if snippet and snippet not in patterns:
            patterns.append(snippet)
        if len(patterns) >= 4:
            break
    return patterns


def _derive_image_prompt(name: str, description: str, personality: str, tags: list[str]) -> str:
    """Best-effort base prompt synthesized from the card text.

    Mirrors the conventions used in the bundled image-prompt templates:
    name first, then a short physical-traits clause, then style tags.
    Stays deterministic so two ingests of the same card produce the same
    prompt.
    """
    bits: list[str] = [name]
    first_sentence = description.split(".", 1)[0].strip() if description else ""
    if first_sentence:
        bits.append(first_sentence)
    if personality:
        personality_first = personality.split(".", 1)[0].strip()
        if personality_first and personality_first != first_sentence:
            bits.append(personality_first)
    if tags:
        bits.append(", ".join(tags[:6]))
    return ", ".join(b for b in bits if b)


# ----------------------------------------------------------------------- #
# Utilities
# ----------------------------------------------------------------------- #


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return slug or "character"


def _coalesce(candidate: Any, fallback: str) -> str:
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    return fallback


def _merge_list(current: list[str] | None, patch: Any) -> list[str]:
    base = list(current or [])
    if not isinstance(patch, (list, tuple)):
        return base
    for item in patch:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        if cleaned and cleaned not in base:
            base.append(cleaned)
    return base


__all__ = [
    "LLMEnrichCallable",
    "LLMEnrichment",
    "enrich_with_llm",
    "extract_relationships_deterministic",
    "ingest_character_card_v2",
]
