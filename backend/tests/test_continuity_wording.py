"""§30: the words the continuity surfaces use, and the ones they never do.

The journal's relation words cover every link relation and read as a
sentence. No source a reader can see -- any non-test file under the backend
package or the frontend's `src/`, and the README -- makes a claim §30 (or
§3.3's "continuity disabled") avoids; the scan is the whole tree rather than a
list of capstone files, because a list would miss the next surface. Because it
is the whole tree, the avoid list holds the spec's phrases and their plain
rewordings and nothing else: one pin holds its shape to the spec, and two more
hold its reach, so a pattern that stops catching "AI detected a duplicate" or
starts catching "health score" fails by example. And each phrase §30 prefers
is held to the surface that shows it, so rewording that surface away from §30
fails by name ("Merged into" also appears in older files, so "somewhere in the
source" never would).
"""

from __future__ import annotations

import re
from pathlib import Path

from grimoire.store.continuity import effective, review

REPO = Path(__file__).resolve().parents[2]


def _sources() -> list[Path]:
    """Every non-test source file a reader's copy could come from."""
    out = sorted((REPO / "backend" / "src" / "grimoire").rglob("*.py"))
    front = REPO / "frontend" / "src"
    testkit = front / "testkit"
    for path in sorted([*front.rglob("*.ts"), *front.rglob("*.tsx")]):
        if path.name.endswith((".test.ts", ".test.tsx")) or testkit in path.parents:
            continue
        out.append(path)
    out.append(REPO / "README.md")
    return out


#: The files the avoid-list guard reads.
SOURCES: list[Path] = _sources()

#: §30's four avoided phrases (Global Constraints, verbatim).
SECTION_30_AVOIDED = {"AI detected duplicate", "Continuity score", "Broken campaign",
                      "Embedding required"}

#: Every phrase the spec tells a reader-facing surface never to use: §30's four,
#: and §3.3's description of a campaign without embeddings.
SPEC_AVOIDED = SECTION_30_AVOIDED | {"continuity disabled"}

#: What the avoid-list guard scans for: patterns, matched case-insensitively as
#: whole words, each mapped to the spec phrase whose claim it catches. A pattern
#: reaches the forms that make the same claim -- an article, a plural, a verb
#: ("AI detected a duplicate", "Duplicates detected", "Embeddings are required")
#: -- and no further. The scan is the whole app, not the continuity surfaces, so
#: a phrase the spec does not name ("health score") would fail an unrelated page,
#: such as a mechanics module's HP display, and has no pattern here.
AVOIDED: dict[str, str] = {
    # "AI detected duplicate", and the same certainty unattributed
    r"\b(?:ai[\s-]+)?detected\s+(?:(?:an?|the)\s+)?duplicates?\b": "AI detected duplicate",
    # the same certainty, reordered
    r"\bduplicates?\s+(?:(?:is|are|was|were|(?:has|have)\s+been)\s+)?detected\b":
        "AI detected duplicate",
    # with §2's "continuity health score"
    r"\bcontinuity\s+(?:health\s+)?scores?\b": "Continuity score",
    r"\bbroken\s+campaigns?\b": "Broken campaign",
    r"\bembeddings?\s+(?:(?:is|are)\s+)?required\b": "Embedding required",
    # §3.3: no embeddings is "Basic matching active", never this
    r"\bcontinuity\s+(?:(?:is|was)\s+)?disabled\b": "continuity disabled",
}

#: §30's preferred phrases, each held to the surface that shows it.
PREFERRED: dict[str, tuple[str, ...]] = {
    "frontend/src/components/continuity/labels.ts": (
        "Possible overlap", "May be finished", "Needs resolution review",
        "Basic matching active", "Semantic matching not configured", "Continuation of"),
    "frontend/src/components/continuity/ReviewedGroup.tsx": ("Merged into",),
    "frontend/src/components/review/ReviewPanel.tsx": (
        "Basic matching active", "Semantic matching not configured"),
    "frontend/src/components/SceneIdeaPicker.tsx": ("Claims to address",),
    "backend/src/grimoire/routes/todo.py": (
        "Possible overlap", "May be finished", "Needs resolution review"),
}

#: §30's eight preferred phrases (Global Constraints, verbatim).
SECTION_30_PREFERRED = {
    "Possible overlap", "May be finished", "Needs resolution review", "Basic matching active",
    "Semantic matching not configured", "Merged into", "Continuation of", "Claims to address",
}


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def test_relation_words_cover_every_relation():
    assert set(review.RELATION_WORDS) == set(effective.RELATIONS)
    for relation, words in review.RELATION_WORDS.items():
        assert "_" not in words, relation
    for relation in ("before", "on", "after", "by"):
        assert review.RELATION_WORDS[relation] == "is due " + relation
    assert review._relation_words("same_as") == "is linked to"


def test_the_avoid_list_is_the_spec_phrases_and_their_rewordings():
    """Every pattern answers to a phrase the spec avoids, and every such phrase has
    one, so the list can neither drop a phrase nor grow one the spec does not
    name. What each pattern still catches is the next test's job."""
    assert set(AVOIDED.values()) == SPEC_AVOIDED
    for source in SPEC_AVOIDED:
        assert _hits(source) == {source}, source


def _hits(text: str) -> set[str]:
    """The avoided phrases `text` makes, by the spec phrase each stands for."""
    return {source for pattern, source in AVOIDED.items()
            if re.search(pattern, text, re.IGNORECASE)}


#: Copy the scan must refuse, each with the spec phrase it makes. The
#: spec's own phrases, then forms that differ only by an article, a
#: number or a verb -- the same claim, so losing one is losing coverage.
MUST_CATCH: dict[str, str] = {
    "AI detected duplicate": "AI detected duplicate",
    "AI detected a duplicate": "AI detected duplicate",
    "AI-detected duplicates": "AI detected duplicate",
    "Detected duplicate": "AI detected duplicate",
    "Duplicate detected": "AI detected duplicate",
    "Duplicates detected": "AI detected duplicate",
    "2 duplicates were detected": "AI detected duplicate",
    "Continuity score": "Continuity score",
    "Continuity health score: 72": "Continuity score",
    "Broken campaign": "Broken campaign",
    "Broken campaigns": "Broken campaign",
    "Embedding required": "Embedding required",
    "Embeddings required": "Embedding required",
    "Embeddings are required": "Embedding required",
    "An embedding is required": "Embedding required",
    "Continuity disabled": "continuity disabled",
    "Continuity is disabled": "continuity disabled",
}

#: Plain copy for other pages that the whole-app scan must leave alone.
MUST_PASS = ("Health score", "HP (health score)", "Duplicate detection runs locally",
             "Basic matching active — semantic matching not configured",
             "Merged into Seraphine's oath")


def test_the_avoid_scan_catches_each_avoided_claim_in_its_plain_forms():
    """Coverage, not just shape: each example is caught, as the phrase it makes."""
    assert set(MUST_CATCH.values()) == SPEC_AVOIDED
    wrong = {text: _hits(text) for text, source in MUST_CATCH.items()
             if _hits(text) != {source}}
    assert wrong == {}


def test_the_avoid_scan_leaves_plain_copy_for_other_pages_alone():
    assert {text: _hits(text) for text in MUST_PASS if _hits(text)} == {}


def test_no_continuity_surface_uses_a_phrase_section_30_avoids():
    names = {_rel(p) for p in SOURCES}
    for folder in ("frontend/src/components/continuity/", "frontend/src/components/storyGraph/",
                   "frontend/src/components/review/", "backend/src/grimoire/store/continuity/"):
        assert any(n.startswith(folder) for n in names), folder
    for required in ("frontend/src/routes/embeddingsOn.ts",
                     "frontend/src/components/StoryPressure.tsx",
                     "backend/src/grimoire/store/suggest.py",
                     "backend/src/grimoire/routes/scenes.py", "README.md"):
        assert required in names, required
    assert not any(".test." in n or n.startswith("frontend/src/testkit/") for n in names)
    found = [(_rel(path), source) for path in SOURCES
             for source in sorted(_hits(path.read_text(encoding="utf-8")))]
    assert found == []


def _uses(text: str, phrase: str) -> bool:
    """`phrase` as whole words, case-insensitively: a plural heading ("Possible
    overlaps") is not the phrase, or rewording the singular would pass."""
    return re.search(rf"\b{re.escape(phrase)}\b", text, re.IGNORECASE) is not None


def test_section_30_preferred_phrases_are_used():
    assert {p for phrases in PREFERRED.values() for p in phrases} == SECTION_30_PREFERRED
    missing = [(name, phrase) for name, phrases in PREFERRED.items()
               for phrase in phrases
               if not _uses((REPO / name).read_text(encoding="utf-8"), phrase)]
    assert missing == []
