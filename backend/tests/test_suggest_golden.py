"""The suggestion prompt's instruction section and the whole intent prompt,
pinned byte for byte as they were before Slice E (capstone spec §15, §15.1).

The golden file was generated on pre-change code by `python -m
tests.suggest_golden` and is never regenerated to make a test pass: these two
prompts are promised unchanged, so a diff here is the promise breaking.
"""

from __future__ import annotations

import json

from grimoire.store import suggest
from tests import suggest_golden


def _golden() -> dict:
    return json.loads(suggest_golden.GOLDEN.read_text(encoding="utf-8"))


def test_the_instruction_section_is_pinned():
    golden = _golden()
    for label, (snap, cands, offscreen, direction) in suggest_golden.SYSTEM_SNAPS.items():
        got = [suggest.build_prompt(snap, cands, offscreen, direction)[0]]
        assert got == golden[f"system/{label}"], label


def test_the_intent_prompt_is_pinned(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    golden = _golden()
    for label, (cid, offscreen) in suggest_golden.intent_campaigns().items():
        got = suggest.build_intent_prompt(cid, suggest_golden.TYPED, offscreen=offscreen)
        assert got == golden[f"intent/{label}"], label


def test_the_golden_file_has_every_variant(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assert set(_golden()) == set(suggest_golden.variants())
