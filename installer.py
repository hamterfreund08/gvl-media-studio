"""Richtet GVL Media Studio in OBS ein oder entfernt es wieder.

Kopiert das Programm nach %LOCALAPPDATA%\\GVLMediaStudio, traegt das Dock in die OBS-Einstellungen ein und
meldet das Lua-Skript in allen Szenensammlungen an. OBS muss dafuer geschlossen sein.
"""
import ctypes
import json
import os
import shutil
import sys
import time
from ctypes import wintypes

TITLE = "GVL Media Studio"
DOCK_UUID = "6f1d3a52-7c0e-4b1e-9a55-0c5a9f2e1b77"
TARGET = os.path.join(os.environ["LOCALAPPDATA"], "GVLMediaStudio")
OBS_DIR = os.path.join(os.environ["APPDATA"], "obs-studio")
SCRIPT = "gvl_media_studio.lua"
MB_ICONERROR = 0x10
MB_ICONINFO = 0x40
SNAPSHOT_PROCESSES = 0x2
REPLAY_SECONDS = 30


def message(text, icon=MB_ICONINFO):
    ctypes.windll.user32.MessageBoxW(None, text, TITLE, icon)


class ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260)]


def obs_running():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(SNAPSHOT_PROCESSES, 0)
    entry = ProcessEntry()
    entry.dwSize = ctypes.sizeof(ProcessEntry)
    found = False
    more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
    while more and not found:
        found = entry.szExeFile.lower() == "obs64.exe"
        more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    kernel.CloseHandle(snapshot)
    return found


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


def install():
    source = os.path.dirname(os.path.abspath(sys.executable))
    if os.path.normcase(source) != os.path.normcase(TARGET):
        shutil.rmtree(TARGET, ignore_errors=True)
        shutil.copytree(source, TARGET)
    dock = {"title": TITLE, "url": "file:///" + os.path.join(TARGET, "loader.html").replace("\\", "/"), "uuid": DOCK_UUID}
    script = {"path": os.path.join(TARGET, SCRIPT).replace("\\", "/"), "settings": {}}
    for path in config_files():
        set_docks(path, lambda docks: other_docks(docks) + [dock])
    for path in scene_files():
        set_script(path, lambda scripts: [s for s in scripts if not is_ours(s.get("path"))] + [script])
    set_replay_buffer(True)
    message("GVL Media Studio ist installiert.\n\nOBS starten und unter Docks den Eintrag GVL Media Studio einschalten.")


def uninstall():
    for path in config_files():
        set_docks(path, other_docks)
    for path in scene_files():
        set_script(path, lambda scripts: [s for s in scripts if not is_ours(s.get("path"))])
    set_replay_buffer(False)
    shutil.rmtree(TARGET, ignore_errors=True)
    message("GVL Media Studio wurde entfernt.")


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
    except (OSError, ValueError) as e:
        message(f"Einrichtung fehlgeschlagen:\n{e}", MB_ICONERROR)
