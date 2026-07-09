# grimoire on Android — architecture

**Decision: ship Android as a thin native shell around the exact same code that runs on PC.**
A small Kotlin app embeds the Python backend with [Chaquopy](https://chaquo.com/chaquopy/),
starts the existing FastAPI app on `127.0.0.1:<random port>`, and renders the existing
React frontend in a full-screen WebView pointed at that server. The markdown/JSON store
lives on the device's filesystem, resolved by the same `store.paths` home/pointer
mechanism the backend already honors. LLM calls go from the device straight to OpenRouter, exactly as
they do today. No server, no new business-logic implementation, no fork.

This document records why, what the shell looks like, the changes required in the
existing codebase (small), the risks, and a phased plan.

---

## 1. Constraints

- **All functionality must be preserved.** The backend is ~10k lines of Python
  (worlds, campaigns, characters + versions, PCs/personas, greetings, entities,
  scenes with streaming chat, lorebooks, chub import, image/thumbnail handling,
  three calendar systems, tagging, context assembly, suggestions, absorb, sync,
  migrations…) behind ~150 API endpoints. Reimplementing this on another stack is
  not a porting task, it is a second product.
- **One team maintains both platforms.** Every feature added on PC must appear on
  Android without doing the work twice. The architecture must make divergence
  structurally difficult, not merely discouraged.
- **No server.** Everything runs on-device except HTTPS calls to OpenRouter (and
  chub.ai for character imports).
- **The store must remain the plain markdown/JSON tree** so a synced folder can
  continue to share one library across devices, now including phones.

## 2. Why this codebase makes the embedded approach cheap

The audit found grimoire is unusually well shaped for running unmodified on Android:

1. **The backend is already the whole app.** `main.py` serves the built SPA via
   `StaticFiles` and mounts the API under `/api`. On desktop the browser talks to
   `127.0.0.1:8000`; on Android a WebView talks to `127.0.0.1:<port>`. Same origin,
   same code, no CORS changes.
2. **All state is on disk, resolved live per request.** `store.paths.home()` checks
   `GRIMOIRE_HOME` on every call. The Android shell sets one env var and the entire
   store works. There is no database, no connection pool, no in-memory session state —
   so Android process death costs nothing: relaunch re-runs `create_app()` in ~a second
   and every record is where it was.
3. **The frontend is a pure static SPA** using relative-path `fetch` (`api/client.ts`)
   and fetch-streaming SSE parsing (`api/stream.ts`). Chromium-based WebViews support
   both. Zero frontend API changes.
4. **The LLM client is plain `httpx` → OpenRouter** with its own certifi-backed TLS
   context (`openrouter.py`). Outbound HTTPS from an Android app is unrestricted
   (one manifest line: `INTERNET` permission).
5. **The one native-dependency hazard already has a fallback in the code.**
   `count_tokens` (`store/context.py:439`) wraps tiktoken in `try/except` and degrades
   to `len(text) // 4`. If tiktoken never loads on Android, context budgeting still works.

## 3. Options considered

### A. Embedded Python shell (Chaquopy + WebView) — **chosen**
Same Python, same React bundle, one thin Kotlin layer (~500 lines, written once,
rarely touched). Maintenance cost of a new feature: **zero Android-specific work**
unless it touches device capabilities. The trade-offs are APK size (~40–60 MB with
the Python runtime, arm64), ~1–2 s cold start for the interpreter + imports, and a
wheel-availability question for two Rust/C dependencies (§7).

### B. Capacitor/TS port — rejected
Capacitor gives a WebView shell but no Python and no Node — the backend's 10k lines
would have to be rewritten in TypeScript running inside the WebView against a
filesystem plugin. That is a full rewrite *plus* permanent dual maintenance with the
Python PC version, unless the PC version also migrates to the TS core (an even bigger
project). Fails the "maintain alongside" constraint.

### C. Native Kotlin app — rejected
Best-feeling result, worst maintenance story: a second UI *and* a second backend,
diverging forever. Fails both core constraints.

### D. python-for-android / BeeWare — rejected in favor of A
Same "embed Python" idea, but Chaquopy's Gradle plugin integration (build-time pip
install, `srcDirs` pointing at existing source, first-class Android Studio support,
MIT-licensed since 2022) is significantly more maintainable than p4a recipes.
The WebView/UI side is plain Android SDK either way.

### E. Termux — dev tool only
`pip install` + `uvicorn` in Termux against a phone browser is a useful smoke test
of the backend on ARM Linux **before any Android work starts** (recommended as part
of the Phase 0 spike), but it is not a shippable app.

## 4. Architecture of the shell

```
android/                          ← new top-level Gradle project
  app/
    src/main/java/…/
      MainActivity.kt             ← full-screen WebView, back ↔ SPA history
      ServerService.kt            ← foreground Service owning the Python server
      PyServer.kt                 ← Chaquopy bootstrap: start server, report port
    src/main/python/
      android_entry.py            ← ~40 lines: set env vars, run uvicorn
    src/main/assets/
      frontend/                   ← Vite dist, copied by a Gradle task
      templates/                  ← repo templates/, copied by a Gradle task
    build.gradle                  ← chaquopy { srcDirs += "../../backend/src" ; pip { … } }
```

### Process model

```
┌─ Android app process ──────────────────────────────────────────┐
│  MainActivity ── WebView ── http://127.0.0.1:<port>/           │
│                                   │                            │
│  ServerService (foreground while a stream is live)             │
│    └─ Python (Chaquopy, same process)                          │
│         └─ uvicorn (pure-python, single worker)                │
│              └─ create_app()  ← identical FastAPI app          │
│                   ├─ StaticFiles → extracted frontend assets   │
│                   ├─ /api/*     → routes.py, store/*           │
│                   └─ httpx ──HTTPS──► openrouter.ai / chub.ai  │
│  HOME → app storage, so the store root resolves on-device      │
└────────────────────────────────────────────────────────────────┘
```

- **`android_entry.py`** sets `HOME` (store root, §Storage), `GRIMOIRE_TEMPLATES`
  and `GRIMOIRE_DIST` (§6), binds uvicorn to `127.0.0.1:0`, and reports the
  OS-assigned port back to Kotlin via a callback so the WebView knows what to load. Binding to loopback only
  means nothing on the network can reach the server; a per-boot random bearer token
  appended by the shell and checked by a middleware is a cheap hardening step if we
  ever care about other apps on the same device probing localhost.
- **Single origin.** WebView loads the SPA *from the Python server*, exactly like
  desktop. We deliberately do not use `WebViewAssetLoader` for the static files —
  splitting origins between assets and API would reintroduce CORS and cookie/URL
  subtleties for zero benefit.
- **Foreground service during generation.** Android kills backgrounded processes
  freely. A scene stream in flight promotes the service to foreground (with the
  standard notification) until the reply is persisted by `_persist_reply`, then demotes.
  At every other moment we simply accept process death — the store's file-per-record
  design makes restart lossless.
- **Back button / predictive back** maps to SPA history (`WebView.canGoBack()`),
  falling through to app exit at the history root. Keyboard insets handled with
  `windowSoftInputMode=adjustResize` + the standard edge-to-edge inset listener so
  the chat composer stays visible.

### Storage and sync

Two modes, mirroring the existing "Storage location" config page:

1. **Default (zero-permission):** the shell points `HOME` — not `GRIMOIRE_HOME`,
   which would permanently override the settings page — at
   `getExternalFilesDir(null)`, so the store lands at
   `/storage/emulated/0/Android/data/<pkg>/files/.grimoire` and the bootstrap
   pointer beside it. Private-ish, backed up by nothing external, visible over
   USB. Right default for a self-contained phone library, and the whole
   pointer/env/default resolution in `store/paths.py` keeps working.
2. **Synced-folder mode (opt-in):** the user grants **All files access**
   (`MANAGE_EXTERNAL_STORAGE`) and points the store at a real POSIX path such as
   `/storage/emulated/0/Documents/grimoire` — the same folder a sync agent
   (Syncthing fork, FolderSync + Drive/Dropbox, etc.) replicates against the PC's
   synced folder. This is the only mechanism on modern Android that yields plain
   POSIX paths for a user-chosen, third-party-accessible directory, which the Python
   `pathlib` store requires. SAF trees are not POSIX-visible and are therefore not
   usable without rewriting the entire store's I/O — rejected.

   *Play Store note:* `MANAGE_EXTERNAL_STORAGE` triggers a policy review. For
   personal/sideloaded use this is a non-issue; if we ever publish, the permission is
   justifiable ("user-configurable document library"), and default mode works without it.

The bootstrap pointer (`~/.grimoire.json`) still works — `Path.home()` under Chaquopy
resolves inside the app sandbox — so the existing pointer/env/default resolution
order in `store/paths.py` runs unchanged. The Configuration page's data-dir editor
functions as-is; Phase 3 adds a native folder-picker affordance.

**Sync-conflict posture:** unchanged from PC. The store is file-per-record, so
folder-sync conflicts are rare and record-scoped; conflict-copy files show up as
extra records rather than corrupting the library. Same guarantees as two PCs
sharing a folder today — phones add no new failure mode.

### LLM calls

Unchanged. `OpenRouterClient` builds its own SSL context from certifi (already in the
dependency closure), so it does not depend on Android's cert store quirks. Streaming
uses `httpx` async line iteration; uvicorn relays it as SSE to the WebView; the
existing `parseSSEChunk` consumes it. The OpenRouter key continues to live in
`config.md` inside the store — meaning synced-folder users get their key on the phone
automatically, same trust model as today.

## 5. Maintenance model — how we keep the platforms from diverging

The Android app has **no copies** of backend or frontend code:

- `chaquopy.srcDirs` includes `../../backend/src` — the APK packages the *working-tree*
  Python sources at build time.
- A Gradle task (`:app:buildFrontend`) shells out to `npm --prefix ../../frontend run build`
  and copies `frontend/dist` + `templates/` into assets before `mergeAssets`.
- Version skew is impossible: an APK is always built from one git revision of the
  whole monorepo.

CI gets one new job: assemble a debug APK (and run the ~40-line `android_entry.py`'s
unit test). The existing pytest and vitest suites remain the correctness gate for both
platforms, because both platforms run that exact code.

**Rules of the road** (add to `CLAUDE.md` when the app lands):
- Backend/frontend changes must not assume a writable repo directory, a desktop
  browser, or `~` outside the store — everything filesystem goes through
  `store.paths` / the env overrides.
- New frontend layout work must satisfy the narrow-viewport budget (§ Phase 2).

## 6. Changes required in the existing codebase

Deliberately tiny; each also makes the desktop build more relocatable.
Items 1, 2 and 5 (plus the `routes._dump` shim from §7 risk 1) shipped together
with the `android/` scaffold:

1. **`prompts.py`:** `TEMPLATES_DIR` is `Path(__file__)…/templates` (repo-relative).
   Honor a `GRIMOIRE_TEMPLATES` env var first. (2 lines + test.) ✅
2. **`main.py`:** `DIST` likewise repo-relative. Honor `GRIMOIRE_DIST`. (2 lines + test.) ✅
3. **`main.py`:** keep `migrate_scene_ids()` in lifespan — it already runs against
   `home()`, nothing to change; listed here only as verified.
4. **Frontend responsive pass (Phase 2):** the two-pane `.editor` pattern
   (sticky `.editor-list` rail + `.editor-body`) collapses on narrow viewports:
   rail becomes the full screen, opening a record pushes the detail view, back
   returns to the rail. This is a CSS/media-query + small state change in the shared
   frontend — it also fixes narrow desktop windows, so it is *not* Android-only debt.
   `SceneView` chat is already a single column and mostly needs touch-target and
   composer-inset polish.
5. **`pyproject.toml` extras split:** `uvicorn[standard]` and `tiktoken` moved
   to a `desktop` extra; Android installs plain `uvicorn` (pure-python h11
   worker — uvloop/httptools don't build there and a single local client
   doesn't need them) and skips tiktoken (heuristic fallback). ✅

Nothing in `routes.py` or `store/` changes.

## 7. Risks, with mitigations

| # | Risk | Severity | Mitigation |
|---|------|----------|------------|
| 1 | **`pydantic-core` wheel on Android.** `fastapi>=0.110` → pydantic v2 → Rust `pydantic-core`. Chaquopy's package repo may not carry it. | **Medium — fallback proven** (still the Phase 0 device check) | In order: (a) check Chaquopy's repo / recent releases; (b) build the wheel ourselves once per pydantic upgrade with maturin + Android NDK (`aarch64-linux-android`) — pydantic-core is a clean maturin build, and Chaquopy accepts local wheel dirs via `pip { options "--find-links", … }`; (c) pin the pure-python pydantic 1.10 line — FastAPI still dual-supports v1. The codebase was audited for this: ~50 request models that are plain typed fields (no `Field`, validators, or `ConfigDict`), and the only v2-specific API (`model_dump()`, 4 call sites) is wrapped in a v1/v2-agnostic `routes._dump()` helper — so (c) is an install-time pin, not a code change. **Verified:** the full 738-test backend suite passes under pydantic 1.10 / FastAPI 0.115 / plain uvicorn / no tiktoken — i.e. under the exact Android dependency set. Dataclasses are *not* a fallback: the pydantic dependency is structural to FastAPI's request parsing, not to our models. |
| 2 | **`tiktoken` wheel (Rust).** | Low | Ship without it — `count_tokens` already falls back to `len//4` (context.py:439). Optionally build the wheel later (same maturin path) for exact budgeting. |
| 3 | **`Pillow` wheel (C).** | Low | In Chaquopy's official repo. Thumbnails (`store/thumbs.py`) work. |
| 4 | Remaining deps (`httpx`, `jinja2`, `holidays`, `pyluach`, `certifi`, `python-multipart`, `uvicorn` sans extras) | None | Pure Python. |
| 5 | Cold start: interpreter + FastAPI import on mid-range hardware. | Medium | Splash screen until the port callback fires; lazy imports already common in the codebase (tiktoken, jinja2). Budget: ≤2.5 s cold on a mid-range device; measure in Phase 0. |
| 6 | Android kills the process mid-stream → reply lost. | Medium | Foreground service during generation (§4); store design makes every other kill free. |
| 7 | WebView version spread on old devices. | Low | `minSdk 26+`; WebView is auto-updated via Play on effectively all such devices. Fetch-streaming has been in Chromium for years. |
| 8 | Desktop UI on a 6″ screen unusable without work. | Certain, bounded | Phase 2 responsive pass on the shared frontend (§6.4). |
| 9 | APK size (~40–60 MB, arm64-only). | Cosmetic | Ship `arm64-v8a` only (covers ~all 2026 devices); add ABIs only if someone asks. |

## 8. Phased plan

**Phase 0 — spike (the go/no-go gate).**
Termux smoke test of the backend on ARM (hours), then a throwaway Chaquopy app that
`pip`-installs the real dependency set, runs `create_app()` under uvicorn, and serves
one API call + one streamed completion to a WebView. Exit criteria: dependency story
resolved (risk 1 has a working answer), cold start measured, streaming verified.
*Everything else in this document is low-risk plumbing; this phase is where the
architecture could be falsified.*

**Phase 1 — functional shell.**
`android/` project as in §4: activity + service + entry script, assets pipeline,
default storage mode, back-button and keyboard handling, the two env-var overrides
(§6.1–2). Result: the complete grimoire feature set on a phone, with a desktop-shaped
UI. CI builds the APK.

**Phase 2 — mobile-fit frontend (shared).**
Responsive collapse of the list/detail pattern, touch targets, safe-area/composer
insets, viewport meta. All in `frontend/`, all benefiting desktop narrow windows,
all covered by vitest per the existing pattern conventions.

**Phase 3 — device integration.**
Synced-folder mode (All-files-access flow + native folder picker feeding the existing
data-dir API), foreground-service polish, share/OPEN intents for character-card
PNG/JSON import (feeding the existing `/characters/import` endpoint), optional
tiktoken wheel, optional localhost bearer-token hardening.

## 9. What we are explicitly not doing

- No server component, no accounts, no cloud store.
- No second implementation of any business logic, in any language.
- No SAF-based store I/O (incompatible with the POSIX-path store; §4 Storage).
- No iOS commitment implied — but it is worth noting the same strategy does not
  transfer (no Chaquopy equivalent with this maintenance profile); iOS would be a
  separate decision.
