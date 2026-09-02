# Offline Slop Grader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `natural-prose` eval case that holds `templates/scene/sections/natural_prose.j2` in the assembled prompt and measures a reply against the subset of that block's instructions a regex can honestly check.

**Architecture:** A new pure module `evals/slop.py` owns the phrase/name/beat lists (each entry pairing a literal `source` that must appear in the rendered template with a broader `pattern` the detector applies), the thresholds, and the detectors. `evals/graders.py` gains one `grade_slop` that assembles nine `Check`s from it. `evals/cases.py` gains a fixture campaign under the `cinematic` length preset and four recordings. Normalization reuses production parsers — `scenes.split_reply` and a newly-public `length_drift.prose`.

**Tech Stack:** Python 3.9+ standard library only (`re`, `statistics`, `dataclasses`). pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-02-slop-grader-design.md`

## Global Constraints

- **Standard library only** in `evals/slop.py`. No NLP dependency, no new entry in `pyproject.toml`.
- **No real library content.** Every fixture name is an invented placeholder from the codebase's existing vocabulary (Realm, Saltmarch, Seraphine Vale, Mara, Winifred, Rowan, Tobin). Never commit counts, proportions or distributions taken from `~/.grimoire`. See CLAUDE.md.
- **Graders re-use production parsers.** `scenes.split_reply`, `length_drift.prose`. A grader that parses output its own way stops testing the app the moment the app's parser changes.
- **Every threshold is a module-level named constant** with its structural justification in a comment beside it, and a note that it should be tuned against real prompts later. No inline numeric literals in detector bodies.
- **Every graded instruction carries a drift source.** If a check can fail, some entry's `source` must be required to appear in the rendered template.
- **Insufficient samples pass, never fail.** The measurability gate is the only check allowed to fail on undersized output.
- Run the gate with `make check`. Individually: `make check-py`, `make check-lint`, `make check-mypy`.
- Windows venv interpreter is `backend\.venv\Scripts\python.exe`; macOS/Linux is `backend/.venv/bin/python`. Commands below give the Windows form, matching this repo's convention of spelling out both.
- The three lint gates are ratcheted. If a new file moves a count, run `make baseline` and commit the smaller file with the change.

---

### Task 1: Promote `length_drift._prose` to public `prose`

The reusable normalizer. It strips roll fences **and** expanded image markdown; reusing only the fence regex would leave image markdown in the text and pollute every sentence, paragraph and word statistic.

**Files:**
- Modify: `backend/src/grimoire/store/length_drift.py:62-84`
- Test: `backend/tests/test_length_drift.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `length_drift.prose(content: str) -> str` — content with roll fences replaced by a space and expanded images removed.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_length_drift.py` (create it if absent, with `from grimoire.store import length_drift` at the top):

```python
def test_prose_is_public_and_strips_fences_and_images():
    """The eval slop grader imports this by name. It must stay public, and it
    must keep doing both jobs -- a fence-only version would let expanded image
    markdown into every sentence and paragraph statistic."""
    text = ("She set the crate down.\n\n"
            "```roll\ncheck: nerve\nactor: Seraphine Vale\n```\n\n"
            "![a lantern](/api/worlds/realm/art/lantern.png)\n\n"
            "The water took the light.")
    out = length_drift.prose(text)
    assert "check: nerve" not in out
    assert "lantern.png" not in out
    assert "She set the crate down." in out
    assert "The water took the light." in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; $env:PYTHONPATH="src"; .venv\Scripts\python.exe -m pytest tests/test_length_drift.py::test_prose_is_public_and_strips_fences_and_images -v`

Expected: FAIL with `AttributeError: module 'grimoire.store.length_drift' has no attribute 'prose'`

- [ ] **Step 3: Rename the function and its two callers**

In `backend/src/grimoire/store/length_drift.py`, rename `_prose` to `prose` and update the docstring's opening line to note the public contract. Its two in-module callers are `_words` and `_paragraphs`:

```python
def prose(content: str) -> str:
    """Content with the roll fence and expanded images removed.

    Public because evals/slop.py measures the same text this measures: two
    normalizers that can disagree is the bug fence.py centralises against.

    A fence is the mechanical block the roll protocol asked for; an image is a
    picture the reply included, and on the wire it is a URL. Counting either as
    prose punishes the model for complying, and the image is not words the
    model wrote.
    """
    return export.remove_images(_ROLL_FENCE.sub(" ", content))


def _words(content: str) -> int:
    return len(prose(content).split())


def _paragraphs(content: str) -> int:
    return max(len([p for p in prose(content).split("\n\n") if p.strip()]), 1)
```

- [ ] **Step 4: Run the test and the module's existing suite**

Run: `cd backend; $env:PYTHONPATH="src"; .venv\Scripts\python.exe -m pytest tests/test_length_drift.py tests/test_evals.py -v`

Expected: PASS. `test_evals.py` exercises `length_drift.measure` through `grade_length`, so a missed caller surfaces here.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/length_drift.py backend/tests/test_length_drift.py
git commit -m "length_drift.prose is public"
```

---

### Task 2: `evals/slop.py` — normalization and the measurability gate

Two normalized texts, not one. `split_reply` moves a marker like `**Elara:**` into the segment's `speaker` field and out of its `content`, so a stock name used as a *speaker label* is invisible to any detector reading bodies only.

**Files:**
- Create: `evals/slop.py`
- Test: `backend/tests/test_eval_graders.py`

**Interfaces:**
- Consumes: `length_drift.prose` (Task 1).
- Produces:
  - `normalize(text: str, players: frozenset[str]) -> tuple[str, list[str]]` returning `(prose, names)`
  - `sentences(prose: str) -> list[str]`
  - `paragraphs(prose: str) -> list[str]`
  - `word_count(s: str) -> int`
  - `MIN_SENTENCES: int`, `MIN_PARAGRAPHS: int`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_eval_graders.py`:

```python
from evals import slop


def test_normalize_returns_bodies_and_speaker_labels_separately():
    """split_reply moves the marker into `speaker` and out of `content`, so a
    stock name used as a speaker label is invisible to any body-only detector.
    normalize hands both back."""
    text = "**Elara:** Evening.\n\n**Seraphine Vale:** You're late."
    prose_text, names = slop.normalize(text, frozenset({"Winifred"}))
    assert "Elara" not in prose_text
    assert "Elara" in names
    assert "Evening." in prose_text


def test_normalize_routes_player_blocks_to_the_narrator():
    """A player-named block is narrator content, per split_reply. Its label is
    not a speaker name and must not be offered as one."""
    _, names = slop.normalize("**Winifred:** I step out of the fog.",
                              frozenset({"Winifred"}))
    assert names == []


def test_normalize_strips_fences_and_images_via_production_parser():
    text = ("**Seraphine Vale:** Mine.\n\n"
            "```roll\ncheck: nerve\nactor: Seraphine Vale\n```\n\n"
            "![crates](/api/worlds/realm/art/crates.png)")
    prose_text, _ = slop.normalize(text, frozenset())
    assert "check: nerve" not in prose_text
    assert "crates.png" not in prose_text


def test_sentences_splits_on_terminators_and_respects_closing_quotes():
    """`"Go." Mara left.` is two sentences: the terminator precedes the closing
    quote. Getting this wrong miscounts every line of dialogue."""
    assert slop.sentences('"Go." Mara left.') == ['"Go."', 'Mara left.']


def test_sentences_does_not_split_on_a_known_abbreviation():
    assert slop.sentences("Dr. Rowan waited. Nobody came.") == [
        "Dr. Rowan waited.", "Nobody came."]


def test_sentences_recognises_an_abbreviation_inside_a_quotation():
    """The common dialogue case. The abbreviation check has to strip the
    LEADING quote as well as the trailing terminator, or `"Dr.` is not
    recognised as `dr` and the line splits mid-quotation."""
    assert slop.sentences('"Dr. Rowan waited." Nobody came.') == [
        '"Dr. Rowan waited."', "Nobody came."]


def test_sentences_drops_spans_with_no_word_tokens():
    assert slop.sentences("Yes.   \n\n  ") == ["Yes."]


def test_paragraphs_splits_on_blank_lines_and_drops_empty_ones():
    assert slop.paragraphs("One.\n\n  \n\nTwo.\n") == ["One.", "Two."]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; $env:PYTHONPATH="src;../evals/.."; .venv\Scripts\python.exe -m pytest tests/test_eval_graders.py -k "normalize or sentences or paragraphs" -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'evals.slop'`

- [ ] **Step 3: Write the module**

Create `evals/slop.py`:

```python
"""Detectors for the natural-prose block, and the lists they read.

Eval-owned rather than production-owned, and deliberately: the app has no
opinion about slop. templates/scene/sections/natural_prose.j2 is prescriptive
and feed-forward, so there is no production constant to borrow -- the same
situation as graders.COLLAPSE_RATIO, which is eval-owned because "the app has
no opinion about a reply being too SHORT". If a production store/slop_drift.py
is ever built, its thresholds become the codebase's definition of "this reply
reads flat" and this module borrows them, as grade_length borrows
length_drift.TRIM.

Every threshold here is a named module constant justified structurally -- from
what the templates and the resolved budget do -- and never from a measurement
over stored content. Each should be tuned against real prompts later.

The sentence splitter is a declared heuristic, not a claim of correctness. It
misses initials, honorifics outside _ABBREVIATIONS, and ellipsis used as a
fragment. That is acceptable because every threshold is calibrated through this
same splitter, so its errors are systematic rather than random.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

from grimoire.store import length_drift, scenes

# --------------------------------------------------------------- sample floors

# `cinematic` targets 900 words. At even 25 words per sentence that is ~36
# sentences, so 12 is a third of a low estimate: high enough for a coefficient
# of variation to mean anything, low enough not to gate a compact reply. Tune
# against real prompts later.
MIN_SENTENCES = 12
# `cinematic` permits at most 7 blocks, and block boundaries are paragraph
# boundaries, so 4 sits comfortably under the ceiling. Tune later.
MIN_PARAGRAPHS = 4

_ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "st", "lt", "capt", "sgt", "sr", "jr",
    "vs", "etc", "e.g", "i.e",
})

# A terminator, any closing quotes/brackets that follow it, whitespace, then an
# opening quote or a capital. The lookahead is what keeps `"Go." Mara left.`
# from splitting inside the quotation.
_SENTENCE_BREAK = re.compile(
    r'([.!?…]+[\"”’\')\]]*)\s+(?=[\"“‘\'(\[]*[A-Z])')


def normalize(text: str, players: frozenset[str]) -> tuple[str, list[str]]:
    """Split one model reply into (prose, speaker names).

    Both halves come from scenes.split_reply, the production parser: measuring
    `**Mara:**` as a sentence would be nonsense, and a stock name used as a
    speaker label would be invisible if only bodies were returned.

    Block boundaries become paragraph boundaries, which is why the bodies are
    rejoined with a blank line.
    """
    segments = scenes.split_reply(text, players)
    bodies = [length_drift.prose(s["content"]) for s in segments]
    names = [s["speaker"] for s in segments if s["speaker"]]
    return "\n\n".join(b for b in bodies if b.strip()), names


def word_count(s: str) -> int:
    return len(s.split())


def sentences(prose: str) -> list[str]:
    """Split prose into sentences. Declared heuristic -- see module docstring."""
    out: list[str] = []
    start = 0
    for m in _SENTENCE_BREAK.finditer(prose):
        head = prose[start:m.end(1)]
        tokens = head.split()
        # Strip quotes and brackets from BOTH ends before the terminator, so
        # `"Dr.` inside a quotation is still recognised as an abbreviation.
        last = tokens[-1].strip("\"“”‘’'()[]").rstrip(".!?…").lower() if tokens else ""
        if last in _ABBREVIATIONS:
            continue
        out.append(head.strip())
        start = m.end()
    tail = prose[start:].strip()
    if tail:
        out.append(tail)
    return [s for s in out if word_count(s)]


def paragraphs(prose: str) -> list[str]:
    """Runs of text between blank lines, dropping any with no word tokens."""
    blocks, current = [], []
    for line in prose.splitlines():
        if line.strip():
            current.append(line)
        elif current:
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))
    return [b.strip() for b in blocks if word_count(b)]


def coefficient_of_variation(counts: list[int]) -> float:
    """Population standard deviation over the mean. 0.0 for a degenerate list,
    which is the flattest possible answer and the right one."""
    mean = sum(counts) / len(counts)
    if not mean:
        return 0.0
    return statistics.pstdev(counts) / mean
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend; $env:PYTHONPATH="src;.."; .venv\Scripts\python.exe -m pytest tests/test_eval_graders.py -k "normalize or sentences or paragraphs" -v`

Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add evals/slop.py backend/tests/test_eval_graders.py
git commit -m "Normalization for the slop detectors, bodies and labels apart"
```

---

### Task 3: The lists, and the drift guard over them

Each entry pairs a literal `source` that must appear in the rendered template with a broader `pattern` the detector applies. That split is what lets `BEAT_WORDS` match `murmuring` while the template only says `murmured`, without the drift guard failing on its own list.

**Files:**
- Modify: `evals/slop.py`
- Test: `backend/tests/test_eval_graders.py`

**Interfaces:**
- Consumes: nothing from Task 2 beyond the module.
- Produces:
  - `@dataclass(frozen=True) class Entry: source: str; pattern: re.Pattern`
  - `LITERAL_PHRASES`, `JUDGMENT_ONLY`, `STOCK_NAMES`, `BEAT_WORDS`, `NOT_X_BUT_Y`, `RHYTHM_SOURCES` — all `tuple[Entry, ...]`
  - `ALL_ENTRIES: tuple[Entry, ...]`
  - `missing_sources(rendered: str) -> list[str]`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_eval_graders.py`:

```python
import pytest
from grimoire import prompts


def _rendered_block() -> str:
    return prompts.render("scene/sections/natural_prose.j2")


@pytest.mark.parametrize("entry", slop.ALL_ENTRIES, ids=lambda e: e.source[:40])
def test_every_entry_source_is_still_in_the_template(entry):
    """The one-way drift guard, per entry so a failure names the culprit.

    Grading a phrase the app has stopped banning is the failure this catches.
    An entry ADDED to the template is not graded until it is mirrored here --
    a stated limitation, and the direction that actually gets exercised, since
    the pink-elephant remedy on record is trimming the ban list.

    Compared whitespace-flat: the template is hard-wrapped, so six of these
    sources span a line break in the render."""
    assert slop._flat(entry.source) in slop._flat(_rendered_block())


def test_missing_sources_reports_what_left_the_template():
    assert slop.missing_sources("nothing here") 
    assert slop.missing_sources(_rendered_block()) == []


def test_every_graded_check_family_has_a_drift_source():
    """An instruction cannot be deleted from the template while its grader
    keeps scoring replies against it."""
    for family in (slop.LITERAL_PHRASES, slop.STOCK_NAMES, slop.BEAT_WORDS,
                   slop.NOT_X_BUT_Y, slop.RHYTHM_SOURCES):
        assert family, "every graded family carries at least one drift source"


def test_judgment_only_phrases_are_never_matched():
    """Their qualifier is the whole test: the template bans the reflexive use,
    not the phrase. They are carried as drift sources only."""
    matched = {e.source for e in slop.LITERAL_PHRASES}
    for entry in slop.JUDGMENT_ONLY:
        assert entry.source not in matched
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; $env:PYTHONPATH="src;.."; .venv\Scripts\python.exe -m pytest tests/test_eval_graders.py -k "entry_source or missing_sources or drift_source or judgment_only" -v`

Expected: FAIL with `AttributeError: module 'evals.slop' has no attribute 'ALL_ENTRIES'`

- [ ] **Step 3: Add the lists**

Append to `evals/slop.py`:

```python
# ------------------------------------------------------------------- the lists
#
# Each entry pairs a `source` -- a literal substring that must still appear in
# the rendered template, which is all the drift guard checks -- with the
# `pattern` the detector actually applies. The split is what lets a beat group
# match inflections the template never spells out without the guard failing on
# its own list.

# The template itself uses only STRAIGHT apostrophes (U+0027) — verified: it
# contains eight of them and no U+2019. Every `source` below must therefore be
# written with a straight apostrophe or the drift guard fails on its own list.
# The `pattern` side admits both, because a model may well type the curly one.
_AP = r"['’]"


@dataclass(frozen=True)
class Entry:
    source: str
    pattern: "re.Pattern[str]"


def _lit(source: str, pattern: str | None = None) -> Entry:
    return Entry(source, re.compile(pattern or (r"\b" + re.escape(source) + r"\b"),
                                    re.IGNORECASE))


# Template: "Phrases -- never use." Only the unconditional bans.
LITERAL_PHRASES: tuple[Entry, ...] = (
    _lit("barely above a whisper"),
    _lit("barely audible"),
    _lit("the air thick with"),
    _lit("a smile playing on", r"\ba smile playing on\b"),
    _lit("eyes never leaving"),
    _lit("couldn't help but", r"\bcouldn" + _AP + r"t help but\b"),
    _lit("couldn't shake the feeling",
         r"\bcouldn" + _AP + r"t shake the feeling\b"),
    # `\w+ ` is OPTIONAL on both tails: the template's own wording is "heart
    # pounding or hammering in a chest or against ribs", so requiring an
    # intervening possessive would miss the bare form the template spells out.
    _lit("heart pounding or hammering",
         r"\bheart (?:pounding|hammering)\b[^.!?…]{0,40}?"
         r"(?:in (?:\w+ )?chest|against (?:\w+ )?ribs)"),
    _lit("casting long shadows"),
    _lit("something else entirely"),
    _lit("spreading across her face",
         r"\bspreading across \w+ face\b"),
    _lit("one last time"),
    _lit("a testament to"),
    _lit("a tapestry, symphony, or dance of anything",
         r"\ba (?:tapestry|symphony|dance) of\b"),
    _lit("ministrations"),
    _lit("the ghost of a smile"),
    _lit("shivers down the spine",
         r"\bshivers? down \w+ spine\b"),
    _lit("knuckles whitening", r"\bknuckles whiten(?:ing|ed)?\b"),
    _lit("the smell of ozone"),
    _lit("an unreadable expression"),
    _lit("lips swollen with kisses"),
    _lit("delve"),
    _lit("nestled"),
    _lit("moreover"),
    _lit("furthermore"),
    _lit("indeed"),
    _lit("albeit"),
)

# Qualifier-dependent: the template bans the reflexive use, not the phrase.
# Carried as drift sources; never matched. Listed in the spec's ungraded table.
JUDGMENT_ONLY: tuple[Entry, ...] = (
    _lit("said in a low voice as a reflex tag"),
    _lit("a deep breath as filler"),
    _lit("foreheads pressed together as the default tender gesture"),
)

# Template: "Never reach for the stock AI pool". Word-bounded, so `Aria` does
# not match inside a longer name.
STOCK_NAMES: tuple[Entry, ...] = tuple(
    _lit(n) for n in ("Elara", "Lyra", "Kael", "Aria", "Seraphina", "Selene",
                      "Thorne", "Voss", "Vance", "Blackwood", "Ashford"))

# Template: "Beat words -- ration." A cap, not a ban: these are ordinary words.
# `source` is the template's own spelling; `pattern` carries the inflections.
BEAT_WORDS: tuple[Entry, ...] = (
    _lit("Flickered", r"\bflicker(?:s|ed|ing)?\b"),
    _lit("leaned", r"\blean(?:s|ed|ing)?\b"),
    _lit("murmured", r"\bmurmur(?:s|ed|ing)?\b"),
    _lit("muttered", r"\bmutter(?:s|ed|ing)?\b"),
    _lit("nodded", r"\bnod(?:s|ded|ding)?\b"),
    _lit("gaze", r"\bgaz(?:e|es|ed|ing)\b"),
    _lit("grinned", r"\bgrin(?:s|ned|ning)?\b"),
    _lit("gestured", r"\bgestur(?:e|es|ed|ing)\b"),
    _lit("glinted", r"\bglint(?:s|ed|ing)?\b"),
    _lit("hesitated", r"\bhesitat(?:e|es|ed|ing)\b"),
    _lit("whispered", r"\bwhisper(?:s|ed|ing)?\b"),
    _lit("blinked", r"\bblink(?:s|ed|ing)?\b"),
    _lit("hummed", r"\bhum(?:s|med|ming)?\b"),
    _lit("smirked", r"\bsmirk(?:s|ed|ing)?\b"),
    _lit("faintly", r"\bfaintly\b"),
)

# Template: 'Constructions -- never use. "Not X, but Y" in every disguise'.
# The spans use a class that EXCLUDES sentence terminators: a length bound
# alone does not stop a match running across two short sentences, which is how
# a construction detector starts reporting contrasts nobody wrote. This is a
# FLOOR on the family, not a decision procedure for it -- "every disguise" is
# not implementable, and the hypothesis row claims only these four forms.
NOT_X_BUT_Y: tuple[Entry, ...] = (
    Entry('"Not X, but Y" in every disguise',
          re.compile(r"\bnot\s+[^.!?…]{1,60}?,\s*but\s+", re.IGNORECASE)),
    Entry("it wasn't just X — it was Y",
          re.compile(r"\b(?:it|he|she|they)\s+(?:wasn|weren)" + _AP +
                     r"t\s+just\s+[^.!?…]{1,60}?[—–]\s*"
                     r"(?:it|he|she|they)\s+(?:was|were)\b", re.IGNORECASE)),
    Entry("she didn't X; she Y'd",
          re.compile(r"\b(?:he|she|they|it)\s+didn" + _AP +
                     r"t\s+[^.!?…;]{1,60}?;\s*(?:he|she|they|it)\s+\w",
                     re.IGNORECASE)),
    Entry("no longer X; now Y",
          re.compile(r"\bno longer\s+[^.!?…;]{1,60}?;\s*now\b",
                     re.IGNORECASE)),
)

# The rhythm checks grade instructions too, so they carry drift sources of
# their own -- otherwise the template could lose the rule while the graders
# went on scoring replies against it.
RHYTHM_SOURCES: tuple[Entry, ...] = (
    _lit("Vary sentence length and paragraph shape"),
    _lit("if the last paragraph used one, the next doesn't"),
)

ALL_ENTRIES: tuple[Entry, ...] = (
    LITERAL_PHRASES + JUDGMENT_ONLY + STOCK_NAMES + BEAT_WORDS
    + NOT_X_BUT_Y + RHYTHM_SOURCES)


def _flat(text: str) -> str:
    """Collapse all whitespace to single spaces.

    `templates/` is hard-wrapped at ~72 columns, so a multi-word source like
    "spreading across her face" is split by a newline in the render and would
    never match verbatim. Six of the sources above span a wrap point. Both
    sides are flattened before comparison, which also makes the guard immune to
    a pure re-wrap of the template — an edit that changes no instruction.
    """
    return " ".join(text.split())


def missing_sources(rendered: str) -> list[str]:
    """Entries whose literal source has left the template. The one-way guard."""
    flat = _flat(rendered)
    return [e.source for e in ALL_ENTRIES if _flat(e.source) not in flat]
```

- [ ] **Step 4: Run tests, and fix any source that does not match the template**

Run: `cd backend; $env:PYTHONPATH="src;.."; .venv\Scripts\python.exe -m pytest tests/test_eval_graders.py -k "entry_source or missing_sources or drift_source or judgment_only" -v`

Expected: PASS. The parameterized test names the offending entry if a `source` string does not appear verbatim in the render — most likely an apostrophe or em-dash mismatch. **Fix the `source` to match the template exactly; never edit the template to match the list.**

- [ ] **Step 5: Commit**

```bash
git add evals/slop.py backend/tests/test_eval_graders.py
git commit -m "The natural-prose lists, each entry guarded against template drift"
```

---

### Task 4: The list-driven and construction detectors

**Files:**
- Modify: `evals/slop.py`
- Test: `backend/tests/test_eval_graders.py`

**Interfaces:**
- Consumes: `Entry` lists and `normalize` from Tasks 2–3.
- Produces:
  - `found_phrases(prose: str) -> list[str]`
  - `found_stock_names(prose: str, names: list[str], established: frozenset[str]) -> list[str]`
  - `overused_beats(prose: str) -> list[tuple[str, int]]`
  - `found_constructions(prose: str) -> list[str]`
  - `established_tokens(names) -> frozenset[str]`
  - `BEAT_REPEAT_MAX: int`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_eval_graders.py`:

```python
@pytest.mark.parametrize("entry", slop.LITERAL_PHRASES, ids=lambda e: e.source[:40])
def test_every_literal_phrase_entry_actually_fires(entry):
    """Per-entry coverage: a dead entry cannot hide behind a named check some
    other entry already makes fail."""
    probe = {
        "heart pounding or hammering": "Her heart pounding in her chest, she ran.",
        "a tapestry, symphony, or dance of anything": "It was a tapestry of light.",
        "spreading across her face": "A grin spreading across her face.",
        "shivers down the spine": "Shivers down her spine.",
        "knuckles whitening": "Knuckles whitening on the rail.",
        "a smile playing on": "A smile playing on her lips.",
    }.get(entry.source, entry.source)
    assert slop.found_phrases(probe), f"{entry.source!r} never fires"


@pytest.mark.parametrize("probe", [
    # Each ALTERNATION inside a multi-form pattern, not just one arm of it.
    # Exercising `tapestry` alone would leave `symphony` and `dance` dead.
    "It was a symphony of rope and water.",
    "It was a dance of lantern light.",
    "His heart hammering against ribs, he waited.",
    "Her heart pounding against her ribs, she waited.",
    "A shiver down his spine.",
    "Knuckles whitened on the rail.",
])
def test_phrase_alternations_each_fire(probe):
    assert slop.found_phrases(probe), f"{probe!r} should have matched"


@pytest.mark.parametrize("entry", slop.BEAT_WORDS, ids=lambda e: e.source)
def test_every_beat_group_actually_fires(entry):
    """Per-entry coverage for the beat cap. Without this, a dead regex for any
    of the fifteen groups stays invisible behind whichever one the recording
    happens to trip."""
    # The template's own spelling, repeated past the cap.
    word = entry.source.lower()
    text = " ".join([f"She {word}."] * (slop.BEAT_REPEAT_MAX + 1))
    assert any(source == entry.source
               for source, _ in slop.overused_beats(text)), \
        f"{entry.source!r} never fires"


@pytest.mark.parametrize("entry", slop.STOCK_NAMES, ids=lambda e: e.source)
def test_every_stock_name_entry_actually_fires(entry):
    assert slop.found_stock_names(f"{entry.source} waited.", [], frozenset())


@pytest.mark.parametrize("entry", slop.NOT_X_BUT_Y, ids=lambda e: e.source[:30])
def test_every_construction_entry_actually_fires(entry):
    probe = {
        '"Not X, but Y" in every disguise': "It was not fear, but fury.",
        "it wasn't just X — it was Y":
            "It wasn't just a warning — it was a promise.",
        "she didn't X; she Y'd":
            "She didn't walk; she prowled.",
        "no longer X; now Y": "He was no longer a guest; now a debt.",
    }[entry.source]
    assert slop.found_constructions(probe), f"{entry.source!r} never fires"


@pytest.mark.parametrize("probe", [
    # The curly apostrophe a model is at least as likely to type as the
    # straight one the template uses.
    "It wasn’t just a warning — it was a promise.",
    "She didn’t walk; she prowled.",
])
def test_constructions_match_the_curly_apostrophe_too(probe):
    assert slop.found_constructions(probe)


def test_constructions_do_not_match_across_a_sentence_boundary():
    """A length bound alone would let this match. The span class excludes
    sentence terminators for exactly this reason."""
    assert not slop.found_constructions("She was not there. But Rowan was.")


def test_stock_name_is_exempt_when_established_as_a_single_token():
    assert not slop.found_stock_names("Selene shrugged.", [],
                                      slop.established_tokens(["Selene"]))


def test_stock_name_is_exempt_when_established_inside_a_multiword_name():
    """Exemption is per token: a cast that includes `Elara Vale` exempts the
    token `Elara` everywhere, including alone. Requiring the full name at the
    match site would flag a reply for obeying the template's own rule that
    established names are reproduced exactly."""
    established = slop.established_tokens(["Elara Vale"])
    assert not slop.found_stock_names("Elara shrugged.", [], established)


def test_stock_name_in_a_speaker_label_is_caught():
    """The blind spot split_reply creates: the label never reaches the body."""
    assert slop.found_stock_names("", ["Elara"], frozenset())


def test_beat_words_cap_counts_inflections_together():
    text = ("She murmured. He was murmuring. They murmur. "
            "The wind murmurs.")
    assert ("murmured", 4) in slop.overused_beats(text)


def test_beat_words_below_the_cap_do_not_fire():
    assert slop.overused_beats("She nodded. He nodded.") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; $env:PYTHONPATH="src;.."; .venv\Scripts\python.exe -m pytest tests/test_eval_graders.py -k "fires or exempt or speaker_label or beat_words or sentence_boundary" -v`

Expected: FAIL with `AttributeError: module 'evals.slop' has no attribute 'found_phrases'`

- [ ] **Step 3: Add the detectors**

Append to `evals/slop.py`:

```python
# ---------------------------------------------------------------- the detectors

# The count is global, not per block, so the block ceiling cannot justify this
# directly. The instruction is that "not every line of dialogue needs a lean,
# nod, or murmur": with `cinematic` permitting at most 7 blocks and 5 speakers,
# a single beat word used a fourth time is reaching for the same reflex more
# often than there are speakers to attribute it to. A structural argument for
# the order of magnitude, not a measured optimum. Tune against real prompts.
BEAT_REPEAT_MAX = 3


def found_phrases(prose: str) -> list[str]:
    """Banned phrases present. JUDGMENT_ONLY is deliberately not consulted."""
    return [e.source for e in LITERAL_PHRASES if e.pattern.search(prose)]


def established_tokens(names) -> frozenset[str]:
    """Every established name flattened to its individual casefolded tokens.

    Exemption is per token because the template's rule is that names already in
    the scene, cast or world "are fixed; reproduce them exactly, even if they
    appear below". A cast holding `Elara Vale` therefore exempts `Elara` alone
    too. Over-permissive by construction, and that is the right direction: a
    false negative reproduces an existing name, a false positive fails a reply
    for obeying the prompt.
    """
    return frozenset(t.casefold() for n in names for t in n.split())


def found_stock_names(prose: str, names: list[str],
                      established: frozenset[str]) -> list[str]:
    """Stock names in the prose OR in a speaker label, minus what is established.

    Both halves are searched because split_reply routes a marker into the
    segment's `speaker` field and out of its `content`: a reply that invents an
    NPC called Elara and gives her a labelled block must fail.
    """
    haystack = prose + "\n" + "\n".join(names)
    return [e.source for e in STOCK_NAMES
            if e.pattern.search(haystack)
            and e.source.casefold() not in established]


def overused_beats(prose: str) -> list[tuple[str, int]]:
    """Beat groups past the cap, as (source, count). Inflections count together."""
    hits = [(e.source, len(e.pattern.findall(prose))) for e in BEAT_WORDS]
    return [(source, n) for source, n in hits if n > BEAT_REPEAT_MAX]


def found_constructions(prose: str) -> list[str]:
    return [e.source for e in NOT_X_BUT_Y if e.pattern.search(prose)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend; $env:PYTHONPATH="src;.."; .venv\Scripts\python.exe -m pytest tests/test_eval_graders.py -k "fires or exempt or speaker_label or beat_words or sentence_boundary" -v`

Expected: PASS. If a parameterized entry fails, its pattern is wrong — widen the `pattern`, never the `source`.

- [ ] **Step 5: Commit**

```bash
git add evals/slop.py backend/tests/test_eval_graders.py
git commit -m "Phrase, stock-name, beat and construction detectors"
```

---

### Task 5: The rhythm detectors and the negative corpus

**Files:**
- Modify: `evals/slop.py`
- Test: `backend/tests/test_eval_graders.py`

**Interfaces:**
- Consumes: `sentences`, `paragraphs`, `word_count`, `coefficient_of_variation`, `MIN_SENTENCES`, `MIN_PARAGRAPHS`.
- Produces:
  - `sentence_variance(prose) -> tuple[bool, str]` — `(ok, detail)`
  - `paragraph_variance(prose) -> tuple[bool, str]`
  - `em_dash_adjacent(prose) -> bool`
  - `is_measurable(prose) -> tuple[bool, str]`
  - `VARIANCE_MIN: float`, `PARA_VARIANCE_MIN: float`, `EM_DASH: str`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_eval_graders.py`:

```python
_FLAT = "\n\n".join(
    ["The lamp was lit and the room was warm and the door was shut."] * 6
    + ["The chair was old and the rug was worn and the clock was slow."] * 6)

# 15 sentences over 8 paragraphs: comfortably past MIN_SENTENCES (12) and
# MIN_PARAGRAPHS (4), so the variance assertions below actually measure rather
# than short-circuiting on sample size. Sentence lengths run 1 to 33 words on
# purpose. No em dash appears at all, so em_dash_adjacent has nothing to find.
_VARIED = (
    "Rain.\n\n"
    "It came in off the water the way it always did at this hour, slow at "
    "first and then all at once, and Winifred pulled her coat tighter and "
    "swore at nobody in particular.\n\n"
    "Seraphine Vale did not move. She had been standing at the rail since "
    "before the fog closed in, and she had the look of somebody who intended "
    "to be standing there long after it lifted.\n\n"
    "\"You waited,\" Winifred said.\n\n"
    "\"I had nothing better on.\" The smuggler tipped her chin at the crates, "
    "stacked three high and sheeted against the weather, and let the silence "
    "do the asking for her. Somewhere below, the water knocked at the "
    "pilings.\n\n"
    "Winifred counted them. Twelve. That was four more than the manifest "
    "admitted to, and the manifest was the only honest thing she had been "
    "given all week.\n\n"
    "\"Well?\"\n\n"
    "Rowan came up the steps behind her with his bad shoulder set against the "
    "wind, and he did not answer until he had looked at every crate in the "
    "stack. \"Eight,\" he said. \"On paper.\"")


def test_measurable_fails_on_undersized_output():
    """Without this gate the whole case passes on an empty reply: no banned
    phrase occurs in nothing, and both variance checks have no sample."""
    ok, _ = slop.is_measurable("")
    assert not ok


def test_measurable_passes_on_a_full_reply():
    ok, _ = slop.is_measurable(_VARIED)
    assert ok


def test_variance_checks_pass_when_the_sample_is_too_small():
    """MANDATORY, not permitted. A one-paragraph reply has a paragraph
    coefficient of variation of exactly 0, which is below the threshold -- so a
    check that measured anyway would fail here too, and the `terse` recording
    could not declare slop.measurable alone."""
    ok, detail = slop.paragraph_variance("One short line.")
    assert ok
    assert "sample" in detail.lower()
    assert slop.sentence_variance("One short line.")[0]


def test_flat_prose_trips_both_variance_checks():
    assert not slop.sentence_variance(_FLAT)[0]
    assert not slop.paragraph_variance(_FLAT)[0]


def test_varied_prose_trips_neither_variance_check():
    assert slop.sentence_variance(_VARIED)[0]
    assert slop.paragraph_variance(_VARIED)[0]


def test_em_dash_in_consecutive_paragraphs_is_caught():
    assert slop.em_dash_adjacent("She — wait.\n\nHe — no.")


def test_em_dash_spaced_out_is_fine():
    assert not slop.em_dash_adjacent(
        "She — wait.\n\nNothing here.\n\nHe — no.")


# ------------------------------------------------------- the negative corpus
#
# Legitimate prose every detector must leave alone. It prevents regression on
# these exact fixtures and nothing more -- it is not an independent
# distribution and yields no statistical false-positive bound. A threshold
# tightened until it trips one of these has gone too far.

# Every passage clears MIN_SENTENCES and MIN_PARAGRAPHS. That is the whole
# point: a passage below the floor short-circuits both variance checks to a
# pass, and would place no constraint on VARIANCE_MIN at all -- a corpus that
# looks like protection and is not.
_LEGITIMATE = {
    "dialogue-heavy": (
        "\"Whose?\" Winifred asked.\n\n"
        "\"Mine.\"\n\n"
        "\"Since when?\"\n\n"
        "\"Since the tide turned and the harbourmaster stopped counting, which "
        "was a good while before you started asking me questions on my own "
        "pier in the rain.\"\n\n"
        "\"That is not an answer.\"\n\n"
        "\"It is the one you get.\" Seraphine Vale crouched, worked a nail "
        "loose from the nearest crate, and held it up to what light there "
        "was.\n\n"
        "\"Ship's iron.\"\n\n"
        "\"So?\"\n\n"
        "\"So it came off a hull, and hulls that lose their nails on my pier "
        "have generally lost something else first, which is the part you are "
        "going to want to hear about before the harbourmaster does.\"\n\n"
        "Winifred took the nail. It was cold. She turned it over twice, "
        "thinking about the manifest and the four crates that were not on it, "
        "and then she put it in her pocket without asking whether she could."),
    "deliberate fragments": (
        "Fog. Rope. The slap of water on stone.\n\n"
        "Winifred went down the steps counting, because counting was the only "
        "thing that had ever kept her steady, and she had needed steadying "
        "since the moment the letter came.\n\n"
        "Twelve steps. Then the boards.\n\n"
        "Somewhere out past the breakwater a bell went, once, and did not go "
        "again, and she stood in the dark a while listening for it anyway.\n\n"
        "Nothing. Wind. The creak of a mooring taking up slack.\n\n"
        "She had been told the pier was quiet at this hour and had believed "
        "it, which she was beginning to understand had been the point of "
        "telling her.\n\n"
        "A light, far out. Then not."),
    "incantatory refrain": (
        "By the salt she swore it. By the keel she swore it. By the cold black "
        "water under the boards she swore it, and meant every word of it, "
        "which was more than she could say for most of the promises she had "
        "made that season.\n\n"
        "Rowan listened the way people listen to weather.\n\n"
        "By the salt. By the keel. By the water.\n\n"
        "The old words had been said on this pier for longer than either of "
        "them had been alive, and they would go on being said here long after "
        "the two of them were done with it, which was rather the point of "
        "them.\n\n"
        "He said them back. Badly. She let it stand, because a promise said "
        "badly is still a promise, and because the tide was not going to wait "
        "for either of them to get the words right.\n\n"
        "By the salt. By the keel. By the water. That was the whole of it, and "
        "it had never needed to be more."),
    "terse action": (
        "The crate went over.\n\n"
        "Winifred caught the edge, took the weight badly, and felt something "
        "give in her shoulder that she would be paying for by morning.\n\n"
        "Rowan swore.\n\n"
        "Then he had the other side, and between them they walked it back "
        "from the drop, one careful pace at a time, until the boards stopped "
        "complaining underfoot and the thing sat where it was meant to sit.\n\n"
        "Her arm was shaking. She let it.\n\n"
        "\"Again?\"\n\n"
        "\"No.\"\n\n"
        "They stood there in the wet with the stack between them and the "
        "water, and neither of them said the obvious thing, which was that "
        "whatever was in it had been worth somebody's while to load in "
        "the dark.\n\n"
        "Rowan sat down on the boards. He rubbed the shoulder. Winifred "
        "watched the fog come apart over the breakwater and put together, for "
        "the first time that week, an order of events that actually "
        "accounted for the four crates nobody would admit to.\n\n"
        "It was not a comfortable order of events. She kept it anyway."),
}


@pytest.mark.parametrize("label", sorted(_LEGITIMATE))
def test_negative_corpus_trips_nothing(label):
    text = _LEGITIMATE[label]
    assert slop.found_phrases(text) == []
    assert slop.found_stock_names(text, [], frozenset()) == []
    assert slop.overused_beats(text) == []
    assert slop.found_constructions(text) == []
    assert not slop.em_dash_adjacent(text)
    assert slop.sentence_variance(text)[0]
    assert slop.paragraph_variance(text)[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; $env:PYTHONPATH="src;.."; .venv\Scripts\python.exe -m pytest tests/test_eval_graders.py -k "measurable or variance or em_dash or negative_corpus" -v`

Expected: FAIL with `AttributeError: module 'evals.slop' has no attribute 'is_measurable'`

- [ ] **Step 3: Add the rhythm detectors**

Append to `evals/slop.py`:

```python
# ------------------------------------------------------------------- rhythm

EM_DASH = "—"

# Prose alternating only between short and long clauses still clears this;
# prose whose sentences cluster within a few words of one mean does not. Tune
# against real prompts later.
VARIANCE_MIN = 0.35
# Lower than the sentence figure because dialogue blocks are legitimately short
# and similar. This catches the uniform 3-to-5-sentence paragraph, not ordinary
# exchange. Tune later.
PARA_VARIANCE_MIN = 0.25


def is_measurable(prose: str) -> tuple[bool, str]:
    """The gate that stops the whole case passing on an empty reply.

    The precedent is graders.length_measurable, which is a FAILURE check: a
    reply with nothing to measure fails rather than being scored as a compliant
    zero. Without this, no banned phrase occurs in nothing, both variance
    checks have no sample, and em-dash spacing needs two paragraphs -- a green
    case on empty output, which is the "very slow way of asserting True" that
    evals/cases.py warns about.
    """
    n_s, n_p = len(sentences(prose)), len(paragraphs(prose))
    ok = n_s >= MIN_SENTENCES and n_p >= MIN_PARAGRAPHS
    return ok, (f"{n_s} sentences, {n_p} paragraphs "
                f"(need {MIN_SENTENCES}/{MIN_PARAGRAPHS})")


def sentence_variance(prose: str) -> tuple[bool, str]:
    """Coefficient of variation of sentence word counts, against VARIANCE_MIN.

    The sample minimum is evaluated BEFORE the statistic and short-circuits to
    a pass. That is mandatory rather than permitted: a degenerate sample has a
    coefficient of variation of 0, so measuring anyway would fail here as well
    as at is_measurable, and the `terse` counterexample could not declare
    slop.measurable alone.
    """
    counts = [word_count(s) for s in sentences(prose)]
    if len(counts) < MIN_SENTENCES:
        return True, f"sample too small to measure ({len(counts)} sentences)"
    cv = coefficient_of_variation(counts)
    return cv >= VARIANCE_MIN, f"sentence length CV {cv:.2f} (need {VARIANCE_MIN})"


def paragraph_variance(prose: str) -> tuple[bool, str]:
    """As sentence_variance, over paragraph word counts."""
    counts = [word_count(p) for p in paragraphs(prose)]
    if len(counts) < MIN_PARAGRAPHS:
        return True, f"sample too small to measure ({len(counts)} paragraphs)"
    cv = coefficient_of_variation(counts)
    return cv >= PARA_VARIANCE_MIN, (f"paragraph length CV {cv:.2f} "
                                     f"(need {PARA_VARIANCE_MIN})")


def em_dash_adjacent(prose: str) -> bool:
    """Two consecutive paragraphs both carrying an em dash.

    Derived from the template's own sentence -- "if the last paragraph used
    one, the next doesn't" -- rather than from a density threshold of our
    invention. It enforces one clause of the rhythm rule, not all of it.
    """
    flags = [EM_DASH in p for p in paragraphs(prose)]
    return any(a and b for a, b in zip(flags, flags[1:]))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend; $env:PYTHONPATH="src;.."; .venv\Scripts\python.exe -m pytest tests/test_eval_graders.py -k "measurable or variance or em_dash or negative_corpus" -v`

Expected: PASS. **If a negative-corpus case fails, the threshold is wrong, not the corpus** — loosen `VARIANCE_MIN`/`PARA_VARIANCE_MIN` and record the new value's justification in its comment.

- [ ] **Step 5: Commit**

```bash
git add evals/slop.py backend/tests/test_eval_graders.py
git commit -m "Rhythm detectors, with legitimate prose they must leave alone"
```

---

### Task 6: `graders.grade_slop`

**Files:**
- Modify: `evals/graders.py`
- Test: `backend/tests/test_eval_graders.py`

**Interfaces:**
- Consumes: everything from `evals/slop.py`.
- Produces: `graders.grade_slop(text: str, players: frozenset[str], established: frozenset[str], rendered_block: str) -> list[Check]` returning nine `Check`s named `slop.list_current`, `slop.measurable`, `slop.phrases`, `slop.stock_names`, `slop.beat_words`, `slop.not_x_but_y`, `slop.sentence_variance`, `slop.paragraph_uniformity`, `slop.em_dash_spacing`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_eval_graders.py`:

```python
from evals import graders


def _grade(text, established=frozenset()):
    return {c.name: c for c in graders.grade_slop(
        text, frozenset({"Winifred"}), established, _rendered_block())}


def test_grade_slop_names_all_nine_checks():
    checks = _grade(_VARIED)
    assert set(checks) == {
        "slop.list_current", "slop.measurable", "slop.phrases",
        "slop.stock_names", "slop.beat_words", "slop.not_x_but_y",
        "slop.sentence_variance", "slop.paragraph_uniformity",
        "slop.em_dash_spacing"}


def test_grade_slop_passes_clean_varied_prose():
    assert all(c.ok for c in _grade(_VARIED).values())


def test_grade_slop_fails_only_measurable_on_a_collapsed_reply():
    """The set-equality property the `terse` recording depends on."""
    failed = {n for n, c in _grade("She nodded.").items() if not c.ok}
    assert failed == {"slop.measurable"}


def test_grade_slop_catches_a_stock_name_in_a_speaker_label():
    text = _VARIED + "\n\n**Elara:** Evening."
    assert not _grade(text)["slop.stock_names"].ok
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; $env:PYTHONPATH="src;.."; .venv\Scripts\python.exe -m pytest tests/test_eval_graders.py -k "grade_slop" -v`

Expected: FAIL with `AttributeError: module 'evals.graders' has no attribute 'grade_slop'`

- [ ] **Step 3: Add the grader**

Add `from . import slop` to the imports in `evals/graders.py`, then append:

```python
# ------------------------------------------------------------------ slop

def grade_slop(text: str, players: frozenset[str], established: frozenset[str],
               rendered_block: str) -> list[Check]:
    """Score a reply against the graded subset of the natural-prose block.

    A strict subset, deliberately: the template's semantic instructions -- the
    rule of three, redundant adjective pairs, explaining an emotion just shown,
    decorative metaphor -- are not gradable by regex, and the spec's ungraded
    inventory names every one. A green result means the graded subset held, not
    that the block was obeyed. slop.not_x_but_y in particular is a floor on its
    family rather than a decision procedure for it.

    `rendered_block` is the CURRENT render of natural_prose.j2, so the drift
    guard fails when an instruction this grader scores against has left the
    template.
    """
    prose, names = slop.normalize(text, players)
    gone = slop.missing_sources(rendered_block)
    measurable, m_detail = slop.is_measurable(prose)
    phrases = slop.found_phrases(prose)
    stock = slop.found_stock_names(prose, names, established)
    beats = slop.overused_beats(prose)
    constructions = slop.found_constructions(prose)
    s_ok, s_detail = slop.sentence_variance(prose)
    p_ok, p_detail = slop.paragraph_variance(prose)
    return [
        Check("slop.list_current", not gone,
              f"no longer in natural_prose.j2: {gone}"),
        Check("slop.measurable", measurable, m_detail),
        Check("slop.phrases", not phrases, f"banned phrases present: {phrases}"),
        Check("slop.stock_names", not stock,
              f"unestablished stock names present: {stock}"),
        Check("slop.beat_words", not beats,
              f"beat words past {slop.BEAT_REPEAT_MAX}: {beats}"),
        Check("slop.not_x_but_y", not constructions,
              f"banned constructions present: {constructions}"),
        Check("slop.sentence_variance", s_ok, s_detail),
        Check("slop.paragraph_uniformity", p_ok, p_detail),
        Check("slop.em_dash_spacing", not slop.em_dash_adjacent(prose),
              "consecutive paragraphs both use an em dash"),
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend; $env:PYTHONPATH="src;.."; .venv\Scripts\python.exe -m pytest tests/test_eval_graders.py -v`

Expected: PASS (whole file)

- [ ] **Step 5: Commit**

```bash
git add evals/graders.py backend/tests/test_eval_graders.py
git commit -m "grade_slop assembles the nine checks"
```

---

### Task 7: The `natural-prose` case and its four recordings

**Files:**
- Modify: `evals/cases.py`
- Create: `evals/recordings/natural-prose.compliant.md`, `.slop.md`, `.flat.md`, `.terse.md`
- Test: `backend/tests/test_eval_graders.py`, `backend/tests/test_evals.py` (existing, runs the new case)

**Interfaces:**
- Consumes: `graders.grade_slop`.
- Produces: `build_natural_prose() -> dict` with keys `cid`, `sid`, `players`, `established`; `grade_natural_prose(ctx, output) -> list[Check]`; a `Case` appended to `CASES`.

- [ ] **Step 1: Write the failing test for the prompt-contract check**

No recording can fail `prompt.natural_prose`, because every recording for a case is graded against the same assembled prompt. It needs its own test. Append to `backend/tests/test_eval_graders.py`:

```python
def test_prompt_natural_prose_fails_when_the_section_is_absent():
    """The check that closes the content hole verify_templates structurally
    cannot: that harness keeps an independent section-order mirror, so a
    DELETED SECTIONS entry already fails there -- but it never pins template
    text, so an emptied template renders to nothing on both sides and passes."""
    messages = [{"role": "system", "content": "Nothing of the sort."}]
    checks = graders.grade_prompt_section(
        messages, "natural_prose", "scene/sections/natural_prose.j2")
    assert not checks[0].ok
    assert checks[0].name == "prompt.natural_prose"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend; $env:PYTHONPATH="src;.."; .venv\Scripts\python.exe -m pytest tests/test_eval_graders.py -k "prompt_natural_prose" -v`

Expected: PASS immediately — `grade_prompt_section` already exists and this asserts existing behaviour. This step is a characterization test that pins the behaviour the case depends on; if it fails, `grade_prompt_section` has changed and the case's premise is gone.

- [ ] **Step 3: Add the case builder and grader**

Append to `evals/cases.py`, before the `CASES` tuple:

```python
# ------------------------------------------------- case 6: natural prose

def build_natural_prose() -> dict:
    """A three-hander at the pier under `cinematic`, the widest preset.

    Three fixture properties are load-bearing:

    - `cinematic` rather than `terse`: rhythm statistics over three blocks are
      noise. Its 900 words are a target and its 7 blocks a maximum, not a
      promised shape, which is why slop.measurable exists rather than the
      preset's numbers being trusted as a sample size.
    - No prose style guide and no voice anchors. natural_prose.j2 says a style
      guide overrides its rhythm guidance and that a character's established
      voice wins where it conflicts; a fixture carrying either would be graded
      against rules the template disclaims.
    - Every name invented, none colliding with slop.STOCK_NAMES, and all of
      them passed to the grader in `established` -- the template's rule is that
      names already in the scene, cast or world are reproduced exactly.
    """
    wid, wroot, sera = _world_with_sera()
    pier = entities.create_entity(wroot, "locations", "Saltmarch Pier",
                                  "Fog-slick planks stacked with unlogged crates.",
                                  keys="pier, dock")
    rowan_card = characters.blank_card("Rowan")
    rowan_card["data"].update({
        "description": "A dock hand with a bad shoulder and a good memory.",
        "personality": "Slow to speak, slower to forget a slight."})
    rowan, _ = characters.create_character(wroot, "Rowan", "default", rowan_card)

    cid = campaigns.create_campaign("Saltmarch Nights", wid)
    campaigns.set_campaign_response(cid, {"response_preset": "cinematic"})
    croot = campaigns.campaign_root(cid)

    persona = pcs.blank_persona("Winifred")
    persona.update({"pronouns": "she/her", "summary": "A courier working off a debt.",
                    "description": "Quick, kind, unlucky."})
    pid, _ = pcs.create_pc(croot, "Winifred", [], persona=persona)

    sid = scenes.create_scene(cid, "The Pier at Dusk")
    appearances.appear(cid, sid, "characters", sera, "default", "npc")
    appearances.appear(cid, sid, "characters", rowan, "default", "npc")
    appearances.appear(cid, sid, "pcs", pid, "default", "player")
    scenes.set_location(cid, sid, pier)
    scenes.append_message(cid, sid, "user",
                          "I come down the steps and ask them both what the "
                          "manifest is missing.",
                          speaker="Winifred")

    players = frozenset(appearances.player_names(cid, sid))
    # The full established set the template's precedence rule requires: cast,
    # players, and every named record this fixture wrote -- the world, the
    # location, the campaign and the scene. Assembled from what was created
    # rather than scraped from record bodies. None of these tokens collides
    # with slop.STOCK_NAMES today, so an omission here would not show up in the
    # recordings; it is written out in full because the rule, not the fixture,
    # is what the check is meant to honour.
    established = slop.established_tokens(
        list(players) + _npc_names(cid, sid)
        + ["Saltmarch Pier", "Realm", "Saltmarch Nights", "The Pier at Dusk"])
    return {"cid": cid, "sid": sid, "players": players, "established": established}


def grade_natural_prose(ctx: dict, output: str) -> list[Check]:
    # The whole section, rendered from the template itself. This is the half a
    # template edit can break silently: verify_templates.py never pins template
    # text, so an emptied natural_prose.j2 passes there.
    return (
        graders.grade_prompt_section(ctx["messages"], "natural_prose",
                                     "scene/sections/natural_prose.j2")
        + graders.grade_slop(output, ctx["players"], ctx["established"],
                             prompts.render("scene/sections/natural_prose.j2")))
```

Add `from grimoire import prompts` and `from . import slop` to the imports at the top of `evals/cases.py`.

- [ ] **Step 4: Register the case**

Append to the `CASES` tuple in `evals/cases.py`:

```python
    Case(id="natural-prose",
         hypothesis="a reply contains none of the stock names or literal "
                    "banned phrases the natural-prose block lists, does not "
                    "repeat a single beat word past the cap or use the "
                    "enumerated not-X-but-Y forms, and does not flatten into "
                    "uniform sentence and paragraph length",
         build=build_natural_prose, prompt=_scene_prompt,
         grade=grade_natural_prose,
         recordings=(
             Recording(BASELINE),
             # Literally sloppy, rhythmically fine: so the four literal checks
             # cannot pass unnoticed behind a rhythm hit.
             Recording("slop", ("slop.phrases", "slop.stock_names",
                                "slop.beat_words", "slop.not_x_but_y")),
             # Rhythmically flat, literally clean: the reverse.
             Recording("flat", ("slop.sentence_variance",
                                "slop.paragraph_uniformity",
                                "slop.em_dash_spacing")),
             # A collapsed generation. Proves the vacuous-pass gate gates:
             # without slop.measurable this recording would score all green.
             Recording("terse", ("slop.measurable",)))),
```

- [ ] **Step 5: Write the four recordings**

**Every recording except `terse` must clear `MIN_SENTENCES` (12) and `MIN_PARAGRAPHS` (4) in its BODIES** — `split_reply` moves each `**Name:**` marker into the segment's `speaker` field, so marker lines do not count toward either floor. A recording below the floor fails `slop.measurable` on top of whatever it declared, and set equality rejects it.

Create `evals/recordings/natural-prose.compliant.md` — 16 body sentences over 9 paragraphs, sentence lengths from 1 to 33 words, no em dash anywhere:

```markdown
Rain.

It came in off the water the way it always did at this hour, slow at first and then all at once, and Winifred pulled her coat tighter and swore at nobody in particular.

**Winifred:** Twelve. The manifest says eight.

Seraphine Vale did not move. She had been standing at the rail since before the fog closed in, and she had the look of somebody who intended to be standing there long after it lifted.

**Seraphine Vale:** Then the manifest is wrong.

**Rowan:** Or somebody wrote it wrong on purpose, which is a different thing, and worth a good deal more to whoever paid for the difference.

He shifted the weight off his bad shoulder. The crates sat sheeted against the weather, three high. None of them had been opened.

**Winifred:** Which is it?

**Seraphine Vale:** Ask me on dry land.
```

Create `natural-prose.slop.md` — 14 body sentences over 7 paragraphs with genuinely varied lengths, so the rhythm checks pass and only the four literal families trip. The single em dash sits in a paragraph whose neighbours have none:

```markdown
The air thick with salt, Elara stood at the rail, a smile playing on her lips.

**Elara:** You came.

Her voice was barely above a whisper. She murmured it. Then she murmured something else, and murmured a third time when Winifred did not answer, and the fourth murmur was lost entirely in the wind coming off the black water below them both.

**Winifred:** I came.

It wasn't just a debt — it was a promise.

The ghost of a smile crossed her face, casting long shadows that were, in their way, a testament to how long she had waited for exactly this, one last time, on this pier, in this rain. She said nothing. She did not have to.

**Elara:** Then we understand each other.

She couldn't help but agree.
```

Create `natural-prose.flat.md` — 12 sentences over 6 paragraphs, every sentence within a word or two of every other, every paragraph the same length, and an em dash in each so consecutive paragraphs both carry one. Literally clean: no banned phrase, stock name, beat word or construction appears:

```markdown
The lamp was lit and the room was warm and the door was shut — tight. The chair was old and the rug was worn and the clock was slow — slower.

The water was black and the boards were wet and the rope was frayed — badly. The wind was cold and the fog was thick and the night was long — longer.

The crate was full and the seal was good and the mark was clear — clearer. The step was loose and the rail was low and the drop was far — farther.

The tide was high and the moon was thin and the watch was late — later.  The dock was long and the lane was dark and the gate was locked — firmly.

The coat was damp and the boot was split and the glove was lost — again. The bell was still and the gull was gone and the street was bare — barer.

The ink was dry and the page was full and the sum was short — shorter. The hour was late and the tale was old and the end was near — nearer.
```

Create `natural-prose.terse.md` — a collapsed generation, which without `slop.measurable` would score all green:

```markdown
**Seraphine Vale:** Mine.
```

- [ ] **Step 6: Run the case until each recording scores exactly its declared set**

Run: `backend\.venv\Scripts\python.exe evals\run.py --case natural-prose`

Expected: the baseline passes; each counterexample fails on **exactly** the checks declared for it. Set equality means "it happened to fail on something else" is also a failure. Iterate on the recording text — never on the declared set — until it matches.

- [ ] **Step 7: Run the full replay suite**

Run: `cd backend; $env:PYTHONPATH="src"; .venv\Scripts\python.exe -m pytest tests/test_evals.py -v`

Expected: PASS, six cases. `test_no_orphan_recordings` covers the four new files by construction.

- [ ] **Step 8: Commit**

```bash
git add evals/cases.py evals/recordings/natural-prose.*.md backend/tests/test_eval_graders.py
git commit -m "The natural-prose case, and four recordings that pin it"
```

---

### Task 8: README, baselines, and the full gate

**Files:**
- Modify: `evals/README.md`
- Modify: `lint-baselines/*.json` (only if a count moved)

- [ ] **Step 1: Add the hypothesis row**

In `evals/README.md`, add to the case table:

```markdown
| `natural-prose` | a reply contains none of the stock names or literal banned phrases the natural-prose block lists, does not repeat a single beat word past the cap or use the enumerated not-X-but-Y forms, and does not flatten into uniform sentence and paragraph length |
```

Change "it is five pass/fail questions" to "six" in the paragraph above it.

- [ ] **Step 2: Extend "What replay can and cannot catch"**

Append to that section:

```markdown
The `natural-prose` case is the sharpest example of the limit above, and is
worth stating plainly. Its output-side `slop.*` checks score a fixed recording,
so **nothing they report says whether the natural-prose block works** — that is
a live-behaviour question, and as with `turn-taking`, one live run is an
anecdote. What they hold offline is that the graders still work, that the
instructions they grade against are still in the template (`slop.list_current`,
a one-way guard: a phrase removed from the template fails loudly, a phrase
added is ungraded until mirrored in `evals/slop.py`), and that a collapsed
generation cannot score green (`slop.measurable`).

The graded set is also a strict subset of what the block asks for. The
template's semantic instructions — the rule of three, redundant adjective
pairs, explaining an emotion just shown, decorative metaphor, and the three
qualifier-dependent phrases — are not gradable by regex and are listed as
ungraded in the design spec. A green case means the graded subset held.
```

- [ ] **Step 3: Add the recordings to the recordings list**

Add `slop`, `flat` and `terse` to the parenthesised list of permanent hand-authored counterexamples.

- [ ] **Step 4: Run the full gate**

Run: `make check`

Expected: PASS. If `check-lint`, `check-mypy` or `check-eslint` reports a count mismatch for `evals/slop.py`, run `make baseline` and commit the regenerated file with the change — per CLAUDE.md an improvement fails the gate too.

- [ ] **Step 5: Commit**

```bash
git add evals/README.md lint-baselines
git commit -m "Document what the natural-prose case does and does not prove"
```

---

## Self-review notes

**Spec coverage.** Every check in the spec's inventory has a task: `prompt.natural_prose` and `slop.list_current` (Tasks 3, 7), `slop.measurable` (Task 5), the three list-driven and one construction check (Task 4), the three rhythm checks (Task 5), assembly (Task 6), fixture and recordings (Task 7), documentation (Task 8). The spec's deferred-items list is resolved in Tasks 3–5: regex text with apostrophe variants, beat inflections, word tokenization, quote-aware splitting, and the `LITERAL_PHRASES`/`JUDGMENT_ONLY` partition entry by entry.

**Known open item for the executor.** The `source` strings in Task 3 are transcribed from the template by hand. The parameterized drift test in Task 3 Step 4 is what catches a mismatch — most likely a straight apostrophe where the template has a curly one, or a hyphen where it has an em dash. Fix the `source`, never the template.

**What this does not deliver.** The thresholds are a regression guard calibrated against hand-authored fixtures, not a validated detector. Replacing `natural-prose.compliant.md` with real model output via `evals\run.py --live --record --case natural-prose` is the first thing worth doing after this lands, and the numbers in `VARIANCE_MIN`, `PARA_VARIANCE_MIN` and `BEAT_REPEAT_MAX` should be revisited against it.

## Fixture verification

The fixture texts in Tasks 5 and 7 were **measured**, not estimated, by running
the plan's own `sentences`/`paragraphs`/`coefficient_of_variation` logic over
them before the plan was committed. An earlier draft asserted counts that were
wrong in five places, which would have made every variance assertion
short-circuit on sample size and broken set equality on three of the four
recordings.

| text | sentences | paragraphs | sentence CV | paragraph CV | adjacent em dash |
|---|---:|---:|---:|---:|---|
| `_VARIED` | 15 | 8 | 1.04 | 0.72 | no |
| negative: dialogue-heavy | 14 | 10 | 1.21 | 1.06 | no |
| negative: deliberate fragments | 13 | 7 | 1.24 | 0.64 | no |
| negative: incantatory refrain | 15 | 6 | 1.19 | 0.55 | no |
| negative: terse action | 14 | 10 | 1.14 | 0.96 | no |
| `compliant.md` (bodies) | 13 | 9 | 1.04 | 0.87 | no |
| `slop.md` (bodies) | 12 | 8 | 1.07 | 1.04 | no |
| `flat.md` | 12 | 6 | 0.00 | 0.00 | **yes** |
| `terse.md` (bodies) | 1 | 1 | — | — | no |

Everything except `terse.md` clears `MIN_SENTENCES` (12) and `MIN_PARAGRAPHS`
(4), so no variance assertion is vacuous. `flat.md` reads 0.00 on both because
every one of its sentences is the same length by construction.

Detector scan over the recordings, same method: `slop.md` trips all four
literal families (8 phrases, `Elara`, `murmured`×4, the `wasn't just X — it was
Y` form) and nothing else; `compliant.md` and `flat.md` trip none of them.

If an executor edits any of these texts, re-run that measurement rather than
trusting the table.
