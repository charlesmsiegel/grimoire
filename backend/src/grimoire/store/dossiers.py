"""Per-character campaign "dossier" — a short standing paragraph (who they are + their
current status in this campaign) feeding the off-scene cast's "Active in this campaign,
elsewhere" tier. Campaign-level; written at absorb. Plain text at
<croot>/characters/<cid>/dossier.md. Pure file IO + prompt/parse only; the LLM call
lives in the route layer and the prompt text in templates/dossier/.
"""

from __future__ import annotations

from pathlib import Path

from .. import prompts
from . import atomic


def dossier_path(croot: Path, cid: str) -> Path:
    return croot / "characters" / cid / "dossier.md"


def read(croot: Path, cid: str) -> str:
    p = dossier_path(croot, cid)
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def write(croot: Path, cid: str, text: str) -> None:
    p = dossier_path(croot, cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(p, text.strip() + "\n")


def build_prompt(name: str, prior: str, transcript: str) -> list[dict]:
    return [{"role": "system", "content": prompts.render("dossier/system.j2")},
            {"role": "user", "content": prompts.render("dossier/user.j2", name=name,
                                                       prior=prior, transcript=transcript)}]


def parse_output(text: str) -> str:
    return text.strip()
