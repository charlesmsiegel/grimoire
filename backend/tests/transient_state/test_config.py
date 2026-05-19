"""TransientStateConfig — per-campaign YAML knobs."""

from __future__ import annotations

from pathlib import Path

from grimoire.transient_state.config import TransientStateConfig
from grimoire.types.transient import EntityKind


def test_defaults_match_spec():
    cfg = TransientStateConfig()
    assert cfg.auto_apply_threshold == 0.85
    assert cfg.review_threshold == 0.60
    assert cfg.promote_to_fact.reinforcement_count == 5
    assert cfg.conflict_window_posts == 10
    assert cfg.vacuum.enabled is True
    assert cfg.vacuum.retain_superseded_days == 30


def test_from_yaml_parses_block(tmp_path: Path):
    path = tmp_path / "transient.yaml"
    path.write_text(
        "auto_apply_threshold: 0.75\n"
        "promote_to_fact:\n"
        "  reinforcement_count: 7\n"
        "conflict_window_posts: 20\n"
        "decay:\n"
        "  character:\n"
        "    mood: { posts: 15 }\n",
        encoding="utf-8",
    )
    cfg = TransientStateConfig.from_yaml(path)
    assert cfg.auto_apply_threshold == 0.75
    assert cfg.promote_to_fact.reinforcement_count == 7
    assert cfg.conflict_window_posts == 20
    assert cfg.decay_table[EntityKind.CHARACTER]["mood"].posts == 15


def test_from_yaml_missing_file_returns_defaults(tmp_path: Path):
    cfg = TransientStateConfig.from_yaml(tmp_path / "nope.yaml")
    assert cfg.auto_apply_threshold == 0.85
    assert cfg.promote_to_fact.reinforcement_count == 5


def test_from_yaml_empty_file_returns_defaults(tmp_path: Path):
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    cfg = TransientStateConfig.from_yaml(path)
    assert cfg.auto_apply_threshold == 0.85
