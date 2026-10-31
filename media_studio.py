"""GVL Media Studio: lokaler Dienst fuer das OBS-Dock und das Overlay.

OBS startet dieses Programm ueber obs/gvl_media_studio.lua. Es lauscht nur auf 127.0.0.1 und baut auf Knopfdruck
einen Cloudflare-Tunnel auf. Der Link enthaelt ein Geheimnis und wird im Spiel eingefuegt, danach schickt der
Stadion-Server seine Daten an /in/<geheimnis>/push. Ueber den Tunnel ist nur dieser Eingang erreichbar.

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

import aiohttp
from aiohttp import web

import installer

BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(BASE_DIR, "web")
EXE_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
CLOUDFLARED = os.path.join(BASE_DIR, "cloudflared.exe")
READY_FILE = os.path.join(EXE_DIR, "ready.txt")
SETTINGS_DIR = os.path.join(os.environ.get("APPDATA", EXE_DIR), "GVLMediaStudio")
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")
DATA_FILE = os.path.join(SETTINGS_DIR, "game_data.json")
LOG_FILE = os.path.join(SETTINGS_DIR, "media_studio.log")
REPLAY_CMD_FILE = os.path.join(EXE_DIR, "replay_cmd.txt")
REPLAY_EVENT_FILE = os.path.join(EXE_DIR, "replay_events.txt")
LOG_MAX_BYTES = 512 * 1024

VERSION = "1.8.1"
HOST = "127.0.0.1"
PORT = 50231
LOCAL_HOSTS = (f"{HOST}:{PORT}", f"localhost:{PORT}")
LOCAL_ORIGINS = (f"http://{HOST}:{PORT}", f"http://localhost:{PORT}", "null")
NO_WINDOW = 0x08000000

THUMB_ASSETS = "https://thumbnails.roblox.com/v1/assets?assetIds={}&size=420x420&format=Png&isCircular=false"
THUMB_AVATARS = "https://thumbnails.roblox.com/v1/users/avatar?userIds={}&size=720x720&format=Png&isCircular=false"

DATA_KEYS = ("league", "teams", "matches", "standings", "games", "top")
DEFAULT_SETTINGS = {"plan": [], "commentators": ["", ""], "ticker": "", "title": "", "subtitle": "", "replayDir": "", "autoClip": False, "language": "de"}
REPLAY_SECONDS = (10, 20, 30)
MAX_CLIPS = 8
CLIP_RATES = (0.5, 1)
GOAL_CLIP_DELAY = 5
REPLAY_POLL = 0.25
LIVE_STATUSES = ("Halftime 1", "Break", "Halftime 2", "Extra Time", "Penalty", "FT", "FFT", "GameFinishedRef")
MAX_CLIENTS = 8
STALE_AFTER = 25
PING_EVERY = 15
ASSET_CACHE_SEC = 6 * 3600
THUMB_BATCH = 50
TUNNEL_WAIT = 40
TOP_SCORERS = 8
SCHEDULE_DAYS = 14
PLAN_REFRESH = 60
RECENT_KICKOFF = 3 * 3600
OBS_START_WAIT = 90
OBS_CHECK_EVERY = 5


def load_settings():
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return settings
    settings.update({k: v for k, v in saved.items() if k in settings and type(v) is type(settings[k])})
    return settings


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
        self.visible = {"full": None, "lower": None, "ticker": None}
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
        self.clients = set()
        self.tunnel = {"state": "off", "link": "", "message": ""}

    def status(self):
        return {"linked": bool(self.job), "stale": self.stale, "by": self.by}

    def message(self, kind="state"):
        return {"type": kind, "version": VERSION, "status": self.status(), "tunnel": self.tunnel, "match": self.match, "visible": self.visible,
                "auto": self.auto, "assets": assets_for(self.match), "events": self.events[-8:],
                "settings": self.settings, "plan": self.plan, "results": self.results, "buffer": self.buffer, "dataStamp": self.data_stamp,
                "clips": [{k: c[k] for k in ("id", "name", "time")} for c in self.clips]}

    def send(self, data):
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        for queue in list(self.clients):
            try:
                queue.put_nowait(text)
            except asyncio.QueueFull:
                self.clients.discard(queue)

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
asset_cache: dict[str, tuple[float, str]] = {}
http: aiohttp.ClientSession | None = None
tunnel_process: asyncio.subprocess.Process | None = None
background: list[asyncio.Task] = []
resolving = False


def digits(value):
    return "".join(c for c in str(value or "") if c.isdigit())


def cached_asset(kind, value):
    entry = asset_cache.get(f"{kind}:{value}")
    return entry[1] if entry and time.time() - entry[0] < ASSET_CACHE_SEC else None


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
    return {"logos": {i: cached_asset("logo", i) for i in logos if cached_asset("logo", i)},
            "avatars": {i: cached_asset("avatar", i) for i in avatars if cached_asset("avatar", i)}}


async def fetch_thumbs(kind, template, ids):
    pending = [i for i in ids if not cached_asset(kind, i)]
    for start in range(0, len(pending), THUMB_BATCH):
        batch = pending[start:start + THUMB_BATCH]
        for _ in range(4):
            async with http.get(template.format(",".join(batch))) as resp:
                data = (await resp.json(content_type=None)).get("data") or []
            waiting = False
            for row in data:
                if row.get("state") == "Completed" and row.get("imageUrl"):
                    asset_cache[f"{kind}:{row.get('targetId')}"] = (time.time(), row["imageUrl"])
                elif row.get("state") == "Pending":
                    waiting = True
            if not waiting:
                break
            await asyncio.sleep(1.5)


async def resolve_assets():
    global resolving
    if resolving:
        return
    resolving = True
    try:
        logos, avatars = wanted_assets(studio.match)
        before = assets_for(studio.match)
        await fetch_thumbs("logo", THUMB_ASSETS, logos)
        await fetch_thumbs("avatar", THUMB_AVATARS, avatars)
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
    await fetch_thumbs("logo", THUMB_ASSETS, {logos[n] for n in names if n in logos})
    for game in games:
        game["homeLogo"] = cached_asset("logo", logos.get(game["home"].lower()))
        game["awayLogo"] = cached_asset("logo", logos.get(game["away"].lower()))
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
    await fetch_thumbs("avatar", THUMB_AVATARS, {digits(p.get("userId")) for p in players if digits(p.get("userId"))})
    return [{"name": p.get("name"), "goals": number(p.get("goals")), "assists": number(p.get("assists")),
             "avatar": cached_asset("avatar", digits(p.get("userId")))} for p in players]


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
    await fetch_thumbs("logo", THUMB_ASSETS, {logos[n] for n in names if n in logos})
    form = {side: team_form(matches, str((match.get(side) or {}).get("name") or "").lower()) for side in ("home", "away")}
    return {"table": table, "live": live, "form": form, "fixtures": fixtures, "top": await top_scorers(),
            "logos": {n: cached_asset("logo", logos[n]) for n in names if n in logos}}


def set_tunnel(state, link="", message=""):
    studio.tunnel = {"state": state, "link": link, "message": message}
    studio.broadcast()


async def watch_tunnel(process):
    found = False
    try:
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            url = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line.decode("utf-8", "replace"))
            if url and not found and "api.trycloudflare" not in url.group(0):
                found = True
                set_tunnel("ready", f"{url.group(0)}/in/{studio.secret}")
    finally:
        if process is tunnel_process:
            set_tunnel("off", message="Der Tunnel wurde beendet. Bitte neu starten.")


async def start_tunnel():
    global tunnel_process
    if studio.tunnel["state"] != "off":
        return
    if not os.path.isfile(CLOUDFLARED):
        set_tunnel("off", message="cloudflared.exe fehlt. Bitte neu installieren.")
        return
    set_tunnel("starting")
    tunnel_process = await asyncio.create_subprocess_exec(
        CLOUDFLARED, "tunnel", "--no-autoupdate", "--url", f"http://{HOST}:{PORT}",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE, creationflags=NO_WINDOW)
    background.append(asyncio.create_task(watch_tunnel(tunnel_process)))
    background.append(asyncio.create_task(tunnel_timeout(tunnel_process)))


async def tunnel_timeout(process):
    await asyncio.sleep(TUNNEL_WAIT)
    if process is tunnel_process and studio.tunnel["state"] == "starting":
        await stop_tunnel()
        set_tunnel("off", message="Cloudflare hat nicht geantwortet. Bitte erneut versuchen.")


async def stop_tunnel():
    global tunnel_process
    process, tunnel_process = tunnel_process, None
    if process and process.returncode is None:
        process.kill()
        await process.wait()
    studio.unlink()
    set_tunnel("off")


@web.middleware
async def guard(request, handler):
    tunneled = "Cf-Ray" in request.headers or "Cf-Connecting-Ip" in request.headers
    inbound = request.path.startswith("/in/")
    if tunneled != inbound or (tunneled and request.match_info.get("secret") != studio.secret):
        raise web.HTTPNotFound()
    if not tunneled:
        origin = request.headers.get("Origin")
        if request.host not in LOCAL_HOSTS or (origin and origin not in LOCAL_ORIGINS):
            raise web.HTTPForbidden()
    return await handler(request)


async def body_of(request):
    try:
        data = await request.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def page(request):
    name = {"dock": "dock.html", "overlay": "overlay.html"}[request.match_info["name"]]
    return web.FileResponse(os.path.join(WEB_DIR, name), headers={"Cache-Control": "no-cache"})


async def asset(request):
    name = {"montserrat.ttf": "Montserrat.ttf", "gvl_logo.png": "gvl_logo.png"}.get(request.match_info["name"])
    if not name:
        raise web.HTTPNotFound()
    return web.FileResponse(os.path.join(WEB_DIR, name), headers={"Cache-Control": "max-age=86400"})


async def events(request):
    if len(studio.clients) >= MAX_CLIENTS:
        return web.json_response({"ok": False}, status=429)
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
    await response.prepare(request)
    queue = asyncio.Queue(64)
    studio.clients.add(queue)
    queue.put_nowait(json.dumps(studio.message(), ensure_ascii=False, separators=(",", ":")))
    try:
        while True:
            try:
                text = await asyncio.wait_for(queue.get(), PING_EVERY)
            except asyncio.TimeoutError:
                await response.write(b": ping\n\n")
                continue
            if text is None:
                break
            await response.write(f"data: {text}\n\n".encode("utf-8"))
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        studio.clients.discard(queue)
    return response


async def command(request):
    data = await body_of(request)
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
        background.append(asyncio.create_task(pick_folder()))
    elif action == "openFolder":
        await asyncio.to_thread(open_folder)
    elif action == "settings" and isinstance(data.get("values"), dict):
        for name, value in data["values"].items():
            if name in studio.settings and type(value) is type(studio.settings[name]):
                studio.settings[name] = value
        save_settings()
        studio.broadcast()
        background.append(asyncio.create_task(refresh_plan()))
    elif action == "tunnel":
        background.append(asyncio.create_task(start_tunnel() if data.get("value") else stop_tunnel()))
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
    data = await body_of(request)
    studio.job, studio.by = str(data.get("jobId") or "studio"), str(data.get("by") or "")
    studio.pushed = time.time()
    studio.stale = False
    studio.broadcast()
    return web.json_response({"ok": True})


async def unlink(request):
    data = await body_of(request)
    if studio.job == str(data.get("jobId") or "studio"):
        studio.unlink()
    return web.json_response({"ok": True})


async def push(request):
    data = await body_of(request)
    if not studio.job or studio.job != str(data.get("jobId") or "studio"):
        return web.json_response({"ok": True, "gone": True})
    studio.pushed = time.time()
    studio.stale = False
    if isinstance(data.get("match"), dict):
        studio.match = data["match"]
        background.append(asyncio.create_task(resolve_assets()))
        del background[:-20]
    fresh = data.get("events") if isinstance(data.get("events"), list) else []
    studio.events.extend(fresh)
    del studio.events[:-20]
    studio.send({**studio.message(), "fresh": fresh})
    if studio.settings["autoClip"] and studio.buffer:
        for event in fresh:
            if isinstance(event, dict) and event.get("type") == "goal":
                background.append(asyncio.create_task(goal_clip(event)))
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
    with open(REPLAY_CMD_FILE, "w", encoding="utf-8") as f:
        f.write(command + "\n")


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
            elif line.startswith("saved|"):
                await store_clip(line.split("|", 1)[1])


async def watch_links():
    while True:
        await asyncio.sleep(10)
        if studio.job and not studio.stale and time.time() - studio.pushed > STALE_AFTER:
            studio.stale = True
            studio.broadcast()


async def watch_obs(app):
    started = time.time()
    seen = False
    while True:
        await asyncio.sleep(OBS_CHECK_EVERY)
        running = installer.obs_running()
        seen = seen or running
        if (seen and not running) or (not seen and time.time() - started > OBS_START_WAIT):
            await stop_tunnel()
            os._exit(0)


async def on_startup(app):
    global http
    for path in (REPLAY_CMD_FILE, REPLAY_EVENT_FILE):
        if os.path.isfile(path):
            os.remove(path)
    http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), headers={"User-Agent": "Mozilla/5.0"})
    background.append(asyncio.create_task(watch_links()))
    background.append(asyncio.create_task(watch_plan()))
    background.append(asyncio.create_task(watch_replay()))
    if "--no-obs-watch" not in sys.argv:
        background.append(asyncio.create_task(watch_obs(app)))
    with open(READY_FILE, "w", encoding="utf-8") as f:
        f.write(str(time.time()))


async def on_shutdown(app):
    for queue in list(studio.clients):
        queue.put_nowait(None)


async def on_cleanup(app):
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
    body = await body_of(request)
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
    data = await body_of(request)
    if not studio.job or studio.job != str(data.get("jobId") or "studio"):
        return web.json_response({"replay": None})
    seconds, studio.replay = studio.replay, None
    return web.json_response({"replay": seconds})


def main():
    if sys.stdout is None:
        open_log()
    app = web.Application(middlewares=[guard], client_max_size=512 * 1024)
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
    if "--install" in sys.argv or "--uninstall" in sys.argv:
        installer.run(installer.install if "--install" in sys.argv else installer.uninstall)
    else:
        main()
