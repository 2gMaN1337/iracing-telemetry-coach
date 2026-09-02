"""Linienvergleich: seitlicher Versatz gegenueber der Referenzrunde.

Die Telemetrie liefert Lat/Lon in Grad. Fuer den Vergleich werden beide
Runden in ein lokales Meter-Koordinatensystem projiziert (aequirektangular,
Bezugspunkt = Streckenmitte). Auf dieser Skala (wenige Kilometer) liegt der
Projektionsfehler deutlich unter einem Zentimeter und spielt keine Rolle.

Der Versatz wird senkrecht zur Fahrtrichtung der *Referenzlinie* gemessen:

    offset > 0  ->  weiter links als die Referenz
    offset < 0  ->  weiter rechts als die Referenz
"""
from __future__ import annotations

import numpy as np

from .channels import POS_CHANNELS
from .lap import Lap

R_EARTH = 6371000.0


def has_position(lap: Lap) -> bool:
    return all(c in lap.data for c in POS_CHANNELS)


def to_xy(lap: Lap, lat0: float, lon0: float) -> tuple[np.ndarray, np.ndarray]:
    """Lat/Lon (Grad) -> lokale Meter-Koordinaten (x = Ost, y = Nord)."""
    lat = np.radians(lap.data["Lat"])
    lon = np.radians(lap.data["Lon"])
    x = R_EARTH * (lon - lon0) * np.cos(lat0)
    y = R_EARTH * (lat - lat0)
    return x, y


def lateral_offset(lap: Lap, ref: Lap) -> np.ndarray | None:
    """Seitlicher Versatz in Metern, punktweise auf dem Distanzraster.

    Gibt None zurueck, wenn einer der beiden Runden die Positionsdaten fehlen.
    """
    if not (has_position(lap) and has_position(ref)):
        return None

    lat0 = float(np.radians(np.mean(ref.data["Lat"])))
    lon0 = float(np.radians(np.mean(ref.data["Lon"])))
    rx, ry = to_xy(ref, lat0, lon0)
    cx, cy = to_xy(lap, lat0, lon0)

    # Fahrtrichtung der Referenz als Tangente des eigenen Pfades
    tx = np.gradient(rx)
    ty = np.gradient(ry)
    norm = np.hypot(tx, ty)
    norm[norm < 1e-9] = 1e-9
    tx, ty = tx / norm, ty / norm

    # Linke Normale zur Fahrtrichtung
    nx, ny = -ty, tx
    return (cx - rx) * nx + (cy - ry) * ny


def corner_line(offset: np.ndarray, c: dict) -> dict:
    """Versatz an Einlenkpunkt, Scheitelpunkt und Ausgang einer Kurve."""
    lo, apex, hi = c["i_start"], c["i_apex"], c["i_end"]
    entry = offset[lo:apex] if apex > lo else offset[lo:lo + 1]
    exit_ = offset[apex:hi] if hi > apex else offset[hi - 1:hi]
    win = offset[max(0, apex - 3):apex + 4]
    return dict(
        entry=float(np.mean(entry)),
        apex=float(np.mean(win)),
        exit=float(np.mean(exit_)),
        max_abs=float(np.max(np.abs(offset[lo:hi]))) if hi > lo else 0.0,
    )


def describe(v: float) -> str:
    """Versatz in Klartext. Unter 0.3 m ist Messrauschen, nicht Fahrstil."""
    if abs(v) < 0.3:
        return "auf der Referenzlinie"
    side = "weiter links" if v > 0 else "weiter rechts"
    return "%.1f m %s" % (abs(v), side)


# Der Schluessel nennt die Seite, auf der du *bist*; die Anweisung im
# Sprach-Modul nennt die Richtung, in die du musst.
_STELLEN = (("entry", "linie_einfahrt", "Einfahrt"),
            ("apex", "linie_scheitel", "Scheitelpunkt"),
            ("exit", "linie_ausgang", "Ausgang"))


def line_findings(L: dict, min_m: float = 0.5) -> list[tuple]:
    """Befunde zur Linie aus den Werten von corner_line()."""
    out = []
    for feld, key, label in _STELLEN:
        v = L.get(feld)
        if v is None or abs(v) < min_m:
            continue
        seite = "links" if v > 0 else "rechts"
        out.append(("%s_%s" % (key, seite), "%s %s." % (label, describe(v))))
    return out
