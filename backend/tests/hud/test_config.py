"""Tests for ``hud.yaml`` reader/writer."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.hud.config import (
    HudConfig,
    HudConfigService,
    OrderedWidget,
    PinnedExtras,
    WidgetGroup,
    default_config,
    deserialize,
    serialize,
)
from grimoire.state_store.errors import InvalidRefError


def test_defaults_when_file_absent(tmp_path: Path) -> None:
    svc = HudConfigService(tmp_path)
    cfg = svc.load("c_1")
    assert cfg.density == "comfortable"
    assert cfg.position == "right"
    visible_ids = {e.id for e in cfg.ordered_widgets if e.visible}
    assert "core.in-game-date" in visible_ids
    assert "core.present-cast" in visible_ids


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    svc = HudConfigService(tmp_path)
    cfg = HudConfig(
        density="compact",
        position="bottom",
        ordered_widgets=[
            OrderedWidget(id="core.in-game-date", visible=True),
            OrderedWidget(id="core.weather", visible=False, options={"units": "C"}),
        ],
        groups=[WidgetGroup(title="World", widgets=["core.in-game-date"])],
        pinned_extras=PinnedExtras(by_character={"char_alice": ["scar"]}),
    )
    svc.save("c_1", cfg)
    assert (tmp_path / "c_1" / "hud.yaml").is_file()
    loaded = svc.load("c_1")
    assert loaded.density == "compact"
    assert loaded.position == "bottom"
    assert [(e.id, e.visible) for e in loaded.ordered_widgets] == [
        ("core.in-game-date", True),
        ("core.weather", False),
    ]
    assert loaded.ordered_widgets[1].options == {"units": "C"}
    assert loaded.groups[0].title == "World"
    assert loaded.pinned_extras.by_character == {"char_alice": ["scar"]}


def test_reset_writes_defaults(tmp_path: Path) -> None:
    svc = HudConfigService(tmp_path)
    svc.save("c_1", HudConfig(density="compact"))
    reset = svc.reset("c_1")
    assert reset.density == "comfortable"
    reloaded = svc.load("c_1")
    assert reloaded.density == "comfortable"


def test_corrupt_yaml_falls_back_to_defaults_and_backs_up(tmp_path: Path) -> None:
    svc = HudConfigService(tmp_path)
    (tmp_path / "c_1").mkdir()
    (tmp_path / "c_1" / "hud.yaml").write_text(": :: not yaml\n", encoding="utf-8")
    cfg = svc.load("c_1")
    assert cfg.density == "comfortable"
    # Original is renamed to *.broken-* so the user can inspect it.
    backups = list((tmp_path / "c_1").glob("hud.broken-*"))
    assert len(backups) == 1


def test_unknown_widget_ids_preserved(tmp_path: Path) -> None:
    """Removing a mechanics module should not delete the user's entries."""
    svc = HudConfigService(tmp_path)
    cfg = HudConfig(
        ordered_widgets=[
            OrderedWidget(id="removed-module.widget", visible=False),
            OrderedWidget(id="core.in-game-date"),
        ]
    )
    svc.save("c_1", cfg)
    loaded = svc.load("c_1")
    assert any(e.id == "removed-module.widget" for e in loaded.ordered_widgets)


def test_with_ensured_entries_appends_missing() -> None:
    cfg = HudConfig(ordered_widgets=[OrderedWidget(id="core.in-game-date")])
    out = cfg.with_ensured_entries(["core.in-game-date", "wod.blood-pool"])
    assert [e.id for e in out.ordered_widgets] == [
        "core.in-game-date",
        "wod.blood-pool",
    ]


def test_serialize_round_trips_default() -> None:
    cfg = default_config()
    data = serialize(cfg)
    again = deserialize(data)
    assert [e.id for e in again.ordered_widgets] == [e.id for e in cfg.ordered_widgets]
    assert [g.title for g in again.groups] == [g.title for g in cfg.groups]


@pytest.mark.parametrize(
    "bad_id",
    ["..", "../escape", "/abs", "a/b", ".hidden", "with\x00null"],
)
def test_unsafe_campaign_id_rejected(tmp_path: Path, bad_id: str) -> None:
    svc = HudConfigService(tmp_path)
    with pytest.raises(InvalidRefError):
        svc.load(bad_id)
    with pytest.raises(InvalidRefError):
        svc.save(bad_id, HudConfig())
    with pytest.raises(InvalidRefError):
        svc.reset(bad_id)
    # Nothing should have escaped the data root.
    assert list(tmp_path.iterdir()) == []
