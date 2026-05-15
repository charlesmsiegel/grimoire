"""YAML-only loading and writing for ``world.yaml``, ``image-preset.yaml``,
sheet files, ``campaign.yaml``, and scene sidecars.

UTF-8 is enforced for all file I/O. ``yaml.safe_load`` / ``yaml.safe_dump``
are used so arbitrary Python objects can't be deserialized.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class YamlError(ValueError):
    """Raised when YAML parsing fails."""


def parse_yaml(text: str) -> Any:
    """Parse a YAML string. Returns ``None`` for empty input."""
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise YamlError(str(exc)) from exc


def load_yaml(path: str | Path) -> Any:
    """Read a UTF-8 YAML file and return the parsed value."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        return parse_yaml(text)
    except YamlError as exc:
        raise YamlError(f"{path}: {exc}") from exc


def dump_yaml(data: Any) -> str:
    """Render ``data`` as a YAML string with block style, sorted keys off."""
    return yaml.safe_dump(
        data,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=4096,
    )


def write_yaml(path: str | Path, data: Any) -> None:
    """Write ``data`` to ``path`` as UTF-8 YAML, creating parents if needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dump_yaml(data), encoding="utf-8")
