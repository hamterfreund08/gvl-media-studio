<p align="center">
  <img src="web/gvl_logo.png" alt="GVL" width="96">
</p>

<h1 align="center">GVL Media Studio</h1>

<p align="center">
  Professionelle Live-Grafiken für Roblox-Fußball in OBS.<br>
  Entwickelt von <a href="https://github.com/hamterfreund08">hamterfreund08</a> für die German Virtual League.
</p>

<p align="center">
  <b>Deutsch</b> | <a href="README.en.md">English</a>
</p>

<p align="center">
  <img src="docs/images/starting.png" alt="Stream Starting Soon" width="760">
</p>

## Was ist das?

GVL Media Studio verbindet ein laufendes GVL-Spiel in Roblox mit OBS. Aufstellungen, Tore, Karten, Wechsel und Tabellen kommen direkt aus dem Spiel und erscheinen als Grafiken im Stream. Bedient wird alles über ein Panel in OBS.

Du brauchst keine Portfreigabe und musst keine IP-Adresse teilen. Die Verbindung läuft über einen Cloudflare-Tunnel, den das Panel per Klick aufbaut.

## Funktionen

**Match-Grafiken**
- Match-Vorschau mit großen Vereinswappen, Form und Anstoßzeit
- Animierte Aufstellung für beide Teams inklusive Bank und Trainer
- Tabelle mit Live-Berechnung während des Spiels
- Spielstand mit Torschützen, Spieltag und Torjägerliste
- Einblendungen für Tore, Karten und Wechsel, automatisch oder per Hand
- Match-Leiste, Schiedsrichter, Trainer, Spieler und Man of the Match

**Stream-Produktion**
- Stream Starting Soon mit Countdown und dem nächsten Spiel
- Gleich zurück und Stream-Ende
- Stream-Plan: Wähle die kommenden GVL-Spiele aus, die du streamst
- Kommentatoren-Einblendung und eigene Bauchbinden
- Laufband mit eigenem Text, heutigen Ergebnissen oder der Aufstellung eines Teams

**Replays**
- 3D-Replay im Spiel, das nur du siehst und das OBS überträgt
- Video-Replay über den OBS Replay Buffer, normal oder in Zeitlupe
- Tore auf Wunsch automatisch als Clip sichern

**Panel**
- Sprache auf Deutsch oder Englisch umschaltbar
- Einstellungen und Stream-Plan bleiben gespeichert

<p align="center">
  <img src="docs/images/plan.png" alt="Stream-Plan" width="49%">
  <img src="docs/images/top.png" alt="Torjäger" width="49%">
</p>

<p align="center">
  <img src="docs/images/panel_de.png" alt="Panel in OBS" width="300">
</p>

## Voraussetzungen

| | |
|---|---|
| **System** | Windows 10 oder 11, 64 Bit |
| **OBS Studio** | Aktuelle Version, getestet mit 32.1.2 |
| **Roblox** | Du bist im GVL-Spiel im Team `Media` |
| **Internet** | Stabile Verbindung für den Cloudflare-Tunnel |

Python oder andere Programme musst du für die normale Nutzung nicht installieren.

## Installation

> **Wichtig:** Lade **nicht** den grünen **Code**-Knopf herunter. Darin fehlt das Programm. Du brauchst die Datei `GVL-Media-Studio.zip` aus den Releases.

1. Lade die aktuelle [`GVL-Media-Studio.zip`](https://github.com/hamterfreund08/gvl-media-studio/releases/latest/download/GVL-Media-Studio.zip) herunter.
2. Entpacke die ZIP komplett in einen Ordner. Starte nichts direkt aus dem ZIP-Fenster.
3. Schließe OBS.
4. Starte `Installieren.bat` und warte auf die Bestätigung.
5. Öffne OBS und schalte im Menü **Docks** den Eintrag **GVL Media Studio** ein.

Das Panel ist jetzt da, und die Quelle `GVL Broadcast Overlay` liegt in deinen Szenen. Das Programm startet und beendet sich ab jetzt automatisch mit OBS.

> Windows SmartScreen kann beim ersten Start warnen, weil das Programm nicht signiert ist. Klicke dann auf **Weitere Informationen** und **Trotzdem ausführen**. Lade die Datei nur aus diesem Repository.

### Aktualisieren

OBS schließen, die neue ZIP entpacken und `Installieren.bat` erneut starten. Einstellungen und Stream-Plan bleiben erhalten.

### Deinstallieren

OBS schließen und `Deinstallieren.bat` starten. Deine Einstellungen liegen in `%APPDATA%\GVLMediaStudio`. Diesen Ordner kannst du bei Bedarf selbst löschen.

## Mit einem Spiel verbinden

1. Öffne im Panel den Reiter **Match** und klicke auf **Verbindung starten**.
2. Klicke auf **Link kopieren**.
3. Gehe im Spiel ins Team `Media`. Unten rechts erscheint das Fenster **Media Studio**.
4. Füge den Link ein und klicke auf **Connect**.

Das Panel zeigt dann **Verbunden**, und alle Match-Grafiken stehen bereit. Spielplan, Tabelle und Torjäger kommen ebenfalls vom Spiel. Der Reiter **Plan** füllt sich deshalb erst nach der ersten Verbindung und merkt sich die Daten danach.

Der Link gilt, solange der Tunnel läuft. Startest du den Tunnel neu, klickst du im Spiel auf **Disconnect** und fügst den neuen Link ein. Teile den Link mit niemandem.

## Ablauf eines Streams

| Wann | Was du machst |
|---|---|
| Vor dem Stream | Unter **Plan** deine Spiele wählen, unter **Stream** die Kommentatoren eintragen |
| Stream beginnt | **Stream Starting Soon** einblenden |
| Vor Anstoß | Match-Vorschau, Aufstellung Heim und Gast, Tabelle |
| Während des Spiels | Tore, Karten und Wechsel laufen automatisch, dazu Replays nach Bedarf |
| Halbzeit | Spielstand, Tabelle, Torjäger, **Gleich zurück** |
| Abpfiff | Spielstand, Man of the Match, Tabelle, **Stream-Ende** |

Mit **Alles ausblenden** räumst du das Bild jederzeit frei.

## Replays

**Replay im Spiel** nutzt das 3D-Replay von Roblox und läuft nur auf deinem Bildschirm. Du startest es im Panel, im Media-Fenster im Spiel oder mit der Taste `F8`. Es speichert bis zu 30 Sekunden. Im Replay erscheinen nur Spieler, die auf dem Platz waren.

**Video-Replay** nutzt den OBS Replay Buffer und zeigt genau das Bild deines Streams.

1. Klicke auf **Replay-Puffer starten**.
2. Klicke nach einer Szene auf **Clip speichern**.
3. Spiele den Clip mit **Normal** oder **Zeitlupe** ab.

Der Puffer braucht zusätzliche CPU-Leistung. Schalte ihn nur beim Streamen ein. Mit **Ordner wählen** legst du fest, wo Clips landen. Am besten ist ein Laufwerk mit viel freiem Platz, denn Clips werden nicht automatisch gelöscht.

## Häufige Probleme

| Problem | Lösung |
|---|---|
| Panel fehlt in OBS | Menü **Docks** öffnen und **GVL Media Studio** einschalten |
| „Dienst nicht erreichbar“ | OBS neu starten |
| Grafiken erscheinen nicht | Prüfen, ob `GVL Broadcast Overlay` in der Szene oben liegt und sichtbar ist |
| Media-Fenster fehlt im Spiel | Du musst im Team `Media` sein |
| „OBS not reachable“ im Spiel | Tunnel im Panel neu starten und den neuen Link einfügen |
| „Disconnect first“ im Spiel | Erst **Disconnect** klicken, dann den neuen Link einfügen |
| Plan bleibt leer | Einmal mit einem Spiel verbinden |
| Tabelle ist ausgegraut | Der Wettbewerb hat keine Tabelle, zum Beispiel ein Pokal |
| Replay ohne Spieler | Es war niemand aus Home, Away oder Referee auf dem Platz |

Fehlerprotokoll: `%APPDATA%\GVLMediaStudio\media_studio.log`

## Sicherheit

- Das Paket enthält keine Zugangsdaten, API-Schlüssel oder Sheet-Adressen.
- Das Programm ist nur auf deinem eigenen PC erreichbar.
- Über den Tunnel ist nur der Dateneingang erreichbar, geschützt durch ein zufälliges Geheimnis im Link.
- Panel und Overlay sind von außen nicht erreichbar.
- Das Spiel akzeptiert nur Links von `trycloudflare.com`.

## Selbst bauen

Nur für Entwickler nötig. Du brauchst Windows und Python 3.13.

```powershell
git clone https://github.com/hamterfreund08/gvl-media-studio.git
cd gvl-media-studio
powershell -ExecutionPolicy Bypass -File build.ps1
```

Das Skript lädt `cloudflared` automatisch herunter und legt das fertige Paket unter `dist\GVL-Media-Studio.zip` ab.

### Aufbau

| Pfad | Inhalt |
|---|---|
| `media_studio.py` | Lokaler Dienst für Panel, Overlay, Tunnel und Replays |
| `installer.py` | Einrichtung und Entfernung in OBS |
| `web/` | Panel (`dock.html`) und Overlay (`overlay.html`) |
| `obs/` | OBS-Skript und Startseite des Panels |
| `build.ps1` | Baut das Paket |

Der Teil im Roblox-Spiel gehört zum GVL-System und ist nicht Teil dieses Repositorys.

## Lizenz

Copyright (c) 2026 hamterfreund08. Alle Rechte vorbehalten.

Enthaltene Komponenten von Drittanbietern stehen unter ihren eigenen Lizenzen, siehe [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Vereinswappen und Markennamen gehören ihren jeweiligen Inhabern.
