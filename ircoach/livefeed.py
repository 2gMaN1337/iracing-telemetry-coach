"""Live-Position aus dem Shared Memory, fuer Brems- und Gasmarker im Overlay.

Laeuft im Server als eigener Leser neben dem Coach - iRacing erlaubt beliebig
viele lesende Zugriffe auf den Shared Memory. Ein Hintergrund-Thread haelt die
aktuellen Werte vor, damit die HTTP-Abfrage sie nur noch abholt und die
Antwortzeit nicht an der SDK-Abfrage haengt.

Die Referenzpunkte (wo die Bestrunde bremst und wieder Gas gibt) kommen aus
der gespeicherten Referenzrunde und werden beim Start einmal berechnet.
"""
from __future__ import annotations

import threading
import time

import irsdk

POLL_HZ = 60   # haelt den Positionsfehler des Markers klein

# Ab welcher Entfernung ein Marker eingeblendet wird
BRAKE_LOOKAHEAD = 300.0   # m
GAS_LOOKAHEAD = 100.0     # m


def reference_points(ref, corners) -> list[dict]:
    """Brems- und Gaspunkt je Kurve aus der Referenzrunde."""
    from .analyze import corner_metrics
    pts = []
    for c in corners:
        m = corner_metrics(ref, c)
        pts.append(dict(n=c["n"], brake=m["d_brake"], gas=m["d_throttle"],
                        apex=m["d_apex"]))
    return pts


def next_marker(dist: float, pts: list[dict], track_len: float) -> dict | None:
    """Naechster Brems- oder Gaspunkt vor dem Fahrzeug.

    Beide Typen werden getrennt gesucht und der naehere gewinnt - so bleibt
    der Gasmarker sichtbar, solange er relevanter ist als die naechste
    Bremszone weit voraus.
    """
    best = None
    for p in pts:
        for kind, key, look in (("brake", "brake", BRAKE_LOOKAHEAD),
                                ("gas", "gas", GAS_LOOKAHEAD)):
            target = p.get(key)
            if target is None:
                continue
            gap = (target - dist) % track_len
            if gap <= look and (best is None or gap < best["dist"]):
                best = dict(type=kind, corner=p["n"], dist=round(gap, 1))
    return best


class LiveFeed:
    """Haelt Position, Tempo und Pedalstellung aktuell."""

    def __init__(self):
        self.ir = irsdk.IRSDK()
        self.lock = threading.Lock()
        self.state: dict = dict(connected=False)
        self.points: list[dict] = []
        self.track_len: float = 0.0
        self.ref_dist = self.ref_speed = None
        self.ref_brake = self.ref_throttle = None
        self.sim = False
        self.sim_pos = 0.0
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def set_reference(self, pts: list[dict], track_len: float,
                      dist=None, speed=None, brake=None, throttle=None) -> None:
        with self.lock:
            self.points = pts
            self.track_len = float(track_len)
            self.ref_dist, self.ref_speed = dist, speed
            self.ref_brake, self.ref_throttle = brake, throttle

    def set_sim(self, on: bool) -> bool:
        """Bestrunde in Echtzeit abspielen - zum Testen ohne laufenden Sim."""
        with self.lock:
            if on and self.ref_dist is None:
                return False
            self.sim = bool(on)
            self.sim_pos = 0.0
            return True

    def snapshot(self) -> dict:
        with self.lock:
            s = dict(self.state)
            pts, tl = self.points, self.track_len
        if s.get("connected") and pts and tl > 0 and s.get("dist") is not None:
            s["marker"] = next_marker(s["dist"], pts, tl)
        return s

    def stop(self) -> None:
        self._stop.set()

    def _sim_step(self, dt: float) -> dict:
        """Position anhand des Geschwindigkeitsprofils der Bestrunde vorruecken."""
        import numpy as np
        with self.lock:
            d, v = self.ref_dist, self.ref_speed
            b, t = self.ref_brake, self.ref_throttle
            pos, tl = self.sim_pos, self.track_len
        spd = float(np.interp(pos, d, v))
        pos = (pos + spd * dt) % tl
        with self.lock:
            self.sim_pos = pos
        return dict(connected=True, simulated=True, dist=pos, speed=spd * 3.6,
                    brake=float(np.interp(pos, d, b)) if b is not None else 0.0,
                    throttle=float(np.interp(pos, d, t)) if t is not None else 0.0,
                    gear=0, surface=3)

    # ---------------- intern ----------------
    def _loop(self) -> None:
        period = 1.0 / POLL_HZ
        last_try = 0.0
        while not self._stop.is_set():
            t0 = time.perf_counter()
            try:
                if self.sim:
                    st = self._sim_step(period)
                elif self.ir.is_initialized and self.ir.is_connected:
                    self.ir.freeze_var_buffer_latest()
                    d = self.ir["LapDist"]
                    st = dict(connected=True,
                              dist=float(d) if d is not None else None,
                              speed=float(self.ir["Speed"] or 0) * 3.6,
                              brake=float(self.ir["Brake"] or 0),
                              throttle=float(self.ir["Throttle"] or 0),
                              gear=int(self.ir["Gear"] or 0),
                              surface=int(self.ir["PlayerTrackSurface"] or -1))
                else:
                    st = dict(connected=False)
                    if t0 - last_try > 1.0:
                        last_try = t0
                        self.ir.shutdown()
                        self.ir.startup()
                with self.lock:
                    self.state = st
            except Exception:
                with self.lock:
                    self.state = dict(connected=False)
            dt = period - (time.perf_counter() - t0)
            if dt > 0:
                time.sleep(dt)
