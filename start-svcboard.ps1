# svcboard: hidden launcher (same pattern as start-wife.ps1).
# Starts the status app with NO visible window, opens the browser itself.
# Stop it with stop-svcboard.ps1.
param([switch]$Inner)

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
& 'C:\Users\User\miniconda3\python.exe' $app *>> $appLog
"$(Get-Date -Format s) app exited rc=$LASTEXITCODE" | Add-Content $log
