#!/bin/sh
# Provisions the Android build toolchain for grimoire — idempotent, per-user,
# no root. JDK 17 and the Android SDK land under ~/.local/share/grimoire-android;
# android/local.properties records sdk.dir and the Chaquopy build python.
# Invoked by `make android-bootstrap`.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
BASE="$HOME/.local/share/grimoire-android"
JDK="$BASE/jdk-17"
SDK="$BASE/Sdk"
mkdir -p "$BASE"

case "$(uname -s)" in
    Darwin) os=mac; jdk_os=mac ;;
    *)      os=linux; jdk_os=linux ;;
esac
case "$(uname -m)" in
    arm64|aarch64) arch=aarch64 ;;
    *)             arch=x64 ;;
esac

if [ ! -x "$JDK/bin/java" ]; then
    echo "Downloading Temurin JDK 17..."
    curl -sSL -o "$BASE/jdk17.tar.gz" \
        "https://api.adoptium.net/v3/binary/latest/17/ga/$jdk_os/$arch/jdk/hotspot/normal/eclipse"
    mkdir -p "$BASE/_jdktmp"
    tar -xzf "$BASE/jdk17.tar.gz" -C "$BASE/_jdktmp"
    inner=$(find "$BASE/_jdktmp" -mindepth 1 -maxdepth 1 -type d | head -1)
    # macOS tarballs nest the JDK under Contents/Home
    [ -d "$inner/Contents/Home" ] && inner="$inner/Contents/Home"
    mv "$inner" "$JDK"
    rm -rf "$BASE/_jdktmp" "$BASE/jdk17.tar.gz"
fi
export JAVA_HOME="$JDK"
echo "JDK: $JDK"

SDKMGR="$SDK/cmdline-tools/latest/bin/sdkmanager"
if [ ! -x "$SDKMGR" ]; then
    echo "Downloading Android command-line tools..."
    curl -sSL -o "$BASE/clt.zip" \
        "https://dl.google.com/android/repository/commandlinetools-${os}-11076708_latest.zip"
    unzip -q "$BASE/clt.zip" -d "$BASE/_clt"
    mkdir -p "$SDK/cmdline-tools"
    mv "$BASE/_clt/cmdline-tools" "$SDK/cmdline-tools/latest"
    rm -rf "$BASE/_clt" "$BASE/clt.zip"
fi

yes | "$SDKMGR" --sdk_root="$SDK" --licenses >/dev/null
"$SDKMGR" --sdk_root="$SDK" "platform-tools" "platforms;android-34" "build-tools;34.0.0"

# Chaquopy 15's build-machine python must be 3.8-3.12 (the 3.11 APK runtime
# is downloaded by the plugin and unrelated). Probe newest supported first.
build_py=""
for v in 3.12 3.11 3.10 3.9 3.8; do
    if command -v "python$v" >/dev/null 2>&1; then
        build_py=$(command -v "python$v")
        break
    fi
done
[ -n "$build_py" ] || echo "WARNING: no python3.8-3.12 found; Chaquopy will fall back to 'python', which it may not support." >&2

{
    echo "sdk.dir=$SDK"
    [ -n "$build_py" ] && echo "grimoire.buildPython=$build_py"
} > "$ROOT/android/local.properties"
echo "Wrote android/local.properties (sdk.dir, buildPython=$build_py)."
echo "Toolchain ready — build with: make apk"
