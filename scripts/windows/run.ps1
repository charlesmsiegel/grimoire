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
