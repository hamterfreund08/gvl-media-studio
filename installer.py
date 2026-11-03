"""Richtet GVL Media Studio in OBS ein oder entfernt es wieder.

Die EXE enthaelt alles. Sie kopiert sich nach %LOCALAPPDATA%\\GVLMediaStudio, legt OBS-Skript, Startseite des
Panels und Stinger-Videos daneben, traegt das Dock in die OBS-Einstellungen ein und meldet das Lua-Skript in
allen Szenensammlungen an. OBS muss dafuer geschlossen sein.

Das Lua-Skript kann Pfade mit Sonderzeichen nicht oeffnen. Enthaelt der Benutzerpfad welche, zum Beispiel
einen Umlaut im Windows-Namen, wird stattdessen nach %PROGRAMDATA%\\GVLMediaStudio installiert.

Blockiert der Virenschutz das Kopieren, bleibt das Programm im entpackten Ordner und OBS startet es von dort.
"""
import ctypes
import json
import os
import shutil
import stat
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from gvl_common import obs
from gvl_common.obs import config_files, obs_running, process_list, read_text, scene_files, set_docks, set_script, write_text

TITLE = "GVL Media Studio"
DOCK_UUID = "6f1d3a52-7c0e-4b1e-9a55-0c5a9f2e1b77"
USER_TARGET = os.path.join(os.environ["LOCALAPPDATA"], "GVLMediaStudio")
SHARED_TARGET = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "GVLMediaStudio")
TARGET = USER_TARGET if USER_TARGET.isascii() else SHARED_TARGET
OBS_DIR = obs.OBS_DIR
SETTINGS_DIR = os.path.join(os.environ["APPDATA"], "GVLMediaStudio")
VERSION_FILE = "version.txt"
SCRIPT = "gvl_media_studio.lua"
MB_ICONERROR = 0x10
MB_ICONINFO = 0x40
REPLAY_SECONDS = 30
SERVICE_EXE = "gvl media studio.exe"
TUNNEL_EXE = "cloudflared.exe"
COPY_ATTEMPTS = 6
COPY_DELAY = 1.5
REQUIRED_FILES = ("GVL Media Studio.exe", SCRIPT, "loader.html")
PACKAGE_DIR = "package"
STINGER_DIR = "stingers"
STINGER_ID = "obs_stinger_transition"
STINGER_PREFIXES = ("GVL Swipe", "GVL Stinger")


def message(text, icon=MB_ICONINFO):
    ctypes.windll.user32.MessageBoxW(None, text, TITLE, icon)


def stop_service():
    obs.stop_service(SERVICE_EXE, TUNNEL_EXE)


def stinger_transitions(program):
    folder = os.path.join(program, STINGER_DIR)
    try:
        listing = json.loads(read_text(os.path.join(folder, "stingers.json")))
    except (OSError, ValueError):
        return []
    return [{"name": item["name"], "id": STINGER_ID,
             "settings": {"path": os.path.join(folder, item["file"]).replace("\\", "/"), "tp_type": 0,
                          "transition_point": int(item["point"]), "preload": True}}
            for item in listing if os.path.isfile(os.path.join(folder, item["file"]))]


def is_our_transition(transition):
    return transition.get("id") == STINGER_ID and str(transition.get("name") or "").startswith(STINGER_PREFIXES)


def stinger_key(transition):
    settings = transition.get("settings") or {}
    return transition.get("name"), settings.get("path"), settings.get("transition_point")


def set_transitions(path, wanted):
    data = json.loads(read_text(path))
    current = data.get("transitions") or []
    ours = [t for t in current if is_our_transition(t)]
    if sorted(map(stinger_key, ours)) == sorted(map(stinger_key, wanted)):
        return
    data["transitions"] = [t for t in current if not is_our_transition(t)] + wanted
    write_text(path, json.dumps(data, ensure_ascii=False, indent=4))


def register_stingers(program):
    wanted = stinger_transitions(program)
    for path in scene_files():
        try:
            set_transitions(path, wanted)
        except (OSError, ValueError):
            pass


def set_ini_value(path, section, key, value):
    lines = read_text(path).split("\n")
    start = next((i for i, line in enumerate(lines) if line.rstrip("\r") == section), None)
    if start is None:
        return
    ending = "\r" if lines[start].endswith("\r") else ""
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("["):
            lines.insert(i, f"{key}={value}{ending}")
            break
        if lines[i].startswith(f"{key}="):
            lines[i] = f"{key}={value}{ending}"
            break
    else:
        lines.append(f"{key}={value}{ending}")
    write_text(path, "\n".join(lines))


def set_replay_buffer(enabled):
    folder = os.path.join(OBS_DIR, "basic", "profiles")
    if not os.path.isdir(folder):
        return
    for name in os.listdir(folder):
        path = os.path.join(folder, name, "basic.ini")
        if os.path.isfile(path):
            for section in ("[SimpleOutput]", "[AdvOut]"):
                set_ini_value(path, section, "RecRB", "true" if enabled else "false")
                set_ini_value(path, section, "RecRBTime", REPLAY_SECONDS)


def is_ours(path):
    return os.path.basename(str(path)).lower() == SCRIPT


def other_docks(docks):
    return [d for d in docks if d.get("title") != TITLE]


def make_writable(path):
    if os.path.exists(path):
        os.chmod(path, stat.S_IWRITE)


def force_delete(function, path, _error):
    try:
        make_writable(path)
        function(path)
    except OSError:
        pass


def copy_file(source, target):
    make_writable(target)
    shutil.copy2(source, target)
    make_writable(target)


def bundled_package():
    folder = os.path.join(getattr(sys, "_MEIPASS", ""), PACKAGE_DIR)
    return folder if os.path.isdir(folder) else ""


def write_program(target):
    exe = os.path.abspath(sys.executable)
    package = bundled_package()
    if not package:
        shutil.copytree(os.path.dirname(exe), target, copy_function=copy_file, dirs_exist_ok=True)
        return
    os.makedirs(target, exist_ok=True)
    own = os.path.join(target, REQUIRED_FILES[0])
    if os.path.normcase(exe) != os.path.normcase(own):
        copy_file(exe, own)
    shutil.copytree(package, target, copy_function=copy_file, dirs_exist_ok=True)


def copy_program(target):
    for attempt in range(1, COPY_ATTEMPTS + 1):
        try:
            write_program(target)
            return
        except OSError:
            if attempt == COPY_ATTEMPTS:
                raise
            time.sleep(COPY_DELAY)


def missing_files(folder):
    return [name for name in REQUIRED_FILES if not os.path.isfile(os.path.join(folder, name))]


def install():
    source = os.path.dirname(os.path.abspath(sys.executable))
    program = TARGET
    if os.path.normcase(source) != os.path.normcase(TARGET):
        stop_service()
        for folder in (USER_TARGET, SHARED_TARGET):
            shutil.rmtree(folder, onexc=force_delete)
        try:
            copy_program(TARGET)
            blocked = bool(missing_files(TARGET))
        except OSError:
            if not source.isascii():
                raise
            blocked = True
        if blocked and source.isascii():
            shutil.rmtree(TARGET, onexc=force_delete)
            program = source
            if bundled_package():
                copy_program(source)
    elif bundled_package():
        copy_program(TARGET)
    missing = missing_files(program)
    if missing:
        raise OSError(f"Dateien fehlen in {program}: {', '.join(missing)}. Der Virenschutz hat sie eventuell entfernt.")
    dock = {"title": TITLE, "url": Path(program, "loader.html").as_uri(), "uuid": DOCK_UUID}
    script = {"path": os.path.join(program, SCRIPT).replace("\\", "/"), "settings": {}}
    for path in config_files():
        set_docks(path, lambda docks: other_docks(docks) + [dock])
    for path in scene_files():
        set_script(path, lambda scripts: [s for s in scripts if not is_ours(s.get("path"))] + [script])
    register_stingers(program)
    set_replay_buffer(True)
    note = "" if program == TARGET else f" Der Virenschutz hat das Kopieren blockiert. Das Programm bleibt deshalb in diesem Ordner, bitte nicht verschieben oder löschen: {program}"
    return f"Installiert. OBS starten und unter Docks den Eintrag GVL Media Studio einschalten.{note}"


def uninstall():
    stop_service()
    for path in config_files():
        set_docks(path, other_docks)
    for path in scene_files():
        set_script(path, lambda scripts: [s for s in scripts if not is_ours(s.get("path"))])
        set_transitions(path, [])
    set_replay_buffer(False)
    for folder in (USER_TARGET, SHARED_TARGET):
        shutil.rmtree(folder, onexc=force_delete)
    return "GVL Media Studio wurde entfernt."


def started_from_package():
    folder = os.path.normcase(os.path.dirname(os.path.abspath(sys.executable)))
    installed = {os.path.normcase(USER_TARGET), os.path.normcase(SHARED_TARGET)}
    return getattr(sys, "frozen", False) and len(sys.argv) == 1 and folder not in installed


def installed_folder():
    for path in config_files():
        if not os.path.isfile(path):
            continue
        for line in read_text(path).split("\n"):
            if not line.startswith("ExtraBrowserDocks="):
                continue
            try:
                docks = json.loads(line.rstrip("\r").split("=", 1)[1] or "[]")
            except ValueError:
                continue
            for dock in docks:
                url = str(dock.get("url") or "")
                if dock.get("title") == TITLE and url.startswith("file:"):
                    return os.path.dirname(urllib.request.url2pathname(urllib.parse.urlparse(url).path))
    return TARGET if os.path.isfile(os.path.join(TARGET, REQUIRED_FILES[0])) else ""


def version_in(folder):
    try:
        return read_text(os.path.join(folder, VERSION_FILE)).strip()
    except OSError:
        return ""


def describe(error):
    if isinstance(error, shutil.Error):
        failed = error.args[0]
        names = ", ".join(os.path.basename(item[1]) for item in failed[:5])
        return (f"Diese Dateien ließen sich nicht kopieren ({len(failed)}): {names}. Grund: {failed[0][2]}. "
                f"Meist blockiert der Virenschutz die Datei. Dort die Datei aus der Quarantäne holen und diesen Ordner als Ausnahme eintragen: {TARGET}")
    return str(error)
