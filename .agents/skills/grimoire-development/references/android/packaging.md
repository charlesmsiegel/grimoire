Source: [android-architecture.md](../../../../../docs/android-architecture.md), [build.gradle.kts](../../../../../android/app/build.gradle.kts), [test_android_manifest.py](../../../../../backend/tests/test_android_manifest.py).

## Android (`android/`)

The Android app is a thin Kotlin/WebView shell that packages `backend/src` and
the built frontend **verbatim** (Chaquopy + APK assets) — never copy grimoire
code into `android/`. Rules that keep the platforms in lockstep
(docs/android-architecture.md):

- Backend code must not assume a repo checkout layout or a desktop `~`:
  filesystem access goes through `store.paths`, `prompts.templates_dir()`
  (`GRIMOIRE_TEMPLATES`) and `main.dist_dir()` (`GRIMOIRE_DIST`).
- `pyproject.toml` **base** deps must stay Android-installable (pure python or
  Chaquopy-wheel'd); compiled desktop-only deps go in the `desktop` extra, and
  the pip block in `android/app/build.gradle.kts` mirrors the base list.
- pydantic usage stays v1/v2-agnostic: plain `BaseModel` fields only, dump via
  `routes.common._dump` (no `model_dump()`, `Field`, validators, `ConfigDict`).
- The **manifest and resources are read by `backend/tests/test_android_manifest.py`**,
  which parses every XML under `android/app/src/main` and checks the service's
  `foregroundServiceType` against the declared permissions. `make check-apk` is
  the real build and it is outside `make check` (it needs `android-bootstrap`
  first), so without this nothing in the ordinary gate opens these files at all
  — an XML comment containing `--` is illegal, and one line of prose failed
  `processDebugMainManifest` on CI with nothing local to say so.

Build: `make android-bootstrap` (once per machine — JDK 17, Android SDK,
licenses, `android/local.properties`), then `make apk` (debug) /
`make apk-release`. On Windows make works from PowerShell, cmd, or Git Bash
(recipes are pinned to cmd.exe).
