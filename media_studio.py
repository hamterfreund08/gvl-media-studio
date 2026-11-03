"""GVL Media Studio: lokaler Dienst fuer das OBS-Dock und das Overlay.

OBS startet dieses Programm ueber obs/gvl_media_studio.lua. Es lauscht nur auf 127.0.0.1 und baut auf Knopfdruck
einen Cloudflare-Tunnel auf. Der Link enthaelt ein Geheimnis und wird im Spiel eingefuegt, danach schickt der
Stadion-Server seine Daten an /in/<geheimnis>/push. Ueber den Tunnel ist nur dieser Eingang erreichbar.

Konferenz: Traegt der Streamer einen Regie-Link ein und sendet fuer die Konferenz, stellt das Lua-Skript den
Stream-Dienst von OBS auf das GVL Konferenz-Modul um. Jedes Update aus dem Spiel geht dann zusaetzlich an die Regie,
die mit dem Tally (live, preview, off) antwortet. Ohne aktive Konferenz laeuft alles wie bisher.

Tabelle, Form und Spieltag kommen aus dem Standings-Sheet, Logos und Avatare von der Roblox-Thumbnail-API.
Das Programm beendet sich selbst, sobald OBS geschlossen wird.
"""
import asyncio
import datetime
import json
import os
import re
import secrets
import shutil
import sys
import time
import traceback
import urllib.parse
import zipfile

FROZEN = getattr(sys, "frozen", False)
BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
if not FROZEN:
    for common in (os.path.join(BASE_DIR, "gvl-obs-common"), os.path.join(BASE_DIR, "..", "gvl-obs-common")):
        if os.path.isdir(common):
            sys.path.insert(0, common)
            break

import aiohttp
from aiohttp import web

import gvl_common
from gvl_common import dns, module, obs, roblox, service
from gvl_common.tunnel import Tunnel

import installer
import updater

WEB_DIR = os.path.join(BASE_DIR, "web")
EXE_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
CLOUDFLARED = os.path.join(BASE_DIR, "cloudflared.exe")
READY_FILE = os.path.join(EXE_DIR, "ready.txt")
SETTINGS_DIR = os.path.join(os.environ.get("APPDATA", EXE_DIR), "GVLMediaStudio")
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")
DATA_FILE = os.path.join(SETTINGS_DIR, "game_data.json")
LOG_FILE = os.path.join(SETTINGS_DIR, "media_studio.log")
REPLAY_CMD_FILE = os.path.join(EXE_DIR, "replay_cmd.txt")
REPLAY_EVENT_FILE = os.path.join(EXE_DIR, "replay_events.txt")
LOG_MAX_BYTES = 512 * 1024

VERSION = "1.15.1"
HOST = "127.0.0.1"
PORT = 40231

DATA_KEYS = ("league", "teams", "matches", "standings", "games", "top")
DEFAULT_CONFERENCE = {"link": "", "game": "", "name": ""}
DEFAULT_SETTINGS = {"plan": [], "commentators": ["", ""], "ticker": "", "title": "", "subtitle": "", "replayDir": "", "autoClip": False, "language": "de", "transition": "gvl",
                    "socials": {"discord": "discord.gg/gvl", "instagram": "", "twitch": "", "tiktok": "", "youtube": ""},
                    "conference": dict(DEFAULT_CONFERENCE)}
SOCIAL_MAX = 40
CONFERENCE_MAX = {"link": 300, "game": 40, "name": 40}
REPLAY_SECONDS = (10, 20, 30)
TRANSITIONS = ("gvl", "fast", "team", "cut")
CUT_FALLBACK = 3
MAX_CLIPS = 8
CLIP_RATES = (0.5, 1)
GOAL_CLIP_DELAY = 5
REPLAY_POLL = 0.25
LIVE_STATUSES = ("Halftime 1", "Break", "Halftime 2", "Extra Time", "Penalty", "FT", "FFT", "GameFinishedRef")
STALE_AFTER = 25
TOP_SCORERS = 8
SCHEDULE_DAYS = 14
PLAN_REFRESH = 60
RECENT_KICKOFF = 3 * 3600
OBS_START_WAIT = 90
OBS_CHECK_EVERY = 5
UPDATE_FIRST_CHECK = 15
UPDATE_CHECK_EVERY = 6 * 3600
STAGE_CLEAN_DELAY = 20
STAGE_DIR = os.path.join(EXE_DIR, updater.STAGE)
NO_UPDATE = {"state": "none", "version": "", "url": "", "page": "", "progress": 0, "message": "", "restartObs": False}

FEED_EVERY = 5
FEED_MIN_GAP = 0.5
FEED_TIMEOUT = 8
FEED_MAX_EVENTS = 20
TALLIES = ("live", "preview", "off")
PASSWORD = re.compile(r"[A-Za-z0-9_-]{6,64}")
SECRET = re.compile(r"[A-Za-z0-9_-]{16,64}")
NO_CONFERENCE = {"active": False, "connection": "off", "message": "", "tally": "off", "obs": "unknown", "streaming": False,
                 "streamId": "", "installing": False}


def load_settings():
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return settings
    settings.update({k: v for k, v in saved.items() if k in settings and type(v) is type(settings[k])})
    saved_socials = settings["socials"]
    settings["socials"] = {key: str(saved_socials.get(key, default) or "")[:SOCIAL_MAX] for key, default in DEFAULT_SETTINGS["socials"].items()}
    settings["conference"] = clean_conference(settings["conference"])
    return settings


def clean_conference(value):
    return {key: str(value.get(key) or "").strip()[:CONFERENCE_MAX[key]] for key in DEFAULT_CONFERENCE}


def load_data():
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        saved = {}
    return {key: saved.get(key) or ("" if key == "league" else []) for key in DATA_KEYS}


class Studio:
    def __init__(self):
        self.secret = secrets.token_urlsafe(24)
        self.job = ""
        self.by = ""
        self.pushed = 0.0
        self.stale = False
        self.match = None
        self.events = []
        self.visible = {"full": None, "lower": None, "ticker": None, "social": None}
        self.settings = load_settings()
        self.data = load_data()
        self.data_stamp = 0
        self.plan = []
        self.results = []
        self.replay = None
        self.buffer = False
        self.clips = []
        self.clip_label = ""
        self.picking = False
        self.auto = {"goal": True, "card": True, "sub": True}
        self.hub = service.Hub()
        self.tunnel = Tunnel(CLOUDFLARED, PORT, self.broadcast)
        self.update = dict(NO_UPDATE)
        self.scenes = []
        self.scene = ""
        self.pending_cut = None
        self.conference = dict(NO_CONFERENCE)

    def status(self):
        return {"linked": bool(self.job), "stale": self.stale, "by": self.by}

    def tunnel_state(self):
        url = self.tunnel.state["url"]
        return {"state": self.tunnel.state["state"], "message": self.tunnel.state["message"], "link": f"{url}/in/{self.secret}" if url else ""}

    def message(self, kind="state"):
        return {"type": kind, "version": VERSION, "status": self.status(), "tunnel": self.tunnel_state(), "match": self.match, "visible": self.visible,
                "auto": self.auto, "assets": assets_for(self.match), "events": self.events[-8:],
                "settings": self.settings, "scenes": self.scenes, "scene": self.scene, "plan": self.plan, "results": self.results, "buffer": self.buffer, "dataStamp": self.data_stamp,
                "clips": [{k: c[k] for k in ("id", "name", "time")} for c in self.clips],
                "update": {**{k: v for k, v in self.update.items() if k != "url"}, "download": bool(self.update["url"])},
                "conference": {**self.conference, "games": conference_games()}}

    def send(self, data):
        self.hub.send(data)

    def broadcast(self):
        self.send(self.message())

    def unlink(self):
        self.job = self.by = ""
        self.match = None
        self.stale = False
        self.broadcast()


def save_settings():
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(studio.settings, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_FILE)


studio = Studio()
thumbs = roblox.Thumbs()
http: aiohttp.ClientSession | None = None
background: list[asyncio.Task] = []
resolving = False
digits = roblox.digits


def later(coroutine):
    background.append(asyncio.create_task(coroutine))
    del background[:-20]


def wanted_assets(match):
    logos, avatars = set(), set()
    if not match:
        return logos, avatars
    for side in ("home", "away"):
        team = match.get(side) or {}
        if digits(team.get("logo")):
            logos.add(digits(team.get("logo")))
        lineup = team.get("lineup") or {}
        for person in (lineup.get("starters") or []) + (lineup.get("subs") or []):
            avatars.add(digits(person.get("userId")))
    avatars.update(digits(user) for user in match.get("userIds") or [])
    avatars -= {"", "0"}
    return logos, avatars


def assets_for(match):
    logos, avatars = wanted_assets(match)
    return {"logos": {i: thumbs.get("logo", i) for i in logos if thumbs.get("logo", i)},
            "avatars": {i: thumbs.get("avatar", i) for i in avatars if thumbs.get("avatar", i)}}


async def resolve_assets():
    global resolving
    if resolving:
        return
    resolving = True
    try:
        logos, avatars = wanted_assets(studio.match)
        before = assets_for(studio.match)
        await thumbs.fetch(http, "logo", logos)
        await thumbs.fetch(http, "avatar", avatars)
        if assets_for(studio.match) != before:
            studio.broadcast()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        print(f"Thumbnails nicht geladen: {e}", flush=True)
    finally:
        resolving = False


def number(value):
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return 0


def team_logos():
    return {str(t.get("name") or "").strip().lower(): digits(t.get("logo")) for t in studio.data["teams"] if t.get("name") and digits(t.get("logo"))}


def last_sunday(year, month):
    last = datetime.date(year + month // 12, month % 12 + 1, 1) - datetime.timedelta(days=1)
    return last - datetime.timedelta(days=(last.weekday() + 1) % 7)


def kickoff_epoch(date, clock):
    try:
        local = datetime.datetime.strptime(f"{date.strip()} {clock.strip()}", "%d.%m.%Y %H:%M")
    except (AttributeError, ValueError):
        return None
    summer = last_sunday(local.year, 3) <= local.date() < last_sunday(local.year, 10)
    return int(local.replace(tzinfo=datetime.timezone.utc).timestamp()) - (7200 if summer else 3600)


async def upcoming():
    now = time.time()
    logos = team_logos()
    games = []
    for m in studio.data["matches"]:
        kickoff = kickoff_epoch(m.get("Date") or "", m.get("KickOffTime") or "")
        if kickoff is None or (m.get("Score") or "").strip() or not now - RECENT_KICKOFF < kickoff < now + SCHEDULE_DAYS * 86400:
            continue
        games.append({"id": m.get("MatchID") or "", "home": (m.get("HomeTeam") or "").strip(), "away": (m.get("AwayTeam") or "").strip(),
                      "competition": m.get("Competition") or "", "stadium": m.get("Stadium") or "", "date": m.get("Date") or "",
                      "time": m.get("KickOffTime") or "", "kickoff": kickoff})
    games.sort(key=lambda g: g["kickoff"])
    names = {g[side].lower() for g in games for side in ("home", "away")}
    await thumbs.fetch(http, "logo", {logos[n] for n in names if n in logos})
    for game in games:
        game["homeLogo"] = thumbs.get("logo", logos.get(game["home"].lower()))
        game["awayLogo"] = thumbs.get("logo", logos.get(game["away"].lower()))
    return games


def today_results():
    day = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).strftime("%d.%m.%Y")
    return [f"{m['HomeTeam'].strip()} {m['Score'].strip()} {m['AwayTeam'].strip()}"
            for m in studio.data["matches"] if m.get("Date") == day and (m.get("Score") or "").strip()]


async def refresh_plan():
    chosen = set(studio.settings["plan"])
    plan = [g for g in await upcoming() if g["id"] in chosen]
    results = today_results()
    if plan != studio.plan or results != studio.results:
        studio.plan = plan
        studio.results = results
        studio.broadcast()


async def watch_plan():
    while True:
        try:
            await refresh_plan()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            print(f"Stream-Plan nicht geladen: {e}", flush=True)
        await asyncio.sleep(PLAN_REFRESH)


async def top_scorers():
    players = studio.data["top"]
    await thumbs.fetch(http, "avatar", {digits(p.get("userId")) for p in players if digits(p.get("userId"))})
    return [{"name": p.get("name"), "goals": number(p.get("goals")), "assists": number(p.get("assists")),
             "avatar": thumbs.get("avatar", digits(p.get("userId")))} for p in players]


def split_score(value):
    found = re.match(r"\s*(\d+)\s*[-:]\s*(\d+)", str(value or ""))
    return (int(found.group(1)), int(found.group(2))) if found else None


def team_form(matches, team):
    form = []
    for m in reversed(matches):
        if len(form) == 5:
            break
        score = split_score(m.get("Score"))
        home, away = (m.get("HomeTeam") or "").strip().lower(), (m.get("AwayTeam") or "").strip().lower()
        if not score or (m.get("Counted") or "").upper() != "TRUE" or team not in (home, away):
            continue
        own, other = score if team == home else (score[1], score[0])
        form.append("W" if own > other else "D" if own == other else "L")
    form.reverse()
    return form


def apply_live(table, match, matches):
    game_id = str(match.get("gameId") or "")
    if match.get("friendly") or match.get("status") not in LIVE_STATUSES:
        return False
    if any(m.get("MatchID") == game_id and (m.get("Counted") or "").upper() == "TRUE" for m in matches):
        return False
    home, away = (match.get("home") or {}).get("name", "").lower(), (match.get("away") or {}).get("name", "").lower()
    goals = match.get("score") or {}
    hg, ag = number(goals.get("home")), number(goals.get("away"))
    applied = 0
    for team in table:
        key = team["name"].lower()
        if key not in (home, away):
            continue
        gf, ga = (hg, ag) if key == home else (ag, hg)
        team["played"] += 1
        team["gf"] += gf
        team["ga"] += ga
        team["gd"] = team["gf"] - team["ga"]
        team["won"] += gf > ga
        team["drawn"] += gf == ga
        team["lost"] += gf < ga
        team["points"] += 3 if gf > ga else 1 if gf == ga else 0
        applied += 1
    if applied == 2:
        table.sort(key=lambda t: (-t["points"], -t["gd"], -t["gf"], t["pos"]))
    return applied == 2


def league_key(value):
    return str(value or "").strip().lower().replace("-", " ")


async def league_data(match):
    key = league_key(match.get("league"))
    if not key or key != league_key(studio.data["league"]):
        return {"table": [], "live": False, "form": {"home": [], "away": []}, "fixtures": [], "top": []}
    logos = team_logos()
    matches = studio.data["games"]
    table = []
    for row in studio.data["standings"]:
        if row.get("Team"):
            table.append({"pos": number(row.get("Position")), "name": row["Team"].strip(),
                          "played": number(row.get("PlayedGames")), "won": number(row.get("Wins")),
                          "drawn": number(row.get("Draw")), "lost": number(row.get("Loses")),
                          "gf": number(row.get("GoalsPlus")), "ga": number(row.get("GoalMinus")),
                          "gd": number(row.get("GoalDifference")), "points": number(row.get("Points"))})
    table.sort(key=lambda t: t["pos"])
    live = apply_live(table, match, matches)
    date = str(match.get("date") or "")
    fixtures = [{"home": (m.get("HomeTeam") or "").strip(), "away": (m.get("AwayTeam") or "").strip(),
                 "score": m.get("Score") or "", "time": m.get("KickOffTime") or ""}
                for m in studio.data["matches"] if date and m.get("Date") == date and league_key(m.get("Competition")) == key]
    fixtures.sort(key=lambda f: f["time"])
    names = {t["name"].lower() for t in table}
    names |= {f[side].lower() for f in fixtures for side in ("home", "away")}
    await thumbs.fetch(http, "logo", {logos[n] for n in names if n in logos})
    form = {side: team_form(matches, str((match.get(side) or {}).get("name") or "").lower()) for side in ("home", "away")}
    return {"table": table, "live": live, "form": form, "fixtures": fixtures, "top": await top_scorers(),
            "logos": {n: thumbs.get("logo", logos[n]) for n in names if n in logos}}


async def stop_tunnel():
    await studio.tunnel.stop()
    studio.unlink()


def conference_id(date, match_id):
    day = re.sub(r"\D", "", str(date or ""))
    day = day[:4] + day[-2:] if len(day) == 8 else day
    game = digits(match_id)
    return f"gvl_{day}_{game}" if day and game else ""


def conference_games():
    """Spiele, fuer die der Streamer senden kann: sein Stream-Plan und das gerade verbundene Spiel."""
    games = []
    for g in studio.plan:
        games.append({"id": conference_id(g["date"], g["id"]), "home": g["home"], "away": g["away"], "date": g["date"], "time": g["time"]})
    match = studio.match or {}
    current = conference_id(match.get("date"), match.get("gameId"))
    if current and all(g["id"] != current for g in games):
        games.insert(0, {"id": current, "home": (match.get("home") or {}).get("name") or "", "away": (match.get("away") or {}).get("name") or "",
                         "date": match.get("date") or "", "time": match.get("kickoff") or ""})
    return [g for g in games if g["id"]]


def parse_regie_link(link):
    parsed = urllib.parse.urlsplit(link.strip())
    parts = parsed.path.strip("/").split("/")
    password = (urllib.parse.parse_qs(parsed.query).get("pw") or [""])[0]
    if parsed.scheme != "https" or not parsed.netloc or len(parts) != 2 or parts[0] != "in" or not SECRET.fullmatch(parts[1]) or not PASSWORD.fullmatch(password):
        return None
    base = f"https://{parsed.netloc}/in/{parts[1]}"
    return {"feed": base + "/feed", "bye": base + "/bye", "password": password}


def streamer_name():
    return studio.settings["conference"]["name"] or studio.by


def set_conference(**values):
    if any(studio.conference.get(k) != v for k, v in values.items()):
        studio.conference.update(values)
        studio.broadcast()


class Feed:
    """Schickt Spielstand und Ereignisse an die Regie. Immer nur eine Anfrage gleichzeitig, Fehler nur im Log."""

    def __init__(self):
        self.target = None
        self.events = []
        self.wake = None
        self.task = None
        self.session = None

    def start(self, target):
        self.target = target
        if not self.session:
            self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=dns.FallbackResolver()),
                                                 headers={"User-Agent": "GVL-Media-Studio"})
        self.events = []
        self.wake = self.wake or asyncio.Event()
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self.run())
        self.wake.set()

    def notify(self, fresh=()):
        if not self.target:
            return
        self.events.extend(e for e in fresh if isinstance(e, dict))
        del self.events[:-FEED_MAX_EVENTS]
        self.wake.set()

    async def stop(self):
        target, self.target = self.target, None
        if self.task:
            self.task.cancel()
            self.task = None
        if target:
            try:
                async with self.session.post(target["bye"], json={"game": target["game"]}, timeout=aiohttp.ClientTimeout(total=FEED_TIMEOUT)):
                    pass
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                print(f"Regie nicht abgemeldet: {e or type(e).__name__}", flush=True)

    async def close(self):
        await self.stop()
        if self.session:
            await self.session.close()
            self.session = None

    async def send(self, target):
        events, self.events = self.events, []
        body = {"game": target["game"], "streamer": streamer_name(), "home": target["home"], "away": target["away"],
                "match": studio.match, "events": events, "streaming": studio.conference["streaming"]}
        try:
            async with self.session.post(target["feed"], json=body, timeout=aiohttp.ClientTimeout(total=FEED_TIMEOUT)) as resp:
                data = await resp.json(content_type=None) if resp.status == 200 else {"ok": False, "error": f"HTTP {resp.status}"}
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            data = {"ok": False, "error": str(e) or type(e).__name__}
        if target is not self.target:
            return
        if data.get("ok"):
            set_conference(connection="ok", tally=data.get("tally") if data.get("tally") in TALLIES else "off", message="")
            return
        self.events[:0] = events
        del self.events[:-FEED_MAX_EVENTS]
        print(f"Regie nicht erreicht: {data.get('error')}", flush=True)
        set_conference(connection="error", tally="off", message="Die Regie antwortet nicht. Ist die Konferenz gestartet und der Link aktuell?")

    async def run(self):
        while self.target:
            self.wake.clear()
            await self.send(self.target)
            await asyncio.sleep(FEED_MIN_GAP)
            try:
                await asyncio.wait_for(self.wake.wait(), FEED_EVERY)
            except asyncio.TimeoutError:
                pass


feed = Feed()


async def start_conference():
    conf = studio.settings["conference"]
    target = parse_regie_link(conf["link"])
    game = next((g for g in conference_games() if g["id"] == conf["game"]), None)
    if not target:
        return set_conference(message="Der Regie-Link ist ungültig. Bitte den ganzen Link aus der Regie einfügen.")
    if not game:
        return set_conference(message="Bitte ein Spiel aus deinem Stream-Plan auswählen.")
    if studio.conference["obs"] == "missing":
        return set_conference(message="Das GVL Konferenz-Modul fehlt in OBS.")
    target.update(game=game["id"], home=game["home"], away=game["away"])
    replay_command(f"conference|start|{game['id']}|{target['password']}")
    set_conference(active=True, connection="connecting", tally="off", message="", streamId=game["id"])
    feed.start(target)


async def stop_conference():
    replay_command("conference|stop")
    set_conference(active=False, connection="off", tally="off", message="")
    await feed.stop()


async def auto_module():
    """Fehlt das Konferenz-Modul, wird es still nachinstalliert. OBS laedt es beim naechsten Start."""
    state, _ = await asyncio.to_thread(module.status)
    if state != "missing":
        return
    try:
        await asyncio.to_thread(module.install, lambda text: print(text, flush=True), True)
        set_conference(message="Konferenz-Modul installiert. Bitte OBS einmal neu starten.")
    except (module.ModuleError, OSError, ValueError) as e:
        print(f"Konferenz-Modul nicht installiert: {e}", flush=True)


async def install_module():
    if studio.conference["installing"]:
        return
    set_conference(installing=True, message="")
    try:
        await asyncio.to_thread(module.install, lambda text: print(text, flush=True), True)
        set_conference(installing=False, message="Konferenz-Modul installiert. Bitte OBS einmal neu starten.")
    except (module.ModuleError, OSError, ValueError) as e:
        print(f"Konferenz-Modul nicht installiert: {e}", flush=True)
        set_conference(installing=False, message=f"Konferenz-Modul nicht installiert: {e}")


async def page(request):
    name = {"dock": "dock.html", "overlay": "overlay.html"}[request.match_info["name"]]
    return web.FileResponse(os.path.join(WEB_DIR, name), headers={"Cache-Control": "no-cache"})


async def asset(request):
    path = {"montserrat.ttf": gvl_common.FONT, "gvl_logo.png": gvl_common.LOGO}.get(request.match_info["name"])
    if not path:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "max-age=86400"})


async def events(request):
    return await studio.hub.stream(request, studio.message())


async def command(request):
    data = await service.body_of(request)
    slot = data.get("slot")
    action = data.get("action")
    if action == "show" and slot in studio.visible and isinstance(data.get("graphic"), dict):
        studio.visible[slot] = data["graphic"]
    elif action == "hide":
        for name in ([slot] if slot in studio.visible else ("full", "lower")):
            studio.visible[name] = None
    elif action == "auto" and data.get("kind") in studio.auto:
        studio.auto[data["kind"]] = bool(data.get("value"))
    elif action == "unlink":
        studio.unlink()
    elif action == "replay" and data.get("seconds") in REPLAY_SECONDS:
        studio.replay = data["seconds"]
    elif action == "buffer":
        replay_command("start" if data.get("value") else "stop")
    elif action == "clip":
        save_clip("")
    elif action == "pickFolder":
        later(pick_folder())
    elif action == "openFolder":
        await asyncio.to_thread(open_folder)
    elif action == "settings" and isinstance(data.get("values"), dict):
        for name, value in data["values"].items():
            if name == "socials" and isinstance(value, dict):
                studio.settings["socials"] = {key: str(value.get(key) or "").strip()[:SOCIAL_MAX] for key in DEFAULT_SETTINGS["socials"]}
            elif name == "conference" and isinstance(value, dict):
                studio.settings["conference"] = clean_conference({**studio.settings["conference"], **value})
                feed.notify()
            elif name in studio.settings and type(value) is type(studio.settings[name]):
                studio.settings[name] = value
        save_settings()
        studio.broadcast()
        later(refresh_plan())
    elif action == "update":
        later(run_update())
    elif action == "openRelease" and studio.update["page"].startswith("https://github.com/"):
        os.startfile(studio.update["page"])
    elif action == "transition" and data.get("style") in TRANSITIONS and (not data.get("scene") or data.get("scene") in studio.scenes):
        start_transition(data["style"], data.get("scene") or "")
        return web.json_response({"ok": True})
    elif action == "cut":
        take_cut(str(data.get("id") or ""))
        return web.json_response({"ok": True})
    elif action == "openApp" and FROZEN:
        updater.launch(os.path.join(EXE_DIR, installer.REQUIRED_FILES[0]), "--app")
    elif action == "updateSeen" and studio.update["state"] == "done":
        set_update(**NO_UPDATE)
    elif action == "tunnel":
        later(studio.tunnel.start() if data.get("value") else stop_tunnel())
    elif action == "conference":
        later(start_conference() if data.get("value") else stop_conference())
    elif action == "conferenceStream":
        replay_command("conference|stream|" + ("1" if data.get("value") else "0"))
    elif action == "installModule":
        later(install_module())
    studio.send({"type": "command", "action": action, "slot": slot, "graphic": data.get("graphic"),
                 "auto": studio.auto, "visible": studio.visible})
    return web.json_response({"ok": True})


async def league(request):
    return web.json_response(await league_data(studio.match or {}))


async def clip_file(request):
    clip = next((c for c in studio.clips if c["id"] == request.match_info["id"]), None)
    if not clip or not os.path.isfile(clip["file"]):
        raise web.HTTPNotFound()
    return web.FileResponse(clip["file"], headers={"Cache-Control": "no-cache"})


async def schedule(request):
    return web.json_response(await upcoming())


async def link(request):
    data = await service.body_of(request)
    studio.job, studio.by = str(data.get("jobId") or "studio"), str(data.get("by") or "")
    studio.pushed = time.time()
    studio.stale = False
    studio.broadcast()
    feed.notify()
    return web.json_response({"ok": True})


async def unlink(request):
    data = await service.body_of(request)
    if studio.job == str(data.get("jobId") or "studio"):
        studio.unlink()
    return web.json_response({"ok": True})


async def push(request):
    data = await service.body_of(request)
    if not studio.job or studio.job != str(data.get("jobId") or "studio"):
        return web.json_response({"ok": True, "gone": True})
    studio.pushed = time.time()
    studio.stale = False
    if isinstance(data.get("match"), dict):
        studio.match = data["match"]
        later(resolve_assets())
    fresh = data.get("events") if isinstance(data.get("events"), list) else []
    studio.events.extend(fresh)
    del studio.events[:-20]
    studio.send({**studio.message(), "fresh": fresh})
    feed.notify(fresh)
    if studio.settings["autoClip"] and studio.buffer:
        for event in fresh:
            if isinstance(event, dict) and event.get("type") == "goal":
                later(goal_clip(event))
    return web.json_response({"ok": True, "gone": False})


def choose_folder(start, title):
    import tkinter
    from tkinter import filedialog
    window = tkinter.Tk()
    window.withdraw()
    window.attributes("-topmost", True)
    try:
        return filedialog.askdirectory(parent=window, initialdir=start or None, mustexist=False, title=title)
    finally:
        window.destroy()


async def pick_folder():
    if studio.picking:
        return
    studio.picking = True
    title = "Choose replay folder" if studio.settings["language"] == "en" else "Replay-Ordner wählen"
    try:
        folder = await asyncio.to_thread(choose_folder, studio.settings["replayDir"], title)
    finally:
        studio.picking = False
    if folder:
        studio.settings["replayDir"] = os.path.normpath(folder)
        save_settings()
        studio.broadcast()


def open_folder():
    folder = studio.settings["replayDir"].strip() or os.path.join(os.path.expanduser("~"), "Videos")
    os.makedirs(folder, exist_ok=True)
    os.startfile(folder)


def replay_command(command):
    with open(REPLAY_CMD_FILE, "a", encoding="utf-8") as f:
        f.write(command + "\n")


def start_transition(style, scene):
    if style == "cut":
        if scene:
            replay_command(f"scene|{scene}")
        return
    cut_id = secrets.token_hex(4)
    studio.pending_cut = (cut_id, scene) if scene else None
    studio.send({"type": "command", "action": "transition", "graphic": {"id": cut_id, "style": style, "scene": scene}})
    if scene:
        later(cut_fallback(cut_id))


def take_cut(cut_id):
    if studio.pending_cut and studio.pending_cut[0] == cut_id:
        scene = studio.pending_cut[1]
        studio.pending_cut = None
        replay_command(f"scene|{scene}")


async def cut_fallback(cut_id):
    await asyncio.sleep(CUT_FALLBACK)
    take_cut(cut_id)


def save_clip(label):
    studio.clip_label = label
    replay_command("save")


async def goal_clip(event):
    await asyncio.sleep(GOAL_CLIP_DELAY)
    if studio.buffer:
        save_clip(f"Tor: {event.get('scorer', {}).get('name', '')} {event.get('minute', '')}'")


async def store_clip(path):
    folder = studio.settings["replayDir"].strip() or os.path.dirname(path)
    target = os.path.join(folder, os.path.basename(path))
    try:
        os.makedirs(folder, exist_ok=True)
        if os.path.normcase(target) != os.path.normcase(path):
            await asyncio.to_thread(shutil.move, path, target)
    except OSError as e:
        print(f"Clip nicht verschoben: {e}", flush=True)
        target = path
    now = datetime.datetime.now().strftime("%H:%M:%S")
    studio.clips.insert(0, {"id": secrets.token_hex(4), "name": studio.clip_label or f"Clip {now}", "time": now, "file": target})
    studio.clip_label = ""
    del studio.clips[MAX_CLIPS:]
    studio.broadcast()


def conference_event(parts):
    """Meldungen des Lua-Skripts zum Stream-Dienst: on, off, busy, missing."""
    state = parts[0]
    if state == "on":
        set_conference(obs="on", streamId=parts[1] if len(parts) > 1 else studio.conference["streamId"])
    elif state == "off":
        set_conference(obs="off")
    elif state == "busy":
        set_conference(obs="off", active=False, connection="off", message="Erst den laufenden Stream beenden, dann für die Konferenz senden.")
        later(feed.stop())
    elif state == "missing":
        set_conference(obs="missing", active=False, connection="off", message="Das GVL Konferenz-Modul fehlt in OBS.")
        later(feed.stop())


async def watch_replay():
    while True:
        await asyncio.sleep(REPLAY_POLL)
        try:
            with open(REPLAY_EVENT_FILE, encoding="utf-8") as f:
                lines = f.read().splitlines()
            os.remove(REPLAY_EVENT_FILE)
        except OSError:
            continue
        for line in lines:
            if line in ("started", "stopped"):
                studio.buffer = line == "started"
                studio.broadcast()
            elif line.startswith("scenes|"):
                studio.scenes = [name for name in line.split("|", 1)[1].split("\t") if name]
                studio.broadcast()
            elif line.startswith("current|"):
                studio.scene = line.split("|", 1)[1]
                studio.broadcast()
            elif line.startswith("saved|"):
                await store_clip(line.split("|", 1)[1])
            elif line.startswith("conference|"):
                conference_event(line.split("|")[1:])
            elif line.startswith("streaming|"):
                set_conference(streaming=line.endswith("|1"))
                feed.notify()
            elif line.startswith("module|") and studio.conference["obs"] != "on":
                set_conference(obs="off" if line.endswith("|1") else "missing")


async def watch_links():
    while True:
        await asyncio.sleep(10)
        if studio.job and not studio.stale and time.time() - studio.pushed > STALE_AFTER:
            studio.stale = True
            studio.broadcast()


async def watch_obs(app):
    """Beendet den Dienst mit dem OBS, das ihn gestartet hat. Gemerkt wird der Prozess, nicht nur der Name, damit ein
    schnell neu gestartetes OBS nicht den alten Dienst am Leben haelt und den Port blockiert. Gestartet aus OBS ist
    das der Elternprozess, sonst jedes OBS, das beim ersten Blick laeuft."""
    started = time.time()
    owner = await asyncio.to_thread(obs.owner_obs)
    watched = {owner} if owner else set()
    while True:
        await asyncio.sleep(OBS_CHECK_EVERY)
        running = await asyncio.to_thread(obs.obs_pids)
        watched = watched or running
        if (watched and not watched & running) or (not watched and time.time() - started > OBS_START_WAIT):
            await feed.stop()
            await stop_tunnel()
            if watched and FROZEN:
                await asyncio.to_thread(installer.register_stingers, EXE_DIR)
            os._exit(0)


def set_update(**values):
    studio.update.update(values)
    studio.broadcast()


async def check_update():
    if studio.update["state"] in ("downloading", "restarting"):
        return
    found = await asyncio.to_thread(updater.latest)
    if not found or not updater.is_newer(found["version"], VERSION):
        return
    if studio.update["state"] in ("available", "error") and studio.update["version"] == found["version"]:
        return
    print(f"Update gefunden: {found['version']}", flush=True)
    set_update(state="available", progress=0, message="", **found)


async def watch_updates():
    await asyncio.sleep(UPDATE_FIRST_CHECK)
    while True:
        try:
            await check_update()
        except (OSError, ValueError) as e:
            print(f"Update-Pruefung fehlgeschlagen: {e}", flush=True)
        await asyncio.sleep(UPDATE_CHECK_EVERY)


async def run_update():
    if studio.update["state"] not in ("available", "error") or not studio.update["url"] or studio.tunnel.state["state"] != "off" or studio.conference["active"]:
        return
    set_update(state="downloading", progress=0, message="")
    loop = asyncio.get_running_loop()

    def progress(percent):
        if percent >= studio.update["progress"] + 5:
            studio.update["progress"] = percent
            loop.call_soon_threadsafe(studio.broadcast)

    try:
        await asyncio.to_thread(updater.download, studio.update["url"], STAGE_DIR, progress)
    except (OSError, zipfile.BadZipFile) as e:
        print(f"Update nicht geladen: {e}", flush=True)
        await asyncio.to_thread(updater.clean, STAGE_DIR)
        set_update(state="error", message=str(e))
        return
    set_update(state="restarting", progress=100)
    await asyncio.sleep(1)
    await stop_tunnel()
    try:
        updater.start(STAGE_DIR, EXE_DIR, "service")
    except OSError as e:
        print(f"Update nicht gestartet: {e}", flush=True)
        set_update(state="error", message=str(e))
        return
    print(f"Update auf {studio.update['version']} gestartet", flush=True)
    os._exit(0)


def after_update():
    result = updater.take_result()
    if not result:
        return
    if result.get("error"):
        studio.update.update(state="error", message=str(result["error"]), page=updater.RELEASE_PAGE)
    else:
        studio.update.update(state="done", version=VERSION, restartObs=bool(result.get("restartObs")))


async def clean_stage():
    await asyncio.sleep(STAGE_CLEAN_DELAY)
    if os.path.isdir(STAGE_DIR):
        await asyncio.to_thread(updater.clean, STAGE_DIR)


async def on_startup(app):
    global http
    for path in (REPLAY_CMD_FILE, REPLAY_EVENT_FILE):
        if os.path.isfile(path):
            os.remove(path)
    http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), headers={"User-Agent": "Mozilla/5.0"})
    later(watch_links())
    later(watch_plan())
    later(watch_replay())
    later(clean_stage())
    after_update()
    if FROZEN:
        later(watch_updates())
        later(auto_module())
    if "--no-obs-watch" not in sys.argv:
        later(watch_obs(app))
    with open(READY_FILE, "w", encoding="utf-8") as f:
        f.write(str(time.time()))


async def on_shutdown(app):
    studio.hub.close()


async def on_cleanup(app):
    await feed.close()
    await stop_tunnel()
    for task in background:
        task.cancel()
    if http:
        await http.close()


def open_log():
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    if os.path.isfile(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
        os.replace(LOG_FILE, LOG_FILE + ".old")
    sys.stdout = sys.stderr = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    print(f"--- Start {datetime.datetime.now():%d.%m.%Y %H:%M:%S}, Version {VERSION}", flush=True)


async def game_data(request):
    body = await service.body_of(request)
    data = body.get("data")
    if not studio.job or studio.job != str(body.get("jobId") or "studio") or not isinstance(data, dict):
        return web.json_response({"ok": False})
    studio.data = {key: data.get(key) or ("" if key == "league" else []) for key in DATA_KEYS}
    studio.data_stamp = time.time()
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(studio.data, f, ensure_ascii=False)
    os.replace(tmp, DATA_FILE)
    await refresh_plan()
    studio.broadcast()
    return web.json_response({"ok": True})


async def poll(request):
    data = await service.body_of(request)
    if not studio.job or studio.job != str(data.get("jobId") or "studio"):
        return web.json_response({"replay": None})
    seconds, studio.replay = studio.replay, None
    return web.json_response({"replay": seconds})


def main():
    if sys.stdout is None:
        open_log()
    app = web.Application(middlewares=[service.make_guard(HOST, PORT, lambda: studio.secret)], client_max_size=512 * 1024)
    app.router.add_get("/media/{name:dock|overlay}", page)
    app.router.add_get("/media/assets/{name}", asset)
    app.router.add_get("/media/events", events)
    app.router.add_post("/media/command", command)
    app.router.add_get("/media/league", league)
    app.router.add_get("/media/schedule", schedule)
    app.router.add_get("/media/clip/{id}", clip_file)
    app.router.add_post("/in/{secret}/link", link)
    app.router.add_post("/in/{secret}/unlink", unlink)
    app.router.add_post("/in/{secret}/push", push)
    app.router.add_post("/in/{secret}/poll", poll)
    app.router.add_post("/in/{secret}/data", game_data)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.on_cleanup.append(on_cleanup)
    try:
        web.run_app(app, host=HOST, port=PORT, print=None, access_log=None)
    except OSError:
        traceback.print_exc()
        sys.exit(0)
    except BaseException:
        traceback.print_exc()
        raise
    finally:
        print(f"--- Ende {datetime.datetime.now():%d.%m.%Y %H:%M:%S}", flush=True)


if __name__ == "__main__":
    if "--apply-update" in sys.argv:
        open_log()
        index = sys.argv.index("--apply-update")
        updater.apply(sys.argv[index + 1], sys.argv[index + 2] if len(sys.argv) > index + 2 else "service")
    elif "--app" in sys.argv or installer.started_from_package():
        import app
        app.run(VERSION)
    else:
        main()
