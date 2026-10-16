#!/usr/bin/env bash
# Writes Grimoire.app to the desktop. install.sh calls this on macOS.
#
# A bare `.command` file has no icon slot: Finder shows the generic script icon
# whatever sits beside it, so the launcher is wrapped in an application bundle,
# and the icon goes in the bundle's Resources. Usage:
#   macos-launcher.sh <repo-root> <desktop-dir>
set -euo pipefail
ROOT="$1"
DESKTOP="$2"
RUN="$ROOT/scripts/unix/run.sh"
APP="$DESKTOP/Grimoire.app"
SOURCE_ICON="$ROOT/frontend/public/grimoire-512.png"

# The bundle is built in a temp directory and moved into place last, so a
# failure part-way leaves the previous launcher on the desktop, not a broken one.
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
BUNDLE="$WORK/Grimoire.app"
mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources"

# The iconset is the layout iconutil reads: each size at 1x, and at 2x where
# the 2x image is no bigger than the source. There is no 1024px entry, since the
# largest PNG we ship is 512px and upscaling it would only look worse.
ICONSET="$WORK/AppIcon.iconset"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$SOURCE_ICON" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  if [ "$double" -le 512 ]; then
    sips -z "$double" "$double" "$SOURCE_ICON" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
  fi
done
iconutil -c icns "$ICONSET" -o "$BUNDLE/Contents/Resources/AppIcon.icns"

cat > "$BUNDLE/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>Grimoire</string>
  <key>CFBundleDisplayName</key>
  <string>Grimoire</string>
  <key>CFBundleIdentifier</key>
  <string>app.grimoire.launcher</string>
  <key>CFBundleExecutable</key>
  <string>Grimoire</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
</dict>
</plist>
PLIST

# The launcher proper is a .command file inside the bundle, which Terminal runs
# as the .command did: the server's log and stop-on-close both belong to that
# window. %q makes the path safe in a shell line, whatever the checkout is called.
printf '#!/usr/bin/env bash\nexec %s\n' "$(printf '%q' "$RUN")" \
  > "$BUNDLE/Contents/Resources/Grimoire.command"
chmod +x "$BUNDLE/Contents/Resources/Grimoire.command"

# The bundle's executable hands that file to Terminal with `open`, which needs
# no Automation (Apple Events) permission, unlike driving Terminal with osascript.
printf '%s\n' '#!/usr/bin/env bash' \
  'exec open -a Terminal "$(dirname "$0")/../Resources/Grimoire.command"' \
  > "$BUNDLE/Contents/MacOS/Grimoire"
chmod +x "$BUNDLE/Contents/MacOS/Grimoire"

# Only now is what is on the desktop replaced: the bare launcher from earlier
# installs, and any previous bundle, whole rather than patched.
rm -f "$DESKTOP/Grimoire.command"
rm -rf "$APP"
mv "$BUNDLE" "$APP"
