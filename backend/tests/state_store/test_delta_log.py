"""Tests for delta_log utility functions."""

from grimoire.state_store.delta_log import _coerce_for_column


class TestCoerceForColumn:
    def test_does_not_double_encode_strings(self):
        already_json = '{"key": "value"}'
        result = _coerce_for_column("character_state", "knowledge_state", already_json)
        assert result == already_json

    def test_plain_string_passes_through(self):
        result = _coerce_for_column("character_state", "location_ref", "some_ref")
        assert result == "some_ref"

    def test_serializes_dicts(self):
        result = _coerce_for_column("character_state", "knowledge_state", {"key": "value"})
        assert isinstance(result, str)
        assert '"key"' in result

    def test_serializes_lists(self):
        result = _coerce_for_column("facts", "tags", ["a", "b"])
        assert isinstance(result, str)
        assert '"a"' in result

    def test_converts_bools_to_int(self):
        assert _coerce_for_column("character_state", "visible_to_pc", True) == 1
        assert _coerce_for_column("character_state", "visible_to_pc", False) == 0

    def test_none_passes_through(self):
        assert _coerce_for_column("character_state", "location_ref", None) is None

    def test_int_passes_through(self):
        assert _coerce_for_column("scenes", "turn_number", 42) == 42
