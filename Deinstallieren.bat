@echo off
if exist "%~dp0GVL Media Studio.exe" (
    start "" "%~dp0GVL Media Studio.exe" --uninstall
    exit /b 0
)
echo.
echo  GVL Media Studio.exe wurde nicht gefunden. Starte diese Datei aus dem entpackten Release-Ordner.
echo  GVL Media Studio.exe was not found. Run this file from the extracted release folder.
echo.
pause
