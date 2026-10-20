# Android

[Documentation](../README.md) · [Getting started](../guides/getting-started.md)

## What the APK contains

The Kotlin shell starts the shared FastAPI backend through Chaquopy and displays
the built React UI in a WebView. The build packages `backend/src`, frontend
assets and Jinja templates from this checkout. There is no Android fork of the
story or persistence code.

README files and `docs/` directories are excluded from Python source packaging,
template assets and source-stamp inputs. Runtime Markdown styles and mechanics
data are included; repository documentation is not an application input.

The embedded server listens on `127.0.0.1`. An installation retains its chosen
port when it can rebind it, preserving the WebView origin across launches. The
shell manages runtime startup, lifecycle and run notifications around that server.

## Build prerequisites

- GNU make, Node 24+ and installed frontend dependencies.
- JDK 17 and the Android SDK; `make android-bootstrap` prepares the supported
  per-user toolchain and writes `android/local.properties`.
- **Python 3.12** for the build machine, matching the Chaquopy runtime minor
  version. This is stricter than the desktop application's Python floor.

Current packaging uses compile/target SDK 34, minimum SDK 26 and `arm64-v8a`.
These values are build configuration, not a promise of testing on every device.
See [app/build.gradle.kts](../../android/app/build.gradle.kts).

## Build and install

From the repository root, after the normal dependency installation:

```sh
make android-bootstrap
make apk
make apk-install
```

| Target | Result |
|---|---|
| `apk` | Debug APK at `build/grimoire-debug.apk` |
| `apk-install` | Build and install to a connected adb device |
| `apk-release` | Unsigned release APK at `build/grimoire-release-unsigned.apk` |
| `check-apk` | Frontend build followed by the APK verification build |

GNU make can run from PowerShell, cmd or Git Bash; the Windows recipes select
cmd explicitly. Gradle's platform launchers are `android/gradlew` and
`android\gradlew.bat` if using the underlying project directly.

The base dependency set stays Android-installable. The APK currently pins
pydantic 1.10 and a compatible FastAPI range, while desktop-only dependencies
live in optional extras. `make check-pydantic1` tests that dependency boundary;
it does not build an APK. `make check-apk` is separate from `make check` because
it needs the Android toolchain.

## Device storage

| Data | Default location |
|---|---|
| Library | `<external app files>/.grimoire` |
| Bootstrap pointer | `<external app files>/.grimoire.json` |
| Extracted web assets/templates | `<internal app files>/web/` |

The external app files directory is normally visible over USB at
`Android/data/app.grimoire/files/`. The entrypoint sets `HOME` to app storage and
does not permanently force `GRIMOIRE_HOME`, so the storage-location setting can
still choose another supported path. Access to a directory remains subject to
Android permissions and the device's storage rules.

Use the app's backup/export workflows before uninstalling or clearing app data.
For cable synchronization, `make sync-phone` reports and `make sync-phone-apply`
copies. Review the report and work on one device at a time.

## Using a model server on another machine

Create an OpenAI-compatible provider using the computer's reachable network
address. `127.0.0.1` on Android names the phone, not your desktop. Make the model
server reachable on its network interface and use the API prefix it exposes.
Assign the provider/model on the Models page as on desktop.

The APK has no desktop Claude subscription SDK integration. For a slow model,
inspect the timeout controls and run status before sending duplicate turns.
The foreground service can keep an active run alive while the UI is backgrounded;
this does not defeat force-stop or other operating-system termination.

## Shared-code constraints

Backend filesystem access must use its resolvers: library paths, templates,
built UI and built-in packs may be extracted or packaged at different locations.
Do not resolve application resources by assuming a desktop checkout exists.
Keep dependency and pydantic changes compatible with the APK's configured set.

Implementation:
[Android entrypoint](../../android/app/src/main/python/android_entry.py),
[runtime](../../android/app/src/main/java/app/grimoire/ServerRuntime.kt),
[service](../../android/app/src/main/java/app/grimoire/ServerService.kt),
[activity](../../android/app/src/main/java/app/grimoire/MainActivity.kt),
[manifest](../../android/app/src/main/AndroidManifest.xml) and
[build targets](../../Makefile).
