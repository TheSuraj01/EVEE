# Builds dist\VoicePCAgent: a PyInstaller one-dir, windowed build stamped with the package
# version. Refuses to ship a secret, then smoke-tests the exe on a clean, empty user profile.
#
#   powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 [-SkipTests]
#
# Needs: pip install -e ".[dev,voice,cloud,windows,ui,build]"
# Models are not bundled: Whisper and the wake word download once, on first run.
param([switch]$SkipTests)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not $SkipTests) {
    python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "tests failed; refusing to build" }
}

# --- no secrets going in -----------------------------------------------------------------
# PyInstaller compresses every module into the exe, so the output scan below can't see a key
# hard-coded in src\. Check the sources the build will read first.
$sourceLeaks = Get-ChildItem -Recurse -File src, plugins, config |
    Select-String -Pattern "gsk_[A-Za-z0-9]{20,}" -List
if ($sourceLeaks) { throw "key-shaped string in the sources, refusing to build: $($sourceLeaks.Path -join ', ')" }

# --- version stamp: voice_agent.__version__ is the one source --------------------------
$version = python -c "import voice_agent; print(voice_agent.__version__)"
if ($LASTEXITCODE -ne 0 -or $version -notmatch '^\d+\.\d+\.\d+$') {
    throw "cannot read a MAJOR.MINOR.PATCH version from voice_agent: '$version'"
}
$numbers = (($version.Split('.') + '0') -join ', ')
New-Item -ItemType Directory -Force build | Out-Null
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($numbers), prodvers=($numbers), mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('ProductName', 'Voice PC Agent'),
      StringStruct('FileDescription', 'Voice PC Agent'),
      StringStruct('FileVersion', '$version'),
      StringStruct('ProductVersion', '$version'),
      StringStruct('OriginalFilename', 'VoicePCAgent.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -Encoding ascii build\version_info.txt

# --- build ------------------------------------------------------------------------------
# Config files are listed one by one: config\user.yaml holds the builder's own settings.
# Absolute paths, because the spec file goes in build\ and relative ones would resolve there.
$here = (Get-Location).Path
pyinstaller --noconfirm --clean `
    --name VoicePCAgent `
    --windowed `
    --specpath build `
    --version-file "$here\build\version_info.txt" `
    --additional-hooks-dir "$here\scripts\pyinstaller_hooks" `
    --paths "$here\src" `
    --add-data "$here\config\default.yaml;config" `
    --add-data "$here\config\commands.yaml;config" `
    --add-data "$here\config\logging.yaml;config" `
    --add-data "$here\plugins;plugins" `
    --collect-data faster_whisper `
    --collect-data openwakeword `
    --collect-binaries ctranslate2 `
    "$here\src\voice_agent\main.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
# PyInstaller leaves an intermediate exe in build\ with no _internal folder beside it:
# launching it only shows "Failed to load Python DLL". Only dist\ runs.
Remove-Item build\VoicePCAgent\VoicePCAgent.exe

$app = "dist\VoicePCAgent"
$exe = "$app\VoicePCAgent.exe"

# --- no secrets in the artifact ---------------------------------------------------------
$leaks = @(Get-ChildItem -Recurse -Force $app -File |
    Where-Object { $_.Name -in ".env", "user.yaml" -or $_.Name -like "*.env" })
$leaks += @(Get-ChildItem -Recurse -Force $app -File |
    Select-String -Pattern "gsk_[A-Za-z0-9]{20,}" -List | ForEach-Object { Get-Item $_.Path })
if ($leaks) { throw "SECRET OR PRIVATE FILE IN BUILD OUTPUT, aborting: $($leaks.FullName -join ', ')" }

# --- smoke test on a clean profile ------------------------------------------------------
# A fresh, empty user profile and no API key: what a new user's first launch sees.
$profileDir = Join-Path ([IO.Path]::GetTempPath()) ("vpa-smoke-" + [guid]::NewGuid())
New-Item -ItemType Directory $profileDir | Out-Null
$redirected = "LOCALAPPDATA", "APPDATA", "USERPROFILE", "HOME", "TEMP", "TMP"
$saved = @{}
foreach ($name in $redirected + "GROQ_API_KEY") {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}
try {
    foreach ($name in $redirected) { [Environment]::SetEnvironmentVariable($name, $profileDir, "Process") }
    [Environment]::SetEnvironmentVariable("GROQ_API_KEY", $null, "Process")
    $run = Start-Process $exe -ArgumentList "--self-test" -Wait -PassThru `
        -RedirectStandardOutput "$profileDir\stdout.txt" -RedirectStandardError "$profileDir\stderr.txt"
} finally {
    foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name], "Process") }
}
Get-Content "$profileDir\stdout.txt", "$profileDir\stderr.txt" -ErrorAction SilentlyContinue
if ($run.ExitCode -ne 0) {
    Get-Content "$profileDir\voice-pc-agent\logs\voice-agent.log" -ErrorAction SilentlyContinue
    throw "smoke test failed on a clean profile (exit $($run.ExitCode)); profile kept at $profileDir"
}
Remove-Item -Recurse -Force $profileDir

$stamped = (Get-Item $exe).VersionInfo.ProductVersion
if ($stamped -ne $version) { throw "exe is stamped '$stamped', expected '$version'" }

Write-Host "Build complete: $app (v$version), smoke test passed" -ForegroundColor Green
