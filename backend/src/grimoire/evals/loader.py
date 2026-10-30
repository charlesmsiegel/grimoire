"""The only file-reading module of ``grimoire.evals``: strict JSON in, typed values out."""

from __future__ import annotations

import json
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

from .format import BenchmarkFormatError, Run, Scenario, parse_run, parse_scenario


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _no_constants(name: str) -> object:
    raise ValueError(f"non-finite number {name}")


def loads_strict(text: str, source: str) -> object:
    """Parse JSON rejecting duplicate keys and NaN/Infinity, which ``json`` accepts by default."""
    try:
        return json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constants)
    except ValueError as error:  # JSONDecodeError is a ValueError
        raise BenchmarkFormatError(f"{source}: invalid JSON: {error}") from None


def load_json(path: str | Path | Traversable) -> object:
    source = getattr(path, "name", str(path))
    try:
        text = path.read_text("utf-8") if isinstance(path, Traversable) else Path(path).read_text("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise BenchmarkFormatError(f"{source}: cannot read: {error}") from None
    return loads_strict(text, source)


def load_scenario(path: str | Path | Traversable) -> Scenario:
    return parse_scenario(load_json(path))


def load_run(path: str | Path | Traversable, scenario: Scenario) -> Run:
    return parse_run(load_json(path), scenario)


def benchmark_file(name: str, filename: str) -> Traversable:
    """A file of a shipped synthetic benchmark, e.g. ``("guild_betrayal", "scenario.json")``."""
    return files("grimoire.evals").joinpath("benchmarks", name, filename)
