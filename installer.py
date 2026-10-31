"""Richtet GVL Media Studio in OBS ein oder entfernt es wieder.

Kopiert das Programm nach %LOCALAPPDATA%\\GVLMediaStudio, traegt das Dock in die OBS-Einstellungen ein und
meldet das Lua-Skript in allen Szenensammlungen an. OBS muss dafuer geschlossen sein.

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
from ctypes import wintypes
from pathlib import Path

TITLE = "GVL Media Studio"
DOCK_UUID = "6f1d3a52-7c0e-4b1e-9a55-0c5a9f2e1b77"
USER_TARGET = os.path.join(os.environ["LOCALAPPDATA"], "GVLMediaStudio")
SHARED_TARGET = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "GVLMediaStudio")
TARGET = USER_TARGET if USER_TARGET.isascii() else SHARED_TARGET
OBS_DIR = os.path.join(os.environ["APPDATA"], "obs-studio")
SCRIPT = "gvl_media_studio.lua"
MB_ICONERROR = 0x10
MB_ICONINFO = 0x40
SNAPSHOT_PROCESSES = 0x2
REPLAY_SECONDS = 30
SERVICE_EXE = "gvl media studio.exe"
TUNNEL_EXE = "cloudflared.exe"
PROCESS_TERMINATE = 0x1
STOP_WAIT = 5
COPY_ATTEMPTS = 6
COPY_DELAY = 1.5
REQUIRED_FILES = ("GVL Media Studio.exe", SCRIPT, "loader.html")
MB_YESNOCANCEL_QUESTION = 0x23
ANSWER_YES = 6
ANSWER_NO = 7


def message(text, icon=MB_ICONINFO):
    ctypes.windll.user32.MessageBoxW(None, text, TITLE, icon)


class ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260)]


def process_list():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(SNAPSHOT_PROCESSES, 0)
    entry = ProcessEntry()
    entry.dwSize = ctypes.sizeof(ProcessEntry)
    found = []
    more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
    while more:
        found.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile.lower()))
        more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    kernel.CloseHandle(snapshot)
    return found


def obs_running():
    return any(name == "obs64.exe" for _, _, name in process_list())


def stop_service():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    me = os.getpid()
    processes = process_list()
    services = {pid for pid, _, name in processes if name == SERVICE_EXE.lower() and pid != me}
    tunnels = {pid for pid, parent, name in processes if name == TUNNEL_EXE and parent in services}
    for pid in services | tunnels:
        handle = kernel.OpenProcess(PROCESS_TERMINATE, False, pid)
        if handle:
            kernel.TerminateProcess(handle, 0)
            kernel.CloseHandle(handle)
    end = time.time() + STOP_WAIT
    while time.time() < end and services & {pid for pid, _, _ in process_list()}:
        time.sleep(0.2)


def read_text(path):
    with open(path, encoding="utf-8-sig") as f:
        return f.read()


def write_text(path, text):
    tmp = path + ".gvl-tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def set_docks(path, change):
    if not os.path.isfile(path):
        return
    lines = read_text(path).split("\n")
    for i, line in enumerate(lines):
        if line.rstrip("\r").startswith("ExtraBrowserDocks="):
            ending = "\r" if line.endswith("\r") else ""
            value = line.rstrip("\r").split("=", 1)[1]
            lines[i] = "ExtraBrowserDocks=" + json.dumps(change(json.loads(value or "[]")), separators=(",", ":")) + ending
            break
    else:
        for i, line in enumerate(lines):
            if line.rstrip("\r") == "[BasicWindow]":
                lines.insert(i + 1, "ExtraBrowserDocks=" + json.dumps(change([]), separators=(",", ":")))
                break
    write_text(path, "\n".join(lines))


def set_script(path, change):
    data = json.loads(read_text(path))
    modules = data.setdefault("modules", {})
    modules["scripts-tool"] = change(modules.get("scripts-tool") or [])
    write_text(path, json.dumps(data, ensure_ascii=False, indent=4))


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


def scene_files():
    folder = os.path.join(OBS_DIR, "basic", "scenes")
    if not os.path.isdir(folder):
        return []
    return [os.path.join(folder, name) for name in os.listdir(folder) if name.endswith(".json")]


def config_files():
    return [os.path.join(OBS_DIR, name) for name in ("user.ini", "global.ini")]


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


def copy_program(source):
    for attempt in range(1, COPY_ATTEMPTS + 1):
        try:
            shutil.copytree(source, TARGET, copy_function=copy_file, dirs_exist_ok=True)
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
            copy_program(source)
            blocked = bool(missing_files(TARGET))
        except shutil.Error:
            if not source.isascii():
                raise
            blocked = True
        if blocked and source.isascii():
            shutil.rmtree(TARGET, onexc=force_delete)
            program = source
    missing = missing_files(program)
    if missing:
        raise OSError(f"Dateien fehlen in {program}: {', '.join(missing)}. Der Virenschutz hat sie eventuell entfernt.")
    dock = {"title": TITLE, "url": Path(program, "loader.html").as_uri(), "uuid": DOCK_UUID}
    script = {"path": os.path.join(program, SCRIPT).replace("\\", "/"), "settings": {}}
    for path in config_files():
        set_docks(path, lambda docks: other_docks(docks) + [dock])
    for path in scene_files():
        set_script(path, lambda scripts: [s for s in scripts if not is_ours(s.get("path"))] + [script])
    set_replay_buffer(True)
    note = "" if program == TARGET else f"\n\nDer Virenschutz hat das Kopieren blockiert. Das Programm bleibt deshalb in diesem Ordner, den Ordner bitte nicht verschieben oder loeschen:\n{program}"
    message(f"GVL Media Studio ist installiert.\n\nOBS starten und unter Docks den Eintrag GVL Media Studio einschalten.{note}")


def uninstall():
    stop_service()
    for path in config_files():
        set_docks(path, other_docks)
    for path in scene_files():
        set_script(path, lambda scripts: [s for s in scripts if not is_ours(s.get("path"))])
    set_replay_buffer(False)
    for folder in (USER_TARGET, SHARED_TARGET):
        shutil.rmtree(folder, onexc=force_delete)
    message("GVL Media Studio wurde entfernt.")


def started_from_package():
    folder = os.path.normcase(os.path.dirname(os.path.abspath(sys.executable)))
    installed = {os.path.normcase(USER_TARGET), os.path.normcase(SHARED_TARGET)}
    return getattr(sys, "frozen", False) and len(sys.argv) == 1 and folder not in installed


def is_installed():
    if os.path.isfile(os.path.join(TARGET, REQUIRED_FILES[0])):
        return True
    return any(TITLE in read_text(path) for path in config_files() if os.path.isfile(path))


def choose():
    if not is_installed():
        run(install)
        return
    text = "GVL Media Studio ist schon installiert.\n\nJa: installieren oder aktualisieren\nNein: entfernen\nAbbrechen: nichts tun"
    answer = ctypes.windll.user32.MessageBoxW(None, text, TITLE, MB_YESNOCANCEL_QUESTION)
    if answer == ANSWER_YES:
        run(install)
    elif answer == ANSWER_NO:
        run(uninstall)


def run(action):
    if not os.path.isdir(OBS_DIR):
        message("OBS wurde auf diesem PC nicht gefunden.", MB_ICONERROR)
        return
    while obs_running():
        if ctypes.windll.user32.MessageBoxW(None, "Bitte OBS schliessen und dann auf OK klicken.", TITLE, 0x31) != 1:
            return
        time.sleep(1)
    try:
        action()
    except shutil.Error as e:
        failed = e.args[0]
        names = "\n".join(os.path.basename(item[1]) for item in failed[:5])
        message(f"Einrichtung fehlgeschlagen.\n\nDiese Dateien liessen sich nicht kopieren ({len(failed)}):\n{names}\n\nGrund: {failed[0][2]}\n\nDen Ordner zu loeschen hilft hier nicht. Meist blockiert der Virenschutz die Datei. Dort die Datei aus der Quarantaene holen und diesen Ordner als Ausnahme eintragen:\n{TARGET}\n\nDanach dieses Programm erneut starten.", MB_ICONERROR)
    except (OSError, ValueError) as e:
        message(f"Einrichtung fehlgeschlagen:\n{e}", MB_ICONERROR)
