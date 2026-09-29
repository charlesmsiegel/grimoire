"""Approximate prose targets for opening narration and actor contributions.

New fields take precedence across all scopes. Legacy response presets remain a
read-only fallback for continuation length so a saved preset does not disappear
when the picker is retired.
"""

from __future__ import annotations

from . import response_presets

FIELDS = (
    "response_opening_words",
    "response_opening_paragraphs",
    "response_continuation_words",
    "response_continuation_paragraphs",
)
DEFAULTS = {"opening": {"words": 400, "paragraphs": 3},
            "continuation": {"words": 150, "paragraphs": 2}}


def coerce(value) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def resolve(*, turn: dict | None = None, scene_meta: dict | None = None,
            campaign_meta: dict | None = None, config: dict | None = None) -> dict:
    """Resolve each target independently, then consult old continuation fields.

    A new global choice must beat an old scene preset: otherwise the retired
    preset UI could leave a length the reader cannot change from Config.
    """
    scopes = (("turn", turn or {}), ("scene", scene_meta or {}),
              ("campaign", campaign_meta or {}), ("global", config or {}))
    legacy = response_presets.resolve(turn=turn, scene_meta=scene_meta,
                                      campaign_meta=campaign_meta, config=config)
    out: dict[str, dict] = {"opening": {}, "continuation": {}, "provenance": {}}
    for phase in ("opening", "continuation"):
        for unit in ("words", "paragraphs"):
            key = f"response_{phase}_{unit}"
            chosen = next(((name, coerce(meta.get(key))) for name, meta in scopes
                           if coerce(meta.get(key)) is not None), None)
            if chosen is not None:
                scope, value = chosen
                source = "override"
            elif phase == "continuation":
                old_key = "reply_words" if unit == "words" else "paragraphs"
                old = legacy["provenance"][old_key]
                if old["scope"] != "default":
                    scope, value, source = old["scope"], legacy[old_key], "legacy"
                else:
                    scope, value, source = "default", DEFAULTS[phase][unit], "default"
            else:
                scope, value, source = "default", DEFAULTS[phase][unit], "default"
            out[phase][unit] = value
            out["provenance"][f"{phase}.{unit}"] = {"scope": scope, "source": source}
    return out
