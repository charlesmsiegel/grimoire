"""Compressed card-view renderers for the Characters module.

Context Builder asks for different depths at different tiers:
* ``full`` — the entire library card body
* ``compressed`` — frontmatter + description + sample line
* ``voice_only`` — just the voice anchor
* ``capsule`` — single-line gloss for archive tier
"""

from __future__ import annotations

from grimoire.types.characters import Character, VoiceAnchor


def render_full(character: Character) -> str:
    parts: list[str] = [f"# {character.name}"]
    if character.aliases:
        parts.append(f"_aliases:_ {', '.join(character.aliases)}")
    if character.age:
        parts.append(f"_age:_ {character.age}")
    if character.tags:
        parts.append(f"_tags:_ {', '.join(character.tags)}")
    if character.description:
        parts.append("")
        parts.append(character.description)
    if character.body:
        parts.append("")
        parts.append(character.body)
    voice = _render_voice(character.voice)
    if voice:
        parts.append("")
        parts.append("## Voice")
        parts.append(voice)
    return "\n".join(parts).strip()


def render_compressed(character: Character) -> str:
    """Frontmatter line + description + (at most) one canonical sample."""
    bits: list[str] = []
    header = character.name
    if character.aliases:
        header += f" ({', '.join(character.aliases[:2])})"
    if character.role:
        header += f" — {character.role.value}"
    bits.append(header)
    if character.description:
        bits.append(character.description.strip())
    if character.voice.summary:
        bits.append(f"Voice: {character.voice.summary.strip()}")
    if character.voice.samples:
        bits.append(f'Sample: "{character.voice.samples[0].strip()}"')
    return "\n".join(bits)


def render_voice_only(character: Character, *, max_samples: int = 3) -> str:
    """Voice-anchor block alone; used when only dialogue voice is needed."""
    rendered = _render_voice(character.voice, max_samples=max_samples)
    return rendered or f"{character.name}: no voice anchor"


def render_capsule(character: Character) -> str:
    """One-line gloss: name + role + first tag (if any)."""
    parts = [character.name]
    if character.role:
        parts.append(character.role.value)
    if character.tags:
        parts.append(character.tags[0])
    return " · ".join(parts)


def _render_voice(voice: VoiceAnchor, *, max_samples: int = 5) -> str:
    if not (voice.summary or voice.samples or voice.dos or voice.donts or voice.speech_patterns):
        return ""
    lines: list[str] = []
    if voice.summary:
        lines.append(voice.summary.strip())
    if voice.voice_register:
        lines.append(f"Register: {voice.voice_register}")
    if voice.speech_patterns:
        lines.append("Patterns: " + "; ".join(voice.speech_patterns))
    if voice.samples:
        lines.append("Samples:")
        for sample in voice.samples[:max_samples]:
            lines.append(f'  - "{sample.strip()}"')
    if voice.dos:
        lines.append("Dos: " + "; ".join(voice.dos))
    if voice.donts:
        lines.append("Don'ts: " + "; ".join(voice.donts))
    if voice.address_terms:
        rendered = "; ".join(f"{k}→{v}" for k, v in voice.address_terms.items())
        lines.append(f"Address: {rendered}")
    return "\n".join(lines)


def rotate_samples(voice: VoiceAnchor, *, seed: int) -> list[str]:
    """Deterministic rotation order over the sample list.

    The first ``min(max_samples, len(samples))`` lines are kept; the order is
    rotated by ``seed`` so that consecutive calls with different seeds (e.g.
    different post counts) surface different lines first.
    """
    samples = list(voice.samples or [])
    if not samples:
        return []
    offset = seed % len(samples)
    return samples[offset:] + samples[:offset]
