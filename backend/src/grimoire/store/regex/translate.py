"""Translating a SillyTavern (JavaScript) regex into Python `re` syntax.

`translate` walks the JavaScript pattern with a small recursive-descent lexer
(escapes, classes, groups, quantifiers), emits Python, and gives a verdict:

- `exact` -- rewritten with its meaning kept (named groups, `\\/`, `[^]`,
  `\\u{...}`, JavaScript's `.`, `$` and `\\s`; `\\w \\d \\b` kept, the `a` flag
  giving them JavaScript's ASCII meaning);
- `approximate` -- importable, with a note saying where it can differ;
- `untranslatable` -- not imported, with the reason.

The lexer never guesses: a construct it has no rule for is untranslatable, not
passed through. The translation is then compiled, and anything Python still
refuses is untranslatable with Python's own message.
"""

from __future__ import annotations

import re
import warnings

EXACT = "exact"
APPROXIMATE = "approximate"
UNTRANSLATABLE = "untranslatable"

# JavaScript's WhiteSpace and LineTerminator, which is what its `\s` matches.
# Spelled out because Python's `\s` (under `a`) is ASCII only, and without `a`
# it is Unicode's set, which is not JavaScript's either.
_JS_SPACE = r"\t\n\v\f\r \xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
# JavaScript's `.` without `s` stops at every line terminator, not only `\n`.
_JS_DOT = r"[^\n\r\u2028\u2029]"

_SYNTAX = set("^$\\.*+?()[]{}|")
_CONTROL = {"n": "\n", "r": "\r", "t": "\t", "v": "\v", "f": "\f"}
_QUANT = re.compile(r"\{(\d+)(?:(,)(\d*))?\}")
_HEX2 = re.compile(r"[0-9A-Fa-f]{2}")
_HEX4 = re.compile(r"[0-9A-Fa-f]{4}")
_CODE_POINT = re.compile(r"\{([0-9A-Fa-f]+)\}")
_GROUP_NAME = re.compile(r"<([^>]*)>")

_JS_FLAGS = "dgimsuvy"
_REFUSED_FLAGS = {
    "y": "the y (sticky) flag has no Python equivalent",
    "d": "the d (match indices) flag is not supported",
    "v": "the v (unicode sets) flag is not supported",
}
_COMPILE_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "a": re.ASCII}

_NOTE_U = "The u flag was dropped: Python patterns are already Unicode."
_NOTE_LINES = ("^ and $ under m: JavaScript also starts a line after \\r, U+2028 and "
               "U+2029; Python only after \\n.")
_NOTE_FOLD = ("i on a pattern with non-ASCII letters: Python folds ASCII case only "
              "here, JavaScript folds more.")
_NOTE_IU = ("i with u: JavaScript also matches k and s against U+212A and U+017F "
            "(and \\w, \\b with them); Python does not.")
_NOTE_BACKREF = ("A backreference to a group that took no part in the match matches "
                 "nothing in JavaScript and fails the match in Python.")
_NOTE_EMPTY = ("This pattern can match the empty string. Under g without u, JavaScript "
               "also tries the position between the two halves of an emoji (or any "
               "character beyond U+FFFF); Python does not.")


class _UntranslatableError(Exception):
    """A construct with no translation; the message is the reason."""


def _cased_beyond_ascii(cp: int) -> bool:
    c = chr(cp)
    return cp > 127 and (c.lower() != c or c.upper() != c)


class _Lexer:
    def __init__(self, src: str, flags: str):
        self.src = src
        self.i = 0
        self.u = "u" in flags
        self.dotall = "s" in flags
        self.multiline = "m" in flags
        self.groups = 0              # capturing groups opened so far
        self.closed: set[int | str] = set()   # numbers and names of closed groups
        self.names: set[str] = set()
        self.refs: list[tuple[int | str, bool]] = []   # (group, closed when referenced)
        self.notes: list[str] = []   # approximate notes, in order
        self.alternation = False
        self.maybe_unset = False     # a capture that can be skipped
        self.folds_wide = False      # a non-ASCII cased letter (or a range past ASCII)
        self.k_or_s = False          # something JavaScript's `iu` widens

    # --- helpers -------------------------------------------------------------

    def peek(self, n: int = 0) -> str:
        j = self.i + n
        return self.src[j] if j < len(self.src) else ""

    def approx(self, note: str) -> None:
        if note not in self.notes:
            self.notes.append(note)

    def char(self, cp: int) -> None:
        """Record a literal code point for the case-folding checks."""
        if _cased_beyond_ascii(cp):
            self.folds_wide = True
        if chr(cp) in "kKsS":
            self.k_or_s = True

    # --- grammar -------------------------------------------------------------

    def pattern(self) -> tuple[str, bool]:
        text, nullable = self.disjunction()
        if self.i < len(self.src):
            raise _UntranslatableError("an unmatched )")
        return text, nullable

    def disjunction(self) -> tuple[str, bool]:
        parts: list[str] = []
        nullable = False
        while True:
            text, alt_nullable = self.alternative()
            parts.append(text)
            nullable = nullable or alt_nullable
            if self.peek() != "|":
                return "|".join(parts), nullable
            self.alternation = True
            self.i += 1

    def alternative(self) -> tuple[str, bool]:
        out: list[str] = []
        nullable = True
        while self.i < len(self.src) and self.peek() not in "|)":
            text, term_nullable = self.term()
            out.append(text)
            nullable = nullable and term_nullable
        return "".join(out), nullable

    def term(self) -> tuple[str, bool]:
        before = self.groups
        text, nullable, quantifiable = self.atom()
        lo = self.quantifier()
        if lo is None:
            return text, nullable
        if not quantifiable:
            raise _UntranslatableError(f"nothing to repeat before {lo[1]!r}")
        if lo[0] == 0 and self.groups > before:
            self.maybe_unset = True
        return text + lo[1], nullable or lo[0] == 0

    def quantifier(self) -> tuple[int, str] | None:
        """(minimum, text) for a quantifier at the cursor, or None."""
        c = self.peek()
        if c in ("*", "+", "?"):
            self.i += 1
            lo, text = (1 if c == "+" else 0), c
        elif c == "{" and (m := _QUANT.match(self.src, self.i)):
            lo = int(m.group(1))
            if m.group(3) and int(m.group(3)) < lo:
                raise _UntranslatableError(f"the quantifier {m.group(0)} is out of order")
            self.i = m.end()
            text = m.group(0)
        else:
            return None
        if self.peek() == "?":
            self.i += 1
            text += "?"
        return lo, text

    def atom(self) -> tuple[str, bool, bool]:
        """(python, nullable, quantifiable) for the atom at the cursor."""
        c = self.peek()
        self.i += 1
        if c in "^$":
            return self.anchor(c), True, False
        if c == ".":
            return ("." if self.dotall else _JS_DOT), False, True
        if c == "(":
            return self.group()
        if c == "[":
            return self.char_class(), False, True
        if c == "\\":
            return self.escape()
        return self.literal(c)

    def anchor(self, c: str) -> str:
        if self.multiline:
            self.approx(_NOTE_LINES)
            return c
        # Python's `$` also matches before a final newline.
        return "^" if c == "^" else r"\Z"

    def literal(self, c: str) -> tuple[str, bool, bool]:
        if c in "*+?" or (c == "{" and _QUANT.match(self.src, self.i - 1)):
            raise _UntranslatableError(f"nothing to repeat before {c!r}")
        if c in "{}]":
            if self.u:
                raise _UntranslatableError(f"a lone {c!r} is a syntax error under u")
            return "\\" + c, False, True
        cp = ord(c)
        if cp > 0xFFFF and not self.u and self.quantifier_next():
            raise _UntranslatableError("a quantifier on a character beyond U+FFFF without u "
                           "(JavaScript repeats only its second half)")
        self.char(cp)
        return c, False, True

    def quantifier_next(self) -> bool:
        return (self.peek() != "" and self.peek() in "*+?") or bool(
            _QUANT.match(self.src, self.i))

    def group(self) -> tuple[str, bool, bool]:
        """`(...)` in any of its forms; the `(` is consumed."""
        src = self.src
        for head in ("?:", "?=", "?!", "?<=", "?<!"):
            if src.startswith(head, self.i):
                self.i += len(head)
                before = self.groups
                text, nullable = self.close_group("(" + head)
                if head == "?:":
                    return text, nullable, True
                # A capture inside a negative lookaround never keeps a value.
                if "!" in head and self.groups > before:
                    self.maybe_unset = True
                # Zero-width, and not repeatable here: JavaScript allows a
                # quantified lookahead only outside u, and it means nothing.
                return text, True, False
        name = None
        if src.startswith("?<", self.i):
            m = _GROUP_NAME.match(src, self.i + 1)
            if m is None:
                raise _UntranslatableError("an unterminated group name")
            name = m.group(1)
            self.i = m.end()
        elif self.peek() == "?":
            raise _UntranslatableError(f"the group syntax (?{self.peek(1)} is not supported")
        self.groups += 1
        number = self.groups
        if name is not None:
            self.names.add(name)
        text, nullable = self.close_group("(" if name is None else f"(?P<{name}>")
        self.closed.add(number)
        if name is not None:
            self.closed.add(name)
        return text, nullable, True

    def close_group(self, head: str) -> tuple[str, bool]:
        inner, nullable = self.disjunction()
        if self.peek() != ")":
            raise _UntranslatableError("a group with no closing )")
        self.i += 1
        return head + inner + ")", nullable

    # --- escapes -------------------------------------------------------------

    def escape(self) -> tuple[str, bool, bool]:
        """`\\` at top level (outside a class); the backslash is consumed."""
        c = self.peek()
        if c in ("b", "B"):
            self.i += 1
            self.k_or_s = True
            return "\\" + c, True, False
        if c and c in "123456789":
            m = re.compile(r"\d+").match(self.src, self.i)
            assert m is not None
            self.i = m.end()
            number = int(m.group(0))
            self.refs.append((number, number in self.closed))
            return "\\" + m.group(0), True, True
        if c == "k":
            m = _GROUP_NAME.match(self.src, self.i + 1)
            if m is None:
                raise _UntranslatableError("\\k without a group name")
            self.i = m.end()
            name = m.group(1)
            self.refs.append((name, name in self.closed))
            return f"(?P={name})", True, True
        text, _cp = self.shared_escape(in_class=False)
        return text, False, True

    def shared_escape(self, *, in_class: bool) -> tuple[str, int | None]:
        """An escape whose meaning is the same in and out of a class, as
        (python, code point or None for a class escape)."""
        c = self.peek()
        if not c:
            raise _UntranslatableError("a trailing backslash")
        self.i += 1
        if c in "dDwWsS":
            return self.class_escape(c, in_class), None
        if c in _CONTROL:
            return "\\" + c, ord(_CONTROL[c])
        if c == "0":
            if self.peek().isdigit():
                raise _UntranslatableError("a legacy octal escape")
            return r"\x00", 0
        if c in "cx":
            return self.code_escape(c)
        if c == "u":
            return self.unicode_escape(in_class)
        return self.identity_escape(c, in_class)

    def class_escape(self, c: str, in_class: bool) -> str:
        if c in "wW":
            self.k_or_s = True
        if c == "s":
            return _JS_SPACE if in_class else f"[{_JS_SPACE}]"
        if c == "S":
            # Inside a class `char_class` deals with it before it gets here.
            return f"[^{_JS_SPACE}]"
        return "\\" + c

    def code_escape(self, c: str) -> tuple[str, int]:
        """`\\cX` or `\\xHH`."""
        if c == "c":
            nxt = self.peek()
            if not (nxt.isascii() and nxt.isalpha()):
                raise _UntranslatableError("\\c without a control letter")
            self.i += 1
            cp = ord(nxt) % 32
            return f"\\x{cp:02x}", cp
        m = _HEX2.match(self.src, self.i)
        if m is None:
            raise _UntranslatableError("\\x without two hex digits")
        self.i = m.end()
        cp = int(m.group(0), 16)
        self.char(cp)
        return "\\x" + m.group(0), cp

    def identity_escape(self, c: str, in_class: bool) -> tuple[str, int]:
        if c in ("p", "P"):
            raise _UntranslatableError(
                f"the property escape \\{c}{{…}} has no Python equivalent")
        if c == "/":
            return "/", ord("/")
        if c in _SYNTAX or (c == "-" and (in_class or not self.u)):
            return "\\" + c, ord(c)
        if self.u:
            raise _UntranslatableError(f"the escape \\{c} is a syntax error under u")
        if c.isascii() and c.isalnum():
            raise _UntranslatableError(f"the escape \\{c} is not recognised")
        # An identity escape: JavaScript (without u) reads `\"` as `"`.
        self.char(ord(c))
        return re.escape(c), ord(c)

    def unicode_escape(self, in_class: bool) -> tuple[str, int]:
        if self.peek() == "{":
            m = _CODE_POINT.match(self.src, self.i)
            if not self.u or m is None:
                raise _UntranslatableError(
                    "\\u{…} needs the u flag" if m else "a malformed \\u{…}")
            cp = int(m.group(1), 16)
            if cp > 0x10FFFF:
                raise _UntranslatableError(f"\\u{{{m.group(1)}}} is beyond Unicode")
            self.i = m.end()
            self.char(cp)
            return f"\\U{cp:08X}", cp
        m = _HEX4.match(self.src, self.i)
        if m is None:
            raise _UntranslatableError("\\u without four hex digits")
        self.i = m.end()
        cp = int(m.group(0), 16)
        if 0xD800 <= cp <= 0xDBFF:
            low = re.compile(r"\\u([Dd][C-Fc-f][0-9A-Fa-f]{2})").match(self.src, self.i)
            # A pair is one character to Python; outside a class JavaScript
            # matches its two halves in a row, which is the same thing, and
            # inside one only u makes it a single member.
            if low is not None and (self.u or not in_class):
                self.i = low.end()
                if not self.u and self.quantifier_next():
                    raise _UntranslatableError("a quantifier on a surrogate pair without u "
                                   "(JavaScript repeats only its second half)")
                cp = 0x10000 + ((cp - 0xD800) << 10) + (int(low.group(1), 16) - 0xDC00)
                return f"\\U{cp:08X}", cp
        if 0xD800 <= cp <= 0xDFFF:
            raise _UntranslatableError(f"the lone surrogate \\u{m.group(0)}")
        self.char(cp)
        return "\\u" + m.group(0), cp

    # --- classes -------------------------------------------------------------

    def char_class(self) -> str:
        """`[...]`; the `[` is consumed."""
        if self.peek() == "]":
            raise _UntranslatableError(
                "the empty class [] (JavaScript never matches it; Python refuses it)")
        if self.src.startswith("^]", self.i):
            self.i += 2
            return r"[\s\S]"
        negate = self.peek() == "^"
        if negate:
            self.i += 1
        items: list[str] = []
        space = non_space = False
        while self.peek() != "]":
            if not self.peek():
                raise _UntranslatableError("a class with no closing ]")
            if self.src.startswith("\\S", self.i):
                self.i += 2
                non_space = True
                continue
            space = space or self.src.startswith("\\s", self.i)
            items.append(self.class_item())
        self.i += 1
        if non_space:
            # Python has no way to say "not JavaScript's whitespace" inside a
            # class -- unless `\s` is there too, and the class is everything.
            if not space:
                raise _UntranslatableError("\\S inside a class has no Python equivalent")
            return "[^\\s\\S]" if negate else r"[\s\S]"
        return "[" + ("^" if negate else "") + "".join(items) + "]"

    def class_item(self) -> str:
        """One member of a class: a character, a class escape, or a range."""
        text, lo = self.class_atom()
        if self.peek() != "-" or self.peek(1) in ("]", ""):
            return text
        self.i += 1
        hi_text, hi = self.class_atom()
        if lo is None or hi is None:
            raise _UntranslatableError("a range with a class escape at one end")
        if lo > hi:
            raise _UntranslatableError("a class range out of order")
        if hi > 127:
            self.folds_wide = True
        if any(lo <= ord(c) <= hi for c in "KSks"):
            self.k_or_s = True
        return f"{text}-{hi_text}"

    def class_atom(self) -> tuple[str, int | None]:
        c = self.peek()
        if c == "\\":
            self.i += 1
            nxt = self.peek()
            if nxt == "b":
                self.i += 1
                return r"\x08", 8
            if nxt == "B" or nxt == "k" or (nxt.isdigit() and nxt != "0"):
                raise _UntranslatableError(f"the escape \\{nxt} inside a class is not supported")
            return self.shared_escape(in_class=True)
        self.i += 1
        cp = ord(c)
        if cp > 0xFFFF and not self.u:
            raise _UntranslatableError("a character beyond U+FFFF inside a class without u "
                           "(JavaScript sees its two halves as two members)")
        self.char(cp)
        # `[` is escaped because Python warns on what looks like a nested set,
        # and `& ~ | -` because doubled they are reserved set operators.
        if c in "[]\\^&~|-":
            return "\\" + c, cp
        return c, cp


def _flags(js_flags: str) -> str:
    """The Python flag string, or `_UntranslatableError`."""
    for f in js_flags:
        if f not in _JS_FLAGS:
            raise _UntranslatableError(f"the flag {f!r} is not a JavaScript flag")
        if js_flags.count(f) > 1:
            raise _UntranslatableError(f"the flag {f!r} is given twice")
        if f in _REFUSED_FLAGS:
            raise _UntranslatableError(_REFUSED_FLAGS[f])
    return "".join(f for f in "gims" if f in js_flags) + "a"


def _compile(pattern: str, flags: str) -> None:
    bits = 0
    for f in flags:
        bits |= _COMPILE_FLAGS.get(f, 0)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        re.compile(pattern, bits)


def _check_refs(lex: _Lexer) -> None:
    for ref, was_closed in lex.refs:
        if isinstance(ref, int) and ref > lex.groups:
            raise _UntranslatableError(
                f"\\{ref} is a legacy octal escape (there is no group {ref})")
        if isinstance(ref, str) and ref not in lex.names:
            raise _UntranslatableError(f"\\k<{ref}> names no group")
        if not was_closed:
            raise _UntranslatableError(f"a backreference to {ref!r} before that group closes "
                           "(JavaScript matches it as empty; Python refuses it)")
    if lex.refs and (lex.alternation or lex.maybe_unset):
        lex.approx(_NOTE_BACKREF)


def translate(body: str, flags: str) -> dict:
    """`{"verdict", "pattern", "flags", "notes"}` for a JavaScript pattern body
    and flag string. `pattern` is None when the verdict is untranslatable."""
    notes: list[str] = []
    try:
        py_flags = _flags(flags)
        if "u" in flags:
            notes.append(_NOTE_U)
        lex = _Lexer(body, flags)
        pattern, nullable = lex.pattern()
        _check_refs(lex)
    except _UntranslatableError as exc:
        return {"verdict": UNTRANSLATABLE, "pattern": None, "flags": "",
                "notes": [*notes, f"Won't translate: {exc}."]}
    except RecursionError:
        return {"verdict": UNTRANSLATABLE, "pattern": None, "flags": "",
                "notes": [*notes, "Won't translate: the pattern nests too deeply."]}
    if "i" in flags:
        if lex.folds_wide:
            lex.approx(_NOTE_FOLD)
        if "u" in flags and lex.k_or_s:
            lex.approx(_NOTE_IU)
    if nullable and "g" in flags and "u" not in flags:
        lex.approx(_NOTE_EMPTY)
    try:
        _compile(pattern, py_flags)
    except (re.error, Warning, RecursionError, OverflowError) as exc:
        return {"verdict": UNTRANSLATABLE, "pattern": None, "flags": py_flags,
                "notes": [*notes, f"Won't translate: Python refuses the translation ({exc})."]}
    verdict = APPROXIMATE if lex.notes else EXACT
    return {"verdict": verdict, "pattern": pattern, "flags": py_flags,
            "notes": notes + lex.notes}
