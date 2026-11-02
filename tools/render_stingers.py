"""Rendert die Stinger aus web/overlay.html als WebM mit Transparenz fuer den OBS-Stinger-Uebergang.

Nur zum Bauen noetig, nicht im Paket. Braucht playwright, imageio-ffmpeg und Google Chrome.
Aufruf: python tools/render_stingers.py
Schreibt obs/stingers/*.webm und obs/stingers/stingers.json mit den Schnittpunkten.
"""
import asyncio
import functools
import http.server
import json
import os
import shutil
import subprocess
import tempfile
import threading

import imageio_ffmpeg
from playwright.async_api import async_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
OUT = os.path.join(ROOT, "obs", "stingers")
FPS = 60
STINGERS = (("gvl", "GVL Swipe", "gvl_stinger.webm"), ("fast", "GVL Swipe Fast", "gvl_stinger_fast.webm"))
FILES = {"/media/overlay": "overlay.html", "/media/assets/montserrat.ttf": "Montserrat.ttf", "/media/assets/gvl_logo.png": "gvl_logo.png"}

TIMELINE = """name => {
  const style = STINGERS[name];
  const t1 = 2 * style.stagger + style.move;
  const t2 = t1 + style.hold;
  const out = t2 + 90;
  const end = out + 2 * style.stagger + style.move;
  const fadeIn = Math.min(360, style.hold - 40);
  const lineEnd = Math.min(t1 + 500, t2);
  stingerColors(name).forEach((color, i) => { BANDS[i].style.backgroundColor = color; });
  stingerCenter.className = "sc" + (style.center === "small" ? " small" : "");
  stingerCenter.innerHTML = stingerContent(name, "");
  stinger.classList.add("on");
  const runs = [];
  BANDS.forEach((band, i) => {
    runs.push(band.animate(bandFrames(BAND_OFF_LEFT, BAND_COVER), { duration: style.move, delay: i * style.stagger, easing: BAND_EASE, fill: "both" }));
    runs.push(band.animate(bandFrames(BAND_COVER, BAND_OFF_RIGHT), { duration: style.move, delay: out + (2 - i) * style.stagger, easing: BAND_EASE, fill: "forwards" }));
  });
  const hidden = "translate(-50%, -50%) scale(.84)";
  const shown = "translate(-50%, -50%)";
  runs.push(stingerCenter.animate([
    { offset: 0, opacity: 0, transform: hidden }, { offset: t1 / end, opacity: 0, transform: hidden, easing: "cubic-bezier(.16, 1, .3, 1)" },
    { offset: (t1 + fadeIn) / end, opacity: 1, transform: shown }, { offset: t2 / end, opacity: 1, transform: shown },
    { offset: (t2 + 160) / end, opacity: 0, transform: shown }, { offset: 1, opacity: 0, transform: shown }], { duration: end, fill: "both" }));
  const line = stingerCenter.querySelector(".sline");
  if (line) runs.push(line.animate([{ offset: 0, width: "0px" }, { offset: Math.min(t1 + 80, lineEnd) / end, width: "0px", easing: "cubic-bezier(.16, 1, .3, 1)" },
    { offset: lineEnd / end, width: "260px" }, { offset: 1, width: "260px" }], { duration: end, fill: "both" }));
  runs.forEach(run => run.pause());
  window.stingerRuns = runs;
  return { end, cut: Math.round(t1 + style.hold / 2) };
}"""


class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        return os.path.join(WEB, FILES.get(path.split("?")[0], "missing"))

    def log_message(self, *args):
        pass


async def render(page, name, target):
    await page.reload()
    await page.wait_for_function("document.fonts.status === 'loaded'")
    info = await page.evaluate(TIMELINE, name)
    frames = int(info["end"] * FPS / 1000) + 2
    folder = tempfile.mkdtemp(prefix="gvl-stinger-")
    try:
        for frame in range(frames):
            await page.evaluate("t => window.stingerRuns.forEach(run => { run.currentTime = t; })", frame * 1000 / FPS)
            await page.screenshot(path=os.path.join(folder, f"f{frame:04d}.png"), omit_background=True)
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", os.path.join(folder, "f%04d.png"),
                        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "28", "-row-mt", "1", "-auto-alt-ref", "0", target], check=True)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return info["cut"]


async def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=WEB))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.makedirs(OUT, exist_ok=True)
    listing = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome")
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        await page.goto(f"http://127.0.0.1:{server.server_port}/media/overlay")
        for key, title, file_name in STINGERS:
            cut = await render(page, key, os.path.join(OUT, file_name))
            listing.append({"name": title, "file": file_name, "point": cut})
            print(f"{file_name}: Schnitt bei {cut} ms, {os.path.getsize(os.path.join(OUT, file_name)) // 1024} KB")
        await browser.close()
    with open(os.path.join(OUT, "stingers.json"), "w", encoding="utf-8") as f:
        json.dump(listing, f, ensure_ascii=False, indent=2)
    server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
