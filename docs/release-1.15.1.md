## GVL Media Studio 1.15.1

Lade `GVL-Media-Studio.exe` herunter, schließe OBS, starte die Datei und klicke auf **Aktualisieren**. Mit 1.9.0 oder neuer geht es auch direkt im Panel über **Jetzt aktualisieren**.

Download `GVL-Media-Studio.exe`, close OBS, run it and click **Update**. With 1.9.0 or newer, update right in the panel with **Update now**.

### Behoben

- OBS konnte beim automatischen Szenenwechsel (Wiederholung, Aufstellung) einfrieren. Szene und Übergang werden jetzt im richtigen Teil von OBS umgestellt
- Nach einem eingefrorenen OBS lief der Dienst im Hintergrund weiter und blockierte den nächsten Start. Er beendet sich jetzt zuverlässig mit seinem OBS

### Fixed

- OBS could freeze during automatic scene switches (replay, lineup). Scene and transition are now switched in the right part of OBS
- After OBS froze, the service kept running in the background and blocked the next start. It now reliably exits together with its OBS
