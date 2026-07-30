"""`grimoire.store`'s public surface matches its frozen snapshot (#248's sibling).

`store_api_baseline.json` was captured from a real `grimoire.store` as
``{"all": sorted(store.__all__), "dir": sorted(n for n in dir(store) if not
n.startswith("_"))}`` -- the facade the ~338 call sites across the package do
``from grimoire.store import x`` against. A refactor that splits store modules
into subpackages is only safe if that facade does not drift: every name the
snapshot recorded has to still resolve, and re-exporting has to stay confined
to what the snapshot already listed.

Both keys are checked, not just ``__all__``: ``module_display`` is bound onto
the module today only as a side effect of an import elsewhere in the package,
so it is reachable via ``dir(store)`` but never listed in ``__all__``. A test
that only compared ``__all__`` would not notice it silently disappearing.

A failure here means one of two things, and they call for opposite responses:

- The split **leaked or dropped** a name -- a submodule now shadows a facade
  attribute, an ``__init__.py`` re-export was missed, or a rename broke a
  caller's ``from grimoire.store import x``. This is a bug in the split; fix
  the code, not this test.
- The facade **genuinely changed on purpose** -- a deliberate addition or
  removal, reviewed as such. Only then does ``store_api_baseline.json`` get
  regenerated, and the regeneration itself belongs in that same reviewed
  change, not folded into an unrelated commit to make this test stop failing.

Regenerating the snapshot to silence a failure defeats the entire point of
having it: the snapshot is the reference the live facade is judged against,
not a knob to bring the two back into agreement.
"""

from __future__ import annotations

import json
import pathlib

from grimoire import store

BASELINE = pathlib.Path(__file__).parent / "store_api_baseline.json"


def _live() -> dict[str, list[str]]:
    return {
        "all": sorted(store.__all__),
        "dir": sorted(n for n in dir(store) if not n.startswith("_")),
    }


def _baseline() -> dict[str, list[str]]:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def _diff(expected: list[str], actual: list[str]) -> str | None:
    expected_set, actual_set = set(expected), set(actual)
    missing = sorted(expected_set - actual_set)
    added = sorted(actual_set - expected_set)
    if not missing and not added:
        return None
    lines = []
    if missing:
        lines.append("  missing (in the baseline, gone from the live facade): "
                      + ", ".join(missing))
    if added:
        lines.append("  added (on the live facade, absent from the baseline): "
                      + ", ".join(added))
    return "\n".join(lines)


def test_the_facade_matches_its_frozen_snapshot():
    expected = _baseline()
    actual = _live()

    reports = []
    for key in ("all", "dir"):
        diff = _diff(expected[key], actual[key])
        if diff is not None:
            reports.append(f"{key!r}:\n{diff}")

    assert not reports, (
        "grimoire.store's public surface has drifted from "
        "backend/tests/store_api_baseline.json:\n\n"
        + "\n\n".join(reports)
        + "\n\nA missing name breaks callers that do "
          "`from grimoire.store import x`; an added one is usually an "
          "accidental leak from a new submodule. If the facade genuinely "
          "changed on purpose, update the baseline as a deliberate, "
          "reviewed part of that change -- do not regenerate it just to "
          "make this test pass.")
