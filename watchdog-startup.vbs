' svcboard-watchdog.vbs — тихий запуск проверки svcboard при входе в систему
' Кладётся в: %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
' Сама логика — в watchdog.ps1 (проект Default Project\svcboard)
CreateObject("WScript.Shell").Run "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""C:\Users\User\Documents\Default Project\svcboard\watchdog.ps1""", 0, False
