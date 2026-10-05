# svcboard-watchdog (ноутбук, режим --direct): обновление из локального bare-репо
# (origin, куда ПК пушит main) + скрытый запуск svcboard.py.
# Лежит рядом с svcboard.py; запускается из Startup: watchdog-startup-laptop.vbs
$ErrorActionPreference = 'SilentlyContinue'
# отметка: проверка реально отработала
Add-Content "$env:TEMP\svcboard-watchdog.log" "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') check"
$dir = $PSScriptRoot
$py  = 'C:\Python310\python.exe'

# 0. Обновление из локального bare-репо (его пополняет ПК через ssh).
# Вокруг git временно включаем EAP=Continue: при SilentlyContinue stderr git
# (и его код возврата через перенаправление) проглатывается - лог был пустым.
# Пустое значение exit= означает, что git не запустился вообще.
if (Test-Path (Join-Path $dir '.git')) {
    $eap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $LASTEXITCODE = $null
    git -C $dir pull --ff-only origin main *> "$env:TEMP\svcboard-pull.log"
    ("exit=" + $LASTEXITCODE) | Out-File -Append "$env:TEMP\svcboard-pull.log"
    $ErrorActionPreference = $eap
}

# 1. Порт слушает - выходим тихо
if (Get-NetTCPConnection -LocalPort 49480 -State Listen -ErrorAction SilentlyContinue) { exit 0 }

# 2. Порта нет. Если процесс есть - висит: перезапускаем
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
         Where-Object { $_.CommandLine -like '*svcboard.py*' }
if ($procs) {
    $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Start-Sleep -Seconds 2
}

# 3. Стартуем скрыто; ноут ходит на ноды напрямую - флаг --direct
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
Start-Process -FilePath $py -ArgumentList 'svcboard.py --direct' -WorkingDirectory $dir -WindowStyle Hidden `
    -RedirectStandardOutput "$env:TEMP\svcboard-out-$stamp.log" `
    -RedirectStandardError  "$env:TEMP\svcboard-err-$stamp.log"
exit 0
