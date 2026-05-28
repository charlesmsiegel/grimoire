"""Top-level pytest configuration for the backend test tree.

Wires the ``--record`` CLI flag for record/replay-aware golden tests
(spec 17 §6). When ``--record`` is passed, the :func:`golden_llm`
fixture returns a :class:`RecordReplayLLM` in RECORD mode pointing
at a real gateway (supplied per-test). The default REPLAY mode reads
fixtures checked into ``backend/tests/fixtures/llm/by_hash/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.testing.record_replay import RecordReplayLLM, ReplayMode

# Directory that holds checked-in LLM fixtures. RecordReplayLLM appends
# ``llm/by_hash/`` to whatever fixture_dir we pass, so we hand it the
# parent ``backend/tests/fixtures/`` directory.
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--record",
        action="store_true",
        default=False,
        help="Record real LLM responses for tests using the golden_llm fixture.",
    )


@pytest.fixture
def golden_llm(request: pytest.FixtureRequest) -> RecordReplayLLM:
    """Return a :class:`RecordReplayLLM` for golden-path tests.

    The mode is decided by the ``--record`` CLI flag: REPLAY (default,
    used in CI) reads checked-in fixtures; RECORD writes new ones and
    requires a real gateway (tests in RECORD mode must construct one
    and attach it via ``RecordReplayLLM(..., real_gateway=...)`` — this
    fixture intentionally does *not* materialise a real provider so
    REPLAY-mode runs stay hermetic).
    """
    recording = bool(request.config.getoption("--record"))
    mode = ReplayMode.RECORD if recording else ReplayMode.REPLAY
    if mode is ReplayMode.RECORD:
        pytest.skip(
            "golden_llm fixture in RECORD mode needs a per-test real gateway; "
            "construct RecordReplayLLM(mode=RECORD, real_gateway=...) explicitly."
        )
    return RecordReplayLLM(FIXTURE_DIR, mode=mode)


@pytest.fixture(autouse=True)
def _no_bundled_plugins(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop the app lifespan from loading real bundled plugins.

    Booting the app (API route tests, scenario harness) runs
    ``PluginsService.rescan()``, which imports every bundled plugin, runs
    conformance, and fires network health probes (10s timeouts) — ~18s per
    boot, the single largest cost in the suite. No route/scenario test needs
    the real plugins.

    The dedicated plugin suites (``tests/plugins``, ``tests/bundled_plugins``)
    drive discovery with explicit bundled roots rather than through
    ``_default_bundled_root()``, so they are unaffected. End-to-end
    ``tests/scenario`` flows do exercise the real bundled plugins through the
    app, so they keep them too.
    """
    if request.path.parent.name in {"plugins", "bundled_plugins", "scenario"}:
        return
    from grimoire.plugins import config as plugins_config

    monkeypatch.setattr(plugins_config, "_default_bundled_root", lambda: None)
