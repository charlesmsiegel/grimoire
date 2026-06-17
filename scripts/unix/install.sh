#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

command -v python3 >/dev/null || { echo "Python 3.11+ not found"; exit 1; }
command -v node >/dev/null || { echo "Node 18+ not found"; exit 1; }

echo "Installing backend…"
cd "$ROOT/backend"
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"

echo "Installing frontend…"
cd "$ROOT/frontend"
npm install

echo "Creating desktop launcher…"
RUN="$ROOT/scripts/unix/run.sh"
chmod +x "$ROOT/scripts/unix/"*.sh
DESKTOP="${HOME}/Desktop"
if [ "$(uname)" = "Darwin" ]; then
  mkdir -p "$DESKTOP"
  printf '#!/usr/bin/env bash\nexec "%s"\n' "$RUN" > "$DESKTOP/Grimoire.command"
  chmod +x "$DESKTOP/Grimoire.command"
else
  ENTRY="[Desktop Entry]
Type=Application
Name=Grimoire
Exec=$RUN
Icon=$ROOT/frontend/public/grimoire-256.png
Terminal=false
Categories=Utility;"
  mkdir -p "$DESKTOP" "$HOME/.local/share/applications"
  echo "$ENTRY" > "$DESKTOP/grimoire.desktop"
  echo "$ENTRY" > "$HOME/.local/share/applications/grimoire.desktop"
  chmod +x "$DESKTOP/grimoire.desktop"
fi

echo "Done. Run with: $RUN"
