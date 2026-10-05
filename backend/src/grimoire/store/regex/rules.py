"""The regex rule: its schema, validation and replacement syntax.

A rule is a plain dict (so it round-trips through JSON untouched) with the keys
in `_DEFAULTS`. `normalise` is the one door a rule comes in through: it fills
defaults, checks types and enums, mints an id and compiles the pattern, so
everything downstream can trust the shape.

The replacement syntax is SillyTavern's, kept so imported replacements need no
translation, and is expanded here rather than by `re.sub`'s own template parser
(which would read `\\1` and choke on `$`).
"""

from __future__ import annotations

import functools
import re
import secrets

TARGETS = ("model", "user")
PHASES = ("display", "prompt")
FLAGS = "gimsa"

# `g` is not a compile flag: it only chooses between replacing every match and
# the first one, which is `apply`'s business.
_COMPILE_FLAGS = {
    "i": re.IGNORECASE,
    "m": re.MULTILINE,
    "s": re.DOTALL,
    "a": re.ASCII,
}

# Keys and the value a missing one takes. `pattern` is the one with none.
_DEFAULTS: dict = {
    "name": "",
    "enabled": True,
    "flags": "g",
    "replacement": "",
    "trim": [],
    "targets": ["model"],
    "applies": ["display", "prompt"],
    "rewrite_stored": False,
    "min_depth": None,
    "max_depth": None,
    "imported": None,
}


class RuleError(ValueError):
    """A rule that cannot be saved. `index` is its place in the list it came in
    (None for a single rule) and `field` the key at fault, so an editor can put
    the message next to the control it belongs to."""

    def __init__(self, message: str, *, index: int | None = None, field: str | None = None):
        super().__init__(message)
        self.index = index
        self.field = field


def mint_id() -> str:
    return "r-" + secrets.token_hex(4)


@functools.lru_cache(maxsize=512)
def _compile(pattern: str, flags: str) -> re.Pattern:
    bits = 0
    for f in flags:
        bits |= _COMPILE_FLAGS.get(f, 0)
    return re.compile(pattern, bits)


def compile_pattern(rule: dict) -> re.Pattern:
    """The rule's compiled pattern, memoised on (pattern, flags). Raises
    `re.error` for a pattern that does not compile."""
    return _compile(rule["pattern"], rule["flags"])


def _fail(msg: str, field: str, index: int | None) -> RuleError:
    return RuleError(f"{field}: {msg}", index=index, field=field)


def _choice_list(raw, field: str, allowed: tuple[str, ...], index: int | None) -> list[str]:
    if not isinstance(raw, list) or not all(isinstance(v, str) for v in raw):
        raise _fail("must be a list of strings", field, index)
    for v in raw:
        if v not in allowed:
            raise _fail(f"{v!r} is not one of {', '.join(allowed)}", field, index)
    # Order is kept, repeats are not.
    return list(dict.fromkeys(raw))


def _depth(raw, field: str, index: int | None) -> int | None:
    if raw is None:
        return None
    # bool is an int in Python, and `true` is not a depth.
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise _fail("must be null or a whole number, 0 or more", field, index)
    return raw


def _check_scalars(rule: dict, raw: dict, index: int | None) -> None:
    if "pattern" not in raw:
        raise _fail("is required", "pattern", index)
    for key in ("name", "pattern", "replacement"):
        if not isinstance(rule[key], str):
            raise _fail("must be a string", key, index)
    for key in ("enabled", "rewrite_stored"):
        if not isinstance(rule[key], bool):
            raise _fail("must be true or false", key, index)
    imported = rule["imported"]
    if imported is not None and not isinstance(imported, dict):
        raise _fail("must be null or an object", "imported", index)
    rule["imported"] = _imported(imported)
    if not isinstance(rule["trim"], list) or not all(isinstance(t, str) for t in rule["trim"]):
        raise _fail("must be a list of strings", "trim", index)


def _imported(raw: dict | None) -> dict | None:
    """`imported` in the one shape the editor renders, `{from, pattern, notes}`,
    or None. It is provenance, not behaviour, so a hand-edited one that lacks
    its source or pattern is dropped rather than refused, and notes keep only
    their strings."""
    if raw is None or not isinstance(raw.get("from"), str) \
            or not isinstance(raw.get("pattern"), str):
        return None
    notes = raw.get("notes")
    return {"from": raw["from"], "pattern": raw["pattern"],
            "notes": [n for n in notes if isinstance(n, str)] if isinstance(notes, list) else []}


def _check_flags(flags, index: int | None) -> str:
    if not isinstance(flags, str):
        raise _fail("must be a string", "flags", index)
    unknown = sorted({f for f in flags if f not in FLAGS})
    if unknown:
        raise _fail(f"unknown flag {''.join(unknown)!r}; use some of {FLAGS}", "flags", index)
    return "".join(f for f in FLAGS if f in flags)


def _check_scope(rule: dict, index: int | None) -> None:
    """Targets, phases and depths: who the rule is for, and where."""
    rule["targets"] = _choice_list(rule["targets"], "targets", TARGETS, index)
    if not rule["targets"]:
        raise _fail("must name at least one of " + ", ".join(TARGETS), "targets", index)
    rule["applies"] = _choice_list(rule["applies"], "applies", PHASES, index)
    if not rule["applies"] and not rule["rewrite_stored"]:
        raise _fail("may be empty only for a rule that rewrites stored text", "applies", index)
    rule["min_depth"] = _depth(rule["min_depth"], "min_depth", index)
    rule["max_depth"] = _depth(rule["max_depth"], "max_depth", index)
    lo, hi = rule["min_depth"], rule["max_depth"]
    if lo is not None and hi is not None and lo > hi:
        raise _fail("min_depth is greater than max_depth", "min_depth", index)


def normalise(raw: dict, *, index: int | None = None) -> dict:
    """A validated copy of `raw` with every key present. Raises `RuleError`.

    A pattern that does not compile is kept only when the rule is disabled --
    the author can save it half-written and come back; enabling it is refused
    with the compile error. Keys this schema does not know are dropped.
    """
    if not isinstance(raw, dict):
        raise RuleError("a rule must be an object", index=index)
    rule: dict = {**_DEFAULTS, **{k: v for k, v in raw.items() if k in _DEFAULTS or k == "pattern"}}
    rid = raw.get("id")
    if rid is None:
        rid = mint_id()
    elif not isinstance(rid, str) or not rid.strip():
        raise _fail("must be a non-empty string", "id", index)

    _check_scalars(rule, raw, index)
    rule["trim"] = list(rule["trim"])
    rule["flags"] = _check_flags(rule["flags"], index)
    _check_scope(rule, index)

    try:
        _compile(rule["pattern"], rule["flags"])
    except (re.error, RecursionError, OverflowError) as exc:
        if rule["enabled"]:
            raise _fail(f"does not compile: {exc}", "pattern", index) from exc
    return {"id": rid, **rule}


# --- Replacement syntax -------------------------------------------------------

_NAMED = re.compile(r"\$<([^>]*)>")


Token = tuple[str, str | int]


def _digits(replacement: str, i: int, ngroups: int) -> tuple[Token, int]:
    """`$` + digit(s) at `i`: two digits when that names a group, else one --
    the way JavaScript reads `$10` against a pattern with a single group."""
    two = replacement[i + 1 : i + 3]
    if len(two) == 2 and two.isdecimal() and two.isascii() and 1 <= int(two) <= ngroups:
        return ("group", int(two)), i + 3
    one = int(replacement[i + 1])
    if 1 <= one <= ngroups:
        return ("group", one), i + 2
    return ("unknown", f"${one}"), i + 2


def _dollar(
    replacement: str, i: int, ngroups: int, names: tuple[str, ...]
) -> tuple[Token | None, int]:
    """The reference starting at the `$` at `i` and where it ends, or (None, i + 1)
    when that `$` is just a dollar sign."""
    nxt = replacement[i + 1] if i + 1 < len(replacement) else ""
    if nxt == "$":
        return ("lit", "$"), i + 2
    if nxt == "&":
        return ("match", ""), i + 2
    if nxt == "<":
        # JavaScript's rule: with no named group in the pattern `$<` is just
        # text; once there is one, a name it lacks is replaced with nothing.
        m = _NAMED.match(replacement, i)
        if m is not None:
            name = m.group(1)
            if name in names:
                return ("name", name), m.end()
            return ("missing" if names else "unknown", m.group(0)), m.end()
    elif nxt.isdecimal() and nxt.isascii():
        return _digits(replacement, i, ngroups)
    return None, i + 1


@functools.lru_cache(maxsize=512)
def _parse(replacement: str, ngroups: int, names: tuple[str, ...]) -> tuple[Token, ...]:
    """The replacement as tokens: ("lit", text), ("group", n), ("name", name),
    ("match", ""), ("unknown", the reference as written) for a numbered group
    the pattern lacks, or ("missing", the reference as written) for a name the
    pattern lacks while it has named groups. An unknown one is emitted
    literally and a missing one as nothing, which is what JavaScript does and
    so what an imported `"costs $5"` expects."""
    out: list[Token] = []
    lit: list[str] = []
    i, n = 0, len(replacement)
    tok: Token | None
    while i < n:
        if replacement.startswith("{{match}}", i):
            tok, end = ("match", ""), i + 9
        elif replacement[i] == "$":
            tok, end = _dollar(replacement, i, ngroups, names)
        else:
            tok, end = None, i + 1
        if tok is None:
            lit.append(replacement[i:end])
        else:
            if lit:
                out.append(("lit", "".join(lit)))
                lit.clear()
            out.append(tok)
        i = end
    if lit:
        out.append(("lit", "".join(lit)))
    return tuple(out)


def _tokens(replacement: str, pattern: re.Pattern) -> tuple[Token, ...]:
    return _parse(replacement, pattern.groups, tuple(pattern.groupindex))


def expand(replacement: str, match: re.Match, trim: list[str]) -> str:
    """`replacement` with its references filled from `match`.

    `$1`..`$99`, `$<name>`, `$&` and `{{match}}` (the whole match), `$$` (a
    literal `$`). A group that did not take part in the match is empty; a
    numbered one the pattern does not have is left as written, and so is
    `$<name>` while the pattern has no named groups -- once it has some, a name
    it lacks is empty. `trim` strings are removed from
    the matched text that `$&` / `{{match}}` substitute -- and only from that.
    """
    out: list[str] = []
    whole: str | None = None
    for kind, arg in _tokens(replacement, match.re):
        if kind in ("lit", "unknown"):
            out.append(str(arg))
        elif kind == "missing":
            continue
        elif kind == "match":
            if whole is None:
                whole = match.group(0)
                for t in trim:
                    if t:
                        whole = whole.replace(t, "")
            out.append(whole)
        else:
            out.append(match.group(arg) or "")
    return "".join(out)


def warnings(rule: dict) -> list[str]:
    """Things worth telling the author that do not stop a save: a replacement
    naming a group the pattern does not have (left literal at run time, or
    replaced with nothing -- see `expand`)."""
    try:
        pattern = compile_pattern(rule)
    except (re.error, RecursionError, OverflowError):
        return []
    seen: dict[str, str] = {}
    for kind, arg in _tokens(rule["replacement"], pattern):
        if kind in ("unknown", "missing") and arg not in seen:
            seen[str(arg)] = ("it is left as written" if kind == "unknown"
                              else "it is replaced with nothing")
    return [
        f"The replacement refers to {ref}, which the pattern has no group for; {fate}."
        for ref, fate in seen.items()
    ]
