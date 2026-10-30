"""Gated command catalog and its fixed English grammar."""

import pytest

from grimoire.intent.commands import (
    CATALOG_VERBS, COMMANDS, NAV_TARGETS, SAFE_STOP_COMMANDS, Command, parse_command,
)
from intent_phrases import CHARACTER_CASES, CHOOSE_CASES, NAV_CASES, SIMPLE_CASES


def test_catalog_gates():
    assert set(COMMANDS) == {
        "navigate", "open_character", "pause", "resume", "stop", "cancel", "review",
        "undo", "reroll", "choose_option", "edit_draft", "delete_scene",
    }
    assert COMMANDS["delete_scene"].gate == "confirm"
    assert {n for n, s in COMMANDS.items() if s.gate == "authorized"} == {
        "undo", "reroll", "choose_option", "edit_draft"}
    assert {n for n, s in COMMANDS.items() if s.gate == "confirm"} == {"delete_scene"}
    assert SAFE_STOP_COMMANDS == {"stop", "pause", "cancel"}
    assert NAV_TARGETS == {"inventory", "map", "journal", "settings", "scenes", "characters"}
    with pytest.raises(TypeError):
        COMMANDS["delete_scene"] = COMMANDS["pause"]


def test_catalog_verbs():
    assert "option" in CATALOG_VERBS and "delete" in CATALOG_VERBS
    assert "the" not in CATALOG_VERBS


def test_gate_is_derived_not_forgeable():
    assert Command("delete_scene").gate == "confirm"
    assert Command("pause").gate == "instant"
    with pytest.raises(TypeError):
        Command("delete_scene", gate="instant")
    command = Command("delete_scene")
    with pytest.raises((AttributeError, TypeError)):
        command.gate = "instant"


@pytest.mark.parametrize("name, argument", [
    ("frobnicate", None), ("navigate", None), ("navigate", "attic"), ("pause", "x"),
    ("choose_option", "0"), ("choose_option", "10"), ("choose_option", None),
    ("open_character", ""), ("open_character", "  "), ("delete_scene", "s1"),
])
def test_command_validation(name, argument):
    with pytest.raises(ValueError):
        Command(name, argument)


@pytest.mark.parametrize("text, target", NAV_CASES)
def test_parse_navigation(text, target):
    assert parse_command(text) == Command("navigate", target)


@pytest.mark.parametrize("text, name", CHARACTER_CASES)
def test_parse_open_character(text, name):
    assert parse_command(text) == Command("open_character", name)


def test_open_character_rejects_reserved_names():
    assert parse_command("open character sheet") is None
    assert parse_command("open character inventory") is None


@pytest.mark.parametrize("text, name", SIMPLE_CASES)
def test_parse_simple(text, name):
    assert parse_command(text) == Command(name)


@pytest.mark.parametrize("text, number", CHOOSE_CASES)
def test_parse_choose_option(text, number):
    assert parse_command(text) == Command("choose_option", number)


@pytest.mark.parametrize("text", ["option 0", "option ten", "option 12", "choose option",
                                  "the tenth option"])
def test_parse_choose_option_out_of_range(text):
    assert parse_command(text) is None


@pytest.mark.parametrize("text", ["delete everything", "open the door", "hello", ""])
def test_parse_rejects_non_commands(text):
    assert parse_command(text) is None
