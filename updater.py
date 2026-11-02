"""Sucht auf GitHub nach einer neuen Version und spielt sie ein.

Die ZIP aus dem neuesten Release landet im Unterordner _update des Programmordners. Von dort startet die neue
EXE mit --apply-update, wartet, bis die alte beendet ist, kopiert sich ueber den Programmordner und startet
danach wieder den Dienst oder die App. Das Lua-Skript liest OBS nur beim Start. Aendert es sich, zeigen Dock
und App den Hinweis, OBS einmal neu zu starten.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

import installer

REPO = "hamterfreund08/gvl-media-studio"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASE_PAGE = f"https://github.com/{REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
ASSET = "GVL-Media-Studio.zip"
STAGE = "_update"
RESULT_NAME = "update_result.json"
OBS_FILES = (installer.SCRIPT, "loader.html")
CHECK_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 60
CHUNK = 256 * 1024
COPY_ATTEMPTS = 8
COPY_DELAY = 1.5
DETACHED = 0x00000008 | 0x00000200


def numbers(version):
    return tuple(int(n) for n in re.findall(r"\d+", str(version or ""))[:3])


def is_newer(remote, current):
    return bool(numbers(remote)) and numbers(remote) > numbers(current)


def request(url):
    return urllib.request.Request(url, headers={"User-Agent": "GVL-Media-Studio", "Accept": "application/vnd.github+json"})


def latest():
    with urllib.request.urlopen(request(LATEST_API), timeout=CHECK_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    url = next((a.get("browser_download_url") or "" for a in data.get("assets") or [] if a.get("name") == ASSET), "")
    return {"version": str(data.get("tag_name") or "").lstrip("vV"), "url": url if url.startswith(DOWNLOAD_PREFIX) else "",
            "page": data.get("html_url") or RELEASE_PAGE}


def download(url, folder, progress):
    clean(folder)
    os.makedirs(folder, exist_ok=True)
    archive = os.path.join(folder, ASSET)
    with urllib.request.urlopen(request(url), timeout=DOWNLOAD_TIMEOUT) as resp, open(archive, "wb") as f:
        size = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(CHUNK):
            f.write(chunk)
            done += len(chunk)
            if size:
                progress(done * 100 // size)
    with zipfile.ZipFile(archive) as z:
        z.extractall(folder)
    os.remove(archive)
    missing = installer.missing_files(folder)
    if missing:
        raise OSError(f"Dateien fehlen nach dem Entpacken: {', '.join(missing)}. Der Virenschutz hat sie eventuell entfernt.")


def launch(exe, *args):
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    subprocess.Popen([exe, *args], cwd=os.path.dirname(exe), env=env, creationflags=DETACHED, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start(folder, target, then):
    launch(os.path.join(folder, installer.REQUIRED_FILES[0]), "--apply-update", target, then)


def read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def apply(target, then):
    stage = os.path.dirname(os.path.abspath(sys.executable))
    installer.stop_service()
    restart_obs = then == "service" and any(read_bytes(os.path.join(stage, n)) != read_bytes(os.path.join(target, n)) for n in OBS_FILES)
    error = ""
    for _ in range(COPY_ATTEMPTS):
        try:
            shutil.copytree(stage, target, copy_function=installer.copy_file, dirs_exist_ok=True)
            missing = installer.missing_files(target)
            if missing:
                raise OSError(f"Dateien fehlen: {', '.join(missing)}")
            error = ""
            break
        except OSError as e:
            error = str(e)
            time.sleep(COPY_DELAY)
    if not error and not os.path.isdir(os.path.join(stage, "_internal")):
        shutil.rmtree(os.path.join(target, "_internal"), onexc=installer.force_delete)
    print(f"Update nach {target}: {error or 'fertig'}", flush=True)
    os.makedirs(installer.SETTINGS_DIR, exist_ok=True)
    with open(os.path.join(installer.SETTINGS_DIR, RESULT_NAME), "w", encoding="utf-8") as f:
        json.dump({"error": error, "restartObs": restart_obs}, f)
    args = ("--service", "--updated") if then == "service" else ("--app", "--updated")
    launch(os.path.join(target, installer.REQUIRED_FILES[0]), *args)


def take_result():
    path = os.path.join(installer.SETTINGS_DIR, RESULT_NAME)
    try:
        with open(path, encoding="utf-8") as f:
            result = json.load(f)
    except (OSError, ValueError):
        result = None
    try:
        os.remove(path)
    except OSError:
        pass
    return result if isinstance(result, dict) and "--updated" in sys.argv else None


def clean(folder):
    shutil.rmtree(folder, onexc=installer.force_delete)
