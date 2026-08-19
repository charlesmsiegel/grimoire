"""Guard: the repo's onboarding documents stay true to the code (#211, #212).

`README.md`, `CONTRIBUTING.md`, `AGENTS.md` and `docs/store-guarantees.md` all
describe things that live in code — guard markers, `make` targets, lock
constants, screenshot files. Documentation of that kind decays silently: the
code moves, nothing fails, and the next reader follows a page that is quietly
wrong. That is the exact failure `store/locks.py` hit when its lock domain was
a docstring list, and #211 filed the same worry about `AGENTS.md` forking
`CLAUDE.md`.

So the claims that *can* be checked mechanically are checked here. Reach,
stated plainly rather than implied, in the spirit of the other guards:

- **Link targets**, not link prose. A relative link that points at a missing
  file fails; whether the target says what the sentence claims is not
  checkable and is not claimed.
- **Enumerations**, not explanations. If a `# <family>-ok:` marker, a `make
  check-*` target or a `test_*_guard.py` exists, `CONTRIBUTING.md` must name
  it. Whether it describes it *correctly* is a human's job.
- **Verbatim duplication** between `AGENTS.md` and `CLAUDE.md`, which is the
  drift #211 named. Short shared lines (headings, link lines) are ignored; a
  long sentence living in both files is a second copy waiting to go stale.
- **Orphan assets.** A screenshot no document references is one nobody will
  notice going stale — and a stray capture in a repo with this one's privacy
  history is worth failing over.

What it cannot see: a stale sentence that names nothing, a table row whose
description drifted from what the guard actually does, and a screenshot whose
*contents* went stale. Those need a reader.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "backend" / "src" / "grimoire"
TESTS = ROOT / "backend" / "tests"

README = ROOT / "README.md"
CONTRIBUTING = ROOT / "CONTRIBUTING.md"
AGENTS = ROOT / "AGENTS.md"
CLAUDE = ROOT / "CLAUDE.md"
GUARANTEES = ROOT / "docs" / "store-guarantees.md"
SHOTS = ROOT / "docs" / "screenshots"

#: The documents this guard holds to the code. Every one is an entry point a
#: newcomer (or an agent) is pointed at, so a broken claim in one is read.
DOCS = (README, CONTRIBUTING, AGENTS, GUARANTEES, SHOTS / "README.md")

# Markdown inline links and images: [text](target) / ![alt](target).
_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
# `# <family>-ok: <reason>` — the exemption marker every AST guard shares.
_MARKER = re.compile(r"#\s*([a-z][a-z-]*-ok):")
# A make target at the start of a line: `check-web:`
_TARGET = re.compile(r"^(check-[a-z0-9-]+):", re.MULTILINE)


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _slug(heading: str) -> str:
    """GitHub's anchor slug for a heading: lowercased, punctuation dropped,
    spaces to dashes. Close enough for the anchors this repo writes."""
    text = heading.strip().lstrip("#").strip()
    text = re.sub(r"[`*_]", "", text).lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s+", "-", text).strip("-")


def _anchors(text: str) -> set[str]:
    return {_slug(line) for line in text.splitlines() if line.startswith("#")}


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_docs_exist(doc: pathlib.Path):
    assert doc.exists(), f"{doc.relative_to(ROOT)} is missing"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_resolve(doc: pathlib.Path):
    """Every relative link and image target exists on disk.

    Skips absolute URLs and pure anchors (covered by the next test). A link
    with a trailing `#anchor` is checked as a path only -- cross-document
    anchor checking would need every target's headings, and the payoff is the
    missing *file*, which is what actually breaks.
    """
    text = _read(doc)
    missing = []
    for target in _LINK.findall(text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path = (doc.parent / target.split("#", 1)[0]).resolve()
        if not path.exists():
            missing.append(target)
    assert not missing, f"{doc.relative_to(ROOT)} links to missing paths: {missing}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_same_document_anchors_resolve(doc: pathlib.Path):
    """A `#anchor` link with no path names a heading in the same document.

    These are the in-page tables of contents, which is exactly the kind of link
    that rots when a heading is reworded.
    """
    text = _read(doc)
    have = _anchors(text)
    broken = [t for t in _LINK.findall(text) if t.startswith("#") and t[1:] not in have]
    assert not broken, f"{doc.relative_to(ROOT)} has anchors matching no heading: {broken}"


def test_contributing_names_every_guard_marker():
    """Every `# <family>-ok:` marker family in use is documented.

    A new guard arrives with a new marker family, and a contributor's first
    encounter with it is a failing test run telling them to add a comment whose
    rules are written down nowhere. `test_usage_guard.py`'s `usage-ok:` was in
    exactly that state before this.
    """
    families = set()
    for path in list(SRC.rglob("*.py")) + list(TESTS.rglob("*.py")):
        families |= set(_MARKER.findall(_read(path)))
    families.discard("ok")  # not a family; guards against a stray `# -ok:`
    text = _read(CONTRIBUTING)
    undocumented = sorted(f for f in families if f not in text)
    assert not undocumented, (
        "CONTRIBUTING.md does not mention these guard-marker families, which are "
        f"in use in the tree: {undocumented}"
    )


def test_contributing_names_every_guard_test():
    """Every `test_*_guard.py` is named in CONTRIBUTING's guard table."""
    guards = sorted(p.name for p in TESTS.glob("test_*guard*.py")
                    if p.name != pathlib.Path(__file__).name)
    text = _read(CONTRIBUTING)
    missing = [g for g in guards if g not in text]
    assert not missing, f"CONTRIBUTING.md does not name these guards: {missing}"


def test_contributing_names_every_check_target():
    """Every `make check-*` target is documented.

    The gate is the one command a contributor is told to run, so a target that
    exists but is not listed is a job that fails in CI having never been
    mentioned.
    """
    targets = set(_TARGET.findall(_read(ROOT / "Makefile")))
    text = _read(CONTRIBUTING)
    missing = sorted(t for t in targets if t not in text)
    assert not missing, f"CONTRIBUTING.md does not mention these make targets: {missing}"


def test_agents_points_at_claude_md_rather_than_forking_it():
    """AGENTS.md routes to CLAUDE.md; it does not restate it.

    #211's condition for adding this file: the two must not become two copies
    of the conventions, because the drift is invisible until an agent follows
    the stale one. A long line present verbatim in both is that second copy
    starting.
    """
    agents, claude = _read(AGENTS), _read(CLAUDE)
    assert "CLAUDE.md" in agents, "AGENTS.md must point at CLAUDE.md"

    claude_lines = {ln.strip() for ln in claude.splitlines()}
    shared = sorted(
        ln.strip() for ln in agents.splitlines()
        if len(ln.strip()) >= 60 and ln.strip() in claude_lines
    )
    assert not shared, (
        "AGENTS.md repeats these lines verbatim from CLAUDE.md — route to it "
        f"instead of copying it: {shared}"
    )


def test_store_guarantees_quotes_the_real_lock_timeout():
    """The doc states the contention timeout as a number; that number is a
    constant in `store/locks.py`, and the two must not disagree."""
    from grimoire.store import locks

    text = _read(GUARANTEES)
    seconds = int(locks.LOCK_TIMEOUT)
    assert f"{seconds} s" in text or f"{seconds} seconds" in text, (
        f"docs/store-guarantees.md does not state LOCK_TIMEOUT ({seconds}s) as "
        "the contention timeout"
    )


def test_store_guarantees_names_every_busy_exception():
    """Every `StoreBusy` subclass a caller can see is named in the doc.

    A new lock domain arrives as a new `StoreBusy` subclass, and a caller
    handling 409s needs to know it exists.
    """
    from grimoire.store import locks

    text = _read(GUARANTEES)
    subclasses = sorted(
        name for name, obj in vars(locks).items()
        if isinstance(obj, type) and issubclass(obj, locks.StoreBusy)
    )
    missing = [name for name in subclasses if name not in text]
    assert not missing, (
        f"docs/store-guarantees.md does not name these busy exceptions: {missing}"
    )


def test_store_guarantees_names_the_lock_domain_constants():
    """The three declarations that *are* the campaign lock domain.

    They are the answer to "does my module have to take the lock?", and the doc
    is where a reader is sent to find that out.
    """
    text = _read(GUARANTEES)
    missing = [n for n in ("DOMAIN_MODULES", "OUTSIDE_DOMAIN", "UNREVIEWED")
               if n not in text]
    assert not missing, f"docs/store-guarantees.md does not name: {missing}"


def test_store_guarantees_names_every_atomic_primitive():
    """The public writers in `store/atomic.py`. A caller choosing between them
    can only do that if the doc lists all of them."""
    from grimoire.store import atomic

    text = _read(GUARANTEES)
    public = sorted(n for n in vars(atomic) if not n.startswith("_")
                    and callable(getattr(atomic, n))
                    and getattr(getattr(atomic, n), "__module__", "") ==
                    "grimoire.store.atomic")
    missing = [n for n in public if n not in text]
    assert not missing, (
        f"docs/store-guarantees.md does not name these atomic writers: {missing}"
    )


def test_every_screenshot_is_referenced():
    """No orphan captures.

    A screenshot no document points at is one nobody notices going stale — and
    in a repo whose privacy rule turns on what images show, a stray capture
    nobody is looking at is worth failing over.
    """
    docs_text = "\n".join(_read(p) for p in DOCS if p.exists())
    orphans = sorted(p.name for p in SHOTS.glob("*.png") if p.name not in docs_text)
    assert not orphans, f"screenshots referenced by no document: {orphans}"


def test_screenshots_carry_their_own_privacy_note():
    """The directory's README states the isolated-store rule.

    #211 flagged screenshots as the item with a privacy trap: a capture of a
    working install shows a real library by default. The rule has to live where
    the person about to re-capture will read it.
    """
    text = _read(SHOTS / "README.md")
    for needle in ("GRIMOIRE_HOME", "8199"):
        assert needle in text, (
            f"docs/screenshots/README.md does not mention {needle!r} — the "
            "isolated-store harness is the whole reason these captures are safe"
        )
