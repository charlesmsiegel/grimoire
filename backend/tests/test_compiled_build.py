"""Portable source stamps and runtime identities (spec 03, slice S1).

The breaks these tests catch are omitted inputs, machine-local paths entering
the digest, import-created bytecode invalidating it, and an APK quietly using
the build machine's Python or dependency versions.
"""

import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _build():
    assert importlib.util.find_spec("grimoire.compiled_build") is not None, (
        "the source/runtime fingerprint helper is missing")
    return importlib.import_module("grimoire.compiled_build")


def _stable_build(monkeypatch):
    build = _build()
    monkeypatch.setattr(build, "dependency_versions",
                        lambda: {"fastapi": "1", "tiktoken": "ABSENT"})
    return build


@pytest.fixture
def inputs(tmp_path):
    package, templates = tmp_path / "package", tmp_path / "templates"
    (package / "climates").mkdir(parents=True)
    templates.mkdir()
    (package / "reader.py").write_bytes(b"VALUE = 'Realm'\n")
    (package / "climates" / "realm.json").write_bytes(b'{"name":"Realm"}')
    (templates / "greeting.j2").write_bytes(b"Welcome to Realm")
    return package, templates


def test_source_manifest_and_stamp(inputs, tmp_path, monkeypatch):
    build = _stable_build(monkeypatch)
    package, templates = inputs
    plain = build.fingerprint(package, templates)
    assert plain is not None and re.fullmatch("[0-9a-f]{64}", plain)
    stamp = tmp_path / "compiled-build.json"
    stamp.write_text(json.dumps({"format": 1,
                                 "source_digest": build.source_digest(*inputs)}))
    assert build.fingerprint(package, templates, stamp_path=stamp,
                             require_stamp=True) == plain
    assert build.fingerprint(tmp_path / "compiled-assets", tmp_path / "extracted-assets",
                             stamp_path=stamp, require_stamp=True) == plain
    # A different checkout location is the same input, including its relative
    # names. Absolute paths must never make a build stamp machine-specific.
    moved = tmp_path / "moved"
    shutil.copytree(package, moved / "package")
    shutil.copytree(templates, moved / "templates")
    assert build.fingerprint(moved / "package", moved / "templates") == plain


def test_bytecode_does_not_move_build(inputs, monkeypatch):
    build = _stable_build(monkeypatch)
    before = build.fingerprint(*inputs)
    package, templates = inputs
    (package / "reader.pyc").write_bytes(b"bytecode")
    (package / "__pycache__").mkdir()
    (package / "__pycache__" / "reader.cpython-314.pyc").write_bytes(b"import")
    (package / "__pycache__" / "generated.py").write_bytes(b"ignored")
    (templates / "unused.tmp").write_bytes(b"runtime output")
    assert build.fingerprint(*inputs) == before


@pytest.mark.parametrize("domain,suffix", [
    ("package", ".py"), ("package", ".json"), ("package", ".md"),
    ("package", ".html"), ("package", ".txt"), ("package", ".ttf"),
    ("templates", ".j2"), ("templates", ".xhtml"), ("templates", ".ncx"),
    ("templates", ".xml"), ("templates", ".opf"), ("templates", ".css"),
])
def test_each_build_input_moves_build(inputs, monkeypatch, domain, suffix):
    build = _stable_build(monkeypatch)
    root = inputs[0 if domain == "package" else 1]
    path = root / ("realm" + suffix)
    before = build.fingerprint(*inputs)
    path.write_bytes(b"Realm")
    added = build.fingerprint(*inputs)
    assert added != before
    path.write_bytes(b"Saltmarch")
    changed = build.fingerprint(*inputs)
    assert changed != added
    path.rename(root / ("saltmarch" + suffix))
    assert build.fingerprint(*inputs) != changed


def test_runtime_python_and_dependency_versions_move_build(inputs, monkeypatch):
    build = _stable_build(monkeypatch)
    before = build.fingerprint(*inputs)
    monkeypatch.setattr(build.sys, "version", "another Python runtime")
    assert build.fingerprint(*inputs) != before
    monkeypatch.undo()
    build = _stable_build(monkeypatch)
    monkeypatch.setattr(build, "dependency_versions",
                        lambda: {"fastapi": "2", "tiktoken": "ABSENT"})
    assert build.fingerprint(*inputs) != before


@pytest.mark.parametrize("document", [
    "not JSON", "null", "[]", '{"format":2,"source_digest":"' + "a" * 64 + '"}',
    '{"format":1,"source_digest":"not a digest"}',
])
def test_malformed_stamps_disable_build(inputs, tmp_path, monkeypatch, document):
    build = _stable_build(monkeypatch)
    stamp = tmp_path / "compiled-build.json"
    stamp.write_text(document)
    assert build.fingerprint(*inputs, stamp_path=stamp) is None


def test_unavailable_inputs_disable_build(inputs, tmp_path, monkeypatch):
    build = _stable_build(monkeypatch)
    assert build.fingerprint(*inputs, require_stamp=True) is None
    assert build.fingerprint(*inputs, stamp_path=tmp_path / "absent") is None
    assert build.fingerprint(tmp_path / "absent", inputs[1]) is None
    assert build.fingerprint(inputs[0], tmp_path / "absent") is None

    def unavailable():
        raise build.metadata.PackageNotFoundError("fastapi")

    monkeypatch.setattr(build, "dependency_versions", unavailable)
    assert build.fingerprint(*inputs) is None


def test_source_read_failure_disables_build(inputs, monkeypatch):
    build = _stable_build(monkeypatch)
    read = Path.read_bytes

    def denied(path):
        if path.name == "reader.py":
            raise PermissionError("unreadable input")
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", denied)
    assert build.fingerprint(*inputs) is None


def test_transitive_versions_and_optional_absence(monkeypatch):
    build = _build()
    versions = dict.fromkeys((*build.BASE_DEPENDENCIES, *build.DESKTOP_DEPENDENCIES), "1")
    versions.pop("tiktoken")
    versions.update({"starlette": "2", "shared": "3"})
    needs = {"fastapi": ["Starlette>=1", "shared>=2",
                         "native; sys_platform == 'other'", "docs; extra == 'docs'"],
             "starlette": ["shared>=2"], "shared": ["fastapi>=1"]}

    def version(name):
        if name not in versions:
            raise build.metadata.PackageNotFoundError(name)
        return versions[name]

    monkeypatch.setattr(build.metadata, "version", version)
    monkeypatch.setattr(build.metadata, "requires", lambda name: needs.get(name, []))
    got = build.dependency_versions()
    assert got["starlette"] == "2" and got["shared"] == "3"
    assert got["tiktoken"] == "ABSENT" and got["native"] == "ABSENT"
    assert "docs" not in got
    versions.pop("shared")
    with pytest.raises(build.metadata.PackageNotFoundError):
        build.dependency_versions()


def test_manifest_and_dependency_roots_cover_shipped_inputs():
    build = _build()
    project = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text())["project"]
    roots = {re.match(r"[\w-]+", requirement)[0].lower()
             for requirement in project["dependencies"]}
    assert set(build.BASE_DEPENDENCIES) == roots
    assert "tiktoken" in build.DESKTOP_DEPENDENCIES
    assert {"httptools", "watchfiles", "websockets", "uvloop", "python-dotenv",
            "pyyaml", "colorama"} <= set(build.DESKTOP_DEPENDENCIES)
    desktop = {re.match(r"[\w-]+", requirement)[0].lower()
               for requirement in project["optional-dependencies"]["desktop"]}
    assert desktop <= set(build.BASE_DEPENDENCIES) | set(build.DESKTOP_DEPENDENCIES)
    standard = {re.match(r"[\w-]+", requirement)[0].lower()
                for requirement in build.metadata.requires("uvicorn") or []
                if re.search(r"extra\s*==\s*['\"]standard['\"]", requirement)}
    assert standard <= set(build.DESKTOP_DEPENDENCIES)
    for root in (ROOT / "backend" / "src" / "grimoire", ROOT / "templates"):
        uncovered = [str(p.relative_to(root)) for p in root.rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts
                     and p.suffix not in build.SOURCE_SUFFIXES]
        assert uncovered == []


def test_cli_uses_explicit_paths(inputs, tmp_path):
    build = _build()
    output = tmp_path / "output" / "compiled-build.json"
    # -S excludes even installed site packages: this CLI must work with only
    # Gradle's build Python and the explicitly supplied package/template paths.
    result = subprocess.run(
        [sys.executable, "-S", "-m", "scripts.compiled_build", "--package", str(inputs[0]),
         "--templates", str(inputs[1]), "--output", str(output)],
        cwd=tmp_path, capture_output=True, text=True, check=False,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(
            [str(ROOT / "backend" / "src"), str(ROOT / "backend")])})
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text()) == {
        "format": 1, "source_digest": build.source_digest(*inputs)}
