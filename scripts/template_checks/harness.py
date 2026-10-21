"""The rendering environment every check compares against, and the tally.

This environment is the harness's own, independent of the app's
(`grimoire.prompts`): a check renders a template here and compares it with what
a builder rendered there.
"""

from __future__ import annotations

import re

from jinja2 import Environment, FileSystemLoader, StrictUndefined

# The one spelling of a `generate` schema (01f-C3), the same function the app's
# environment registers: a template and `inference.generate`'s in-prompt check
# must agree on it, so this harness does not restate it.
from grimoire import schemas

from . import REPO


class _Recording(FileSystemLoader):
    """templates/, remembering every name this harness's environment loaded.

    The check count says how many comparisons ran; `reached` says what they
    covered, and the reach check at the end holds it to the whole directory.
    It records at the loader rather than around `render`, because an include,
    extends or import is loaded by the environment, not by a call made here --
    and a fragment that is only ever included is still reached by the render
    that includes it. The environment caches what it compiled, so this sees
    each name once, which is all a set needs. Only this environment's loads
    count: a builder renders through the app's own (`grimoire.prompts`), and a
    template only that side renders is compared with nothing independent."""

    def __init__(self, searchpath: str) -> None:
        super().__init__(searchpath)
        self.reached: set[str] = set()

    def get_source(self, environment, template):
        self.reached.add(template)
        return super().get_source(environment, template)


loader = _Recording(str(REPO / "templates"))
env = Environment(loader=loader, undefined=StrictUndefined)
env.filters["schema_json"] = schemas.render


class Report:
    """The running tally: how many comparisons were made, and which failed.

    A class rather than two module-level names mutated through `global`, which
    is what this was. The counter and the failure list only ever move together
    -- every comparison bumps one and may append to the other -- so a reader
    asking "what does a failed check do to the tally" had to find two `global`
    statements in two functions to answer it. Owning both here also means the
    epilogue reports out of one object rather than reaching for two module
    variables it hopes nothing else wrote.
    """

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.checks = 0

    def compare(self, label: str, expected: str, actual: str) -> None:
        """One byte-for-byte comparison, counted whether or not it passes."""
        self.checks += 1
        if expected == actual:
            return
        i = next((n for n, (a, b) in enumerate(zip(expected, actual, strict=False)) if a != b),
                 min(len(expected), len(actual)))
        self.failures.append(
            f"{label}: mismatch at char {i}\n"
            f"  expected …{expected[max(0, i - 60):i + 60]!r}…\n"
            f"  actual   …{actual[max(0, i - 60):i + 60]!r}…")

    def require(self, label: str, ok: bool, message: str) -> None:
        """A comparison that is not byte-for-byte -- a fragment that must be
        present -- counted whether or not it passes."""
        self.checks += 1
        if not ok:
            self.failures.append(f"{label}: {message}")

    def note(self, label: str, message: str) -> None:
        """A failure that is not a text mismatch -- a role shape that differs,
        where comparing the contents would be comparing different things."""
        self.checks += 1
        self.failures.append(f"{label}: {message}")

    def verdict(self) -> str:
        return (f"all {self.checks} checks passed — builders and templates/ agree byte-for-byte"
                if not self.failures else
                f"{len(self.failures)}/{self.checks} checks FAILED\n\n"
                + "\n\n".join(self.failures))


REPORT = Report()


def render(_template: str, **variables) -> str:
    return env.get_template(_template).render(**variables)


def check(label: str, expected: str, actual: str) -> None:
    REPORT.compare(label, expected, actual)


def check_messages(label: str, expected: list[dict], actual: list[dict]) -> None:
    if [m["role"] for m in expected] != [m["role"] for m in actual]:
        REPORT.note(label, f"role shape {[m['role'] for m in expected]} != "
                           f"{[m['role'] for m in actual]}")
        return
    for n, (e, a) in enumerate(zip(expected, actual, strict=False)):
        check(f"{label}[{n}] ({e['role']})", e["content"], a["content"])


def _source(name: str) -> str:
    """A template's source with its `{# #}` comments removed and every
    whitespace run collapsed to one space: what a fragment is looked for in. A
    comment is documentation, so a fragment quoted there carries nothing."""
    text = (REPO / "templates" / name).read_text(encoding="utf-8")
    return " ".join(re.sub(r"\{#.*?#\}", " ", text, flags=re.DOTALL).split())


def require_carried(prefix: str, carried) -> None:
    """Each (fragment, template) pair: the fragment, word for word, in the
    template's source. The legacy one-call prompts' criteria are held to the
    decide-era templates they were carried into this way (I8)."""
    for fragment, target in carried:
        REPORT.require(f"{prefix} carried ({target}: {fragment[:40]}…)",
                       fragment in _source(target), f"not in {target}")
