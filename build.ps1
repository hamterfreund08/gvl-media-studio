$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venv = Join-Path $env:TEMP "gvl-media-build"
$cloudflared = Join-Path $root "cloudflared.exe"
if (-not (Test-Path $cloudflared)) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile $cloudflared -UseBasicParsing
}
if (-not (Test-Path $venv)) { python -m venv $venv }
& "$venv\Scripts\python.exe" -m pip install --quiet -r (Join-Path $root "requirements.txt")
Push-Location $root
& "$venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --noconsole --onedir --name "GVL Media Studio" --hidden-import installer --hidden-import updater --hidden-import app --add-data "$root\web;web" --add-binary "$root\cloudflared.exe;." --distpath "$root\dist" --workpath "$root\build" --specpath "$root\build" "$root\media_studio.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller ist fehlgeschlagen" }
$app = Join-Path $root "dist\GVL Media Studio"
Copy-Item obs\gvl_media_studio.lua, obs\loader.html, THIRD_PARTY_NOTICES.md $app
Copy-Item obs\stingers (Join-Path $app "stingers") -Recurse -Force
$version = (Select-String -Path (Join-Path $root "media_studio.py") -Pattern '^VERSION = "(.+)"').Matches[0].Groups[1].Value
[IO.File]::WriteAllText((Join-Path $app "version.txt"), $version)
& "$venv\Scripts\python.exe" -c "import shutil, sys; shutil.make_archive(sys.argv[1], 'zip', sys.argv[2])" (Join-Path $root "dist\GVL-Media-Studio") $app
Pop-Location
