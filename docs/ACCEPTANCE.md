# Offline browser acceptance

The executable Wave 6/7 browser gate is `backend/acceptance/`. It drives the
shipped Python-served V2 UI in Chromium with Playwright. It is separate from
`backend/tests/test_app_release_proof.py`, whose real-process HTTP proof is
supporting evidence, not a browser or accessibility result.

## Run

From the V2 checkout, using Python 3.11+:

```sh
python -m venv .venv
.venv/bin/python -m pip install -e './backend[dev]' 'playwright>=1.51,<2'
.venv/bin/python -m playwright install chromium
PYTHONPATH=backend/src:backend/tests:backend/acceptance .venv/bin/python -m pytest backend/acceptance -q
PYTHONPATH=backend/src .venv/bin/python -m pytest backend/tests/test_app_release_proof.py -q
PYTHONPATH=backend/src make check
```

Use the same interpreter for `make check` (activate the environment first).
On Windows use its `Scripts` executables and semicolons in `PYTHONPATH`.
`GRIMOIRE_TEST_CHROMIUM` may name an installed Chromium executable. Otherwise the
harness uses `chromium` on PATH, or Playwright's installed browser. Missing
Playwright or Chromium fails the explicit gate; it is not silently skipped.
The ordinary `make check` suite does not collect this optional browser directory.

Every run creates new synthetic home, V2 and V1 directories under pytest's
scratch directory. The existing synthetic V1 fixture is prepared before launch,
then checked for unchanged bytes at teardown. All product mutations after
launch use visible UI controls. API reads independently verify persistence,
request accounting, art bytes and canonical identities. The effect refusal test
corrupts only the fingerprint of a UI-originated request to exercise the real
server's stale-review refusal; it does not fabricate a successful response.

An explicit test-only child bootstrap blocks non-numeric/non-loopback backend
connections before importing the app. Browser requests are restricted to the
session's exact app origin. A local streaming model fixture records requests;
no hosted provider, key, private library or paid call is needed. Separate browser
contexts and app processes represent paired clients. They are not physical
phones. No screenshots, traces, tokens or generated libraries are committed.

## Coverage and integration dependencies

| Journey | Executable coverage | Boundary |
|---|---|---|
| Fresh setup | Keyboard folder check, actionable missing-device error, source creation, cancellation/focus restoration | No V1 default reuse |
| Models | Save/check local endpoint, no setup calls, request counts and provider-reported usage, changed token limit after restart | 112's full production role/context/budget work still needs integrated rerun |
| World-less play | Imported V3 card, manual persona, distinct NPC calls/voices, newcomer exclusion, keep/retry, process reopen | Synthetic voice separation, not human/live quality certification |
| Manual character play | No-greeting manually created character must answer | Native voice derives from own description/personality; creation supplies default speaking facets only when omitted |
| Review | Real effect proposal, cancel, stale fingerprint refusal, separate player/director keys, accepted-state readback | Supported action-backed relation effect; broader 113 production integrations still require their own tests |
| Navigation | Same chat through all/character/scenario filters; draft after back/reload | Canonical ID and one chat asserted |
| Cast picker | Delayed real metadata response, retained qualified selection, real duplicate-binding refusal, corrected retry | No fabricated successful writes, sleeps or automatic retry |
| Migration | Browser scan/preview/approval, art-loss warning, two-NPC play and process resume | Synthetic source snapshot must remain unchanged |
| Media/sync | UI upload, pairing, manual transfer, image bytes, two offline branches and preserved alternative | Reachable loopback clients only |
| Accessibility/responsiveness | Tab/Enter/Escape, focus return, alert text, 1280×900 and 390×844 layout, phone control geometry and cast panel | DOM/geometry evidence, not real screen-reader or physical-touch proof |

The audit base `e551c709f7e38a13ce85009502539d31bd2c67b8` failed the
manual-character journey: the library could not supply its required voice or
speaking facets. The integrated native-authoring fix derives bounded voice from
the character's own description/personality and defaults omitted creation traits;
it does not alter existing records or override explicit traits. The unchanged
manual-character browser test exercises that path without injected voices/facets.

The browser suite also exposed a cast-picker race: late chat metadata rebuilt
the selector and erased the pending choice. The page now retains the qualified
choice across redraws and real server refusals, and clears it after a successful
addition. Timing-controlled regression tests verify both success and correction
without weakening the ordinary two-voice/newcomer-privacy journey.

The suite deliberately retains unmet release contracts as ordinary failures.
Its existence does not close a roadmap checkmark. The 033 actor-kind, 047 claim
extraction, 112 inference integration and 113 canonical-state branches remain
separate owners; this suite does not repair or duplicate their feature work.
After those changes integrate, rerun this suite and the ordinary CI gates against
that exact integrated head before making a full acceptance claim.

## Manual release evidence still required

Use the same synthetic library and a local/offline provider. On an actual phone,
record device/OS/browser, viewport/zoom and input mode. Exercise setup, import,
chat composer/cast, take review, gallery and sync with touch; check scrolling,
keyboard occlusion, back navigation and target reachability. A 390px desktop
viewport is not evidence that this passed.

On desktop with a real screen reader, record the reader/browser/OS versions.
Traverse landmarks and controls by keyboard, submit an invalid form, cancel and
complete a modal, review a take and a pending effect, navigate back to a draft,
and check announced labels, errors, live updates and focus. DOM roles alone do
not establish correct announcements. Neither manual gate has been certified by
this harness. Final Wave 7 acceptance also waits for integrated feature branches.
