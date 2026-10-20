"""Exercise pointer generation as a CLI, including its file ownership boundary."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sync_skills.py"


def _repository(tmp_path: Path) -> Path:
    assert SCRIPT.is_file(), "the skill pointer generator does not exist"
    root = tmp_path / "Realm"
    (root / "scripts").mkdir(parents=True)
    shutil.copyfile(SCRIPT, root / "scripts" / SCRIPT.name)
    return root


def _skill(root: Path, name: str = "verify") -> Path:
    source = root / ".agents" / "skills" / name / "SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        f"---\nname: {name}\ndescription: >-\n  Use when verifying Realm.\n---\n\n"
        "# Procedure\n\nThe procedure belongs only in this file.\n",
        encoding="utf-8",
    )
    return source


def _run(root: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / SCRIPT.name), *args],
        cwd=cwd or root, capture_output=True, text=True, check=False,
    )


def test_check_is_read_only_and_write_generates_discovery_for_every_skill(tmp_path):
    root = _repository(tmp_path)
    sources = [_skill(root), _skill(root, "create-world")]
    resource = sources[0].parent / "references" / "notes.md"
    resource.parent.mkdir()
    resource.write_text("Supporting instructions stay beside the procedure.", encoding="utf-8")

    missing = _run(root)  # Checking is the safe default, including on a fresh clone.
    assert missing.returncode == 1, missing.stderr
    assert "verify" in missing.stdout and "create-world" in missing.stdout
    assert not (root / ".claude").exists()

    written = _run(root, "--write", cwd=tmp_path)
    assert written.returncode == 0, written.stderr
    for source in sources:
        pointer = root / ".claude" / "skills" / source.parent.name / "SKILL.md"
        text = pointer.read_text(encoding="utf-8")
        assert source.read_text(encoding="utf-8").split("---\n", 2)[1] in text
        assert "The procedure belongs only in this file." not in text
        target = f"../../../.agents/skills/{source.parent.name}/SKILL.md"
        assert target in text and (pointer.parent / target).resolve() == source
        assert list(pointer.parent.iterdir()) == [pointer]
    assert resource.read_text(encoding="utf-8") == "Supporting instructions stay beside the procedure."
    current = _run(root, "--check")
    assert current.returncode == 0, current.stdout + current.stderr


def test_metadata_and_pointer_edits_are_detected_and_regenerated(tmp_path):
    root = _repository(tmp_path)
    source = _skill(root)
    assert _run(root, "--write").returncode == 0
    pointer = root / ".claude" / "skills" / "verify" / "SKILL.md"
    pointer.write_text(pointer.read_text(encoding="utf-8") + "Extra workflow steps.\n", encoding="utf-8")
    source.write_text(source.read_text(encoding="utf-8").replace("verifying Realm", "checking Realm"), encoding="utf-8")

    assert _run(root, "--check").returncode == 1
    assert "Extra workflow steps." in pointer.read_text(encoding="utf-8")
    assert _run(root, "--write").returncode == 0
    updated = pointer.read_text(encoding="utf-8")
    assert "checking Realm" in updated and "Extra workflow steps." not in updated
    assert _run(root, "--check").returncode == 0


def test_removed_skills_remove_only_their_generated_pointers(tmp_path):
    root = _repository(tmp_path)
    _skill(root)
    removed = _skill(root, "create-world")
    assert _run(root, "--write").returncode == 0
    removed.unlink()
    removed.parent.rmdir()

    assert _run(root, "--check").returncode == 1
    assert _run(root, "--write").returncode == 0
    assert not (root / ".claude" / "skills" / "create-world").exists()
    assert (root / ".claude" / "skills" / "verify" / "SKILL.md").is_file()


@pytest.mark.parametrize("obstacle", ["handwritten", "resource", "malformed", "missing_manifest"])
def test_unsafe_trees_refuse_before_publishing_any_pointers(tmp_path, obstacle):
    root = _repository(tmp_path)
    _skill(root)
    other = _skill(root, "create-world")
    destination = root / ".claude" / "skills" / "create-world"
    if obstacle == "handwritten":
        destination.mkdir(parents=True)
        (destination / "SKILL.md").write_text("Hand-authored procedure.", encoding="utf-8")
    elif obstacle == "resource":
        assert _run(root, "--write").returncode == 0
        (destination / "notes.md").write_text("Keep this resource.", encoding="utf-8")
        (root / ".claude" / "skills" / "verify" / "SKILL.md").unlink()
        (root / ".claude" / "skills" / "verify").rmdir()
    elif obstacle == "malformed":
        other.write_text("Missing frontmatter.", encoding="utf-8")
    else:
        other.unlink()

    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    refused = _run(root, "--write")
    assert refused.returncode != 0, refused.stdout
    assert refused.stderr
    after = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert before == after


def test_missing_authoritative_directory_never_prunes_existing_pointers(tmp_path):
    root = _repository(tmp_path)
    source = _skill(root)
    assert _run(root, "--write").returncode == 0
    source.unlink()
    source.parent.rmdir()
    source.parent.parent.rmdir()
    pointer = root / ".claude" / "skills" / "verify" / "SKILL.md"
    before = pointer.read_bytes()

    assert _run(root, "--write").returncode != 0
    assert pointer.read_bytes() == before
