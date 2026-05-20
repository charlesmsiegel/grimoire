"""Closed-set macro expansion for SillyTavern-shaped character cards.

See ``docs/superpowers/specs/2026-05-19-card-imports-design.md`` §1.
``expand_macros`` is called at ingest time across every text field of an
imported card; ``{{user}}`` is preserved literal and resolved later in
:func:`grimoire.context.builder._resolve_runtime_macros`.

Determinism: pick/roll macros are seeded from
``SHA-256(card_asset_id::field_name::macro_index)[:8]`` so two ingests of
the same card produce the same expansion.
"""

from __future__ import annotations

import hashlib
import random
import re

# Non-greedy match to the *closest* closing ``}}``. A body containing
# ``{{`` indicates nesting — we detect that in ``_expand_one`` and leave
# the whole outer macro literal with a warning, which prevents recursive
# expansion of the inner content.
_MACRO_PATTERN = re.compile(r"\{\{(.*?)\}\}")
_ROLL_PATTERN = re.compile(r"^(\d+)d(\d+)$")

# Caps for ``{{roll:NdM}}`` — the regex accepts arbitrarily large integers,
# and ``ingest`` / the preview REST route both run the macro pass
# synchronously. A card with ``{{roll:999999999d2}}`` would otherwise stall
# the FastAPI event loop for the duration of a billion iterations. 100
# dice with 1000 sides easily covers every legitimate SillyTavern card.
_ROLL_MAX_N = 100
_ROLL_MAX_SIDES = 1000
_TRIM_SENTINEL = "\x00TRIM\x00"
_TRIM_PATTERN = re.compile(r" ?\x00TRIM\x00 ?")


def expand_macros(
    text: str,
    *,
    char_name: str,
    card_asset_id: str,
    field_name: str,
    keep_user: bool = True,
) -> tuple[str, list[str]]:
    """Expand closed-set macros in ``text``; return ``(expanded, warnings)``.

    Macros recognised: ``{{char}}``, ``{{user}}`` (preserved when
    ``keep_user``), ``{{random:a,b,c}}`` / ``{{pick:...}}``,
    ``{{roll:NdM}}``, ``{{newline}}``, ``{{trim}}``, ``{{// comment}}``.
    Unknown macros pass through literally with a warning.
    """
    if not text:
        return text, []
    warnings: list[str] = []
    out: list[str] = []
    pos = 0
    for macro_index, match in enumerate(_MACRO_PATTERN.finditer(text)):
        out.append(text[pos : match.start()])
        body = match.group(1).strip()
        replacement, warn = _expand_one(
            body,
            char_name=char_name,
            card_asset_id=card_asset_id,
            field_name=field_name,
            macro_index=macro_index,
            keep_user=keep_user,
        )
        if warn:
            warnings.append(warn)
        out.append(replacement)
        pos = match.end()
    out.append(text[pos:])
    result = "".join(out)
    result = _apply_trim(result)
    return result, warnings


def _seed(card_asset_id: str, field_name: str, macro_index: int) -> int:
    digest = hashlib.sha256(f"{card_asset_id}::{field_name}::{macro_index}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _expand_one(
    body: str,
    *,
    char_name: str,
    card_asset_id: str,
    field_name: str,
    macro_index: int,
    keep_user: bool,
) -> tuple[str, str | None]:
    if "{{" in body or "}}" in body:
        return f"{{{{{body}}}}}", f"nested macro left literal: {body!r} in {field_name}"

    lower = body.lower()

    if lower == "char":
        return char_name, None
    if lower == "user":
        return "{{user}}" if keep_user else "the player", None
    if lower == "newline":
        return "\n", None
    if body == "trim":
        return _TRIM_SENTINEL, None
    if body.startswith("//"):
        return "", None

    if lower.startswith("random:") or lower.startswith("pick:"):
        opts = body.split(":", 1)[1]
        choices = opts.split("::") if "::" in opts else opts.split(",")
        choices = [c.strip() for c in choices if c.strip()]
        if not choices:
            return f"{{{{{body}}}}}", f"empty {lower.split(':', 1)[0]} list in {field_name}"
        rng = random.Random(_seed(card_asset_id, field_name, macro_index))
        return rng.choice(choices), None

    if lower.startswith("roll:"):
        spec = body.split(":", 1)[1].strip()
        m = _ROLL_PATTERN.match(spec)
        if not m:
            return f"{{{{{body}}}}}", f"bad roll spec: {spec!r} in {field_name}"
        n, sides = int(m.group(1)), int(m.group(2))
        if n <= 0 or sides <= 0:
            return f"{{{{{body}}}}}", f"bad roll spec: {spec!r} in {field_name}"
        if n > _ROLL_MAX_N or sides > _ROLL_MAX_SIDES:
            return (
                f"{{{{{body}}}}}",
                f"roll spec exceeds caps (n<={_ROLL_MAX_N}, sides<={_ROLL_MAX_SIDES}): "
                f"{spec!r} in {field_name}",
            )
        rng = random.Random(_seed(card_asset_id, field_name, macro_index))
        total = sum(rng.randint(1, sides) for _ in range(n))
        return str(total), None

    return f"{{{{{body}}}}}", f"unknown macro: {body!r} in {field_name}"


def _apply_trim(text: str) -> str:
    while True:
        new = _TRIM_PATTERN.sub("", text)
        if new == text:
            return new
        text = new


__all__ = ["expand_macros"]
