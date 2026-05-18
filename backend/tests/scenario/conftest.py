"""Per-directory fixtures + marker for L5 scenario tests (spec 17 §L5).

Every module in this directory is automatically tagged with the
``scenario`` pytest marker so the CI ``backend-scenario`` job picks
them up without each file having to remember.

L5 tests intentionally run pre-release rather than per-commit; the
companion CI job is gated to ``main`` pushes and manual dispatches.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Local marker module-level — kept for callers that import the conftest
# directly, but pytest does not propagate ``pytestmark`` from conftest to
# sibling test modules. The collection hook below is what actually tags
# every test in this directory with the ``scenario`` marker.
pytestmark = pytest.mark.scenario


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Tag every test under ``tests/scenario`` with the ``scenario`` marker.

    Saves each test module from having to remember to set ``pytestmark``
    locally and keeps the directory's CI contract (``pytest -m scenario``)
    self-enforcing.
    """
    scenario_dir = Path(__file__).resolve().parent
    for item in items:
        try:
            item_path = Path(item.fspath)
        except (AttributeError, TypeError):
            continue
        if scenario_dir in item_path.parents or item_path == scenario_dir:
            item.add_marker(pytest.mark.scenario)


# Path to the frozen-campaign snapshot directory defined in spec 17 §L4.
# §4 of the testing-design plan is a parallel workstream — until those
# snapshots are checked in, scenarios that need them skip cleanly.
FROZEN_CAMPAIGN_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "campaigns"


def frozen_snapshot_path(name: str) -> Path | None:
    """Return the path to a §4 frozen-campaign snapshot, or ``None``.

    ``name`` is the bare snapshot stem from spec 17 (e.g.
    ``"wod_london_session_47"``). Returns ``None`` if the fixture has
    not been checked in yet so the caller can ``pytest.skip`` with a
    clear reason instead of erroring on a missing file.
    """
    candidate = FROZEN_CAMPAIGN_DIR / f"{name}.sqlite"
    return candidate if candidate.is_file() else None


__all__ = ["FROZEN_CAMPAIGN_DIR", "frozen_snapshot_path"]
