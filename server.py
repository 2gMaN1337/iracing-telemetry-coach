"""Web-Dashboard fuer den Telemetrie-Coach.

Liest die vom Live-Coach geschriebenen Runden aus laps/ und liefert sie als
JSON an das Dashboard. Laeuft parallel zum Coach - beide teilen sich nur die
Dateien, es gibt keine direkte Kopplung.

    python server.py [--port 8099]

Danach im Browser: http://localhost:8099
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ircoach import corners as corner_set
from ircoach.analyze import compare, detect_corners, make_tips
from ircoach.frame import render as render_frame
from ircoach.lap import Lap
from ircoach.line import lateral_offset, to_xy
from ircoach.livefeed import LiveFeed, reference_points
from ircoach.report import fmt_time
from ircoach.voice import load_turn_names, spoken_line
from live_coach import slug

BASE = os.path.dirname(os.path.abspath(__file__))
LAPS_DIR = os.path.join(BASE, "laps")
FEED = os.path.join(BASE, "session.jsonl")
REFS_DIR = os.path.join(BASE, "refs")
WEB = os.path.join(BASE, "web")

PLOT_N = 700          # Stuetzstellen fuer die Diagramme
_cache: dict = {}
_lock = threading.Lock()
_feed: LiveFeed | None = None
_ref_key = None       # welcher Referenzstand ist im Feed hinterlegt
_ref_checked = 0.0    # Zeitpunkt der letzten Pruefung


# --------------------------------------------------------------------------
# Daten
# --------------------------------------------------------------------------
def newest_set() -> str | None:
    """Der zuletzt beschriebene Auto/Strecken-Ordner."""
    dirs = [d for d in glob.glob(os.path.join(LAPS_DIR, "*")) if os.path.isdir(d)]
    return max(dirs, key=os.path.getmtime) if dirs else None


def ref_for(set_dir: str) -> Lap | None:
    p = os.path.join(REFS_DIR, os.path.basename(set_dir) + ".npz")
    try:
        return Lap.load(p) if os.path.exists(p) else None
    except Exception:
        return None


def lap_files(set_dir: str) -> list[str]:
    return sorted(glob.glob(os.path.join(set_dir, "lap_*.npz")), key=os.path.getmtime)


def thin(a: np.ndarray, n: int = PLOT_N) -> list:
    idx = np.linspace(0, len(a) - 1, min(n, len(a))).astype(int)
    return [round(float(v), 3) for v in np.asarray(a)[idx]]


def list_laps() -> dict:
    set_dir = newest_set()
    if not set_dir:
        return dict(laps=[], set=None)
    ref = ref_for(set_dir)
    out = []
    for i, f in enumerate(lap_files(set_dir)):
        try:
            lap = Lap.load(f)
        except Exception:
            continue
        out.append(dict(
            idx=i, file=os.path.basename(f), lap_no=lap.lap_no,
            time=round(lap.lap_time, 3), time_str=fmt_time(lap.lap_time),
            valid=bool(lap.valid), reason=lap.invalid_reason,
            warning=getattr(lap, "warning", ""),
            delta=round(lap.lap_time - ref.lap_time, 3) if ref and lap.valid else None,
            mtime=os.path.getmtime(f),
        ))
    return dict(
        set=os.path.basename(set_dir),
        car=out and Lap.load(lap_files(set_dir)[-1]).car or "",
        track=out and Lap.load(lap_files(set_dir)[-1]).track or "",
        ref_time=round(ref.lap_time, 3) if ref else None,
        ref_time_str=fmt_time(ref.lap_time) if ref else None,
        laps=out,
    )


def lap_detail(idx: int) -> dict:
    set_dir = newest_set()
    if not set_dir:
        return dict(error="keine Daten")
    files = lap_files(set_dir)
    if not (0 <= idx < len(files)):
        return dict(error="Runde nicht gefunden")
    path = files[idx]
    key = (path, os.path.getmtime(path))
    with _lock:
        if key in _cache:
            return _cache[key]

    lap = Lap.load(path)
    ref = ref_for(set_dir)
    res = dict(idx=idx, lap_no=lap.lap_no, time=lap.lap_time,
               time_str=fmt_time(lap.lap_time), valid=bool(lap.valid),
               reason=lap.invalid_reason, warning=getattr(lap, "warning", ""),
               car=lap.car, track=lap.track)

    D = lap.data
    res["traces"] = dict(
        dist=thin(lap.dist),
        speed=thin(D["Speed"] * 3.6),
        throttle=thin(D.get("Throttle", np.zeros_like(lap.dist)) * 100),
        brake=thin(D.get("Brake", np.zeros_like(lap.dist)) * 100),
        gear=thin(D.get("Gear", np.zeros_like(lap.dist))),
    )

    if ref is not None:
        corners = detect_corners(ref)
        cmp = compare(lap, ref, corners)
        tips = make_tips(cmp, max_corners=99)
        names = load_turn_names(REFS_DIR, lap.track.replace(" ", "_").lower())
        res["ref_time_str"] = fmt_time(ref.lap_time)
        res["delta"] = cmp["total_delta"]
        res["has_line"] = cmp.get("has_line", False)
        res["stats"] = {k: round(cmp[k], 1) for k in
                        ("full_throttle_pct", "brake_pct", "coast_pct", "overlap_m")
                        if k in cmp}
        res["traces"]["delta"] = thin(cmp["delta_trace"])
        res["traces"]["ref_speed"] = thin(ref.data["Speed"] * 3.6)
        res["traces"]["ref_throttle"] = thin(ref.data.get("Throttle", np.zeros_like(lap.dist)) * 100)
        res["traces"]["ref_brake"] = thin(ref.data.get("Brake", np.zeros_like(lap.dist)) * 100)
        res["spoken"] = spoken_line(cmp, make_tips(cmp), names)[0]

        res["corners"] = []
        tip_by_corner = {t["corner"]: t for t in tips}
        for c in cmp["corners"]:
            t = tip_by_corner.get(c["n"])
            res["corners"].append(dict(
                n=c["n"], name=names.get(c["n"], "Kurve %d" % c["n"]),
                d_start=round(c.get("d_seg_start", c["d_start"])),
                delta=round(c["delta"], 3),
                delta_straight=round(c.get("delta_straight", 0.0), 3),
                v_min=round(c["v_min"]), v_min_ref=round(c["ref"]["v_min"]),
                d_brake=round(c["d_brake"]) if c["d_brake"] is not None else None,
                d_brake_ref=round(c["ref"]["d_brake"]) if c["ref"]["d_brake"] is not None else None,
                abs_pct=round(c["abs_pct"]) if c["abs_pct"] is not None else None,
                gear=c["gear_apex"],
                line=({k: round(v, 2) for k, v in c["line"].items()} if c.get("line") else None),
                findings=[x[1] for x in (t["findings"] if t else [])],
            ))

        # Streckenkarte: Referenzlinie + gefahrene Linie in lokalen Metern
        off = lateral_offset(lap, ref)
        if off is not None:
            lat0 = float(np.radians(np.mean(ref.data["Lat"])))
            lon0 = float(np.radians(np.mean(ref.data["Lon"])))
            rx, ry = to_xy(ref, lat0, lon0)
            cx, cy = to_xy(lap, lat0, lon0)
            res["map"] = dict(rx=thin(rx), ry=thin(ry), cx=thin(cx), cy=thin(cy),
                              offset=thin(off), delta=thin(cmp["delta_trace"]))
            res["corner_marks"] = [
                dict(n=c["n"], x=round(float(rx[c["i_apex"]]), 1),
                     y=round(float(ry[c["i_apex"]]), 1),
                     name=names.get(c["n"], "T%d" % c["n"]))
                for c in corners]

    with _lock:
        _cache.clear()          # nur den letzten Abruf halten, Speicher sparen
        _cache[key] = res
    return res


def refresh_live_reference() -> None:
    """Brems- und Gaspunkte der aktuellen Referenzrunde in den Live-Feed legen."""
    global _ref_key, _ref_checked
    # Dateisystem nicht bei jeder Abfrage anfassen - der Marker laeuft mit 15 Hz.
    now = time.time()
    if now - _ref_checked < 1.0:
        return
    _ref_checked = now
    set_dir = newest_set()
    if not set_dir or _feed is None:
        return
    p = os.path.join(REFS_DIR, os.path.basename(set_dir) + ".npz")
    if not os.path.exists(p):
        return
    key = (p, os.path.getmtime(p))
    if key == _ref_key:
        return
    try:
        ref = Lap.load(p)
        apex = corner_set.load(REFS_DIR, slug(ref.track))   # Strecke, nicht Setup
        corners = detect_corners(ref, apex_dists=apex)
        _feed.set_reference(reference_points(ref, corners), ref.track_len,
                            dist=ref.dist, speed=ref.data.get("Speed"),
                            brake=ref.data.get("Brake"),
                            throttle=ref.data.get("Throttle"))
        _ref_key = key
    except Exception:
        pass


def live_state() -> dict:
    if _feed is None:
        return dict(connected=False)
    refresh_live_reference()
    return _feed.snapshot()


def render_panel() -> bytes:
    """Serverseitig gerendertes Overlay - kommt ohne JavaScript aus.

    Fuer Overlay-Programme, die kein oder nur eingeschraenktes JavaScript
    ausfuehren. Aktualisiert sich per meta-refresh einmal pro Sekunde; die
    Live-Marker sind dadurch traeger als in der JS-Fassung, aber vorhanden.
    """
    d = latest_lap()
    live = live_state()
    mk = live.get("marker") if live.get("connected") else None

    def esc(t):
        return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    def short(t):
        if not t:
            return ""
        return esc(t.split(" - ")[-1].rstrip(".")[:44])

    if mk:
        col = "#FF3B2F" if mk["type"] == "brake" else "#25D96F"
        word = "JETZT" if mk["dist"] <= 6 else ("BREMSEN" if mk["type"] == "brake" else "GAS")
        span = 300.0 if mk["type"] == "brake" else 100.0
        pct = 100 - min(100, mk["dist"] / span * 100)
        body = ('<div class="mk" style="color:%s"><span class="w">%s</span>'
                '<span class="c">Kurve %s</span><span class="d">%d<i>m</i></span></div>'
                '<div class="bar"><div style="width:%.0f%%;background:%s"></div></div>'
                ) % (col, word, mk["corner"], round(mk["dist"]), pct, col)
    elif d.get("empty"):
        body = '<div class="tip"><span class="k">warte auf Runde</span></div>'
    elif not d.get("valid", True):
        body = ('<div class="tip"><span class="k">Runde ung&uuml;ltig</span>'
                '<span class="f">%s</span></div>') % esc(str(d.get("reason", "")).split(",")[0])
    else:
        second = ""
        if d.get("corner2") and d.get("fix2"):
            second = '<div class="sec">Kurve %s &nbsp;<b>%s</b></div>' % (
                d["corner2"], short(d["fix2"]))
        body = ('<div class="tip"><span class="k">%s</span><span class="f">%s</span></div>%s'
                ) % ("Kurve %s" % d["corner"] if d.get("corner") else "sauber",
                     short(d.get("fix")) or "so weiter", second)

    rot = d.get("rotation")
    rot_cls = "" if rot is None else ("hot" if rot > 22 else ("cold" if rot < 12 else "ok"))
    rot_txt = "&mdash;" if rot is None else ("%+d" % round(rot))
    adv = d.get("bias_advice")
    adv_html = ('<span class="adv">%s</span>' %
                ("BALANCE &rarr;VORN" if "vorn" in adv else "BALANCE &rarr;HINTEN")) if adv else ""

    html = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="1">
<title>Coach Panel</title><style>
*{box-sizing:border-box;margin:0}html,body{height:100%%}
body{background:#0a0c0f;color:#F2F5F8;font:500 16px/1.2 "Segoe UI",Arial,sans-serif;
 display:flex;flex-direction:column;padding:3.5vh 4vw;overflow:hidden}
.mk{flex:1;display:flex;align-items:center}
.mk .w{font-size:13vh;font-weight:800}
.mk .c{font-size:5vh;color:#8A94A2;margin-left:3vw;text-transform:uppercase;letter-spacing:.1em}
.mk .d{margin-left:auto;font-size:13vh;font-weight:800}
.mk .d i{font-size:5vh;color:#8A94A2;font-style:normal;margin-left:.6vw}
.bar{height:5vh;background:#161C24;border-radius:.8vh;overflow:hidden;margin-bottom:2vh}
.bar div{height:100%%;border-radius:.8vh}
.tip{flex:1;display:flex;flex-direction:column;justify-content:center;
 border-left:1vw solid #FFB020;padding-left:3.5vw}
.tip .k{font-size:6vh;color:#FFB020;text-transform:uppercase;letter-spacing:.08em;font-weight:600}
.tip .f{font-size:11vh;font-weight:700;line-height:1.1}
.sec{margin-top:2.5vh;border-left:1vw solid #1E252E;padding-left:3.5vw;
 color:#8A94A2;font-size:5.5vh}.sec b{color:#C4CCD6}
footer{display:flex;align-items:center;margin-top:2.5vh;border-top:.5vh solid #1E252E;padding-top:2.4vh}
footer span{margin-right:3vw}
.lbl{font-size:4vh;letter-spacing:.16em;text-transform:uppercase;color:#8A94A2}
.v{font-size:6vh;font-weight:700}
.hot{color:#FF3B2F}.cold{color:#4CC9D4}.ok{color:#25D96F}
.adv{margin-left:auto;margin-right:0;font-size:5.5vh;font-weight:700;color:#0a0c0f;
 background:#FFB020;padding:1vh 2.2vw;border-radius:.7vh}
</style></head><body>%s
<footer><span class="lbl">Balance</span><span class="v">%s</span>
<span class="lbl">Rotation</span><span class="v %s">%s</span>%s</footer>
</body></html>""" % (body, d.get("bias") or "&mdash;", rot_cls, rot_txt, adv_html)
    return html.encode("utf-8")


def latest_lap() -> dict:
    """Kompakte Fassung der letzten Runde - guenstig genug zum haeufigen Abfragen."""
    if not os.path.exists(FEED):
        return dict(empty=True)
    last = None
    with open(FEED, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    last = json.loads(line)
                except Exception:
                    pass
    if last is None:
        return dict(empty=True)
    tips = last.get("tips") or []
    top = tips[0] if tips else None
    second = tips[1] if len(tips) > 1 else None
    return dict(
        lap=last.get("lap"), time=last.get("time"), delta=last.get("delta"),
        valid=last.get("valid", True), reason=last.get("reason", ""),
        new_ref=last.get("new_ref", False), spoken=last.get("spoken", ""),
        corner=top["corner"] if top else None,
        fix=(top["findings"][0] if top and top.get("findings") else None),
        corner2=second["corner"] if second else None,
        fix2=(second["findings"][0] if second and second.get("findings") else None),
        rotation=last.get("rotation"), bias=last.get("bias"),
        bias_advice=last.get("bias_advice"), ts=last.get("ts"),
    )


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj):
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _stream(self, q):
        """MJPEG-Strom: fertig gerenderte Bilder statt einer Seite, die sich
        selbst aktualisiert. Laeuft dadurch auch ohne JavaScript fluessig."""
        import io
        w = max(200, min(1920, int(q.get("w", ["1280"])[0])))
        h = max(60, min(1080, int(q.get("h", ["140"])[0])))
        fps = max(1, min(30, int(q.get("fps", ["12"])[0])))
        # Ohne kleinen Sendepuffer stauen sich Bilder auf, wenn der Betrachter
        # langsamer zeichnet als der Server liefert - die Anzeige laeuft dann
        # sichtbar hinterher. 24 KB fassen hoechstens ein bis zwei Bilder.
        try:
            import socket as _sock
            self.connection.setsockopt(_sock.IPPROTO_TCP, _sock.TCP_NODELAY, 1)
            self.connection.setsockopt(_sock.SOL_SOCKET, _sock.SO_SNDBUF, 24576)
        except OSError:
            pass
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        period = 1.0 / fps
        try:
            while True:
                t0 = time.perf_counter()
                live = live_state()
                mk = live.get("marker") if live.get("connected") else None
                buf = io.BytesIO()
                render_frame(latest_lap(), mk, w, h).save(buf, "JPEG", quality=72)
                data = buf.getvalue()
                sep = chr(13) + chr(10)
                head = ("--frame" + sep + "Content-Type: image/jpeg" + sep +
                        "Content-Length: " + str(len(data)) + sep + sep).encode()
                self.wfile.write(head + data + sep.encode())
                dt = period - (time.perf_counter() - t0)
                if dt > 0:
                    time.sleep(dt)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass          # Betrachter hat die Seite geschlossen

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path in ("/", "/index.html"):
                with open(os.path.join(WEB, "index.html"), "rb") as f:
                    return self._send(f.read(), "text/html; charset=utf-8")
            if u.path == "/overlay":
                with open(os.path.join(WEB, "overlay.html"), "rb") as f:
                    return self._send(f.read(), "text/html; charset=utf-8")
            if u.path == "/vr":
                q = parse_qs(u.query)
                w = int(q.get("w", ["1280"])[0]); h = int(q.get("h", ["140"])[0])
                page = ("<!doctype html><meta charset='utf-8'><title>Coach</title>"
                        "<style>html,body{margin:0;height:100%%;background:#0a0c0f;"
                        "overflow:hidden}img{width:100%%;height:100%%;display:block;"
                        "object-fit:contain}</style>"
                        "<img src='/stream?w=%d&h=%d'>") % (w, h)
                return self._send(page.encode("utf-8"), "text/html; charset=utf-8")
            if u.path == "/stream":
                return self._stream(parse_qs(u.query))
            if u.path == "/panel":
                return self._send(render_panel(), "text/html; charset=utf-8")
            if u.path == "/api/sim":
                on = parse_qs(u.query).get("on", ["1"])[0] not in ("0", "off", "false")
                refresh_live_reference()
                ok = _feed.set_sim(on) if _feed else False
                return self._json(dict(simulating=on and ok, ok=ok))
            if u.path == "/api/live":
                return self._json(live_state())
            if u.path == "/api/latest":
                return self._json(latest_lap())
            if u.path == "/api/laps":
                return self._json(list_laps())
            if u.path == "/api/lap":
                q = parse_qs(u.query)
                return self._json(lap_detail(int(q.get("i", ["0"])[0])))
            self._send(b"not found", "text/plain", 404)
        except Exception as e:
            self._json(dict(error=repr(e)))

    def log_message(self, *a):
        pass          # keine Zugriffs-Logs auf der Konsole


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8099)
    a = ap.parse_args()
    global _feed
    _feed = LiveFeed()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print("Dashboard: http://localhost:%d" % a.port, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
