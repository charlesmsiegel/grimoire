# Two ways to run. The default is what a player wants: one backend serving the
# production bundle from frontend\dist beside the API, on one port. -Dev is the
# contributor's loop and exactly what this script used to do every time --
# uvicorn --reload plus the Vite dev server with HMR -- which costs each page
# load an unbundled request per source module, React's development build and a
# Node proxy in front of every API call and stream. scripts/unix/run.sh mirrors
# this with --dev, and says more about why.
param([switch]$Dev)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$RunDir = "$Root\.run"
$PidFile = "$RunDir\pids"
$DistIndex = "$Root\frontend\dist\index.html"
$StaleNotice = $null
$BackendPort = 8173
$VitePort = 5173
if ($Dev) {
    $Url = "http://127.0.0.1:$VitePort"
    $Ports = @($BackendPort, $VitePort)
} else {
    $Url = "http://127.0.0.1:$BackendPort"
    # Only the port this mode binds: sweeping Vite's too would kill an unrelated
    # dev server that happens to sit on Vite's default port.
    $Ports = @($BackendPort)
}
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

# Kill a process tree, tolerating already-dead PIDs. taskkill's stderr must be
# swallowed by cmd.exe, not a PowerShell 2>$null redirect: under
# $ErrorActionPreference = "Stop", PowerShell turns a native command's redirected
# stderr into a terminating NativeCommandError, killing the whole script the
# first time a recorded PID is stale.
function Stop-Tree([int]$Id) {
    cmd /c "taskkill /PID $Id /T /F >nul 2>&1"
}

if (Test-Path $PidFile) {
    $existing = Get-Content $PidFile | Select-Object -First 1
    if ($existing -and (Get-Process -Id $existing -ErrorAction SilentlyContinue)) {
        Write-Host "grimoire is already running (http://127.0.0.1:$BackendPort, or :$VitePort if started with -Dev). Use shutdown.ps1 to stop it."
        exit 0
    }
    # Stale pid file: the recorded parents died, but on Windows their children
    # (uvicorn's reload worker, npm's node) survive them. Kill the full trees.
    foreach ($id in Get-Content $PidFile) {
        if ($id) { Stop-Tree $id }
    }
}
# Orphaned workers can hold the ports even with no pid file on record (a killed
# supervisor never takes its children with it) — a fresh launch would then bind
# alongside a zombie serving stale code. Free the ports before starting.
foreach ($port in $Ports) {
    $owners = (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique
    foreach ($o in $owners) {
        if ($o) { Stop-Tree $o }
    }
}

# What `vite build` reads. The bundle is rebuilt when any of these is newer than
# the dist\index.html the last build wrote, which is what makes the first launch
# after a `git pull` serve the new UI rather than the one the installer built:
# git stamps every file it writes with the checkout time. The inputs themselves
# are compared as well as everything under them, since deleting a file changes
# nothing but its directory's timestamp. A missing entry is skipped rather than
# fatal; test_install_scripts.py asserts they all exist and that
# scripts/unix/run.sh watches the same list.
$BundleInputs = @("src", "public", "index.html", "package.json", "package-lock.json", "tsconfig.json", "vite.config.ts")

function Test-BundleStale {
    if (-not (Test-Path -LiteralPath $DistIndex)) { return $true }
    $built = (Get-Item -LiteralPath $DistIndex).LastWriteTimeUtc
    $paths = @($BundleInputs | ForEach-Object { Join-Path "$Root\frontend" $_ } |
        Where-Object { Test-Path -LiteralPath $_ })
    if ($paths.Count -eq 0) { return $false }
    $items = @(Get-Item -LiteralPath $paths -Force) + @(Get-ChildItem -LiteralPath $paths -Recurse -Force)
    $newer = $items | Where-Object { $_.LastWriteTimeUtc -gt $built } | Select-Object -First 1
    return [bool]$newer
}

if (-not $Dev -and (Test-BundleStale)) {
    Write-Host "Building the UI (the first launch after an update takes a few seconds)..."
    # `vite build` through the .bin shim, not `npm run build`: that script's
    # `tsc -b` is a type check, the gate's job rather than a launch's. Errors
    # only -- the bundler's size advisories are for whoever is changing the code.
    # A missing shim (packages never installed) throws rather than setting an
    # exit code, so both count as a failed build.
    $buildOk = $false
    Push-Location "$Root\frontend"
    try {
        .\node_modules\.bin\vite.cmd build --logLevel error
        $buildOk = ($LASTEXITCODE -eq 0)
    } catch {
        Write-Host $_
    } finally {
        Pop-Location
    }
    if (-not $buildOk) {
        # Vite empties dist\ only once bundling has succeeded, so a failure here
        # (typically dependencies an update changed) leaves the previous build in
        # place. Serving it beats not starting, and the next launch tries again.
        if (Test-Path -LiteralPath $DistIndex) {
            Write-Host "The UI build failed; serving the previous build. After an update, re-run scripts\windows\install.ps1."
            # ...but an old UI against an updated backend can fail in ways that
            # look like bugs, and this console is behind the browser by the
            # time they do. So the browser is told too, as scripts/unix/run.sh
            # does: a page opened beside the app.
            $StaleNotice = "$RunDir\ui-build-failed.html"
            Set-Content -LiteralPath $StaleNotice -Encoding UTF8 -Value @'
<!doctype html>
<meta charset="utf-8">
<title>Grimoire is running its previous interface</title>
<body style="font: 16px/1.5 system-ui, sans-serif; max-width: 40em; margin: 3em auto; padding: 0 1em">
<h1>Grimoire is running its previous interface</h1>
<p>After the update, rebuilding Grimoire's interface failed, so this launch is
serving the build from before it. The updated backend may not work with it:
pages can fail to load or behave oddly.</p>
<p>To fix it, close Grimoire, run <code>scripts\windows\install.ps1</code> again,
and start Grimoire. What the build reported is in the console window Grimoire
runs in.</p>
'@
        } else {
            Write-Host "The UI build failed and there is no previous build to serve. Re-run scripts\windows\install.ps1."
            exit 1
        }
    }
}

# Kill-on-close job object: when THIS PowerShell process dies for any reason
# (window closed, taskkill, logoff, crash), the OS terminates every process in
# the job. This guarantees "close the terminal -> grimoire stops" even when no
# finally block gets to run. Descendants -- under -Dev, uvicorn's --reload
# worker and npm's node child -- are inherited into the job automatically.
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

# -NoNewWindow streams the servers' output into THIS console (interleaved under
# -Dev) for live debugging; -PassThru returns the Process so we can join it to
# the job.
if ($Dev) {
    $back = Start-Process -FilePath "$Root\backend\.venv\Scripts\python.exe" `
        -ArgumentList "-m", "uvicorn", "grimoire.main:app", "--reload", "--port", "$BackendPort" `
        -WorkingDirectory "$Root\backend" -PassThru -NoNewWindow
    $front = Start-Process -FilePath "npm.cmd" `
        -ArgumentList "run", "dev", "--", "--port", "$VitePort" `
        -WorkingDirectory "$Root\frontend" -PassThru -NoNewWindow
    $procs = @($back, $front)
} else {
    # No --reload: its supervisor is a second process watching the source tree
    # for edits a player never makes. main.py mounts frontend\dist beside /api,
    # with the client-route fallback that lets a deep link survive a reload.
    $back = Start-Process -FilePath "$Root\backend\.venv\Scripts\python.exe" `
        -ArgumentList "-m", "uvicorn", "grimoire.main:app", "--port", "$BackendPort" `
        -WorkingDirectory "$Root\backend" -PassThru -NoNewWindow
    $procs = @($back)
}
foreach ($p in $procs) { [GrimoireJob]::Assign($job, $p.Handle) }
Set-Content -Path $PidFile -Value @($procs | ForEach-Object { $_.Id })

if ($Dev) {
    Write-Host "grimoire running at $Url (backend $($back.Id), frontend $($front.Id))"
} else {
    Write-Host "grimoire running at $Url (pid $($back.Id))"
}
Write-Host "Logs stream below. Close this window or press Ctrl+C to stop grimoire."

# Wait for a TCP port to accept connections (cold starts can exceed any fixed delay:
# Vite pre-bundles deps on first run, uvicorn imports the app). Given a process,
# give up at once when it has exited: a backend that died (an import error, a
# bind failure) is not going to become ready.
function Wait-Port {
    param([string]$Name, [int]$Port, [System.Diagnostics.Process]$Proc = $null, [int]$Tries = 60)
    Write-Host -NoNewline "Waiting for $Name to be ready"
    for ($i = 0; $i -lt $Tries; $i++) {
        try {
            $client = New-Object System.Net.Sockets.TcpClient
            $client.Connect("127.0.0.1", $Port)
            $client.Close()
            Write-Host ""
            return $true
        } catch {
            if ($Proc -and $Proc.HasExited) {
                Write-Host ""
                return $false
            }
            Write-Host -NoNewline "."
            Start-Sleep -Seconds 1
        }
    }
    Write-Host ""
    return $false
}

# Block in the foreground streaming logs. Ctrl+C interrupts the wait so the
# finally runs a tidy teardown; an ungraceful window-close is caught by the job
# object above instead.
try {
    if ($Dev) {
        if (-not (Wait-Port "backend" $BackendPort)) {
            Write-Host "Backend did not become ready (port $BackendPort). The config page will fail to load; check the backend output above."
        }
        if (-not (Wait-Port "frontend" $VitePort)) {
            Write-Host "Frontend did not become ready in time. Check logs; opening $Url anyway."
        }
    } else {
        # Without --reload uvicorn binds only after the app's startup (the store
        # migrations) has run, so an open port means ready -- and a slow start
        # on a large library earns a longer wait than the dev servers get.
        if (-not (Wait-Port -Name "backend" -Port $BackendPort -Proc $back -Tries 180)) {
            if ($back.HasExited) {
                Write-Host "The backend exited before it was ready; check its output above."
                exit 1
            }
            Write-Host "The backend is still not answering on port $BackendPort; opening $Url anyway."
        }
    }

    Start-Process $Url
    if ($StaleNotice) { Start-Process $StaleNotice }

    Wait-Process -Id ($procs | ForEach-Object { $_.Id }) -ErrorAction SilentlyContinue
} finally {
    foreach ($p in $procs) {
        if ($p.Id) { Stop-Tree $p.Id }
    }
    foreach ($port in $Ports) {
        $owners = (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique
        foreach ($o in $owners) {
            if ($o) { Stop-Tree $o }
        }
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Write-Host "grimoire stopped."
}
