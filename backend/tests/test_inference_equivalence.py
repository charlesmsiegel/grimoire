"""Today's connection routing, sampling and fallback resolution, held fixed.

`fixtures/inference_baseline.json` was generated ONCE, from the tree as it stood
before the inference resolver refactor (`python -m tests.inference_baseline
--write`), and **is never regenerated after the commit that introduced it**: the
refactor is behaviour-neutral, so a failure here means the new resolver answers
differently from the code it replaced, and the fix is in the code. What each
state contains and what is observed is in `tests/inference_baseline.py`.
"""

from __future__ import annotations

import json

import pytest

from . import inference_baseline as baseline

BASELINE = json.loads(baseline.FIXTURE.read_text(encoding="utf-8"))


def test_every_state_has_a_recorded_baseline():
    assert sorted(BASELINE) == sorted(baseline.STATES)


@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_resolution_matches_the_baseline(state, tmp_path):
    with baseline.client_at(tmp_path) as client:
        ctx = baseline.STATES[state](client)
        assert baseline.observe(client, ctx) == BASELINE[state]
