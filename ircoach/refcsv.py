"""Referenzrunden als CSV lesen und schreiben.

Ein verbreitetes Austauschformat fuer Rundendaten: eine Kopfzeile, eine
Zeile je Abtastpunkt, Werte in iRacing-Einheiten (Speed in m/s, Winkel im
Bogenmass).

    LapDistPct,Throttle,Brake,Speed,SteeringWheelAngle,Gear,Lat,Lon,RPM,
    Clutch,ABSActive,DRSActive,LatAccel,LongAccel,VertAccel,Yaw,YawRate,
    PositionType

Damit laesst sich eine fremde Bestrunde als Referenz einlesen - und
umgekehrt eine eigene Runde ausgeben, um sie in einem anderen Werkzeug
als Referenz zu verwenden.

Zwei Fallstricke stecken im Format:

  * Die Zeitachse fehlt. Aus der CSV laesst sich nur der Geschwindigkeits-
    verlauf ableiten, die Zeit wird daraus integriert. Fuer Deltas reicht
    das; die absolute Rundenzeit muss separat mitgegeben werden, sonst
    weicht sie um den Integrationsfehler ab.
  * Es steht nicht drin, ob die Runde sauber war. Eine fremde Referenz mit
    Track-Limits-Verstoss laesst sich nicht erkennen.
"""
from __future__ import annotations

import csv
import io
import os

import numpy as np

from .lap import GRID_N, Lap

# Reihenfolge wie im Zielformat - die ersten vier sind Pflicht.
COLUMNS = ["LapDistPct", "Throttle", "Brake", "Speed", "SteeringWheelAngle",
           "Gear", "Lat", "Lon", "RPM", "Clutch", "ABSActive", "DRSActive",
           "LatAccel", "LongAccel", "VertAccel", "Yaw", "YawRate", "PositionType"]
REQUIRED = ("LapDistPct", "Speed")

# CSV-Spalte -> Kanalname im Coach. Was hier fehlt, wird nicht uebernommen.
TO_LAP = {
    "Throttle": "Throttle", "Brake": "Brake", "Speed": "Speed",
    "SteeringWheelAngle": "SteeringWheelAngle", "Gear": "Gear", "RPM": "RPM",
    "LatAccel": "LatAccel", "LongAccel": "LongAccel", "Yaw": "Yaw",
    "YawRate": "YawRate", "ABSActive": "BrakeABSactive",
    "Lat": "Lat", "Lon": "Lon",
}
FROM_LAP = {v: k for k, v in TO_LAP.items()}


def _num(v) -> float:
    """'true'/'false' kommen als Text - der Parser der Gegenseite macht es genauso."""
    s = str(v).strip()
    if s in ("true", "True"):
        return 1.0
    if s in ("false", "False", ""):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return float("nan")


def read(path: str, *, track_len: float, lap_time: float = 0.0,
         car: str = "?", track: str = "?", lap_no: int = 0,
         source: str = "csv") -> Lap:
    """Liest eine Referenz-CSV und legt sie auf das Distanzraster des Coaches."""
    with io.open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if len(rows) < 100:
        raise ValueError("Zu wenige Zeilen (%d) - das ist keine vollstaendige Runde."
                         % len(rows))
    missing = [c for c in REQUIRED if c not in rows[0]]
    if missing:
        raise ValueError("Pflichtspalten fehlen: %s" % ", ".join(missing))

    cols = {c: np.array([_num(r.get(c)) for r in rows]) for c in rows[0]}
    pct = cols["LapDistPct"]
    # Manche Exporte geben Prozent statt Anteil.
    if np.nanmax(pct) > 1.5:
        pct = pct / 100.0
    order = np.argsort(pct)
    pct = pct[order]
    # Doppelte Stuetzstellen brechen np.interp - der letzte Wert gewinnt.
    keep = np.r_[np.diff(pct) > 0, True]
    pct = pct[keep]
    d_src = pct * float(track_len)

    grid = np.linspace(0.0, float(track_len), GRID_N)
    data = {}
    for c, name in TO_LAP.items():
        if c not in cols:
            continue
        v = cols[c][order][keep]
        if np.all(np.isnan(v)):
            continue
        data[name] = np.interp(grid, d_src, v).astype(np.float32)

    if "Speed" not in data:
        raise ValueError("Spalte Speed enthaelt keine brauchbaren Werte.")

    # Zeitachse aus dem Geschwindigkeitsverlauf integrieren: dt = ds / v.
    v = np.clip(data["Speed"], 0.5, None)
    step = float(grid[1] - grid[0])
    t = np.concatenate([[0.0], np.cumsum(step / v[:-1])])
    if lap_time and t[-1] > 0:
        # Auf die bekannte Rundenzeit skalieren - der Integrationsfehler
        # verteilt sich dann gleichmaessig statt sich am Ende zu sammeln.
        t = t * (float(lap_time) / float(t[-1]))
    data["t"] = t.astype(np.float32)

    return Lap(lap_no=lap_no, car=car, track=track, session=source,
               track_len=float(track_len), dist=grid, data=data,
               lap_time=float(lap_time) if lap_time else float(t[-1]),
               valid=True, n_samples=len(rows))


def write(lap: Lap, path: str) -> list[str]:
    """Schreibt eine Runde im Zielformat. Gibt die fehlenden Spalten zurueck."""
    n = len(lap.dist)
    pct = lap.dist / max(float(lap.track_len), 1.0)
    out = {"LapDistPct": pct}
    missing = []
    for col in COLUMNS:
        if col == "LapDistPct":
            continue
        name = TO_LAP.get(col)
        if name and name in lap.data:
            out[col] = np.asarray(lap.data[name], dtype=float)
        else:
            out[col] = np.zeros(n)
            missing.append(col)

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for i in range(n):
            w.writerow(["%.6f" % out[c][i] for c in COLUMNS])
    return missing
