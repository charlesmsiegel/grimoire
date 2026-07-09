# grimoire for Android

A thin Kotlin shell around the real grimoire: the APK packages
`backend/src` (via Chaquopy) and the freshly built `frontend/dist` (as assets),
runs the FastAPI app on `127.0.0.1:<random port>` inside the app process, and
shows it in a full-screen WebView. There is no Android copy of any grimoire
code — see `docs/android-architecture.md` for the full design.

## Building

Prerequisites on the build machine:

- Android Studio (AGP 8.5) with an Android SDK, platform 34
- JDK 17
- Node 18+ (`npm run build` for the frontend runs as a Gradle task)
- Python 3.x on PATH (Chaquopy's build-time `buildPython`; the 3.11 runtime
  that ships in the APK is downloaded by the plugin)

Then:

```
cd android
./gradlew :app:assembleDebug     # or open android/ in Android Studio
```

The build runs `npm run build` in `frontend/`, stages `dist/` and the repo's
`templates/` into APK assets, pip-installs the backend's base dependencies for
Android, and packages `backend/src` as Python source.

### If pip fails on `pydantic-core`

FastAPI pulls pydantic v2, whose core is a Rust wheel that may not be available
for Android in Chaquopy's repository. Two documented fallbacks
(`docs/android-architecture.md` §7, risk 1):

1. Build the wheel once with maturin against the Android NDK and add
   `options("--find-links", "wheels/")` to the `pip` block in
   `app/build.gradle.kts`.
2. Pin the pure-python line instead: `install("pydantic==1.10.*")` plus a
   FastAPI version that accepts it. The backend is v1/v2-agnostic — the only
   v2-specific API call is wrapped in `routes._dump`.

## Runtime layout on the device

| What | Where |
|---|---|
| Store (worlds, campaigns, config) | `<external app dir>/.grimoire` — visible over USB at `Android/data/app.grimoire/files/.grimoire` |
| Bootstrap pointer | `<external app dir>/.grimoire.json` |
| Extracted frontend + templates | `<internal files>/web/`, re-extracted per install/update |

The shell sets `HOME` (not `GRIMOIRE_HOME`), so the Storage-location page in
Configuration still works; pointing it at a shared folder is the Phase 3
synced-library flow (requires All-files access).

## First launch

Cold start shows a spinner for roughly the interpreter + import time (budget
≤2.5 s mid-range; measure per device class), then the regular grimoire UI.
Add the OpenRouter key under Configuration, exactly as on desktop.
