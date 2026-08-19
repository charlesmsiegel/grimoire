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
  checkable and is not claimed. Both markdown links and the HTML `<img>` /
  `<a>` a README reaches for when markdown cannot size an image.
- **Enumerations**, not explanations. If a `# <family>-ok:` marker, a `make
  check-*` target or a `test_*_guard.py` exists, `CONTRIBUTING.md` must name
  it. Whether it describes it *correctly* is a human's job.
- **Verbatim duplication between any two of these documents**, which is the
  drift #211 named. Every ordered pair, not each page against `CLAUDE.md`:
  restricting it to `CLAUDE.md` was an arbitrary line, and it let the two
  worst duplications in this PR's own drafts through — a capture procedure
  spelled out in both `CONTRIBUTING.md` and `docs/screenshots/README.md`, and
  a sentence about sync clients copied between `README.md` and
  `docs/store-guarantees.md`.

  Measured as a run of identical words after normalization, because markdown
  rewrapping defeats any line-based comparison: a paragraph pasted and then
  reflowed shares no whole line with its source, so a whole-line check called
  the first draft of `CONTRIBUTING.md` clean when a fifth of it was copied.
- **Orphan assets and orphan documents.** An image no document references, or
  a document nothing links to, is one nobody notices going stale — and a stray
  capture in a repo with this one's privacy history is worth failing over.

What it cannot see: a stale sentence that names nothing, a table row whose
description drifted from what the guard actually does, a count written out in
prose (which is why the pages here avoid writing one), and a screenshot whose
*contents* went stale. Those need a reader.
"""

from __future__ import annotations

import itertools
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

#: `DOCS` plus `CLAUDE.md`, for the duplication check. `CLAUDE.md` is not in
#: `DOCS` because this guard does not maintain it -- it is the authority the
#: others route to -- but it is very much a document they can fork.
PROSE = DOCS + (CLAUDE,)

#: Documents that must be reachable from `README.md`, the only page a fresh
#: clone is guaranteed to be shown. An unlinked one is an orphan by any other
#: name -- the same failure `test_no_orphan_images` covers for captures.
LINKED_FROM_README = (CONTRIBUTING, AGENTS, GUARANTEES)

#: Image extensions a capture could plausibly arrive as. Globbing only `*.png`
#: would let a `.webp` sit in the directory unreferenced and unchecked, which
#: is precisely the case the privacy rule cares about.
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".svg"})

#: Longest run of identical words two of these documents may share.
#: Calibrated rather than guessed: when this was written the longest run any
#: pair shared innocently was 13 words (the one-line "what this project is"
#: sentence, and a factual phrase naming `backend/.venv`), while the pasted
#: paragraphs this guard exists to catch ran 29 and up. 16 clears the first
#: with room and stays well under the second. If a legitimate pair ever
#: exceeds it, reword one of them -- raising this number is how the rule dies.
MAX_SHARED_RUN = 16

# Markdown inline links and images: [text](target) / ![alt](target).
_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
# The HTML escape hatch, which markdown has no syntax for: `<img src=... width=...>`.
_HTML_REF = re.compile(r"<(?:img|a)\b[^>]*?(?:src|href)=[\"']([^\"']+)[\"']")
# `# <family>-ok: <reason>` -- the exemption marker every AST guard shares.
_MARKER = re.compile(r"#\s*([a-z][a-z-]*-ok):")
# A make target at the start of a line: `check-web:`
_TARGET = re.compile(r"^(check-[a-z0-9-]+):", re.MULTILINE)
# Words, for the duplication check. Keeps the characters that make an
# identifier one token (`store.paths`, `check-py`, `backend/src`).
_WORD = re.compile(r"[a-z0-9_./-]+")
_FENCE = re.compile(r"```.*?```", re.DOTALL)


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _refs(text: str) -> list[str]:
    """Every link target in the document, markdown and HTML alike."""
    return _LINK.findall(text) + _HTML_REF.findall(text)


def _outside_fences(text: str) -> str:
    """The document with fenced code blocks blanked out.

    A `# comment` on the first column of a bash fence is not a heading, and
    counting it as one hands the anchor check anchors that do not exist --
    making it pass where it should fail. `README.md` has three.
    """
    return _FENCE.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def _spelled_exactly(path: pathlib.Path) -> bool:
    """Whether every component of `path` matches the name on disk, case included.

    `Path.exists()` cannot answer this on a case-insensitive volume, so walk
    down from the repo root checking each component against a real listing.
    A path outside the repo is out of scope and passes.
    """
    try:
        parts = path.relative_to(ROOT).parts
    except ValueError:
        return True
    current = ROOT
    for part in parts:
        try:
            if part not in {child.name for child in current.iterdir()}:
                return False
        except OSError:
            return False
        current = current / part
    return True


def _slug(heading: str) -> str:
    """GitHub's anchor slug for a heading: lowercased, punctuation dropped,
    spaces to dashes. Close enough for the anchors this repo writes."""
    text = heading.strip().lstrip("#").strip()
    text = re.sub(r"[`*_]", "", text).lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s+", "-", text).strip("-")


def _anchors(text: str) -> set[str]:
    return {_slug(ln) for ln in _outside_fences(text).splitlines() if ln.startswith("#")}


def _words(text: str) -> list[str]:
    """Normalized word stream: fenced code dropped, lowercased.

    Code blocks are excluded deliberately. Two documents legitimately quote the
    same command, and flagging that would push a contributor to paraphrase an
    invocation -- the one kind of text that must be copied exactly.
    """
    return _WORD.findall(_FENCE.sub(" ", text).lower())


def _longest_shared_run(text: str, other: str) -> tuple[int, str]:
    """(length, text) of the longest run of words `text` shares with `other`."""
    haystack = " " + " ".join(_words(other)) + " "
    w = _words(text)
    best, worst_offender = 0, ""
    for i in range(len(w)):
        n = best + 1                       # only ever look for a longer one
        while i + n <= len(w) and f" {' '.join(w[i:i + n])} " in haystack:
            best, worst_offender = n, " ".join(w[i:i + n])
            n += 1
    return best, worst_offender


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_docs_exist(doc: pathlib.Path):
    assert doc.exists(), f"{doc.relative_to(ROOT)} is missing"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_resolve(doc: pathlib.Path):
    """Every relative link, image and HTML reference target exists on disk.

    Skips absolute URLs and pure anchors (covered by the next test). A link
    with a trailing `#anchor` is checked as a path only -- cross-document
    anchor checking would need every target's headings, and the payoff is the
    missing *file*, which is what actually breaks.
    """
    text = _read(doc)
    missing, miscased = [], []
    for target in _refs(text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        rel = target.split("#", 1)[0]
        path = (doc.parent / rel).resolve()
        if not path.exists():
            missing.append(target)
        elif not _spelled_exactly(path):
            miscased.append(target)
    assert not missing, f"{doc.relative_to(ROOT)} links to missing paths: {missing}"
    # A link that differs from the real filename only in case resolves on a
    # case-insensitive volume (the macOS default, which `store/proclock.py`
    # already has to reason about) and 404s once GitHub serves it. Catching it
    # only on Linux CI would make this the kind of bug that reaches users from
    # a green local run.
    assert not miscased, (
        f"{doc.relative_to(ROOT)} links to paths whose case does not match the "
        f"files on disk: {miscased}"
    )


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_same_document_anchors_resolve(doc: pathlib.Path):
    """A `#anchor` link with no path names a heading in the same document.

    These are the in-page tables of contents, which is exactly the kind of link
    that rots when a heading is reworded.
    """
    text = _read(doc)
    have = _anchors(text)
    broken = [t for t in _refs(text) if t.startswith("#") and t[1:] not in have]
    assert not broken, f"{doc.relative_to(ROOT)} has anchors matching no heading: {broken}"


@pytest.mark.parametrize("doc", LINKED_FROM_README,
                         ids=lambda p: str(p.relative_to(ROOT)))
def test_no_orphan_documents(doc: pathlib.Path):
    """`README.md` links to every document this guard maintains.

    Without this, deleting one pointer leaves a maintained page nothing points
    at -- the document-shaped version of the orphan capture below, and just as
    invisible.
    """
    rel = str(doc.relative_to(ROOT))
    assert rel in _read(README), f"README.md does not link to {rel}"


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
    """Every `test_*guard*.py` is named in CONTRIBUTING's guard table.

    This file included: a guard absent from the table it maintains is the
    joke writing itself, and the earlier version of this test excused itself.
    """
    guards = sorted(p.name for p in TESTS.glob("test_*guard*.py"))
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


@pytest.mark.parametrize(
    "a,b",
    [(a, b) for a, b in itertools.combinations(PROSE, 2)],
    ids=lambda p: str(p.relative_to(ROOT)),
)
def test_no_document_restates_another(a: pathlib.Path, b: pathlib.Path):
    """No two of these pages carry the same paragraph; one links to the other.

    #211's condition for adding `AGENTS.md`, applied to every pair, because the
    failure is the same wherever it happens: two copies of a rule drift, and
    nothing surfaces it until someone follows the stale one. Pairwise rather
    than everything-against-`CLAUDE.md`, since the duplications this PR
    actually shipped in draft were between two of the *other* files.

    The measure is a run of identical words after normalization rather than a
    shared line, and the difference is not academic. The first
    `CONTRIBUTING.md` written for this PR shared 22% of its 8-word runs with
    `CLAUDE.md` and carried 13 byte-identical long lines; reflowing any one of
    those paragraphs would have hidden it from a line-based check while
    changing nothing about the duplication.

    Fenced code blocks are excluded (see `_words`): two pages legitimately
    quote the same command, and the one kind of text that *must* be copied
    exactly is the one this rule must not push anyone to paraphrase.
    """
    length, offender = _longest_shared_run(_read(a), _read(b))
    assert length <= MAX_SHARED_RUN, (
        f"{a.relative_to(ROOT)} and {b.relative_to(ROOT)} share a "
        f"{length}-word run — one of them should link to the other instead of "
        f"restating it:\n    {offender}"
    )


def test_agents_points_at_claude_md():
    """The routing half of the rule above: AGENTS.md must actually link there.

    Not restating `CLAUDE.md` is only half of what #211 asked for; a file that
    neither restates nor points at it is worse than either.
    """
    assert "CLAUDE.md" in _read(AGENTS), "AGENTS.md must point at CLAUDE.md"


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


def test_store_guarantees_names_every_public_atomic_writer():
    """Every public callable in `store/atomic.py`.

    A caller choosing between them can only do that if the doc lists all of
    them — so a new one added without a line in the table fails here. The doc
    deliberately writes no *count* of them, since nothing could check that.
    """
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


def test_no_orphan_images():
    """No capture under `docs/` that nothing references.

    An image no document points at is one nobody notices going stale — and in a
    repo whose privacy rule turns on what images *show*, a stray capture nobody
    is looking at is worth failing over.

    Two widenings over the obvious version, both because the rule is about what
    a file depicts rather than where it was filed: every image extension, not
    just `.png`; and all of `docs/`, not just `docs/screenshots/` — a capture
    dropped one directory over is exactly as unreviewed.

    *Any* markdown file in `docs/` may be the referrer, not only the pages this
    guard maintains. A spec that embeds its own diagram and points at it from
    its own prose is properly owned; forcing it through one of the five entry
    points would be this guard inventing a rule nobody agreed to.

    "Referenced" means an actual link or embed, resolved to a path — not the
    filename appearing somewhere in prose. The looser version passed a capture
    that was merely listed in `docs/screenshots/README.md`'s inventory table,
    which is the one line the person adding an orphan would also write.
    """
    referenced: set[pathlib.Path] = set()
    for md in [README, *(ROOT / "docs").rglob("*.md")]:
        if not md.is_file():
            continue
        for target in _refs(md.read_text(encoding="utf-8", errors="ignore")):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            referenced.add((md.parent / target.split("#", 1)[0]).resolve())

    orphans = sorted(
        str(p.relative_to(ROOT)) for p in (ROOT / "docs").rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
        and p.resolve() not in referenced
    )
    assert not orphans, (
        f"images under docs/ that no document links to or embeds: {orphans}"
    )


def test_screenshots_readme_owns_the_isolation_procedure():
    """The capture procedure has exactly one owner, and it states the rule.

    #211 flagged screenshots as the item with a privacy trap: a capture of a
    working install shows a real library by default. Two things follow, and
    both are checked — the directory's own README must carry the rule (it is
    what the next person to re-capture opens), and it must be the only page
    that spells the procedure out, so there is no second copy to go stale.
    """
    shots_readme = _read(SHOTS / "README.md")
    for needle in ("GRIMOIRE_HOME", "isolated"):
        assert needle in shots_readme, (
            f"docs/screenshots/README.md does not mention {needle!r} — the "
            "isolated-store harness is the whole reason these captures are safe"
        )

    # The harness ports are an operational detail of one procedure, and naming
    # them anywhere else is how two copies start. Every maintained document
    # except the owner, not a hand-picked pair. (`README.md` names 8173 / 5173,
    # the *defaults a user runs on* -- a different fact, and precisely the one
    # the harness exists to stay away from.)
    for other in PROSE:
        if other == SHOTS / "README.md":
            continue
        assert "8199" not in _read(other), (
            f"{other.relative_to(ROOT)} spells out the capture harness's "
            "ports — link to docs/screenshots/README.md rather than keeping a "
            "second copy"
        )


# --- the guard's own helpers ------------------------------------------------
#
# Everything above reads real files, so a helper that quietly stopped finding
# anything would turn every test in this module green while checking nothing --
# the exact vacuity the AST guards warn about. These pin the three helpers that
# do real work, on inputs small enough to reason about.

def test_refs_finds_markdown_and_html_targets():
    """The HTML forms matter: `README.md`'s logo is an `<img>` because markdown
    cannot size one, and the first version of this guard never looked at it."""
    text = ('[a](one.md) ![b](two.png) [ext](https://example.com) '
            '[anchor](#here) <img src="three.png" width="32"> '
            '<a href="four.md">x</a>')
    assert _refs(text) == ["one.md", "two.png", "https://example.com", "#here",
                           "three.png", "four.md"]


def test_anchors_ignores_headings_inside_code_fences():
    """A `#` comment on the first column of a bash fence is not a heading.

    Counting it as one would hand the anchor check anchors that do not exist,
    making a broken link pass. `README.md` has three such comments.
    """
    text = "# Real Heading\n\n```bash\n# not a heading\n```\n\n## Second One\n"
    assert _anchors(text) == {"real-heading", "second-one"}


def test_longest_shared_run_measures_words_not_lines():
    """The property the whole duplication rule rests on: rewrapping a copied
    paragraph must not hide it."""
    source = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
    reflowed = "prefix\nalpha beta gamma\ndelta epsilon zeta eta\ntheta iota kappa\nsuffix"
    length, offender = _longest_shared_run(reflowed, source)
    assert length == 10
    assert offender.startswith("alpha beta gamma")

    unrelated, _ = _longest_shared_run("nothing at all in common here", source)
    assert unrelated == 0


def test_longest_shared_run_ignores_fenced_code():
    """Two pages must be free to quote the same command exactly."""
    fence = "\n\n```bash\nmake check-py PY=/some/very/long/path\n```\n"
    length, offender = _longest_shared_run("alpha bravo charlie" + fence,
                                           "delta echo foxtrot" + fence)
    assert (length, offender) == (0, "")
