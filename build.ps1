$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venv = Join-Path $env:TEMP "gvl-media-build"
$package = Join-Path $env:TEMP "gvl-media-package"
$cloudflared = Join-Path $root "cloudflared.exe"
$common = @((Join-Path $root "gvl-obs-common"), (Join-Path $root "..\gvl-obs-common")) | Where-Object { Test-Path (Join-Path $_ "gvl_common") } | Select-Object -First 1
if (-not $common) { throw "gvl-obs-common fehlt. Es muss neben media-studio oder als Unterordner liegen." }
if (-not (Test-Path $cloudflared)) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile $cloudflared -UseBasicParsing
}
if (-not (Test-Path $venv)) { python -m venv $venv }
& "$venv\Scripts\python.exe" -m pip install --quiet -r (Join-Path $root "requirements.txt")
Push-Location $root
$version = (Select-String -Path (Join-Path $root "media_studio.py") -Pattern '^VERSION = "(.+)"').Matches[0].Groups[1].Value
if (Test-Path $package) { Remove-Item $package -Recurse -Force }
New-Item -ItemType Directory $package | Out-Null
Copy-Item obs\gvl_media_studio.lua, obs\loader.html, THIRD_PARTY_NOTICES.md $package
Copy-Item obs\stingers (Join-Path $package "stingers") -Recurse
[IO.File]::WriteAllText((Join-Path $package "version.txt"), $version)
& "$venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --noconsole --onefile --name "GVL Media Studio" --hidden-import installer --hidden-import updater --hidden-import app --paths "$common" --add-data "$root\web;web" --add-data "$common\gvl_common\assets;gvl_common\assets" --add-data "$package;package" --add-binary "$root\cloudflared.exe;." --distpath "$root\build\onefile" --workpath "$root\build" --specpath "$root\build" "$root\media_studio.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller ist fehlgeschlagen" }
$exe = Join-Path $root "build\onefile\GVL Media Studio.exe"
$app = Join-Path $root "dist\GVL Media Studio"
if (Test-Path $app) { Remove-Item $app -Recurse -Force }
New-Item -ItemType Directory $app | Out-Null
Copy-Item $exe $app
Copy-Item (Join-Path $package "*") $app -Recurse
Copy-Item $exe (Join-Path $root "dist\GVL-Media-Studio.exe") -Force
& "$venv\Scripts\python.exe" -c "import shutil, sys; shutil.make_archive(sys.argv[1], 'zip', sys.argv[2])" (Join-Path $root "dist\GVL-Media-Studio") $app
Pop-Location
