"""Spotlight-tier stanza renderer for the Context Builder.

The compact stanza per spec §Context Builder integration is rendered as a
plain-text block per present character::

    winifred Allard — current state:
      mood: guarded
      intent: hide letter
      action: fastening her cloak
      thinking: the silence is louder than usual

Empty / missing fields are omitted. The Context Builder calls this with
already-privacy-filtered values (i.e. ``internal_thought`` will already be
absent when the active observer should not see it).
"""

from __future__ import annotations

from grimoire.types.transient import TransientValue

# Order mirrors the spec stanza: mood / intent / action / thinking,
# followed by less-essential fields appended on the same compact form.
DEFAULT_STANZA_FIELDS: tuple[tuple[str, str], ...] = (
    ("mood", "mood"),
    ("intent", "intent"),
    ("current_action", "action"),
    ("internal_thought", "thinking"),
    ("posture", "posture"),
    ("focus_of_attention", "focus"),
    ("relationship_tone_toward_pc", "tone toward PC"),
    ("energy_level", "energy"),
)


def render_transient_stanza(
    character_name: str,
    bundle: dict[str, TransientValue],
    *,
    field_order: tuple[tuple[str, str], ...] = DEFAULT_STANZA_FIELDS,
) -> str:
    """Return the multi-line stanza for one character.

    If ``bundle`` is empty or carries none of the recognized fields, an
    empty string is returned so callers can skip emitting a tier item.
    """
    lines: list[str] = []
    for field_key, label in field_order:
        value = bundle.get(field_key)
        if value is None:
            continue
        formatted = _format_value(value.value)
        if not formatted:
            continue
        lines.append(f"  {label}: {formatted}")
    if not lines:
        return ""
    return f"{character_name} — current state:\n" + "\n".join(lines)


def _format_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return ", ".join(_format_value(item) for item in value if item is not None)
    if isinstance(value, dict):
        return ", ".join(f"{k}={_format_value(v)}" for k, v in value.items())
    return str(value)
