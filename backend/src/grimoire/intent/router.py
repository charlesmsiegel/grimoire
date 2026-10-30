"""Route an utterance into one lane: command, in-character, director, or none.

Deterministic and pure: no model calls, clock, or I/O. The result is a
hypothesis; callers execute commands and commit fiction through their own
authorization paths.
"""

from __future__ import annotations

import re

from .commands import CATALOG_VERBS, COMMANDS, Command, parse_command
from .model import PC_KINDS, Intent, IntentKind, RoutingContext, Utterance
from .text import (
    first_word, grammar_form, marker_of, norm, split_sentences, straighten, strip_marker,
    wrapped,
)

_FIRST_PERSON = frozenset({"i", "i'm", "i'll", "i've", "i'd"})
_MODE_LANE = {"command": "command", "director": "director", "speech": None, "action": None}
_POLITE = re.compile(r"(?P<prefix>(?:can|could|would|will) you|let's|lets) .+")
_QUOTED_SPAN = re.compile(r'"[^"]*"')
_INNER_WAKE = re.compile(r"(?<![\w'])grimoire(?![\w'])\s*(?P<next>[,:]|[a-z]+)?")
_IGNORED = {"empty": "nothing was said", "no_active_pc": "you are not controlling a character",
            "not_director": "you don't have director authority"}
_TRAILING = ".!?,;: "


def _command_label(command: Command) -> str:
    label = COMMANDS[command.name].label
    return f"{label} {command.argument}" if command.argument else label


def _feedback(kind: IntentKind, text: str, command: Command | None, reason: str) -> str:
    if kind == "ui_command":
        suffix = " — needs confirmation" if command.gate == "confirm" else ""
        return f"Command: {_command_label(command)}{suffix}"
    if kind == "pc_speech":
        return f"In character (speech): {text}"
    if kind == "pc_action":
        return f"In character (action): {text}"
    if kind == "director_instruction":
        return f"Director: {text}"
    if kind == "uncertain":
        return "Not sure what you meant — nothing was done"
    return f"Ignored: {_IGNORED[reason]}"


def _intent(kind: IntentKind, text: str, reason: str, command: Command | None = None,
            actor_id: str | None = None, candidates: tuple[Intent, ...] = ()) -> Intent:
    return Intent(kind, text, reason, _feedback(kind, text, command, reason),
                  command=command, actor_id=actor_id, candidates=candidates)


def _reject(text: str, reason: str) -> Intent:
    return _intent("reject", text, reason)


def _allowed(kind: IntentKind, context: RoutingContext) -> str | None:
    """The reject reason when the speaker lacks authority for this lane."""
    if kind in PC_KINDS and context.active_pc is None:
        return "no_active_pc"
    if kind == "director_instruction" and not context.can_direct:
        return "not_director"
    return None


def _lane(kind: IntentKind, payload: str, reason: str, original: str,
          context: RoutingContext) -> Intent:
    """A fiction or director intent, subject to the speaker's authority."""
    if not payload:
        return _reject(original, "empty")
    if refusal := _allowed(kind, context):
        return _reject(original, refusal)
    actor = context.active_pc if kind in PC_KINDS else None
    return _intent(kind, payload, reason, actor_id=actor)


def _command(command: Command, text: str, reason: str) -> Intent:
    return _intent("ui_command", text, reason, command=command)


def _uncertain(text: str, reason: str, readings: list[tuple[IntentKind, str, Command | None]],
               context: RoutingContext) -> Intent:
    """Offer only readings the speaker may act on; destructive ones never."""
    candidates = []
    for kind, payload, command in readings:
        if command is not None and command.gate == "confirm":
            continue
        if _allowed(kind, context):
            continue
        actor = context.active_pc if kind in PC_KINDS else None
        candidates.append(_intent(kind, payload, "explicit_mode", command, actor))
    return _intent("uncertain", text, reason, candidates=tuple(candidates))


def _first_units(original: str, normalised_unit: str) -> str:
    """The original-case words that normalise to the given leading unit."""
    words = straighten(original).split()
    return " ".join(words[:len(normalised_unit.split())]).rstrip(_TRAILING)


def _sentence_lane(sentence: str) -> str:
    lane, separated = marker_of(sentence)
    return lane if lane and separated else "fiction"


def _is_mixed(text: str, sentences: list[str]) -> bool:
    unquoted = _QUOTED_SPAN.sub(" _ ", norm(text)).strip()
    for match in _INNER_WAKE.finditer(unquoted):
        following = match.group("next")
        if match.start() > 0 and following and (following in ",:" or following in CATALOG_VERBS):
            return True
    if len(sentences) < 2:
        return False
    first_lane = _sentence_lane(sentences[0])
    first_command = parse_command(sentences[0])
    for sentence in sentences[1:]:
        lane, separated = marker_of(sentence)
        if lane and separated and lane != first_lane:
            return True
        command = parse_command(sentence)
        if command is not None and command != first_command:
            return True
    return False


def _repeated(text: str, sentences: list[str]) -> Intent | None:
    """'stop. stop!' or 'stop stop': the same command said more than once."""
    for units in (sentences, norm(text).split(" ")):
        if len(units) < 2:
            continue
        commands = {parse_command(unit) for unit in units}
        if len(commands) == 1 and None not in commands:
            return _command(commands.pop(), _first_units(text, units[0]), "bare_command")
    return None


def _polite(text: str, context: RoutingContext) -> Intent | None:
    match = _POLITE.fullmatch(grammar_form(text))
    if match is None:
        return None
    words = straighten(text).split()
    if words and words[0].strip(_TRAILING).lower() == "please":
        words = words[1:]
    phrase = " ".join(words[len(match.group("prefix").split()):]).rstrip(_TRAILING)
    command = parse_command(phrase)
    if command is None:
        return None
    return _uncertain(text, "polite_command",
                      [("ui_command", phrase, command), ("pc_speech", text, None)], context)


def _route_auto(text: str, context: RoutingContext) -> Intent:
    if (inner := wrapped(text, '"', '"')) is not None:
        return _lane("pc_speech", inner, "quoted", text, context)
    if (inner := wrapped(text, "*", "*")) is not None:
        return _lane("pc_action", inner, "asterisks", text, context)
    sentences = split_sentences(text)
    if _is_mixed(text, sentences):
        return _uncertain(text, "mixed_utterance", [], context)
    if first_word(text) == "grimoire":
        remainder = strip_marker(text)
        command = parse_command(remainder)
        if command is None:
            return _uncertain(text, "unknown_command", [], context)
        return _command(command, remainder, "wake_word")
    if (repeated := _repeated(text, sentences)) is not None:
        return repeated
    lane, separated = marker_of(text)
    if lane == "director":
        if separated:
            return _lane("director_instruction", strip_marker(text), "director_marker", text, context)
        return _uncertain(text, "marker_no_separator",
                          [("director_instruction", strip_marker(text), None),
                           ("pc_speech", text, None)], context)
    if first_word(text) in _FIRST_PERSON:
        return _lane("pc_action", text, "first_person", text, context)
    if (command := parse_command(text)) is not None:
        return _command(command, text, "bare_command")
    if (polite := _polite(text, context)) is not None:
        return polite
    if grammar_form(text).split(" ", 1)[0] in CATALOG_VERBS:
        return _uncertain(text, "command_like", [("pc_speech", text, None)], context)
    return _lane("pc_speech", text, "default_speech", text, context)


def _route_explicit(text: str, mode: str, context: RoutingContext) -> Intent:
    lane, separated = marker_of(text)
    if lane is not None and separated and lane != _MODE_LANE[mode]:
        return _uncertain(text, "lane_conflict", [], context)
    if mode == "command":
        remainder = strip_marker(text) if lane == "command" else text
        command = parse_command(remainder)
        if command is None:
            return _uncertain(text, "unknown_command", [], context)
        return _command(command, remainder, "explicit_mode")
    if mode == "speech":
        return _lane("pc_speech", wrapped(text, '"', '"') or text, "explicit_mode", text, context)
    if mode == "action":
        return _lane("pc_action", wrapped(text, "*", "*") or text, "explicit_mode", text, context)
    payload = strip_marker(text) if lane == "director" else text
    return _lane("director_instruction", payload, "explicit_mode", text, context)


def _route_text(text: str, mode: str, context: RoutingContext) -> Intent:
    """Route one transcript as if typed: explicit modes, auto rules, authority."""
    text = text.strip()
    if not text:
        return _reject(text, "empty")
    if mode == "auto":
        return _route_auto(text, context)
    return _route_explicit(text, mode, context)


def route(utterance: Utterance, context: RoutingContext) -> Intent:
    """Classify one utterance. Never executes, navigates or commits anything."""
    return _route_text(utterance.text, utterance.mode, context)
