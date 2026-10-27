"""Execute the APK's frontend task against controlled npm build inputs.

No Android SDK or plugins are needed for this shared-bundle contract. Set
GRIMOIRE_TEST_GRADLE to a Gradle executable, or put Gradle on PATH, to exercise
incremental rebuilds; APK builds continue to cover the Android integration.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def bundle(tmp_path):
    gradle = os.environ.get("GRIMOIRE_TEST_GRADLE") or shutil.which("gradle")
    if not gradle or not shutil.which("npm"):
        pytest.skip("Gradle and npm are needed for the frontend build contract")
    android = tmp_path / "android"
    frontend = tmp_path / "frontend"
    android.mkdir()
    frontend.mkdir()
    shutil.copy2(REPO / "android" / "frontend.gradle.kts", android)
    (android / "settings.gradle.kts").write_text('rootProject.name = "bundle-fixture"\n')
    (android / "build.gradle.kts").write_text('apply(from = "frontend.gradle.kts")\n')
    (frontend / "src").mkdir()
    (frontend / "public").mkdir()
    (frontend / "index.html").write_text("fixture")
    (frontend / "package.json").write_text(json.dumps({"scripts": {"build": "node build.mjs"}}))
    (frontend / "package-lock.json").write_text('{"lockfileVersion":3}')
    (frontend / "vite.config.ts").write_text("initial Vite configuration")
    (frontend / "tsconfig.json").write_text('{"compilerOptions":{}}')
    (frontend / "build-inputs.txt").write_text(
        "src\npublic\nindex.html\npackage.json\npackage-lock.json\ntsconfig.json\nvite.config.ts\n")
    (frontend / "build.mjs").write_text('''
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
mkdirSync('dist', { recursive: true });
const names = ['vite.config.ts', 'tsconfig.json', 'package-lock.json', 'extra.json'];
const artifact = Object.fromEntries(names.map(name =>
  [name, existsSync(name) ? readFileSync(name, 'utf8') : null]));
writeFileSync('dist/bundle.json', JSON.stringify(artifact));
''')

    def build():
        result = subprocess.run(
            [gradle, "--no-daemon", "--console=plain", "--offline", "buildFrontend"],
            cwd=android, env=os.environ.copy(), text=True, capture_output=True,
            timeout=120, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout, json.loads((frontend / "dist" / "bundle.json").read_text())

    return frontend, build


@pytest.mark.parametrize("name,changed", [
    ("vite.config.ts", "changed Vite configuration"),
    ("tsconfig.json", '{"compilerOptions":{"strict":true}}'),
    ("package-lock.json", '{"lockfileVersion":3,"packages":{"example":{}}}'),
])
def test_changed_build_input_reaches_the_packaged_bundle(bundle, name, changed):
    frontend, build = bundle
    build()
    output, _ = build()
    assert "buildFrontend UP-TO-DATE" in output
    (frontend / name).write_text(changed)
    _, artifact = build()
    assert artifact[name] == changed


def test_new_inventory_entry_becomes_a_build_dependency(bundle):
    frontend, build = bundle
    build()
    inventory = frontend / "build-inputs.txt"
    inventory.write_text(inventory.read_text() + "extra.json\n")
    (frontend / "extra.json").write_text("first version")
    _, artifact = build()
    assert artifact["extra.json"] == "first version"
    (frontend / "extra.json").write_text("second version")
    _, artifact = build()
    assert artifact["extra.json"] == "second version"
