# Python commands and source selection

Run from the repository root. Select the backend interpreter that exists; in a
worktree, use the main checkout's interpreter and put this worktree's source first:

```sh
PY=/absolute/path/to/main/backend/.venv/bin/python # macOS/Linux
PY=/absolute/path/to/main/backend/.venv/Scripts/python.exe # Windows Git Bash
export PYTHONPATH="$PWD/backend/src"
```

```powershell
$py = '/path/to/main/backend/.venv/bin/python' # macOS/Linux PowerShell
$py = 'C:/path/to/main/backend/.venv/Scripts/python.exe'
$env:PYTHONPATH = (Join-Path (Get-Location).Path 'backend/src')
# Invoke Python with & $py; Bash's $PY command syntax does not work in PowerShell.
```

Use the same `GRIMOIRE_HOME` for every command in a private-library batch.
Resolve it with `-m grimoire.where`; do not assume the default home directory.
Worktree and verification details are in
[CONTRIBUTING.md](../../../../CONTRIBUTING.md#four-things-that-will-bite-you).
