"""Kurvensatz einer Strecke - aus vielen Runden gemittelt und gespeichert.

Die Kurven sind eine Eigenschaft der Strecke, nicht einer einzelnen Runde.
Aus einer einzigen Runde erkannt schwankt die Zahl je nach Referenz zwischen
9 und 16, weil der Querbeschleunigungsverlauf einer Runde verrauscht und
fahrstilabhaengig ist. Gemittelt ueber acht oder mehr Runden ist das Ergebnis
stabil (getestet: 10 Kurven, Scheitelpunkte auf ±10 m reproduzierbar).
"""
from __future__ import annotations

import json
import os

import numpy as np
from scipy.signal import find_peaks

MIN_LAPS = 8          # ab hier ist der gemittelte Verlauf stabil
PROMINENCE = 0.30     # g, zwischen 0.25 und 0.35 liegt das Ergebnis gleich
MIN_GAP_M = 90.0


def _smooth(y, win_m, dist):
    step = float(dist[1] - dist[0])
    n = max(3, int(round(win_m / step)) | 1)
    k = np.ones(n) / n
    return np.convolve(np.pad(y, n // 2, mode="edge"), k, mode="valid")[: len(y)]


def build_profile(laps) -> np.ndarray | None:
    """Mittlerer Betrag der Querbeschleunigung (g) auf dem Distanzraster."""
    usable = [l for l in laps if l.valid and "LatAccel" in l.data]
    if len(usable) < 2:
        return None
    d = usable[0].dist
    return np.mean([np.abs(_smooth(l.data["LatAccel"], 35.0, d)) / 9.81
                    for l in usable], axis=0)


def apexes_from_profile(profile: np.ndarray, dist: np.ndarray) -> list[float]:
    """Scheitelpunkt-Distanzen aus dem gemittelten Verlauf."""
    step = float(dist[1] - dist[0])
    idx, _ = find_peaks(profile, prominence=PROMINENCE, distance=int(MIN_GAP_M / step))
    return [float(dist[i]) for i in idx]


def build(laps) -> list[float] | None:
    """Kurvensatz aus einer Rundensammlung. None, wenn zu wenige Runden."""
    usable = [l for l in laps if l.valid and "LatAccel" in l.data]
    if len(usable) < MIN_LAPS:
        return None
    prof = build_profile(usable)
    return apexes_from_profile(prof, usable[0].dist) if prof is not None else None


# ---------------- Persistenz ----------------
def path_for(refs_dir: str, track_slug: str) -> str:
    return os.path.join(refs_dir, "corners_%s.json" % track_slug)


def load(refs_dir: str, track_slug: str) -> list[float] | None:
    p = path_for(refs_dir, track_slug)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return [float(x) for x in data["apex_m"]]
    except Exception:
        return None


def save(refs_dir: str, track_slug: str, apexes: list[float], n_laps: int) -> None:
    os.makedirs(refs_dir, exist_ok=True)
    with open(path_for(refs_dir, track_slug), "w", encoding="utf-8") as f:
        json.dump(dict(apex_m=[round(a, 1) for a in apexes],
                       n_corners=len(apexes), built_from_laps=n_laps),
                  f, ensure_ascii=False, indent=1)
