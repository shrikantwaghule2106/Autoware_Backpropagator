# Autoware Backpropagator — one-command install (Windows, Offline mode).
#   powershell -ExecutionPolicy Bypass -File install.ps1            (or: install.ps1 -Dev)
# Online mode on Windows means Docker Desktop with `adviewer probe run --like <autoware-container>`
# (Docker Desktop has no host network; not tested on Windows yet).
param([switch]$Dev)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

function Fail($what, $fix) { Write-Host "  x $what" -ForegroundColor Red; Write-Host "    fix: $fix"; exit 1 }
function Ok($what) { Write-Host "  + $what" -ForegroundColor Green }
function Note($what) { Write-Host "  ! $what" -ForegroundColor Yellow }

# A Python command (list: exe + arguments) that is >= 3.10, newest first; skips the Microsoft Store placeholder.
function Find-Python {
  $candidates = @()
  if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($v in "3.14", "3.13", "3.12", "3.11", "3.10") { $candidates += ,@("py", "-$v") }
  }
  foreach ($name in "python", "python3") {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notlike "*WindowsApps*") { $candidates += ,@($cmd.Source) }
  }
  foreach ($c in $candidates) {
    $exe = $c[0]; $rest = @($c | Select-Object -Skip 1)
    & $exe @rest -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) { return ,$c }
  }
  return $null
}

Write-Host "> Python 3.10 or newer"
$py = Find-Python
if (-not $py) {
  $store = Get-Command python -ErrorAction SilentlyContinue
  if ($store -and $store.Source -like "*WindowsApps*") {
    Fail "only the Microsoft Store placeholder for Python was found" "install Python 3.10+ from https://www.python.org/downloads/ (tick 'Add python.exe to PATH')"
  }
  Fail "no Python 3.10 or newer found" "install Python 3.10+ from https://www.python.org/downloads/ (tick 'Add python.exe to PATH')"
}
$pyExe = $py[0]; $pyArgs = @($py | Select-Object -Skip 1)
Ok (& $pyExe @pyArgs -c "import sys; print('Python', sys.version.split()[0], sys.executable)")

Write-Host "> Virtual environment (.venv)"
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (Test-Path .venv) {
  # an old or broken .venv (made by a Python that is gone, or copied from another PC): start again
  $healthy = $false
  if (Test-Path $venvPy) {
    & $venvPy -c "import sys, pip; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
    $healthy = ($LASTEXITCODE -eq 0)
  }
  if (-not $healthy) {
    $broken = ".venv.broken-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    Rename-Item .venv $broken
    Note "the old .venv did not work; moved it to $broken and made a new one"
  }
}
if (-not (Test-Path $venvPy)) {
  & $pyExe @pyArgs -m venv .venv
  if ($LASTEXITCODE -ne 0) { Fail "could not create .venv" "check write permission in $PSScriptRoot" }
}
& $venvPy -m pip install --quiet --upgrade pip
if ($LASTEXITCODE -ne 0) { Fail "pip upgrade failed" "check the internet connection / proxy (pip honours HTTPS_PROXY)" }
$target = if ($Dev) { ".[dev]" } else { "." }
& $venvPy -m pip install --quiet -e $target
if ($LASTEXITCODE -ne 0) { Fail "pip install failed" "check the internet connection / proxy, then run install.ps1 again" }
$adviewer = Join-Path $PSScriptRoot ".venv\Scripts\adviewer.exe"
Ok (& $adviewer --version)

Write-Host "> Environment check (Offline mode needs only Python)"
& $adviewer doctor
Write-Host ""
Write-Host "Ready for Offline mode. Next:"
Write-Host "  .venv\Scripts\Activate.ps1      (if scripts are blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned,"
Write-Host "                                   or in cmd.exe: .venv\Scripts\activate.bat)"
Write-Host "  cd C:\path\to\your\project; adviewer init --from-source; adviewer fetch-source; adviewer up"
Write-Host "  (no Docker needed; with Docker: adviewer init; adviewer extract; adviewer fetch-source; adviewer up)"
