"""Guard: every `<name>.py` a source file names in prose actually exists.

Module docstrings across the store lean on "mirrors <module>.py" cross-references to
explain a shared pattern. When the named module gets renamed or never lands, the
pointer rots silently and a reader who greps for it finds nothing (issue #244:
chronicle.py and playstate.py both pointed at a `briefs.py` that was designed but
never built).

Scope: this resolves *basenames*, so it proves a named module exists somewhere in the
checkout — not that the reference points at the right one. Prose says "mirrors
dossiers.py" without a path, so a path-accurate check would demand prose we don't
write. Existence is the failure mode that actually bites a reader.

The search covers the whole repo, not just backend/: python that is deliberately NOT
shipped in the package lives at the repo root (evals/, scripts/), and store docstrings
legitimately point at it. Scoping this to backend/ would force those pointers to be
deleted or vagued down to "the eval suite", which is the rot this guard exists to
prevent, arrived at from the other direction.
"""

from __future__ import annotations

import re
from pathlib import Path

import grimoire

PKG = Path(grimoire.__file__).parent          # backend/src/grimoire
BACKEND = PKG.parents[1]                      # backend/
REPO = BACKEND.parent                         # the checkout root

# Trees that are not this repo's source: a sibling worktree would let a name resolve
# against a branch this checkout has never seen, and a vendored dependency would let
# almost any name resolve, which is the silently-green failure guarded against below.
_SKIP = {".venv", "node_modules", ".worktrees", ".git", "build", "dist"}

# "absorb.py", "scene_ids.py" -- module basenames as written in prose.
_REF = re.compile(r"\b([a-z_][a-z0-9_]*\.py)\b")


def _known_modules() -> set[str]:
    """Every .py basename in the checkout (package, scripts, tests, evals).

    Assumes BACKEND is the checkout's backend/. Imported from a non-editable install
    it would instead be the environment root, and rglob would sweep every third-party
    module into the set -- at which point nearly any name resolves and this test passes
    without testing anything. Fail loudly on that rather than silently green.
    """
    assert (BACKEND / "pyproject.toml").is_file(), (
        f"expected a source checkout at {BACKEND}; grimoire was imported from "
        f"{PKG}, so this guard cannot see the backend tree. Run pytest against the "
        f"checkout (see CLAUDE.md: PYTHONPATH=backend/src)."
    )
    # Matched against the path RELATIVE to REPO. Absolute parts would also match the
    # checkout's own location -- this repo is routinely worked on from
    # <repo>/.worktrees/<branch>, where every absolute path contains ".worktrees" and
    # an absolute-parts filter silently excludes the entire tree, failing every
    # reference in the codebase at once.
    return {p.name for p in REPO.rglob("*.py")
            if not _SKIP & set(p.relative_to(REPO).parts)}


def test_named_modules_exist():
    known = _known_modules()
    dangling: list[str] = []
    for path in sorted(PKG.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            for name in _REF.findall(line):
                if name not in known:
                    rel = path.relative_to(BACKEND).as_posix()
                    dangling.append(f"{rel}:{lineno} references {name}")
    assert not dangling, "references to non-existent modules:\n  " + "\n  ".join(dangling)
