"""Import helpers for SillyTavern v2/v3, charx, and plaintext characters.

Each helper returns a ``CharacterData`` (the lightweight create payload) and a
list of warnings. Callers (typically :class:`CharactersService`) decide
whether to write the resulting character to disk.
"""

from __future__ import annotations

import json
import re
import zipfile
from io import BytesIO

from grimoire.types.characters import CharacterData, CharacterRole, VoiceAnchor

from .errors import ImportError_

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return slug or "character"


def parse_sillytavern(card: bytes) -> tuple[CharacterData, list[str]]:
    """Parse a SillyTavern v2/v3 character card (JSON-shaped bytes).

    Accepts both the v2 ``{spec: 'chara_card_v2', data: {...}}`` shape and the
    v3 layout that adds optional ``creator_notes`` / ``alternate_greetings``.
    Returns the projected ``CharacterData`` plus any warnings.
    """
    try:
        payload = json.loads(card.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImportError_(f"sillytavern card is not valid UTF-8 JSON: {exc}") from exc
    warnings: list[str] = []

    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        spec = str(payload.get("spec") or "")
        data = payload["data"]
    else:
        spec = ""
        data = payload if isinstance(payload, dict) else {}

    if not isinstance(data, dict) or not data.get("name"):
        raise ImportError_("sillytavern card missing 'name'")

    name = str(data.get("name") or "").strip() or "Unnamed"
    asset_id = _slugify(str(data.get("character_book_id") or data.get("char_id") or name))
    description = str(data.get("description") or "").strip()
    personality = str(data.get("personality") or "").strip()
    scenario = str(data.get("scenario") or "").strip()
    first_mes = str(data.get("first_mes") or "").strip()
    mes_examples = str(data.get("mes_example") or "").strip()
    system_prompt = str(data.get("system_prompt") or "").strip()

    samples = _extract_dialogue_samples(mes_examples, first_mes)
    tags_raw = data.get("tags") or []
    tags = [str(t) for t in tags_raw if isinstance(t, (str, int))]

    voice = VoiceAnchor(
        summary=personality or description.split(".", 1)[0].strip(),
        samples=samples,
    )

    body_parts: list[str] = []
    if description:
        body_parts.append("## Description\n\n" + description)
    if personality:
        body_parts.append("## Personality\n\n" + personality)
    if scenario:
        body_parts.append("## Scenario\n\n" + scenario)
    if system_prompt:
        body_parts.append("## System prompt\n\n" + system_prompt)
    body = "\n\n".join(body_parts).strip()

    if spec and spec not in {"chara_card_v2", "chara_card_v3"}:
        warnings.append(f"unknown sillytavern spec {spec!r}; treating as v2-compatible")
    if not samples:
        warnings.append("no dialogue samples could be extracted from mes_example/first_mes")

    return (
        CharacterData(
            id=asset_id,
            name=name,
            role=CharacterRole.MAJOR_NPC,
            aliases=[],
            tags=tags,
            voice=voice,
            description=description.split("\n", 1)[0].strip(),
            body=body,
        ),
        warnings,
    )


def parse_charx(charx_bytes: bytes) -> tuple[CharacterData, list[str]]:
    """Parse a CharacterX (zip) bundle.

    A ``.charx`` archive contains a ``card.json`` at the root (with v2/v3
    SillyTavern-style data) and optionally an avatar PNG. We extract the JSON
    and reuse the SillyTavern parser.
    """
    try:
        zf = zipfile.ZipFile(BytesIO(charx_bytes))
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
    with zf.open(candidate) as fh:
        raw = fh.read()
    data, warnings = parse_sillytavern(raw)
    warnings.append(f"charx: extracted from {candidate}")
    return data, warnings


def parse_plaintext(text: str) -> tuple[CharacterData, list[str]]:
    """Parse a plaintext character description.

    Heuristics: the first non-empty line is the name; quoted lines become
    voice samples; the remaining prose becomes the description / body.
    """
    lines = [ln.rstrip() for ln in (text or "").splitlines()]
    nonempty = [ln for ln in lines if ln.strip()]
    if not nonempty:
        raise ImportError_("plaintext import is empty")
    warnings: list[str] = []

    name_line = nonempty[0].strip()
    # Strip "Name:" prefix or markdown heading if present.
    name = re.sub(r"^#+\s*", "", name_line)
    name = re.sub(r"^name\s*[:\-]\s*", "", name, flags=re.IGNORECASE).strip()
    name = name or "Unnamed"
    asset_id = _slugify(name)

    samples = re.findall(r'"([^"\n]{3,})"', text)
    body_lines = [ln for ln in lines[1:] if not _looks_like_sample(ln)]
    body = "\n".join(body_lines).strip()
    description = body.split("\n", 1)[0] if body else ""

    voice = VoiceAnchor(
        summary=description[:200].strip(),
        samples=samples[:5],
    )
    if not samples:
        warnings.append("no quoted dialogue samples found")
    return (
        CharacterData(
            id=asset_id,
            name=name,
            role=CharacterRole.MINOR_NPC,
            voice=voice,
            description=description,
            body=body,
        ),
        warnings,
    )


def _extract_dialogue_samples(mes_examples: str, first_mes: str) -> list[str]:
    """Pull canonical sample lines from a SillyTavern card.

    The ``mes_example`` field is conventionally a chunk of dialogue separated
    by ``<START>`` markers; we keep lines starting with ``{{char}}:`` or
    ``"`` and strip the speaker label.
    """
    samples: list[str] = []
    raw_chunks = re.split(r"<START>", mes_examples or "", flags=re.IGNORECASE)
    for chunk in raw_chunks:
        for ln in chunk.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            if ln.lower().startswith("{{char}}:"):
                samples.append(ln.split(":", 1)[1].strip())
            elif ln.startswith('"') and ln.endswith('"') and len(ln) > 4:
                samples.append(ln.strip('"'))
            if len(samples) >= 5:
                break
        if len(samples) >= 5:
            break
    if not samples and first_mes:
        for match in re.finditer(r'"([^"\n]{3,})"', first_mes):
            samples.append(match.group(1))
            if len(samples) >= 5:
                break
    return samples


def _looks_like_sample(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return stripped.startswith('"') and stripped.endswith('"') and len(stripped) >= 4
