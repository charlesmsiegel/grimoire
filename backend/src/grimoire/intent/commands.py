"""The fixed voice/keyboard command catalog and its English grammar.

A command's gate belongs to the catalog, never to the input modality: a
spoken command can't obtain a weaker gate than the same click would.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from .text import grammar_form, norm, straighten

Gate = Literal["instant", "authorized", "confirm"]


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    gate: Gate
    label: str


COMMANDS = MappingProxyType({spec.name: spec for spec in (
    CommandSpec("navigate", "instant", "open"),
    CommandSpec("open_character", "instant", "open character"),
    CommandSpec("pause", "instant", "pause"),
    CommandSpec("resume", "instant", "resume"),
    CommandSpec("stop", "instant", "stop"),
    CommandSpec("cancel", "instant", "cancel"),
    CommandSpec("review", "instant", "open review"),
    CommandSpec("undo", "authorized", "undo"),
    CommandSpec("reroll", "authorized", "reroll"),
    CommandSpec("choose_option", "authorized", "choose option"),
    CommandSpec("edit_draft", "authorized", "edit draft"),
    CommandSpec("delete_scene", "confirm", "delete the scene"),
)})

# Acting on a misheard stop/pause/cancel only halts something.
SAFE_STOP_COMMANDS = frozenset({"stop", "pause", "cancel"})

CATALOG_VERBS = frozenset({
    "open", "show", "go", "navigate", "pause", "resume", "unpause", "continue", "stop",
    "cancel", "undo", "reroll", "regenerate", "choose", "pick", "select", "edit", "review",
    "delete", "remove", "erase", "option",
})

_NAV_SYNONYMS = {
    "inventory": "inventory", "map": "map", "journal": "journal", "settings": "settings",
    "scenes": "scenes", "scene list": "scenes",
    "characters": "characters", "character list": "characters",
}
NAV_TARGETS = frozenset(_NAV_SYNONYMS.values())
_RESERVED_NAMES = frozenset(_NAV_SYNONYMS) | {"list", "sheet", "sheets"}
_ARGUMENT_REQUIRED = frozenset({"navigate", "open_character", "choose_option"})

_NUMBERS = {word: str(n) for n, word in enumerate(
    ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine"), start=1)}
_NUMBERS.update({str(n): str(n) for n in range(1, 10)})
_ORDINALS = {word: str(n) for n, word in enumerate(
    ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth"),
    start=1)}


@dataclass(frozen=True, slots=True)
class Command:
    """A recognised app command. Its gate is looked up, so it cannot be forged."""

    name: str
    argument: str | None = None

    def __post_init__(self) -> None:
        if self.name not in COMMANDS:
            raise ValueError(f"unknown command: {self.name!r}")
        if (self.argument is not None) != (self.name in _ARGUMENT_REQUIRED):
            raise ValueError(f"{self.name} argument is {'required' if self.argument is None else 'forbidden'}")
        if self.name == "navigate" and self.argument not in NAV_TARGETS:
            raise ValueError(f"unknown navigation target: {self.argument!r}")
        if self.name == "choose_option" and self.argument not in _ORDINALS.values():
            raise ValueError("choose_option argument must be '1'..'9'")
        if self.name == "open_character" and not (isinstance(self.argument, str) and self.argument.strip()):
            raise ValueError("open_character needs a character name")

    @property
    def gate(self) -> Gate:
        return COMMANDS[self.name].gate


def _alternatives(words) -> str:
    return "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))


_NUMBER = f"(?P<number>{_alternatives(_NUMBERS)})"
_ORDINAL = f"(?P<ordinal>{_alternatives(_ORDINALS)})"
_FIXED = [(re.compile(pattern), name) for pattern, name in (
    (rf"(?:open|show|go to|navigate to)(?: the| my)? (?P<target>{_alternatives(_NAV_SYNONYMS)})",
     "navigate"),
    (r"(?:open|show)(?: the)? review(?: queue)?|review changes", "review"),
    (r"pause", "pause"),
    (r"resume|continue|unpause", "resume"),
    (r"stop|stop audio|stop talking|stop generating|be quiet", "stop"),
    (r"cancel|never mind", "cancel"),
    (r"undo|undo that", "undo"),
    (r"reroll|regenerate|try again", "reroll"),
    (r"edit(?: the)? draft|edit my reply", "edit_draft"),
    (r"(?:delete|remove|erase)(?: the| this)? scene", "delete_scene"),
    (rf"(?:(?:choose|pick|select) )?(?:the )?option {_NUMBER}", "choose_option"),
    (rf"(?:(?:choose|pick|select) )?the {_ORDINAL} option", "choose_option"),
)]
_CHARACTER = re.compile(r"(?P<prefix>(?:open|show)(?: the)? character) .+")
_SHEET = re.compile(r"(?:open|show) .+'s sheet")
_TRAILING = ".!?,;: "


def _original_words(text: str) -> list[str]:
    words = straighten(text).split()
    if words and words[0].strip(_TRAILING).lower() == "please":
        words = words[1:]
    return words


def _character(text: str, form: str) -> Command | None:
    words = _original_words(text)
    if match := _CHARACTER.fullmatch(form):
        name = " ".join(words[len(match.group("prefix").split()):]).rstrip(_TRAILING)
    elif _SHEET.fullmatch(form):
        name = re.sub(r"'s\s+sheet$", "", " ".join(words[1:]).rstrip(_TRAILING), flags=re.I)
    else:
        return None
    if not name or norm(name) in _RESERVED_NAMES:
        return None
    return Command("open_character", name)


def parse_command(text: str) -> Command | None:
    """Match one whole utterance against the catalog grammar, or return None."""
    form = grammar_form(text)
    for pattern, name in _FIXED:
        match = pattern.fullmatch(form)
        if match is None:
            continue
        groups = match.groupdict()
        if name == "navigate":
            return Command(name, _NAV_SYNONYMS[groups["target"]])
        if name == "choose_option":
            number = groups.get("number")
            return Command(name, _NUMBERS[number] if number else _ORDINALS[groups["ordinal"]])
        return Command(name)
    return _character(text, form)
