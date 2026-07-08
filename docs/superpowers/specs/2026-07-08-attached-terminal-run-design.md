# Attached terminal run mode — design

**Date:** 2026-07-08
**Status:** Approved, ready for implementation plan

## Goal

Running the launch script (`scripts\windows\run.ps1` / `scripts/unix/run.sh`)
opens a terminal that **stays attached** for the life of the app: the backend
(uvicorn, port 8173) and frontend (Vite, port 5173) stream their logs into that
one terminal for quick debugging, and **closing the terminal reliably shuts
both servers down** — window-close (X), Ctrl+C, or logoff — leaving no orphaned
process holding either port.

This replaces the previous Windows behavior (detached, hidden, returns
immediately, stopped via `shutdown.ps1`). The desktop / Start-Menu launcher is
updated to match so its window is actually visible and closable.

## Motivation

Other local apps keep a console open while running so status/errors are visible
without hunting for a log file, and so the app is trivially stopped by closing
the window. Today grimoire runs its servers hidden and detached; there is no
live status and no window to close — you must remember to run a separate
shutdown script.

## Scope

- **Windows** (`run.ps1`, `install.ps1`): the substantive change.
- **Unix** (`run.sh`): a smaller change — it is already foreground; it only
  lacks guaranteed teardown.

Out of scope: `shutdown.ps1` / `shutdown.sh` (kept as-is, still the way to stop
a run started by other means); ports, browser auto-open, and port-readiness
waiting (all preserved).

## Windows — `scripts\windows\run.ps1`

Change from "detached hidden + return" to "attached + block + guaranteed
teardown". Steps:

1. **Pre-flight (unchanged).** Keep the existing "already running" guard, the
   stale-pid tree-kill, and the port-freeing sweep (8173/5173) so a prior
   crashed run cannot collide with the new one.

2. **Kill-on-close Job Object.** Create a Windows Job Object configured with
   `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` via a small `Add-Type` P/Invoke shim
   exposing `CreateJobObject`, `SetInformationJobObject`
   (`JobObjectExtendedLimitInformation`), and `AssignProcessToJobObject`. The OS
   terminates every process in the job the instant the job's last handle
   closes. Because the launching PowerShell process holds that handle, the job
   closes when that process dies **for any reason** — the X button, `taskkill`,
   logoff, or a crash. This is the mechanism that makes "close the terminal →
   app dies" bulletproof; a `try/finally` cannot cover an ungraceful
   window-close because the OS terminates the shell without running `finally`.

3. **Start attached.** Launch backend and frontend with
   `Start-Process -NoNewWindow -PassThru` so their stdout/stderr stream
   interleaved into the current console. Immediately assign each returned
   process to the job. uvicorn's `--reload` worker and npm's node child are
   created as descendants and are inherited into the job automatically, so they
   are covered too. Continue to write both PIDs to `.run\pids` so
   `shutdown.ps1` keeps working.

4. **Wait for ports + open browser (unchanged UX).** Keep `Wait-Port` for 8173
   and 5173 and `Start-Process $Url`.

5. **Block in foreground.** `Wait-Process` on the two PIDs inside
   `try { … } finally { … }`. Ctrl+C interrupts the wait, so `finally` runs a
   graceful teardown: kill the recorded process trees, free the ports, remove
   `.run\pids`, and print `grimoire stopped.` The job object is the backstop for
   the ungraceful window-close case (where `finally` does not run).

### `scripts\windows\install.ps1`

The desktop / Start-Menu shortcut currently targets
`powershell.exe … -WindowStyle Hidden -File run.ps1` with shortcut
`WindowStyle = 7` (minimized) so nothing shows. For an attached terminal the
user can watch and close:

- Drop `-WindowStyle Hidden` from the arguments.
- Set the shortcut `WindowStyle = 1` (normal window).

Closing that console now stops grimoire via the job object. The "Pin to
taskbar" affordance (shortcut targets `powershell.exe` directly) is preserved.

## Unix — `scripts/unix/run.sh`

`run.sh` already runs in the foreground (`wait` at the end) and streams logs.
The only gap is guaranteed cleanup of the whole process tree. Add:

```bash
trap cleanup EXIT INT TERM HUP
```

where `cleanup` kills the recorded PIDs **and their descendants** — reusing the
`shutdown.sh` approach (`pkill -TERM -P "$pid"` then `kill "$pid"`, escalating
to `-9`) — and removes the pid file. `HUP` covers terminal close, `INT` covers
Ctrl+C, `TERM` covers external termination, and `EXIT` covers a normal exit.
Guard against double-teardown (the trap can fire more than once) by making
`cleanup` idempotent (e.g. return early if the pid file is already gone).

## What stays the same

- Ports 8173 / 5173; browser auto-open; port-readiness waiting; the `.run\pids`
  file; the `run.ps1` "already running" guard.
- `shutdown.ps1` / `shutdown.sh` — unchanged, still usable.

## Testing / verification

- **Windows (verify on this machine):**
  - Launch `run.ps1`; confirm backend and frontend logs stream into the console.
  - Close the window; then assert ports 8173 and 5173 are free via
    `Get-NetTCPConnection -LocalPort 8173,5173 -State Listen` (no owners) — i.e.
    no orphaned uvicorn worker or node child.
  - Repeat, stopping with Ctrl+C instead; confirm the same clean state plus the
    `grimoire stopped.` message and removed `.run\pids`.
  - Confirm the desktop shortcut opens a **visible** console.
- **Unix (cannot fully exercise on this Windows box):** review the trap logic
  for correctness and idempotency; flag as needing a real run on macOS/Linux
  before relying on it.

## Documentation

Update the README **Run** section: the terminal now stays open and streams
logs; closing it (or Ctrl+C) stops grimoire. Note the `shutdown` scripts remain
available for runs started another way.

## Risks / notes

- **Assign-to-job race.** A child could, in principle, spawn its own child
  before assignment completes. In practice `Start-Process` returns before
  uvicorn/npm spawn workers, and descendants inherit the job; the pre-flight
  port sweep on the next launch and the `finally` teardown are additional
  backstops. Accepted.
- **Nested jobs.** Windows 8+ permits a process to belong to nested jobs, so a
  launcher already inside a job (rare here) still works.
