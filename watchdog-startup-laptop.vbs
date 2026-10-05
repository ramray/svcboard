' svcboard-watchdog (ноутбук) - тихий запуск проверки при входе в систему.
' Копируется в: %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
' Сама логика - watchdog-laptop.ps1 (этот же каталог репозитория).
CreateObject("WScript.Shell").Run "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""C:\Users\roman\svcboard\watchdog-laptop.ps1""", 0, False
