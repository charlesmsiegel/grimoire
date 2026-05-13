"""Jinja2 prompt templates.

All LLM/image prompts in Grimoire are rendered from Jinja2 templates that
live under this package, one subfolder per template. The default variant
is ``default.j2``; users may drop additional variants into the same
folder (e.g. ``terse.j2``, ``my-house-style.j2``) and select between them
at runtime.

Layout::

    templates/
        <template_name>/
            default.j2
            <variant>.j2
            ...

Resolution
----------
Search paths are tried in order; the first hit wins. The default path is
the in-package ``templates/`` directory. Extra paths can be supplied via
:func:`register_search_path` or the ``GRIMOIRE_TEMPLATES_DIR``
environment variable (``os.pathsep``-separated). User-supplied paths take
precedence over the bundled defaults, so a modder can override a single
template by dropping a same-named file into their own directory.

Variant selection
-----------------
``render(name, ..., variant=...)`` picks an explicit variant. When
omitted, the registry consults its per-template overrides
(:func:`set_variant`) and finally falls back to ``"default"``. This keeps
call sites free of variant strings while still allowing host code or
config to swap implementations.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

__all__ = [
    "DEFAULT_VARIANT",
    "TEMPLATE_SUFFIX",
    "TemplateRegistry",
    "list_variants",
    "register_search_path",
    "registry",
    "render",
    "set_variant",
]

DEFAULT_VARIANT = "default"
TEMPLATE_SUFFIX = ".j2"
_ENV_VAR = "GRIMOIRE_TEMPLATES_DIR"


class TemplateRegistry:
    """Renders Jinja2 prompt templates with per-template variant overrides."""

    def __init__(self, search_paths: list[Path] | None = None) -> None:
        self._lock = threading.RLock()
        self._search_paths: list[Path] = []
        self._variants: dict[str, str] = {}
        self._env: Environment | None = None
        for path in search_paths or self._default_search_paths():
            self._add_path_unlocked(path)

    @staticmethod
    def _default_search_paths() -> list[Path]:
        paths: list[Path] = []
        env_value = os.environ.get(_ENV_VAR, "").strip()
        if env_value:
            for raw in env_value.split(os.pathsep):
                raw = raw.strip()
                if raw:
                    paths.append(Path(raw))
        paths.append(Path(__file__).resolve().parent)
        return paths

    def _add_path_unlocked(self, path: Path) -> None:
        resolved = Path(path).resolve()
        if resolved in self._search_paths:
            return
        self._search_paths.append(resolved)
        self._env = None  # rebuild on next render

    def register_search_path(self, path: Path | str, *, prepend: bool = True) -> None:
        """Add another directory to search for templates.

        ``prepend=True`` (default) makes the new path take precedence over
        previously-registered ones — typical for user overrides.
        """
        with self._lock:
            resolved = Path(path).resolve()
            if resolved in self._search_paths:
                return
            if prepend:
                self._search_paths.insert(0, resolved)
            else:
                self._search_paths.append(resolved)
            self._env = None

    @property
    def search_paths(self) -> list[Path]:
        with self._lock:
            return list(self._search_paths)

    def set_variant(self, template_name: str, variant: str | None) -> None:
        """Pin ``template_name`` to ``variant`` for subsequent renders.

        Pass ``variant=None`` to clear the override and revert to
        ``"default"``.
        """
        with self._lock:
            if variant is None:
                self._variants.pop(template_name, None)
            else:
                self._variants[template_name] = variant

    def get_variant(self, template_name: str) -> str:
        with self._lock:
            return self._variants.get(template_name, DEFAULT_VARIANT)

    def list_variants(self, template_name: str) -> list[str]:
        """Return the variant names available for ``template_name``.

        Variants discovered in earlier search paths shadow same-named
        files in later ones, but each name appears once.
        """
        seen: dict[str, None] = {}
        with self._lock:
            paths = list(self._search_paths)
        for base in paths:
            folder = base / template_name
            if not folder.is_dir():
                continue
            for entry in sorted(folder.iterdir()):
                if entry.is_file() and entry.suffix == TEMPLATE_SUFFIX:
                    seen.setdefault(entry.stem, None)
        return list(seen.keys())

    def render(self, template_name: str, /, variant: str | None = None, **context) -> str:
        """Render ``<template_name>/<variant>.j2`` with ``context``."""
        chosen = variant or self.get_variant(template_name)
        env = self._get_env()
        relative = f"{template_name}/{chosen}{TEMPLATE_SUFFIX}"
        try:
            template = env.get_template(relative)
        except TemplateNotFound:
            if chosen != DEFAULT_VARIANT:
                template = env.get_template(
                    f"{template_name}/{DEFAULT_VARIANT}{TEMPLATE_SUFFIX}"
                )
            else:
                raise
        return template.render(**context)

    def _get_env(self) -> Environment:
        with self._lock:
            if self._env is None:
                loader = FileSystemLoader([str(p) for p in self._search_paths])
                self._env = Environment(
                    loader=loader,
                    autoescape=False,
                    keep_trailing_newline=False,
                    trim_blocks=True,
                    lstrip_blocks=True,
                    undefined=StrictUndefined,
                )
            return self._env


registry = TemplateRegistry()


def render(template_name: str, /, variant: str | None = None, **context) -> str:
    return registry.render(template_name, variant=variant, **context)


def list_variants(template_name: str) -> list[str]:
    return registry.list_variants(template_name)


def set_variant(template_name: str, variant: str | None) -> None:
    registry.set_variant(template_name, variant)


def register_search_path(path: Path | str, *, prepend: bool = True) -> None:
    registry.register_search_path(path, prepend=prepend)
