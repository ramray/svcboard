# svcboard-watchdog.ps1 — следит за svcboard (порт 49480)
# Логика: порт жив -> выходим. Порта нет -> (процесс висит мёртвым? убиваем) -> стартуем svcboard.py скрыто.
# Запускается задачей планировщика "svcboard-watchdog" каждую минуту.

$ErrorActionPreference = 'SilentlyContinue'
# отметка в логе: проверка реально отработала (для диагностики)
Add-Content "$env:TEMP\svcboard-watchdog.log" "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') check"
$port = 49480
$dir  = 'C:\Users\User\Documents\Default Project\svcboard'
$py   = 'C:\Users\User\miniconda3\python.exe'

# 1. Порт слушает — всё в порядке, выходим тихо
if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { exit 0 }

# 2. Порта нет. Если процесс svcboard есть — он завис (порт не поднял): перезапускаем
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
         Where-Object { $_.CommandLine -like '*svcboard.py*' }
if ($procs) {
    $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Start-Sleep -Seconds 2
}

# 3. Стартуем скрыто; логи вывода — в TEMP с меткой времени (не затирают друг друга)
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
Start-Process -FilePath $py -ArgumentList 'svcboard.py' -WorkingDirectory $dir -WindowStyle Hidden `
    -RedirectStandardOutput "$env:TEMP\svcboard-out-$stamp.log" `
    -RedirectStandardError  "$env:TEMP\svcboard-err-$stamp.log"
exit 0
