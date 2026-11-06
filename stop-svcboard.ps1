# Stop the svcboard app and its SSH tunnel (all hidden, so no window to close).
$ErrorActionPreference = 'SilentlyContinue'
$hits = @()

$svc = Get-NetTCPConnection -LocalPort 49480 -State Listen
if ($svc) {
  $svc.OwningProcess | Select-Object -Unique | ForEach-Object {
    Stop-Process -Id $_ -Force
    $hits += "app PID $_"
  }
}

$tun = Get-NetTCPConnection -State Listen |
  Where-Object { $_.LocalPort -in 2333,2334,2335,2336,2351,2352,2353,2354,2355 }
if ($tun) {
  $tun.OwningProcess | Select-Object -Unique | ForEach-Object {
    Stop-Process -Id $_ -Force
    $hits += "tunnel PID $_"
  }
}

# test-run artifacts (app stopped mid-run): the runner process and its 2222 tunnel
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -like '*run_tests.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $hits += "runner PID $($_.ProcessId)" }
$tt = Get-NetTCPConnection -LocalPort 2222 -State Listen -ErrorAction SilentlyContinue
if ($tt) {
  $tt.OwningProcess | Select-Object -Unique | ForEach-Object {
    if ((Get-Process -Id $_ -ErrorAction SilentlyContinue).ProcessName -eq 'ssh') {
      Stop-Process -Id $_ -Force
      $hits += "tests tunnel PID $_"
    }
  }
}

Start-Sleep -Milliseconds 500
$left = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object { $_.LocalPort -in 49480,2222,2333,2334,2335,2336,2351,2352,2353,2354,2355 }

if ($hits) {
  $msg = "$(Get-Date -Format s) stopped: " + ($hits -join ', ')
  $msg | Add-Content "$env:TEMP\svcboard-start.log"
  $msg
  if ($left) { "WARNING still listening: " + (($left.LocalPort | Sort-Object -Unique) -join ',') }
  else { "CLEAN: 49480, 2222, 2333-2336, 2351-2355 free" }
} else {
  "svcboard not running"
}
