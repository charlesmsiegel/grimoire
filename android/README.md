# Grimoire for Android

The APK runs the shared Python backend and React interface inside a Kotlin shell.

See the [Android reference](../docs/reference/android.md) for build prerequisites,
APK targets, device storage, model servers and packaging constraints. For normal
application use, start with [Getting started](../docs/guides/getting-started.md).

From the repository root, after installing the desktop development dependencies
and selecting Python 3.12 for the Android build:

```sh
make android-bootstrap
make apk
make apk-install
```

The debug APK is staged at `build/grimoire-debug.apk`. The Android build is
separate from the normal `make check` gate because it requires its own toolchain.
