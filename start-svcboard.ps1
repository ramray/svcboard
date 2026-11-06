# svcboard: hidden launcher (same pattern as start-wife.ps1).
# Starts the status app with NO visible window, opens the browser itself.
# Stop it with stop-svcboard.ps1.
param([switch]$Inner, [switch]$Direct)

$log = "$env:TEMP\svcboard-start.log"
$app = "$PSScriptRoot\svcboard.py"
$appLog = "$PSScriptRoot\svcboard.log"

# single-instance guard: someone already holds the port
if (Get-NetTCPConnection -LocalPort 49480 -State Listen -ErrorAction SilentlyContinue) {
  "$(Get-Date -Format s) port busy, exit" | Add-Content $log
  exit 0
}

if (-not $Inner) {
  # Windows 11 routes a plain console into a visible Windows Terminal window.
  # Relaunch under the legacy headless conhost (invisible) and exit this one.
  "$(Get-Date -Format s) relaunch hidden" | Add-Content $log
  Start-Process -FilePath "conhost.exe" -ArgumentList @(
    '--headless',
    'powershell.exe',
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    # manual quotes: this path contains spaces ("Default Project"), and
    # Start-Process -ArgumentList does NOT quote elements for us
    '-File', ('"{0}"' -f $PSCommandPath),
    '-Inner'
  ) -WindowStyle Hidden
  exit 0
}

# inner (hidden): run the app forever; its own timer opens the browser
# 1s after the HTTP port is bound; stdout/stderr -> svcboard.log (append)
"$(Get-Date -Format s) starting app" | Add-Content $log
$env:PYTHONIOENCODING = 'utf-8'

# python: SVCBOARD_PYTHON > local embed (../tools/python*/python.exe) >
# original miniconda > python in PATH (worked on every machine so far)
$py = $env:SVCBOARD_PYTHON
if (-not $py) {
  $localPy = Get-ChildItem (Join-Path (Split-Path $PSScriptRoot -Parent) 'tools\python*\python.exe') -ErrorAction SilentlyContinue |
             Sort-Object FullName -Descending | Select-Object -First 1
  if ($localPy) { $py = $localPy.FullName }
  elseif (Test-Path 'C:\Users\User\miniconda3\python.exe') { $py = 'C:\Users\User\miniconda3\python.exe' }
  else { $py = 'python' }
}
# --direct: explicit switch wins; otherwise auto-enable when host 'lab' does
# not resolve (tunnels via lab are impossible on this machine then)
if (-not $Direct) {
  $labOk = $false
  try { [System.Net.Dns]::GetHostAddresses('lab') | Out-Null; $labOk = $true } catch {}
  if (-not $labOk) {
    $Direct = $true
    "$(Get-Date -Format s) auto-direct: host 'lab' unresolvable" | Add-Content $log
  }
}
$appArgs = @($app)
if ($Direct) { $appArgs += '--direct' }
"$(Get-Date -Format s) python=$py direct=$($Direct.IsPresent -or $appArgs -contains '--direct')" | Add-Content $log
& $py @appArgs *>> $appLog
"$(Get-Date -Format s) app exited rc=$LASTEXITCODE" | Add-Content $log
