#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OS="$(uname -s)"

echo "=== Grimoire Setup ==="
echo ""

request_install() {
    local name="$1" command="$2" url="$3"
    echo "  $name is not installed."
    echo -n "  Install it now? ($command) [Y/n] "
    read -r answer
    if [ "$answer" = "" ] || [ "$answer" = "y" ] || [ "$answer" = "Y" ]; then
        echo "  Installing $name..."
        eval "$command"
    else
        echo "  Grimoire requires $name. Install manually: $url"
        exit 1
    fi
}

linux_install_cmd() {
    local apt_cmd="$1" dnf_cmd="$2"
    if command -v apt-get &>/dev/null; then
        echo "$apt_cmd"
    elif command -v dnf &>/dev/null; then
        echo "$dnf_cmd"
    elif command -v pacman &>/dev/null; then
        echo "sudo pacman -S --noconfirm ${dnf_cmd##* }"
    else
        echo ""
    fi
}

platform_install() {
    local name="$1" brew_cmd="$2" apt_cmd="$3" url="$4" dnf_cmd="${5:-}"
    case "$OS" in
        Darwin) request_install "$name" "$brew_cmd" "$url" ;;
        *)
            local cmd
            cmd=$(linux_install_cmd "$apt_cmd" "${dnf_cmd:-$apt_cmd}")
            if [ -z "$cmd" ]; then
                echo "  No supported package manager found. Install $name manually: $url"
                exit 1
            fi
            request_install "$name" "$cmd" "$url"
            ;;
    esac
}

echo "Checking prerequisites..."

# Python 3.12+
if ! command -v python3 &>/dev/null; then
    platform_install "Python 3.12+" "brew install python@3.12" \
        "sudo add-apt-repository -y ppa:deadsnakes/ppa && sudo apt-get update && sudo apt-get install -y python3.12 python3.12-venv" \
        "https://python.org" "sudo dnf install -y python3.12"
else
    py_ver=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    py_major=${py_ver%%.*}
    py_minor=${py_ver##*.}
    if [ "$py_major" -lt 3 ] || { [ "$py_major" -eq 3 ] && [ "$py_minor" -lt 12 ]; }; then
        echo "  Python $py_ver found, but 3.12+ required."
        platform_install "Python 3.12+" "brew install python@3.12" \
        "sudo add-apt-repository -y ppa:deadsnakes/ppa && sudo apt-get update && sudo apt-get install -y python3.12 python3.12-venv" \
        "https://python.org" "sudo dnf install -y python3.12"
    else
        echo "  Python $py_ver"
    fi
fi

# Node 20+
if ! command -v node &>/dev/null; then
    platform_install "Node.js 20+" "brew install node@20" \
        "curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs" \
        "https://nodejs.org" "sudo dnf install -y nodejs20"
else
    node_major=$(node --version | sed 's/v\([0-9]*\).*/\1/')
    if [ "$node_major" -lt 20 ]; then
        echo "  Node $node_major found, but 20+ required."
        platform_install "Node.js 20+" "brew install node@20" \
        "curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs" \
        "https://nodejs.org" "sudo dnf install -y nodejs20"
    else
        echo "  Node $node_major"
    fi
fi

# uv
if ! command -v uv &>/dev/null; then
    request_install "uv" "curl -LsSf https://astral.sh/uv/install.sh | sh" "https://docs.astral.sh/uv/"
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if ! command -v uv &>/dev/null; then
        echo "  uv was installed but is not on PATH. Please restart your shell and re-run setup."
        exit 1
    fi
else
    echo "  uv"
fi

# pnpm
if ! command -v pnpm &>/dev/null; then
    request_install "pnpm" "corepack enable && corepack prepare pnpm@latest --activate" "https://pnpm.io"
else
    echo "  pnpm"
fi

echo ""
echo "Installing dependencies..."

echo "  Backend (uv sync)..."
(cd "$REPO_ROOT/backend" && uv sync)

echo "  Frontend (pnpm install)..."
(cd "$REPO_ROOT/frontend" && pnpm install)

echo ""
echo "Creating desktop shortcut..."

case "$OS" in
    Darwin)
        APP_DIR="$HOME/Desktop/Grimoire.app"
        mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"

        cat > "$APP_DIR/Contents/MacOS/Grimoire" <<LAUNCHER
#!/bin/bash
cd "$REPO_ROOT"
open -a Terminal ./scripts/run.sh
LAUNCHER
        chmod +x "$APP_DIR/Contents/MacOS/Grimoire"

        cat > "$APP_DIR/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>Grimoire</string>
    <key>CFBundleIconFile</key>
    <string>grimoire</string>
    <key>CFBundleName</key>
    <string>Grimoire</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
</dict>
</plist>
PLIST

        if command -v iconutil &>/dev/null; then
            ICONSET=$(mktemp -d)/grimoire.iconset
            mkdir -p "$ICONSET"
            cp "$REPO_ROOT/assets/icons/grimoire-16.png"  "$ICONSET/icon_16x16.png"
            cp "$REPO_ROOT/assets/icons/grimoire-32.png"  "$ICONSET/icon_16x16@2x.png"
            cp "$REPO_ROOT/assets/icons/grimoire-32.png"  "$ICONSET/icon_32x32.png"
            cp "$REPO_ROOT/assets/icons/grimoire-64.png"  "$ICONSET/icon_32x32@2x.png"
            cp "$REPO_ROOT/assets/icons/grimoire-128.png" "$ICONSET/icon_128x128.png"
            cp "$REPO_ROOT/assets/icons/grimoire-256.png" "$ICONSET/icon_128x128@2x.png"
            cp "$REPO_ROOT/assets/icons/grimoire-256.png" "$ICONSET/icon_256x256.png"
            cp "$REPO_ROOT/assets/icons/grimoire.png"     "$ICONSET/icon_256x256@2x.png"
            cp "$REPO_ROOT/assets/icons/grimoire-512.png" "$ICONSET/icon_512x512.png"
            iconutil -c icns "$ICONSET" -o "$APP_DIR/Contents/Resources/grimoire.icns"
            rm -rf "$(dirname "$ICONSET")"
        else
            echo "  Note: iconutil not found, app will use default icon."
        fi
        echo "  $APP_DIR"
        ;;
    *)
        DESKTOP_DIR="$HOME/.local/share/applications"
        mkdir -p "$DESKTOP_DIR"
        DESKTOP_FILE="$DESKTOP_DIR/grimoire.desktop"
        cat > "$DESKTOP_FILE" <<DESKTOP
[Desktop Entry]
Name=Grimoire
Exec="$REPO_ROOT/scripts/run.sh"
Icon=$REPO_ROOT/assets/icons/grimoire-256.png
Terminal=true
Type=Application
Categories=Game;
DESKTOP
        chmod +x "$DESKTOP_FILE"
        if [ -d "$HOME/Desktop" ]; then
            cp "$DESKTOP_FILE" "$HOME/Desktop/grimoire.desktop"
            chmod +x "$HOME/Desktop/grimoire.desktop"
        fi
        echo "  $DESKTOP_FILE"
        ;;
esac

echo ""
echo "=== Setup complete! ==="
echo "Double-click the Grimoire shortcut on your desktop, or run ./scripts/run.sh"
