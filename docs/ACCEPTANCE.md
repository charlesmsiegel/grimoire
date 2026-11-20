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
`GRIMOIRE_TEST_CHROMIUM` may name an installed Chromium executable (a Chrome for Testing headless shell
works). Otherwise the
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
| Structural accessibility audit | `backend/tests/test_app_a11y_js.py` (ordinary CI, Node, no browser): every route, each Import tab, a source page, each screen's disclosures opened, the command palette and a modal, against names, labels, id references, roles and states, landmarks, one main and a top heading, and keyboard reachability of anything clickable (`backend/tests/js/a11y.mjs`) | Document tree only: no contrast, focus visibility, reflow, target size, Tab order or announcements |
| Emulated phone (touch) | A Pixel 7 profile (touch, mobile viewport, 2.625 pixel ratio): every control tapped through setup, model, card import, start, post/review/keep, cast panel picture via the image chooser, a touch-drag transcript scroll, a viewport shrunk for an on-screen keyboard with composer and Post still hit-testable, browser back to the same chat and draft, and a 24px target sweep of nine pages and the chat. The paired-sync journey's second client is the same touch phone: its setup, model, pairing, seat and offline play are taps; the desktop syncs and keeps a branch | Chromium emulation: not a physical phone, its keyboard, camera, file chooser, browser engine or zoom |

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

## Connection bursts and overload

The app retains eight active HTTP handlers. One accepted connection can wait in
the accept loop for up to one second before its handler starts; the existing
header/body deadlines then apply unchanged. This absorbs short navigation/module
bursts without spawning more handlers or an application queue. Persistent
saturation receives a fixed, generic HTTP 503 with connection close, sent with a
0.1-second write deadline. Delivery is best-effort if a peer disconnects or does
not read. Sustained overload cannot guarantee availability, but cannot create
unbounded waiting threads or disclose request/library data.

Event-controlled real-server tests verify burst admission, the eight-handler
cap, bounded overload/shutdown and permit recovery after failures. A supplemental
browser test exercises twelve Chats/back/reload cycles with draft and required
JavaScript transport assertions. It contains no sleeps or retries; it does not
replace the deterministic capacity regression or certify physical devices.

## What the emulated phone found

Walking the manual phone checklist in the touch profile found tap targets of 13 to 23 CSS px: the chat's turn
tools and **Add picture**, the header's **Chats** link, column sort headers, tag buttons, disclosure summaries
and the home page's "more" links. On a touch screen (`pointer: coarse`) standalone controls are now at least
32 px; links inside running text keep their line, which WCAG 2.5.8 allows. The page also asks Android Chrome to
shrink itself above the on-screen keyboard (`interactive-widget=resizes-content`) instead of drawing the
keyboard over the composer. The emulated keyboard is a smaller viewport, which is that behaviour; whether a real
keyboard does it is part of the phone check below.

## Manual release evidence still required

On an actual phone, with a synthetic library (never a private one), set up as [MOBILE](MOBILE.md) says. A 390px
desktop viewport or the emulated phone above is not evidence that this passed. Record the phone's model, its
Android or iOS version, the browser and its version, and the page zoom; then, using touch only:

1. **Open.** The phone's browser on the desktop's app (MOBILE's quickest check). The page fits the width with
   no sideways scroll, and **Menu** opens and closes the side menu.
2. **Chat.** Start a chat (**New chat**, a character, who you play, **Start chat**). Tap the box, type a line,
   tap **Post**. While the keyboard is up, the line being typed and **Post** stay visible above it. Review the
   take, tap **Keep this take**. Post three or four more, then drag the transcript up and down with a finger.
3. **Picture.** **Cast and details**, **Add picture** for the character: the phone offers its camera or files.
   Take or choose a picture; the character's avatar changes. Close the panel.
4. **Back.** Type a line without posting, tap **Chats** in the header, then the browser's back: the same chat
   comes back with the line still in the box.
5. **Targets.** Note any button, link or field that took more than one tap, was covered, or was too small.
6. **Sync.** With Termux on the phone and the two libraries paired (MOBILE's **Set up** and **Play**), tap
   **Sync** on the phone: the desktop's chats arrive. Open one, **Take your seat**, play a turn, **Sync** again;
   the desktop shows the turn.
7. **Offline fork.** Take the forward away (`adb reverse --remove tcp:8765`). Play one turn of the same chat on
   the phone and a different one on the desktop. Put the forward back, **Sync** on the phone: the chat says it
   forked. Keep one branch; the other is set aside, not lost. **Sync** again, and both devices show the kept
   branch.
8. **Resume.** Close the phone's browser tab and stop its `serve`, start it again and open the new address: the
   chat and its turns are still there.

For each step, record passed, or what happened instead (a screenshot helps). Step 6 onward needs Termux;
steps 1 to 5 need only a USB cable and `adb`. Reviewing a take needs a model on the device whose `serve` the
page belongs to ([MOBILE](MOBILE.md#answers-from-a-model)); without one, posting, seating, pictures and sync
still work.

On desktop with a real screen reader, record the reader/browser/OS versions.
Traverse landmarks and controls by keyboard, submit an invalid form, cancel and
complete a modal, review a take and a pending effect, navigate back to a draft,
and check announced labels, errors, live updates and focus. DOM roles alone do
not establish correct announcements. Neither manual gate has been certified by
this harness. Final Wave 7 acceptance also waits for integrated feature branches.
