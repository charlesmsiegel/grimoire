#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Floors, kept in step with backend/pyproject.toml's `requires-python` and
# frontend/package.json's `engines.node` by tests/test_install_scripts.py.
# Checked here, before the venv exists: `python3 -m venv` succeeds on a python
# far too old for the dependency set, and the failure then surfaces several
# minutes later inside `pip install` as a wheel that has no build for this
# interpreter -- an error naming a package rather than the actual cause.
PY_MIN="3.11"
NODE_MIN="18"

command -v python3 >/dev/null || { echo "Python $PY_MIN+ not found on PATH"; exit 1; }
python3 -c "import sys; raise SystemExit(0 if sys.version_info >= tuple(int(n) for n in '$PY_MIN'.split('.')) else 1)" \
  || { echo "Python $PY_MIN+ required; found $(python3 -V 2>&1)"; exit 1; }
command -v node >/dev/null || { echo "Node $NODE_MIN+ not found on PATH"; exit 1; }
node -e "process.exit(parseInt(process.versions.node, 10) >= $NODE_MIN ? 0 : 1)" \
  || { echo "Node $NODE_MIN+ required; found $(node -v)"; exit 1; }

echo "Installing backend…"
cd "$ROOT/backend"
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev,desktop]"

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

# The store is made lazily by the first API call, so nothing above created it.
# Ask the resolver where it will land, rather than printing a `~/.grimoire`
# that GRIMOIRE_HOME or the bootstrap pointer may already have overridden.
echo
"$ROOT/backend/.venv/bin/python" -m grimoire.where
echo
echo "Done. Run with: $RUN"
