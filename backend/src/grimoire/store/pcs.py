"""Player-character containers: one folder per PC, one markdown persona per version.

Mirrors characters.py but with a simpler payload:
  <root>/pcs/<pid>/pc.md          # frontmatter: name, tags (comma-joined), default_version
  <root>/pcs/<pid>/<vid>.md       # frontmatter: name, pronouns, summary ; body: description
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import slugify, uniquify

PERSONA_FIELDS = ("name", "pronouns", "summary", "birthdate")  # frontmatter scalars; description is the body


class PCNotFound(Exception):
    pass


class PCVersionNotFound(Exception):
    pass


def _safe(part: str) -> bool:
    return part not in ("", ".", "..") and "/" not in part and "\\" not in part


def _pcs_dir(root: Path) -> Path:
    return root / "pcs"


def _pc_dir(root: Path, pid: str) -> Path:
    return _pcs_dir(root) / pid


def _meta_path(root: Path, pid: str) -> Path:
    return _pc_dir(root, pid) / "pc.md"


def _version_path(root: Path, pid: str, vid: str) -> Path:
    return _pc_dir(root, pid) / f"{vid}.md"


def blank_persona(name: str) -> dict:
    return {"name": name, "pronouns": "", "summary": "", "birthdate": "", "description": ""}


def _dump_persona(persona: dict) -> str:
    meta = {f: persona.get(f, "") for f in PERSONA_FIELDS}
    return dump_frontmatter(meta, persona.get("description", ""))


def _load_persona(text: str) -> dict:
    meta, body = parse_frontmatter(text)
    return {**{f: meta.get(f, "") for f in PERSONA_FIELDS}, "description": body.strip()}


def _require_pc(root: Path, pid: str) -> Path:
    if not _safe(pid) or not _meta_path(root, pid).exists():
        raise PCNotFound(pid)
    return _pc_dir(root, pid)


def _read_meta(root: Path, pid: str) -> dict:
    meta, _ = parse_frontmatter(_meta_path(root, pid).read_text(encoding="utf-8"))
    return meta


def _write_meta(root: Path, pid: str, name: str, tags: list[str], default_version: str) -> None:
    _meta_path(root, pid).write_text(
        dump_frontmatter({"name": name, "tags": ",".join(tags), "default_version": default_version}, ""),
        encoding="utf-8",
    )


def _tags_of(meta: dict) -> list[str]:
    return [t for t in meta.get("tags", "").split(",") if t]


def create_pc(root: Path, name: str, tags: list[str], version_name: str = "default",
              persona: dict | None = None) -> tuple[str, str]:
    _pcs_dir(root).mkdir(parents=True, exist_ok=True)
    pid = uniquify(slugify(name), lambda c: _pc_dir(root, c).exists())
    _pc_dir(root, pid).mkdir(parents=True)
    vid = slugify(version_name)
    _version_path(root, pid, vid).write_text(_dump_persona(persona or blank_persona(name)), encoding="utf-8")
    _write_meta(root, pid, name, tags, vid)
    return pid, vid


def create_version(root: Path, pid: str, version_name: str, persona: dict) -> str:
    _require_pc(root, pid)
    vid = uniquify(slugify(version_name), lambda v: _version_path(root, pid, v).exists())
    _version_path(root, pid, vid).write_text(_dump_persona(persona), encoding="utf-8")
    return vid


def update_version(root: Path, pid: str, vid: str, persona: dict) -> None:
    _require_pc(root, pid)
    p = _version_path(root, pid, vid)
    if not _safe(vid) or not p.exists():
        raise PCVersionNotFound(vid)
    p.write_text(_dump_persona(persona), encoding="utf-8")


def set_default_version(root: Path, pid: str, vid: str) -> None:
    _require_pc(root, pid)
    if not _safe(vid) or not _version_path(root, pid, vid).exists():
        raise PCVersionNotFound(vid)
    meta = _read_meta(root, pid)
    _write_meta(root, pid, meta.get("name", pid), _tags_of(meta), vid)


def set_tags(root: Path, pid: str, tags: list[str]) -> None:
    _require_pc(root, pid)
    meta = _read_meta(root, pid)
    _write_meta(root, pid, meta.get("name", pid), tags, meta.get("default_version", ""))


def _version_ids(root: Path, pid: str) -> list[str]:
    return sorted(p.stem for p in _pc_dir(root, pid).glob("*.md") if p.name != "pc.md")


def read_persona(root: Path, pid: str, vid: str) -> dict:
    _require_pc(root, pid)
    p = _version_path(root, pid, vid)
    if not _safe(vid) or not p.exists():
        raise PCVersionNotFound(vid)
    return _load_persona(p.read_text(encoding="utf-8"))


def read_pc(root: Path, pid: str) -> dict:
    _require_pc(root, pid)
    meta = _read_meta(root, pid)
    versions = [{"id": v, "name": read_persona(root, pid, v)["name"], "persona": read_persona(root, pid, v)}
                for v in _version_ids(root, pid)]
    return {"meta": {"id": pid, "name": meta.get("name", pid), "tags": _tags_of(meta),
                     "default_version": meta.get("default_version", "")}, "versions": versions}


def list_pcs(root: Path) -> list[dict]:
    out: list[dict] = []
    d = _pcs_dir(root)
    if d.exists():
        for pd in sorted(p for p in d.iterdir() if p.is_dir() and (p / "pc.md").exists()):
            pid = pd.name
            meta = _read_meta(root, pid)
            out.append({"id": pid, "name": meta.get("name", pid), "tags": _tags_of(meta),
                        "default_version": meta.get("default_version", ""),
                        "versions": [{"id": v, "name": read_persona(root, pid, v)["name"]}
                                     for v in _version_ids(root, pid)]})
    return out


def delete_version(root: Path, pid: str, vid: str) -> None:
    _require_pc(root, pid)
    p = _version_path(root, pid, vid)
    if not _safe(vid) or not p.exists():
        raise PCVersionNotFound(vid)
    if len(_version_ids(root, pid)) == 1:
        raise ValueError("cannot delete the last version of a PC")
    p.unlink()
    meta = _read_meta(root, pid)
    if meta.get("default_version") == vid:
        _write_meta(root, pid, meta.get("name", pid), _tags_of(meta), _version_ids(root, pid)[0])


def delete_pc(root: Path, pid: str) -> None:
    _require_pc(root, pid)
    shutil.rmtree(_pc_dir(root, pid))


def version_hash(root: Path, pid: str, vid: str) -> str | None:
    p = _version_path(root, pid, vid)
    if not _safe(pid) or not _safe(vid) or not p.exists():
        return None
    return hashlib.sha256(p.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def dir_hash(root: Path, pid: str) -> str | None:
    """Whole-actor content hash: pc.md plus every version persona, name-tagged."""
    if not _safe(pid) or not _meta_path(root, pid).exists():
        return None
    h = hashlib.sha256()
    for p in [_meta_path(root, pid)] + [_version_path(root, pid, v) for v in _version_ids(root, pid)]:
        h.update(p.name.encode("utf-8"))
        h.update(p.read_text(encoding="utf-8").encode("utf-8"))
    return h.hexdigest()


def pc_count(root: Path) -> int:
    d = _pcs_dir(root)
    return sum(1 for p in d.iterdir() if p.is_dir() and (p / "pc.md").exists()) if d.exists() else 0


def pc_refs(root: Path) -> list[str]:
    d = _pcs_dir(root)
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and (p / "pc.md").exists())
