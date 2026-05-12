from pathlib import Path

import pytest

from grimoire.files.yaml_io import YamlError, dump_yaml, load_yaml, parse_yaml, write_yaml


def test_parse_yaml_mapping() -> None:
    data = parse_yaml("id: oil-painting\nname: Gothic\ntags: [a, b]\n")
    assert data == {"id": "oil-painting", "name": "Gothic", "tags": ["a", "b"]}


def test_parse_yaml_empty_returns_none() -> None:
    assert parse_yaml("") is None
    assert parse_yaml("   \n") is None


def test_parse_yaml_invalid_raises() -> None:
    with pytest.raises(YamlError):
        parse_yaml("key: [unclosed\n")


def test_load_yaml_reads_utf8(tmp_path: Path) -> None:
    path = tmp_path / "setting.yaml"
    path.write_text("name: café\nseasons: [été, hiver]\n", encoding="utf-8")
    assert load_yaml(path) == {"name": "café", "seasons": ["été", "hiver"]}


def test_load_yaml_error_includes_path(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("key: [oops\n", encoding="utf-8")
    with pytest.raises(YamlError) as info:
        load_yaml(path)
    assert str(path) in str(info.value)


def test_write_yaml_creates_parents_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "a" / "b" / "campaign.yaml"
    data = {"id": "by-night-london", "pcs": ["alistair"], "version": 3}
    write_yaml(path, data)
    assert path.exists()
    assert load_yaml(path) == data


def test_dump_yaml_preserves_key_order() -> None:
    data = {"id": "x", "name": "y", "tags": ["a"]}
    rendered = dump_yaml(data)
    assert rendered.index("id:") < rendered.index("name:") < rendered.index("tags:")
