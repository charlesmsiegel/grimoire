# PR #554 VN reader screenshots

Captured on 2026-10-23 from the completed VN implementation in this commit.
These are direct, unedited Chromium viewport captures at device scale 1.

| Capture | Viewport |
|---|---|
| [Desktop reader](vn-desktop.png) | 1440 × 1000 |
| [Mobile reader](vn-mobile.png) | 390 × 1100 |
| [Presentation controls](vn-presentation-controls.png) | 1440 × 1100 |

The isolated library uses only synthetic names and story text from
[the documentation fixture](../../../backend/tests/fixtures/documentation/library.json).
The character silhouettes and Counting House backdrop are procedurally generated
fixture art, stored through the application asset API. They demonstrate sprite
placement, speaker highlighting and background rendering; they are not real
library assets or model-generated product output.

The session followed the [isolated capture procedure](../README.md), using fresh
pytest storage, backend port 8209, Vite port 5209 and a fresh browser profile.
The empty library was verified before seeding. Historical checkpoints were
created for the fixture posts. A cassette-backed fake supplied the model
dependency, and provider endpoints were redirected to loopback. No hosted model
calls were made. No DOM replacement, image editing or redaction was used.

The reader is on the second post, with Seraphine speaking. The controls capture
shows per-character outfit, expression and visibility options and the location
background setting. Each image was visually inspected; the browser reported no
page errors or failed requests, and mobile content had no horizontal overflow.
Temporary scripts, browser profiles, stores and logs remain outside the public tree.
