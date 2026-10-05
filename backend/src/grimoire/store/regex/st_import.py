"""SillyTavern regex scripts, read into rules (spec §7).

`scripts` finds the scripts in whatever was uploaded -- one script, a list, or
a settings / preset export carrying `regex_scripts` -- and `preview` maps each
to a proposed rule with a verdict and notes. Nothing here writes: the import
route commits the rows the user keeps, with fresh ids.

A script's pattern goes through `translate`; its other fields map per the
table in the spec. Anything that cannot be carried over faithfully makes the
row untranslatable (reported, never imported as a guess); anything that is
carried over with a difference is a note.
"""

from __future__ import annotations

import re

from . import rules, translate

# SillyTavern's `regex_placement`.
_PLACEMENT_TARGET = {1: "user", 2: "model"}
_PLACEMENT_DROPPED = {3: "slash commands", 5: "world info", 6: "reasoning"}

_SLASHED = re.compile(r"/(.+)/([A-Za-z]*)", re.DOTALL)
# `{{match}}` is the only macro Grimoire expands in a replacement.
_MACRO = re.compile(r"\{\{(?!match\}\})[^{}]*\}\}")

_NOTE_STORED = ("SillyTavern rewrote the stored chat with this script. It is "
                "imported to apply on display and in the prompt, leaving stored "
                "text raw; rewriting stored text is yours to switch on.")
_NOTE_ON_EDIT = ("runOnEdit is ignored: display and prompt rules apply on every "
                 "read, so edits are always covered.")

_RANK = {translate.EXACT: 0, translate.APPROXIMATE: 1, translate.UNTRANSLATABLE: 2}


def scripts(data) -> list:
    """The regex scripts in `data`, in file order. Raises ValueError when it
    holds none: an upload that is not a script, a list or an export."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "regex_scripts" in data:
            found = data["regex_scripts"]
        elif isinstance(data.get("extensions"), dict) and "regex_scripts" in data["extensions"]:
            found = data["extensions"]["regex_scripts"]
        elif "findRegex" in data:
            return [data]
        else:
            raise ValueError("no regex scripts found: expected a script, a list of "
                             "scripts, or an export with regex_scripts")
        if not isinstance(found, list):
            raise ValueError("regex_scripts must be a list")
        return found
    raise ValueError("expected a regex script, a list of them, or a SillyTavern export")


def _split(find: str) -> tuple[str, str]:
    """(body, flags) of `/body/flags`, or of a bare body (no flags) -- the way
    SillyTavern's `regexFromString` reads it."""
    m = _SLASHED.fullmatch(find)
    if m is None:
        return find, ""
    return m.group(1), m.group(2)


class _Row:
    """One script's mapping in progress: its verdict so far and its notes."""

    def __init__(self) -> None:
        self.verdict = translate.EXACT
        self.notes: list[str] = []

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    def mark(self, verdict: str, note: str | None = None) -> None:
        if _RANK[verdict] > _RANK[self.verdict]:
            self.verdict = verdict
        if note:
            self.note(note)

    def refuse(self, reason: str) -> None:
        self.mark(translate.UNTRANSLATABLE, f"Won't translate: {reason}.")


def _targets(script: dict, row: _Row) -> list[str]:
    placement = script.get("placement", [])
    if not isinstance(placement, list):
        placement = []
    targets: list[str] = []
    for p in placement:
        known = isinstance(p, int) and not isinstance(p, bool)
        if known and p in _PLACEMENT_TARGET:
            targets.append(_PLACEMENT_TARGET[p])
        elif known and p in _PLACEMENT_DROPPED:
            row.note(f"Placement {p} ({_PLACEMENT_DROPPED[p]}) has no "
                     "counterpart here and was dropped.")
        else:
            row.note(f"Placement {p!r} is not one SillyTavern documents "
                     "and was dropped.")
    if not targets:
        row.refuse("it runs on nothing Grimoire processes (no user-input or "
                   "AI-output placement)")
    return [t for t in rules.TARGETS if t in targets]


def _applies(script: dict, row: _Row) -> list[str]:
    markdown, prompt = bool(script.get("markdownOnly")), bool(script.get("promptOnly"))
    if not markdown and not prompt:
        row.note(_NOTE_STORED)
        return ["display", "prompt"]
    return [p for p, on in (("display", markdown), ("prompt", prompt)) if on]


def _depth(raw, field: str, row: _Row) -> int | None:
    if raw is None or raw == -1 or raw == "":
        return None
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
        return raw
    row.note(f"{field} {raw!r} is not a depth and was ignored.")
    return None


def _replacement(script: dict, row: _Row) -> str:
    replacement = script.get("replaceString", "")
    if not isinstance(replacement, str):
        row.refuse("replaceString is not a string")
        return ""
    i = 0
    while i < len(replacement) - 1:
        pair = replacement[i:i + 2]
        if pair in ("$`", "$'"):
            row.refuse(f"the replacement uses {pair} (the text before or after the "
                       "match), which is not supported")
            break
        i += 2 if pair == "$$" else 1
    for macro in dict.fromkeys(_MACRO.findall(replacement)):
        row.mark(translate.APPROXIMATE, f"The replacement contains {macro}, which is "
                                        "not expanded here; it is inserted as written.")
    return replacement


def _trim(script: dict, row: _Row) -> list[str]:
    trim = script.get("trimStrings", [])
    if trim is None:
        return []
    if not isinstance(trim, list) or not all(isinstance(t, str) for t in trim):
        row.refuse("trimStrings is not a list of strings")
        return []
    return list(trim)


def _pattern(script: dict, row: _Row) -> tuple[str | None, str, str]:
    """(python pattern, python flags, the original as `/body/flags`)."""
    find = script.get("findRegex")
    if not isinstance(find, str) or not find:
        row.refuse("the script has no findRegex")
        return None, "", ""
    body, flags = _split(find)
    original = f"/{body}/{flags}"
    if script.get("substituteRegex"):
        row.refuse("the pattern substitutes macros (substituteRegex), which is not supported")
        return None, "", original
    result = translate.translate(body, flags)
    for note in result["notes"]:
        row.note(note)
    row.mark(result["verdict"])
    return result["pattern"], result["flags"], original


def _map(script: dict) -> tuple[_Row, dict | None]:
    row = _Row()
    pattern, flags, original = _pattern(script, row)
    rule = {
        "name": script.get("scriptName") if isinstance(script.get("scriptName"), str) else "",
        "enabled": not script.get("disabled", False),
        "pattern": pattern,
        "flags": flags,
        "replacement": _replacement(script, row),
        "trim": _trim(script, row),
        "targets": _targets(script, row),
        "applies": _applies(script, row),
        "rewrite_stored": False,
        "min_depth": _depth(script.get("minDepth"), "minDepth", row),
        "max_depth": _depth(script.get("maxDepth"), "maxDepth", row),
    }
    if script.get("runOnEdit"):
        row.note(_NOTE_ON_EDIT)
    if row.verdict == translate.UNTRANSLATABLE:
        return row, None
    if row.verdict == translate.APPROXIMATE or row.notes:
        rule["imported"] = {"from": "sillytavern", "pattern": original, "notes": list(row.notes)}
    try:
        clean = rules.normalise(rule)
    except rules.RuleError as exc:
        row.refuse(str(exc))
        return row, None
    # The import mints the id it commits under; a preview has none to show.
    clean.pop("id", None)
    return row, clean


def preview(data) -> list[dict]:
    """One row per script: `{"index", "name", "verdict", "notes", "rule",
    "original"}`. `rule` is the proposed rule (no id), or None when the
    script will not translate; `original` is the script as given. Raises
    ValueError when `data` holds no scripts (see `scripts`)."""
    rows = []
    for index, script in enumerate(scripts(data)):
        if not isinstance(script, dict):
            rows.append({"index": index, "name": "", "verdict": translate.UNTRANSLATABLE,
                         "notes": ["Won't translate: this entry is not a regex script."],
                         "rule": None, "original": script})
            continue
        row, rule = _map(script)
        name = script.get("scriptName")
        rows.append({"index": index, "name": name if isinstance(name, str) else "",
                     "verdict": row.verdict, "notes": row.notes, "rule": rule,
                     "original": script})
    return rows
