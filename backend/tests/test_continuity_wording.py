"""§30: the words the continuity surfaces use, and the ones they never do.

Three pins. The journal's relation words cover every link relation and read as
a sentence. No source a reader can see -- any non-test file under the backend
package or the frontend's `src/`, and the README -- uses a phrase §30 avoids;
the scan is the whole tree rather than a list of capstone files, because a list
would miss the next surface. And each phrase §30 prefers is held to the surface
that shows it, so rewording that surface away from §30 fails by name ("Merged
into" also appears in older files, so "somewhere in the source" never would).
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

#: §30's avoided phrases, lower-cased, with the forms a rewording would reach.
AVOIDED = ("ai detected", "detected duplicate", "duplicate detected", "continuity score",
           "health score", "broken campaign", "embedding required", "embeddings required",
           "continuity disabled")

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
    found = [(_rel(path), phrase) for path in SOURCES
             for phrase in AVOIDED if phrase in path.read_text(encoding="utf-8").lower()]
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
