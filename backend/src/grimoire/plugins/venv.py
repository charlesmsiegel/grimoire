"""Per-plugin virtualenvs.

Spec §1 of ``2026-05-18-plugins-design.md`` and spec 15 §Configuration's
``isolation.per_plugin_venv: true``. When a plugin opts in via
``isolated_venv: true`` in its manifest and the app's
:class:`IsolationConfig` enables the feature, we install the plugin's
``requirements.txt`` into ``<venv_root>/<plugin-id>/`` and prepend the
venv's ``site-packages`` onto ``sys.path`` while the plugin is being
imported.

We chose ``sys.path`` augmentation over subprocess isolation for v1:

* it keeps the in-process plugin contract intact (an LLMProvider can
  still ``await`` directly into the event loop the orchestrator owns);
* the core ``grimoire.*`` packages stay resolvable from inside the
  plugin because the host ``sys.path`` is *prepended to*, not replaced;
* it's reversible — we capture and restore the original ``sys.path``
  every import so an isolated plugin can't leak its deps into other
  plugins' import resolution.

The trade-off is that this is not a security sandbox: plugins still run
in the host process. Spec 15 §Security calls out subprocess / WASM
isolation as v2/v3 work; that decision needs the threat-model exercise
mentioned in §12 of the remaining-work spec.

pip install failures are non-fatal: we log a warning and the caller
falls back to importing against the host environment. That matches the
v1 contract — bundled plugins' requirements have to be present in the
host env anyway, so missing a venv is no worse than today's status quo.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import venv as _venv
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


def ensure_plugin_venv(
    plugin_id: str,
    requirements_path: Path,
    venv_root: Path,
    *,
    pip_timeout_seconds: int = 600,
) -> Path | None:
    """Build (or reuse) a venv for ``plugin_id`` and return its site-packages.

    Returns ``None`` when the venv cannot be created or the pip install
    fails — callers should fall back to importing the plugin against the
    host environment. ``None`` is also returned when ``requirements_path``
    is missing so the helper is safe to call unconditionally.
    """
    if not requirements_path.is_file():
        return None
    venv_dir = venv_root / plugin_id
    venv_root.mkdir(parents=True, exist_ok=True)
    needs_create = not (venv_dir / "pyvenv.cfg").is_file()
    if needs_create:
        try:
            _venv.create(venv_dir, with_pip=True, system_site_packages=False)
        except Exception as exc:
            logger.warning("plugin %s: could not create venv at %s: %r", plugin_id, venv_dir, exc)
            return None

    pip = _pip_executable(venv_dir)
    if pip is None or not pip.exists():
        logger.warning("plugin %s: venv pip not found under %s", plugin_id, venv_dir)
        return None

    # Always run pip install (idempotent for already-satisfied deps); a
    # plugin can bump its requirements without us needing to track a
    # marker file.
    try:
        result = subprocess.run(
            [str(pip), "install", "--disable-pip-version-check", "-r", str(requirements_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=pip_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.warning("plugin %s: pip install timed out after %ds", plugin_id, pip_timeout_seconds)
        return None
    except Exception as exc:  # pragma: no cover — subprocess.run rarely raises
        logger.warning("plugin %s: pip install raised: %r", plugin_id, exc)
        return None
    if result.returncode != 0:
        logger.warning(
            "plugin %s: pip install failed (exit %s): %s",
            plugin_id,
            result.returncode,
            (result.stderr or "").strip(),
        )
        return None

    site = _site_packages(venv_dir)
    if site is None or not site.is_dir():
        logger.warning("plugin %s: site-packages directory not found under %s", plugin_id, venv_dir)
        return None
    return site


def cleanup_orphaned_venvs(venv_root: Path, current_plugin_ids: set[str]) -> None:
    """Remove venv subdirectories that no longer correspond to a plugin.

    Called from the rescan path so a uninstalled plugin's venv doesn't
    survive on disk forever. Silently no-ops when ``venv_root`` is absent.
    """
    if not venv_root.is_dir():
        return
    import shutil

    for entry in venv_root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue
        if entry.name in current_plugin_ids:
            continue
        try:
            shutil.rmtree(entry)
        except Exception as exc:
            logger.warning("could not remove orphaned venv at %s: %r", entry, exc)


@contextmanager
def prepended_sys_path(extra: Path | None) -> Iterator[None]:
    """Temporarily prepend ``extra`` onto ``sys.path`` while imports happen.

    Restores the original list on exit so isolated venvs do not bleed
    their deps into other plugins' imports. A ``None`` argument makes the
    context manager a no-op so callers can wrap conditionally without
    branching on the venv path.
    """
    if extra is None:
        yield
        return
    original = list(sys.path)
    sys.path.insert(0, str(extra))
    try:
        yield
    finally:
        sys.path[:] = original


def _pip_executable(venv_dir: Path) -> Path | None:
    if sys.platform == "win32":  # pragma: no cover — runtime is linux in CI
        return venv_dir / "Scripts" / "pip.exe"
    return venv_dir / "bin" / "pip"


def _site_packages(venv_dir: Path) -> Path | None:
    """Locate the venv's ``site-packages`` directory across platforms."""
    if sys.platform == "win32":  # pragma: no cover
        return venv_dir / "Lib" / "site-packages"
    # ``python3.12`` etc. — pick the first match rather than assuming a
    # specific minor version so we don't break when the host upgrades.
    matches = sorted((venv_dir / "lib").glob("python*/site-packages"))
    if not matches:
        return None
    return matches[0]


__all__ = [
    "cleanup_orphaned_venvs",
    "ensure_plugin_venv",
    "prepended_sys_path",
]
