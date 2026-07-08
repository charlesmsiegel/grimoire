# Attached Terminal Run Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `run.ps1` / `run.sh` open a terminal that stays attached, streams both servers' logs for live debugging, and tears both servers down when the terminal is closed (window-close, Ctrl+C, or logoff).

**Architecture:** On Windows, launch backend and frontend with `Start-Process -NoNewWindow -PassThru` so their output streams into the current console, and join them to a Windows Job Object created with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` — the OS kills the whole job the instant the launching PowerShell dies for any reason. A `try/finally` around a blocking `Wait-Process` handles graceful Ctrl+C teardown; the job object is the backstop for ungraceful window-close. The desktop shortcut is switched to a visible console. On Unix, `run.sh` is already foreground (`wait`); it only gains a `trap cleanup EXIT INT TERM HUP` that kills the recorded PIDs and their descendants.

**Tech Stack:** PowerShell 5.1 (`Add-Type` C# P/Invoke into kernel32 job-object APIs), Bash, uvicorn, npm/Vite.

## Global Constraints

- Ports are fixed: backend **8173**, frontend **5173**. Browser URL **http://127.0.0.1:5173**.
- Preserve the existing `.run\pids` file (one PID per line: backend then frontend) — `shutdown.ps1` / `shutdown.sh` read it.
- Preserve existing behaviors: the "already running" guard, the pre-flight stale-pid tree-kill and port-freeing sweep, `Wait-Port` readiness polling, and browser auto-open.
- `shutdown.ps1` / `shutdown.sh` are **not** modified.
- These are launch scripts: verification is by execution + port-state assertions, not unit tests. On this Windows machine the PowerShell tasks are fully verifiable; the Unix task is verified only by `bash -n` syntax check and review, and flagged as needing a real macOS/Linux run.
- Commit after each task.

---

### Task 1: Windows `run.ps1` — attached streaming + kill-on-close job object

**Files:**
- Modify (full rewrite): `scripts\windows\run.ps1`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: the running app; writes `.run\pids` (backend id line 1, frontend id line 2). No functions consumed by later tasks.

- [ ] **Step 1: Replace the whole file with the attached-mode version**

Replace the entire contents of `scripts\windows\run.ps1` with:

```powershell
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$RunDir = "$Root\.run"
$PidFile = "$RunDir\pids"
$Url = "http://127.0.0.1:5173"
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

if (Test-Path $PidFile) {
    $existing = Get-Content $PidFile | Select-Object -First 1
    if ($existing -and (Get-Process -Id $existing -ErrorAction SilentlyContinue)) {
        Write-Host "grimoire is already running ($Url). Use shutdown.ps1 to stop it."
        exit 0
    }
    # Stale pid file: the recorded parents died, but on Windows their children
    # (uvicorn's reload worker, npm's node) survive them. Kill the full trees.
    foreach ($id in Get-Content $PidFile) {
        if ($id) { taskkill /PID $id /T /F 2>$null | Out-Null }
    }
}
# Orphaned workers can hold the ports even with no pid file on record (a killed
# supervisor never takes its children with it) — a fresh launch would then bind
# alongside a zombie serving stale code. Free the ports before starting.
foreach ($port in 8173, 5173) {
    $owners = (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique
    foreach ($o in $owners) {
        if ($o) { taskkill /PID $o /T /F 2>$null | Out-Null }
    }
}

# Kill-on-close job object: when THIS PowerShell process dies for any reason
# (window closed, taskkill, logoff, crash), the OS terminates every process in
# the job. This guarantees "close the terminal -> grimoire stops" even when no
# finally block gets to run. uvicorn's --reload worker and npm's node child are
# created as descendants and are inherited into the job automatically.
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class GrimoireJob {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr CreateJobObject(IntPtr a, string lpName);
    [DllImport("kernel32.dll")]
    static extern bool SetInformationJobObject(IntPtr hJob, int infoClass, IntPtr lpInfo, uint cbInfo);
    [DllImport("kernel32.dll")]
    static extern bool AssignProcessToJobObject(IntPtr hJob, IntPtr hProcess);

    [StructLayout(LayoutKind.Sequential)]
    struct JOBOBJECT_BASIC_LIMIT_INFORMATION {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct IO_COUNTERS {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }
    const int JobObjectExtendedLimitInformation = 9;
    const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000;

    public static IntPtr Create() {
        IntPtr hJob = CreateJobObject(IntPtr.Zero, null);
        var ext = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
        ext.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        int len = Marshal.SizeOf(ext);
        IntPtr p = Marshal.AllocHGlobal(len);
        Marshal.StructureToPtr(ext, p, false);
        SetInformationJobObject(hJob, JobObjectExtendedLimitInformation, p, (uint)len);
        Marshal.FreeHGlobal(p);
        return hJob;
    }
    public static void Assign(IntPtr hJob, IntPtr hProcess) {
        AssignProcessToJobObject(hJob, hProcess);
    }
}
'@
$job = [GrimoireJob]::Create()

# -NoNewWindow streams both servers' output into THIS console (interleaved) for
# live debugging; -PassThru returns the Process so we can join it to the job.
$back = Start-Process -FilePath "$Root\backend\.venv\Scripts\python.exe" `
    -ArgumentList "-m", "uvicorn", "grimoire.main:app", "--reload", "--port", "8173" `
    -WorkingDirectory "$Root\backend" -PassThru -NoNewWindow
$front = Start-Process -FilePath "npm.cmd" `
    -ArgumentList "run", "dev", "--", "--port", "5173" `
    -WorkingDirectory "$Root\frontend" -PassThru -NoNewWindow
[GrimoireJob]::Assign($job, $back.Handle)
[GrimoireJob]::Assign($job, $front.Handle)
Set-Content -Path $PidFile -Value @($back.Id, $front.Id)

Write-Host "grimoire running at $Url (backend $($back.Id), frontend $($front.Id))"
Write-Host "Logs stream below. Close this window or press Ctrl+C to stop grimoire."

# Wait for a TCP port to accept connections (cold starts can exceed any fixed delay:
# Vite pre-bundles deps on first run, uvicorn imports the app).
function Wait-Port {
    param([string]$Name, [int]$Port)
    Write-Host -NoNewline "Waiting for $Name to be ready"
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $client = New-Object System.Net.Sockets.TcpClient
            $client.Connect("127.0.0.1", $Port)
            $client.Close()
            Write-Host ""
            return $true
        } catch {
            Write-Host -NoNewline "."
            Start-Sleep -Seconds 1
        }
    }
    Write-Host ""
    return $false
}

if (-not (Wait-Port "backend" 8173)) {
    Write-Host "Backend did not become ready (port 8173). The config page will fail to load; check the backend output above."
}
if (-not (Wait-Port "frontend" 5173)) {
    Write-Host "Frontend did not become ready in time. Check logs; opening $Url anyway."
}

Start-Process $Url

# Block in the foreground streaming logs. Ctrl+C interrupts Wait-Process so the
# finally runs a tidy teardown; an ungraceful window-close is caught by the job
# object above instead.
try {
    Wait-Process -Id $back.Id, $front.Id -ErrorAction SilentlyContinue
} finally {
    foreach ($id in @($back.Id, $front.Id)) {
        if ($id) { taskkill /PID $id /T /F 2>$null | Out-Null }
    }
    foreach ($port in 8173, 5173) {
        $owners = (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique
        foreach ($o in $owners) {
            if ($o) { taskkill /PID $o /T /F 2>$null | Out-Null }
        }
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Write-Host "grimoire stopped."
}
```

- [ ] **Step 2: Syntax-check the script**

Run:
```powershell
powershell -NoProfile -Command "$null = [System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw scripts\windows\run.ps1), [ref]$null); 'parse ok'"
```
Expected: prints `parse ok` with no parse errors.

- [ ] **Step 3: Launch and confirm streaming + readiness**

Run (in a normal PowerShell window, not the agent's non-interactive shell):
```powershell
scripts\windows\run.ps1
```
Expected: uvicorn startup lines AND Vite's `VITE ready`/`Local: http://…:5173` lines appear interleaved in the SAME window; `Waiting for backend…`/`Waiting for frontend…` complete; the browser opens to http://127.0.0.1:5173; the window stays open streaming logs.

- [ ] **Step 4: Verify window-close tears everything down**

While it is running, close the window with the X button. Then in a fresh PowerShell run:
```powershell
Get-NetTCPConnection -LocalPort 8173,5173 -State Listen -ErrorAction SilentlyContinue
```
Expected: no rows (both ports free — no orphaned uvicorn worker or node child).

- [ ] **Step 5: Verify Ctrl+C tears everything down**

Launch again with `scripts\windows\run.ps1`, wait for ready, press **Ctrl+C**. Expected: `grimoire stopped.` prints and control returns. Then in a fresh PowerShell:
```powershell
Get-NetTCPConnection -LocalPort 8173,5173 -State Listen -ErrorAction SilentlyContinue
Test-Path .run\pids
```
Expected: no listener rows; `Test-Path` prints `False` (pid file removed).

- [ ] **Step 6: Commit**

```bash
git add scripts/windows/run.ps1
git commit -m "feat(run): attached Windows terminal with kill-on-close job object"
```

---

### Task 2: Windows `install.ps1` — visible-console desktop shortcut

**Files:**
- Modify: `scripts\windows\install.ps1` (the `New-GrimoireShortcut` function and its comment)

**Interfaces:**
- Consumes: `run.ps1` from Task 1 (the shortcut targets it).
- Produces: desktop + Start-Menu `Grimoire.lnk` that opens a visible console.

- [ ] **Step 1: Replace the comment + shortcut function**

In `scripts\windows\install.ps1`, replace this block:

```powershell
# The shortcut must target powershell.exe directly: Explorer offers "Pin to taskbar"
# only for shortcuts to ordinary executables, and shortcuts hosted by wscript.exe
# (the previous launch.vbs approach) don't get the option. WindowStyle 7 starts the
# console minimized so nothing flashes on screen before -WindowStyle Hidden hides it.
function New-GrimoireShortcut($path) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $powershell
    $lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""$run"""
    $lnk.WorkingDirectory = $Root
    $lnk.IconLocation = $icon
    $lnk.WindowStyle = 7
    $lnk.Description = "Grimoire"
    $lnk.Save()
}
```

with:

```powershell
# The shortcut must target powershell.exe directly: Explorer offers "Pin to taskbar"
# only for shortcuts to ordinary executables, and shortcuts hosted by wscript.exe
# (the previous launch.vbs approach) don't get the option. WindowStyle 1 opens a
# normal, visible console: run.ps1 stays attached and streams the backend and
# frontend logs, and closing that window shuts grimoire down.
function New-GrimoireShortcut($path) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $powershell
    $lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -File ""$run"""
    $lnk.WorkingDirectory = $Root
    $lnk.IconLocation = $icon
    $lnk.WindowStyle = 1
    $lnk.Description = "Grimoire"
    $lnk.Save()
}
```

- [ ] **Step 2: Recreate the shortcut and assert its properties**

Run only the shortcut-creation half of the installer (re-running the full installer's venv/npm steps is unnecessary). From a normal PowerShell window:
```powershell
scripts\windows\install.ps1
```
(Backend/frontend are already installed, so this just reinstalls deps and rewrites the shortcuts — acceptable. If you prefer to skip the deps reinstall, dot-source only the function and call it.)

Then assert the shortcut no longer hides the window:
```powershell
$s = (New-Object -ComObject WScript.Shell).CreateShortcut("$([Environment]::GetFolderPath('Desktop'))\Grimoire.lnk")
$s.Arguments; $s.WindowStyle
```
Expected: `Arguments` contains `-File` and does **not** contain `-WindowStyle Hidden`; `WindowStyle` prints `1`.

- [ ] **Step 3: Launch via the shortcut**

Double-click the desktop **Grimoire** shortcut. Expected: a visible console opens and stays open streaming logs; the browser opens; closing the console frees ports 8173/5173 (re-check with `Get-NetTCPConnection` as in Task 1 Step 4).

- [ ] **Step 4: Commit**

```bash
git add scripts/windows/install.ps1
git commit -m "feat(run): desktop launcher opens a visible, closable console"
```

---

### Task 3: Unix `run.sh` — teardown trap

**Files:**
- Modify: `scripts/unix/run.sh` (add a `cleanup` function and `trap`)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `cleanup` shell function bound to `EXIT INT TERM HUP`.

- [ ] **Step 1: Add the cleanup function + trap**

In `scripts/unix/run.sh`, immediately after these existing lines:

```bash
echo "$BACK" > "$PIDFILE"
echo "$FRONT" >> "$PIDFILE"
```

insert:

```bash
# Guaranteed teardown: closing the terminal sends SIGHUP, Ctrl+C sends SIGINT;
# either way kill the recorded servers AND their descendants (uvicorn's reload
# worker, npm's node) so nothing keeps holding ports 8173/5173. Idempotent so the
# EXIT trap can fire after a signal trap already cleaned up.
cleanup() {
  [ -f "$PIDFILE" ] || return 0
  while read -r pid; do
    if kill -0 "$pid" 2>/dev/null; then
      pkill -TERM -P "$pid" 2>/dev/null || true
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done < "$PIDFILE"
  sleep 1
  while read -r pid; do
    pkill -9 -P "$pid" 2>/dev/null || true
    kill -9 "$pid" 2>/dev/null || true
  done < "$PIDFILE"
  rm -f "$PIDFILE"
  echo "grimoire stopped."
}
trap cleanup EXIT INT TERM HUP
```

The final `wait` at the end of the file is unchanged — it keeps the script in the foreground so a signal can reach the trap.

- [ ] **Step 2: Syntax-check**

Run:
```bash
bash -n scripts/unix/run.sh && echo "syntax ok"
```
Expected: prints `syntax ok` (no parse errors).

- [ ] **Step 3: Note manual verification needed on Unix**

This Windows machine cannot run `run.sh` end-to-end. Record in the task's completion note that a real macOS/Linux run is still required: launch `scripts/unix/run.sh`, confirm logs stream, close the terminal, and confirm no process still listens on 8173/5173 (`lsof -iTCP:8173 -sTCP:LISTEN`). Do **not** claim end-to-end Unix verification.

- [ ] **Step 4: Commit**

```bash
git add scripts/unix/run.sh
git commit -m "feat(run): trap-based teardown so closing the terminal stops grimoire"
```

---

### Task 4: README — update the Run section

**Files:**
- Modify: `README.md:86-110` (the `## Run` section)

**Interfaces:**
- Consumes: the new behavior from Tasks 1–3.
- Produces: nothing.

- [ ] **Step 1: Replace the Run section body**

Replace lines 100–110 (from `This starts the backend` through the closing `` ``` `` of the shutdown block) with:

```markdown
This starts the backend (port **8173**) and the frontend (port **5173**) in the
**current terminal**, waits for both to be ready, opens
**http://127.0.0.1:5173** in your browser, and then streams both servers' logs
into that terminal so you can watch status and errors live. The installer also
drops a **Grimoire** launcher on your desktop that opens the same console.

**The terminal stays open while grimoire runs.** Closing the window — or pressing
**Ctrl+C** — shuts down both servers cleanly (no leftover process holding a
port).

If you ever start grimoire another way and need to stop it, the shutdown scripts
still work:

```bash
scripts/unix/shutdown.sh        # macOS / Linux
scripts\windows\shutdown.ps1    # Windows
```
```

- [ ] **Step 2: Verify the section reads correctly**

Run:
```bash
sed -n '86,116p' README.md
```
Expected: the Run section describes the attached terminal and closing-to-stop behavior; the shutdown block is still present.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(run): describe the attached terminal and close-to-stop behavior"
```

---

## Self-Review

**Spec coverage:**
- Attached streaming console (Windows) → Task 1. ✓
- Kill-on-close job object → Task 1 (Steps 1, 4). ✓
- Graceful Ctrl+C `try/finally` teardown → Task 1 (Steps 1, 5). ✓
- Preserved pre-flight sweep / Wait-Port / browser-open / pid file → Task 1. ✓
- Desktop shortcut becomes a visible console → Task 2. ✓
- Unix trap teardown (EXIT INT TERM HUP), idempotent, kills descendants → Task 3. ✓
- Unchanged shutdown scripts → not modified (Global Constraints). ✓
- README Run-section update → Task 4. ✓
- Verification via launch + `Get-NetTCPConnection` / `lsof`; Unix flagged as needing real run → Tasks 1, 3. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete file/function content. ✓

**Type/name consistency:** The C# helper is `GrimoireJob` with static `Create()` and `Assign(hJob, hProcess)`, referenced consistently in Task 1. Job-object flag `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000` and info class `JobObjectExtendedLimitInformation = 9` are the correct Win32 values. The bash function `cleanup` is defined and referenced by the same name in the trap. ✓
