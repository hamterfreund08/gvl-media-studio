@echo off
if exist "%~dp0GVL Media Studio.exe" (
    start "" "%~dp0GVL Media Studio.exe" --install
    exit /b 0
)
echo.
echo  GVL Media Studio.exe wurde nicht gefunden.
echo  GVL Media Studio.exe was not found.
echo.
echo  Lade GVL-Media-Studio.zip unter Releases herunter und entpacke die ganze ZIP.
echo  Download GVL-Media-Studio.zip from Releases and extract the whole ZIP.
echo.
echo  Der gruene Code-Knopf auf GitHub enthaelt das Programm nicht.
echo  The green Code button on GitHub does not include the program.
echo.
start "" "https://github.com/hamterfreund08/gvl-media-studio/releases/latest"
pause
