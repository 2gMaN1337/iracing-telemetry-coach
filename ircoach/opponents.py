"""Gegnerdaten mitschreiben: Positionen, Abstaende, Verkehr.

iRacing stellt im Shared Memory Arrays fuer alle Autos bereit (CarIdx...).
In die .ibt-Datei schreibt der Sim sie nicht - wer sie haben will, muss sie
live mitschneiden. Damit laesst sich hinterher beantworten, was aus der reinen
Eigen-Telemetrie nicht hervorgeht:

  * War eine langsame Runde Verkehr oder ein Fahrfehler?
  * Wie gross war der Abstand nach vorn und hinten?
  * Wer hatte die schnellste Rennrunde?

Abgetastet wird mit wenigen Hertz - fuer Abstaende und Positionen reicht das,
und es haelt die Datei klein.
"""
from __future__ import annotations

import json
import os

import numpy as np

# Arrays, die pro Auto einen Wert liefern
ARRAYS = ("CarIdxLapDistPct", "CarIdxPosition", "CarIdxLap",
          "CarIdxOnPitRoad", "CarIdxTrackSurface",
          "CarIdxLastLapTime", "CarIdxBestLapTime", "CarIdxF2Time")

TRAFFIC_S = 1.5      # naeher als das gilt als beeinflusst


def available(ir) -> list[str]:
    return [c for c in ARRAYS if ir[c] is not None]


def sample(ir, chans) -> dict | None:
    """Ein Abtastpunkt: nur Autos, die tatsaechlich auf der Strecke sind."""
    out = {}
    for c in chans:
        v = ir[c]
        if v is None:
            continue
        out[c] = list(v)
    return out or None


def drivers(ir) -> dict:
    """Startnummer und Name je CarIdx aus der Session-Info."""
    di = ir["DriverInfo"] or {}
    res = {}
    for d in di.get("Drivers", []):
        res[d.get("CarIdx")] = dict(name=d.get("UserName"), num=d.get("CarNumber"),
                                    car=d.get("CarScreenName"))
    return res


# --------------------------------------------------------------------------
# Auswertung
# --------------------------------------------------------------------------
def gaps(snap: dict, me: int, track_len: float, my_speed: float) -> dict:
    """Abstand zum naechsten Auto vorn und hinten, in Metern und Sekunden."""
    pcts = snap.get("CarIdxLapDistPct")
    surf = snap.get("CarIdxTrackSurface") or []
    if not pcts or me >= len(pcts) or pcts[me] < 0:
        return {}
    mine = pcts[me]
    ahead = behind = None
    for i, p in enumerate(pcts):
        if i == me or p < 0:
            continue
        if surf and i < len(surf) and surf[i] != 3:      # nicht auf der Strecke
            continue
        d = ((p - mine) % 1.0) * track_len               # vor mir
        if ahead is None or d < ahead[1]:
            ahead = (i, d)
        d2 = ((mine - p) % 1.0) * track_len              # hinter mir
        if behind is None or d2 < behind[1]:
            behind = (i, d2)
    v = max(my_speed, 5.0)
    res = {}
    if ahead:
        res["ahead_idx"], res["ahead_m"] = ahead[0], round(ahead[1], 1)
        res["ahead_s"] = round(ahead[1] / v, 2)
    if behind:
        res["behind_idx"], res["behind_m"] = behind[0], round(behind[1], 1)
        res["behind_s"] = round(behind[1] / v, 2)
    return res


def rival_gap(snap: dict, me: int, track_len: float, my_speed: float) -> dict | None:
    """Abstand zum Auto auf dem Platz vor mir.

    Bevorzugt CarIdxF2Time (Rueckstand zum Fuehrenden) - die Differenz daraus
    ist der echte Zeitabstand. Fehlt der Kanal, wird ueber die Streckendistanz
    genaehert, was bei stark unterschiedlichem Tempo ungenauer ist.
    """
    pos = snap.get("CarIdxPosition")
    if not pos or me >= len(pos) or pos[me] <= 0:
        return None
    mypos = pos[me]
    rival = next((i for i, p in enumerate(pos) if p == mypos - 1), None)
    if rival is None:
        return None
    f2 = snap.get("CarIdxF2Time")
    if f2 and me < len(f2) and rival < len(f2) and f2[me] > 0 and f2[rival] >= 0:
        gap = float(f2[me]) - float(f2[rival])
    else:
        pcts = snap.get("CarIdxLapDistPct") or []
        if me >= len(pcts) or rival >= len(pcts) or pcts[rival] < 0:
            return None
        gap = ((pcts[rival] - pcts[me]) % 1.0) * track_len / max(my_speed, 5.0)
    if not (0 < gap < 120):
        return None
    return dict(rival=rival, pos=mypos - 1, gap=round(gap, 2))


def gap_change(now: dict | None, before: dict | None) -> dict | None:
    """Veraenderung des Abstands gegenueber der Vorrunde - nur bei gleichem
    Gegner, sonst ist der Vergleich sinnlos (Ueberholung, Boxenstopp)."""
    if not now or not before or now["rival"] != before["rival"]:
        return None
    d = now["gap"] - before["gap"]
    return dict(delta=round(d, 2), gap=now["gap"], rival=now["rival"], pos=now["pos"])


def lap_traffic(samples: list[dict]) -> dict:
    """Verdichtet die Abtastpunkte einer Runde zu einer Verkehrsbilanz."""
    ah = [s["ahead_s"] for s in samples if "ahead_s" in s]
    be = [s["behind_s"] for s in samples if "behind_s" in s]
    if not ah:
        return dict(traffic_pct=0.0)
    close = float(np.mean(np.array(ah) < TRAFFIC_S) * 100)
    return dict(
        traffic_pct=round(close, 1),
        ahead_min_s=round(float(np.min(ah)), 2),
        ahead_med_s=round(float(np.median(ah)), 2),
        behind_min_s=round(float(np.min(be)), 2) if be else None,
        clean=close < 10.0,
    )


def best_laps(snap: dict, names: dict) -> list[dict]:
    """Bestzeiten aller Fahrer aus dem letzten Abtastpunkt."""
    bl = snap.get("CarIdxBestLapTime") or []
    out = []
    for i, t in enumerate(bl):
        if t and t > 0:
            d = names.get(i, {})
            out.append(dict(idx=i, best=round(float(t), 3),
                            name=d.get("name"), num=d.get("num")))
    return sorted(out, key=lambda x: x["best"])


# --------------------------------------------------------------------------
# Persistenz
# --------------------------------------------------------------------------
def write_lap(path: str, lap_no: int, summary: dict, standings: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(lap=lap_no, **summary, standings=standings[:8]),
                           ensure_ascii=False) + "\n")
