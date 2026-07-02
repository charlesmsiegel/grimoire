"""Per-character campaign "dossier" — a short standing paragraph (who they are + their
current status in this campaign) feeding the off-scene cast's "Active in this campaign,
elsewhere" tier. Campaign-level; written at absorb. Plain text at
<croot>/characters/<cid>/dossier.md. Pure file IO + prompt/parse only; the LLM call
lives in the route layer.
"""

from __future__ import annotations

from pathlib import Path

DOSSIER_INSTRUCTION = (
    "You are updating a game master's dossier on a character in an ongoing campaign. "
    "Given the character's prior dossier (may be empty) and the latest scene transcript, "
    "reply with ONE short paragraph (3-4 sentences) on who they are and their current "
    "standing after this scene. Third person, present tense. No headings or labels."
)


def dossier_path(croot: Path, cid: str) -> Path:
    return croot / "characters" / cid / "dossier.md"


def read(croot: Path, cid: str) -> str:
    p = dossier_path(croot, cid)
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def write(croot: Path, cid: str, text: str) -> None:
    p = dossier_path(croot, cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.strip() + "\n", encoding="utf-8")


def build_prompt(name: str, prior: str, transcript: str) -> list[dict]:
    head = f"Character: {name}\nPrior dossier: {prior or '(none)'}\n\nScene transcript:\n"
    return [{"role": "system", "content": DOSSIER_INSTRUCTION},
            {"role": "user", "content": head + transcript}]


def parse_output(text: str) -> str:
    return text.strip()
