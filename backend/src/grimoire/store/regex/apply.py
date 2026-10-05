"""Run a layered list of regex rules over text.

`run` is the hot path (a display frame, a prompt build); `trace` is the same
walk reporting one step per rule, for the editor's test pane. Both skip a rule
that cannot apply here and let a rule that fails at run time pass the text
through unchanged -- a user-authored pattern must never fail a turn.
"""

from __future__ import annotations

import logging

from ..scenes import serialize as scenes_serialize
from . import rules

log = logging.getLogger(__name__)

# One entry of a resolved rule list, in run order:
#   level   "connection" | "global" | "world" | "campaign"
#   rule    a normalised rule
#   off     switched off by a lower level's `off` list
#   source  the connection id for a connection entry, else ""
Entry = dict

# Failures already logged, by (rule id, pattern + flags): a rule that breaks on
# every frame of a stream says so once, and editing the rule says it again.
_logged: set[tuple[str, str]] = set()

# A result past this many times its input (plus a floor) is a replacement that
# expands every match, which is a failure of that rule rather than an answer.
_CAP_FACTOR = 4
_CAP_FLOOR = 4096


class _ExpansionError(Exception):
    pass


def role_of(m: dict) -> str | None:
    """`"user"` for a player post, `"model"` for a model post, None for a line
    no rule may touch -- a roll, a transition or a director note."""
    if m.get("speaker") in scenes_serialize.SYNTHETIC_SPEAKERS:
        return None
    role = m.get("role")
    if role == "user":
        return "user"
    if role == "assistant":
        return "model"
    return None


def skip_reason(entry: Entry, *, role: str | None, phase: str, depth: int) -> str | None:
    """Why `entry` does not apply to this message, or None when it does.

    `phase` is `display`, `prompt` or `store`. `store` qualifies on
    `rewrite_stored` alone and ignores depth: new text is always the newest.
    """
    rule = entry["rule"]
    if not rule["enabled"]:
        return "disabled"
    if entry["off"]:
        return "switched off"
    if role not in rule["targets"]:
        return "not for this role"
    if phase == "store":
        return None if rule["rewrite_stored"] else "not for this phase"
    if phase not in rule["applies"]:
        return "not for this phase"
    lo, hi = rule["min_depth"], rule["max_depth"]
    if (lo is not None and depth < lo) or (hi is not None and depth > hi):
        return "outside depth"
    return None


def _apply(rule: dict, text: str) -> tuple[str, int]:
    """`text` through one rule, and how many matches it replaced. Raises when
    the rule cannot run: a pattern that does not compile, an expansion that
    raises, or a result past the cap."""
    pattern = rules.compile_pattern(rule)
    cap = _CAP_FACTOR * len(text) + _CAP_FLOOR
    replacement, trim = rule["replacement"], rule["trim"]
    grown = len(text)
    count = 0

    def sub(m):
        nonlocal grown, count
        out = rules.expand(replacement, m, trim)
        count += 1
        # Stop as soon as the result must be over the cap, not after building it.
        grown += len(out) - len(m.group(0))
        if grown > cap:
            raise _ExpansionError("the replacement grows the text past the size limit")
        return out

    return pattern.sub(sub, text, count=0 if "g" in rule["flags"] else 1), count


def _log_once(rule: dict, exc: Exception) -> None:
    key = (rule["id"], rule["pattern"] + "/" + rule["flags"])
    if key in _logged:
        return
    _logged.add(key)
    # The rule's id and the error, never the text: a message is private prose.
    log.warning("regex rule %s skipped: %s: %s", rule["id"], type(exc).__name__, exc)


def run(text: str, entries: list[Entry], *, role: str | None, phase: str, depth: int) -> str:
    for entry in entries:
        if skip_reason(entry, role=role, phase=phase, depth=depth) is not None:
            continue
        try:
            text, _ = _apply(entry["rule"], text)
        except Exception as exc:  # noqa: BLE001 -- a user's rule never fails a turn
            _log_once(entry["rule"], exc)
    return text


def trace(
    text: str, entries: list[Entry], *, role: str | None, phase: str, depth: int
) -> list[dict]:
    """One step per entry in run order: `{rule_id, level, name, applied, reason,
    matches, text_after}`. `reason` is why a rule did not apply, or
    `"error: <msg>"` when it failed at run time."""
    steps: list[dict] = []
    for entry in entries:
        rule = entry["rule"]
        reason = skip_reason(entry, role=role, phase=phase, depth=depth)
        matches = 0
        if reason is None:
            try:
                text, matches = _apply(rule, text)
            except Exception as exc:  # noqa: BLE001 -- reported, not raised
                _log_once(rule, exc)
                reason = f"error: {exc}"
        steps.append(
            {
                "rule_id": rule["id"],
                "level": entry["level"],
                "name": rule["name"],
                "applied": reason is None,
                "reason": reason,
                "matches": matches,
                "text_after": text,
            }
        )
    return steps
