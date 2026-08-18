# Provisions the Android build toolchain for grimoire - idempotent, per-user,
# no admin rights. JDK 17 and the Android SDK land under %LOCALAPPDATA%\Android;
# android\local.properties records sdk.dir and the Chaquopy build python.
# Invoked by `make android-bootstrap`.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Android = "$env:LOCALAPPDATA\Android"
$Jdk = "$Android\jdk-17"
$Sdk = "$Android\Sdk"

if (-not (Test-Path "$Jdk\bin\java.exe")) {
    Write-Host "Downloading Temurin JDK 17..."
    $zip = "$env:TEMP\grimoire-jdk17.zip"
    Invoke-WebRequest "https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse" -OutFile $zip
    Expand-Archive $zip -DestinationPath "$Android\_jdktmp" -Force
    $inner = Get-ChildItem "$Android\_jdktmp" -Directory | Select-Object -First 1
    Move-Item $inner.FullName $Jdk
    Remove-Item -Recurse -Force "$Android\_jdktmp"
    Remove-Item $zip
}
$env:JAVA_HOME = $Jdk
Write-Host "JDK: $Jdk"

$SdkMgr = "$Sdk\cmdline-tools\latest\bin\sdkmanager.bat"
if (-not (Test-Path $SdkMgr)) {
    Write-Host "Downloading Android command-line tools..."
    $zip = "$env:TEMP\grimoire-cmdline-tools.zip"
    Invoke-WebRequest "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip" -OutFile $zip
    Expand-Archive $zip -DestinationPath "$Android\_clt" -Force
    New-Item -ItemType Directory -Force "$Sdk\cmdline-tools" | Out-Null
    Move-Item "$Android\_clt\cmdline-tools" "$Sdk\cmdline-tools\latest"
    Remove-Item -Recurse -Force "$Android\_clt"
    Remove-Item $zip
}

# Piping y's straight into the .bat from PowerShell drops them; feed stdin
# from a file via cmd instead.
$yes = "$env:TEMP\grimoire-yes.txt"
Set-Content $yes ("y`r`n" * 40) -Encoding ascii
cmd /c "`"$SdkMgr`" --sdk_root=`"$Sdk`" --licenses < `"$yes`"" | Select-Object -Last 1
cmd /c "`"$SdkMgr`" --sdk_root=`"$Sdk`" platform-tools `"platforms;android-34`" `"build-tools;34.0.0`" < `"$yes`"" | Select-Object -Last 1
Remove-Item $yes

# Chaquopy 17 requires the build-machine python to be the *same* minor version
# as the runtime packaged in the APK, which android/app/build.gradle.kts pins to
# $RuntimePy. (Chaquopy 15 accepted any supported version, so this used to probe
# a range; 17 fails the build on a mismatch.) Keep this in lockstep with the
# `version =` line in the chaquopy block.
$RuntimePy = "3.12"
$buildPy = $null
try {
    $exe = & py "-$RuntimePy" -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $exe) { $buildPy = "$exe".Trim() }
} catch {}
if (-not $buildPy) {
    Write-Warning "Python $RuntimePy not found. Chaquopy $RuntimePy builds require it exactly; install it or pass -Pgrimoire.buildPython=<path>."
}

$props = @("sdk.dir=" + $Sdk.Replace('\', '/'))
if ($buildPy) { $props += "grimoire.buildPython=" + $buildPy.Replace('\', '/') }
Set-Content "$Root\android\local.properties" ($props -join "`n") -Encoding ascii
Write-Host "Wrote android\local.properties (sdk.dir, buildPython=$buildPy)."
Write-Host "Toolchain ready - build with: make apk"
