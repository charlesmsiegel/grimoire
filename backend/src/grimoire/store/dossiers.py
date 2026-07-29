"""Per-character campaign "dossier" — a short standing paragraph (who they are + their
current status in this campaign) feeding the off-scene cast's "Active in this campaign,
elsewhere" tier. Campaign-level; proposed at absorb and written when the reviewer saves
the chronicle. Plain text at <croot>/characters/<cid>/dossier.md. Pure file IO +
prompt/parse/stage only; the LLM call lives in the route layer and the prompt text in
templates/dossier/.
"""

from __future__ import annotations

from pathlib import Path

from .. import prompts
from . import atomic


class BadDossierId(Exception):
    """An id that could escape the characters directory."""


def _safe_id(cid: str) -> bool:
    """Reject ids that could escape the characters directory (defense in depth,
    mirroring entities._safe_id). Dossier ids now arrive on client-supplied
    PUT /chronicle edit rows, not just from the scene cast."""
    return isinstance(cid, str) and cid not in ("", ".", "..") \
        and "/" not in cid and "\\" not in cid


def dossier_path(croot: Path, cid: str) -> Path:
    if not _safe_id(cid):
        raise BadDossierId(cid)
    # overlay-ok: dossier.md is campaign-local, merely filed inside the actor's
    # dir for locality — it is never inherited from the world, so there is
    # nothing for store/overlay.py to resolve here
    return croot / "characters" / cid / "dossier.md"


def read(croot: Path, cid: str) -> str:
    try:
        p = dossier_path(croot, cid)
    except BadDossierId:
        return ""              # nothing can live there: read like a missing file
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def write(croot: Path, cid: str, text: str) -> None:
    p = dossier_path(croot, cid)     # raises BadDossierId before mkdir touches disk
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(p, text.strip() + "\n")


def stage_edit(croot: Path, cid: str, name: str, text: str) -> dict | None:
    """The refreshed dossier as a StagedEdit against the stored one, or None when
    there is nothing to propose (blank reply, or the same paragraph again).

    Absorb never writes dossiers itself (#235): a refresh that lands before the
    reviewer saves would survive a Cancel, and a run interrupted mid-loop would
    leave half the cast holding post-scene dossiers for a scene the chronicle
    never recorded. Staging puts them on the same commit boundary as every other
    edit -- absorb proposes, PUT /chronicle applies."""
    after = text.strip()
    before = read(croot, cid)
    if not after or after == before:
        return None
    return {"id": f"dossier:{cid}", "kind": "dossier",
            "target": {"kind": "characters", "id": cid},
            "label": f"{name} — campaign dossier", "field": "dossier",
            "before": before, "after": after, "authored": False}


def build_prompt(name: str, prior: str, transcript: str) -> list[dict]:
    return [{"role": "system", "content": prompts.render("dossier/system.j2")},
            {"role": "user", "content": prompts.render("dossier/user.j2", name=name,
                                                       prior=prior, transcript=transcript)}]


def parse_output(text: str) -> str:
    return text.strip()
