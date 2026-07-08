@echo off
setlocal
cd /d "%~dp0"

if not exist venv (
    echo Ersteinrichtung: erstelle virtuelle Umgebung und installiere Abhaengigkeiten...
    python -m venv venv
    if errorlevel 1 (
        echo Fehler: Python wurde nicht gefunden. Bitte Python 3.11+ installieren.
        pause
        exit /b 1
    )
    venv\Scripts\python.exe -m pip install --upgrade pip >nul
    venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Fehler beim Installieren der Abhaengigkeiten.
        pause
        exit /b 1
    )
)

echo Starte Budget Tracker...
start "Budget Tracker Server" cmd /k venv\Scripts\python.exe -m server.app
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:5000

echo.
echo Der Server laeuft in einem separaten Fenster ("Budget Tracker Server").
echo Zum Beenden dieses Fenster einfach schliessen oder STRG+C druecken.
