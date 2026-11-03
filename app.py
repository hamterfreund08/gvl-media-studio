"""Fenster der EXE: installieren, aktualisieren, reparieren und deinstallieren. Das Fenster steckt in gvl_common.app."""
from gvl_common import app

import installer
import updater

EN = {
    "Match-Grafiken für OBS": "Match graphics for OBS",
    "Wirklich deinstallieren? Einstellungen und Stream-Plan bleiben erhalten.": "Really uninstall? Settings and stream plan are kept.",
    "Installiert. OBS starten und unter Docks den Eintrag GVL Media Studio einschalten.": "Installed. Start OBS and enable GVL Media Studio under Docks.",
    "GVL Media Studio wurde entfernt.": "GVL Media Studio was removed.",
}


def run(version):
    app.run(version, installer, updater, "GVL Media Studio", "Match-Grafiken für OBS",
            "Wirklich deinstallieren? Einstellungen und Stream-Plan bleiben erhalten.", EN)
