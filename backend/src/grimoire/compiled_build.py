"""The portable source identity and the runtime part of compiled-cache BUILD.

Spec 03 S1 needs the same source identity in a checkout, a wheel and an APK.
Only relative names and exact file bytes enter that identity. Import-created
bytecode never does. Gradle can run this module with a bare build Python and
ship a source-only stamp; the phone then adds its own Python and dependency
versions, rather than mistaking the build machine's runtime for its own.

Missing required metadata or unreadable manifest inputs make fingerprinting
unavailable. They cannot make an old derivation look current. Optional desktop
packages have an explicit absent identity. The dependency walk conservatively
includes installed platform-conditional dependencies; unrelated extras are
excluded, with uvicorn's desktop extra represented by explicit roots. These
boundaries are pinned by tests/test_compiled_build.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import deque
from importlib import metadata
from pathlib import Path

SOURCE_SUFFIXES = frozenset({
    ".py", ".json", ".md", ".html", ".txt", ".ttf", ".j2", ".xhtml",
    ".ncx", ".xml", ".opf", ".css",
})
# Root lists are checked against pyproject and the desktop extra's metadata.
# No packaging import: the source-stamp build needs only the standard library.
BASE_DEPENDENCIES = (
    "fastapi", "uvicorn", "httpx", "python-multipart", "holidays", "pyluach",
    "pillow", "jinja2", "markdown", "markdown-it-py",
)
DESKTOP_DEPENDENCIES = (
    "tiktoken", "colorama", "httptools", "python-dotenv", "pyyaml", "uvloop",
    "watchfiles", "websockets",
)
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_DIGEST = re.compile(r"[0-9a-f]{64}")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def _walk_error(error: OSError) -> None:
    raise error


def source_digest(package_dir: Path, templates_dir: Path) -> str:
    """Hash the fixed source/data manifest; an incomplete read raises.

    os.walk's error callback matters: silently skipping an unreadable subtree
    would give yesterday's build identity to code the manifest never saw.
    Linked subdirectories likewise disable fingerprinting rather than letting
    a skipped or cyclic subtree silently disappear from that identity.
    """
    manifest = []
    for domain, root in (("package", Path(package_dir)), ("templates", Path(templates_dir))):
        if not root.is_dir():
            raise OSError("build inputs unavailable")
        for directory, names, filenames in os.walk(root, onerror=_walk_error):
            names[:] = sorted(name for name in names if name != "__pycache__")
            if any((Path(directory) / name).is_symlink() for name in names):
                raise OSError("build subtree unavailable")
            for name in sorted(filenames):
                path = Path(directory) / name
                if path.suffix in SOURCE_SUFFIXES:
                    manifest.append((domain, path.relative_to(root).as_posix(),
                                     hashlib.sha256(path.read_bytes()).hexdigest()))
    return hashlib.sha256(_canonical(sorted(manifest))).hexdigest()


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def dependency_versions() -> dict[str, str]:
    """Versions of runtime roots and reachable installed dependencies.

    A platform marker's installed dependency is included conservatively; an
    absent conditional dependency is explicit, so installing it moves BUILD.
    Unconditional requirements must have metadata. We do not interpret every
    project's extra as active: doing so would fingerprint its test toolchain.
    """
    pending = deque([(name, True) for name in BASE_DEPENDENCIES]
                    + [(name, False) for name in DESKTOP_DEPENDENCIES])
    versions: dict[str, str] = {}
    while pending:
        name, required = pending.popleft()
        name = _normalise(name)
        if name in versions:
            if required and versions[name] == "ABSENT":
                raise metadata.PackageNotFoundError(name)
            continue
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            if required:
                raise
            versions[name] = "ABSENT"
            continue
        if not isinstance(version, str) or not version:
            raise ValueError("dependency version unavailable")
        versions[name] = version
        for requirement in metadata.requires(name) or []:
            dependency, _, marker = requirement.partition(";")
            if re.search(r"\bextra\b", marker):
                continue
            match = _NAME.match(dependency.strip())
            if match is None:
                raise ValueError("dependency metadata unavailable")
            pending.append((match[0], not bool(marker.strip())))
    return dict(sorted(versions.items()))


def fingerprint(package_dir: Path, templates_dir: Path, *,
                stamp_path: Path | None = None, require_stamp: bool = False) -> str | None:
    """Return BUILD, or disable caching when its identity cannot be vouched for."""
    try:
        if stamp_path is not None:
            stamp = json.loads(Path(stamp_path).read_text(encoding="utf-8"))
            if (not isinstance(stamp, dict) or type(stamp.get("format")) is not int
                    or stamp["format"] != 1 or not isinstance(stamp.get("source_digest"), str)
                    or not _DIGEST.fullmatch(stamp["source_digest"])):
                return None
            source = stamp["source_digest"]
        elif require_stamp:
            return None
        else:
            source = source_digest(package_dir, templates_dir)
        return hashlib.sha256(_canonical(
            [source, sys.version, dependency_versions()])).hexdigest()
    except (OSError, ValueError, TypeError, ImportError):
        return None
