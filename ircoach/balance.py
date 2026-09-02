"""Bremsbalance-Bewertung aus dem Rotationsueberschuss beim Anbremsen.

Die Radgeschwindigkeiten, mit denen sich Blockierer direkt messen liessen,
stellt iRacing im Live-Speicher nicht bereit. Stattdessen wird die Drehrate
gegen die Querbeschleunigung gehalten:

    Stationaer gilt  a_quer = v * Drehrate.
    Rotationsueberschuss = Drehrate - a_quer / v,  auf Kurvenrichtung normiert.

Positiv heisst: Das Auto dreht staerker ein, als die Querkraft hergibt - also
Uebersteuern. Gegen die Radgeschwindigkeiten aus der Disk-Telemetrie geprueft:
Korrelation +0.73 mit Heckblockierern, nur +0.25 mit Vorderradblockierern. Der
Wert bildet also gezielt die Heckstabilitaet ab.

Referenzwerte aus 17 Rennrunden (Porsche 911 Cup, Red Bull Ring):

    Balance   Rotation      Heck-Lock   Front-Lock
    -2.00     +26.1 mrad/s      24.6         48.8
    -1.50     +16.0 mrad/s      16.2         72.2
    -1.25     +13.2 mrad/s      28.3        108.3

Unterhalb von etwa 12 mrad/s nimmt das Vorderradblockieren stark zu, ohne dass
das Heck ruhiger wird - deshalb das Zielband 12 bis 22.
"""
from __future__ import annotations

import json
import os

import numpy as np

# Zielband des Rotationsueberschusses in mrad/s
TARGET_LO, TARGET_HI = 12.0, 22.0

MIN_BRAKE = 0.25      # nur echte Bremsphasen
MIN_SPEED = 20.0      # m/s
MIN_LAT = 2.0         # m/s^2, sonst ist es keine Kurve


def rotation_excess(lap) -> float | None:
    """Mittlerer Rotationsueberschuss beim Anbremsen, in mrad/s."""
    D = lap.data
    need = ("Speed", "Brake", "YawRate", "LatAccel")
    if not all(k in D for k in need):
        return None
    v, brk, yaw, lat = D["Speed"], D["Brake"], D["YawRate"], D["LatAccel"]
    m = (brk > MIN_BRAKE) & (v > MIN_SPEED) & (np.abs(lat) > MIN_LAT)
    if m.sum() < 30:
        return None
    expected = lat[m] / np.maximum(v[m], 1e-3)
    return float(np.mean((yaw[m] - expected) * np.sign(lat[m])) * 1000)


def per_corner(lap, corners) -> list[tuple]:
    """Rotationsueberschuss je Kurve - zeigt, wo die Balance stoert."""
    D = lap.data
    if not all(k in D for k in ("Speed", "Brake", "YawRate", "LatAccel")):
        return []
    out = []
    for c in corners:
        lo, hi = c["i_start"], c["i_end"]
        v, brk = D["Speed"][lo:hi], D["Brake"][lo:hi]
        yaw, lat = D["YawRate"][lo:hi], D["LatAccel"][lo:hi]
        m = (brk > MIN_BRAKE) & (v > MIN_SPEED) & (np.abs(lat) > MIN_LAT)
        if m.sum() < 10:
            continue
        exp = lat[m] / np.maximum(v[m], 1e-3)
        out.append((c["n"], float(np.mean((yaw[m] - exp) * np.sign(lat[m])) * 1000)))
    return out


# ---------------- Anzeige in Fahrzeugeinheiten ----------------
def load_offset(refs_dir: str, car_slug: str) -> float | None:
    """Offset, um dcBrakeBias (%) in die Cockpit-Anzeige umzurechnen."""
    p = os.path.join(refs_dir, "biasoffset_%s.json" % car_slug)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return float(json.load(f)["offset"])
    except Exception:
        return None


def save_offset(refs_dir: str, car_slug: str, bias_pct: float, shown: float) -> None:
    """Einmal kalibrieren: aktueller Prozentwert und was im Cockpit steht."""
    os.makedirs(refs_dir, exist_ok=True)
    with open(os.path.join(refs_dir, "biasoffset_%s.json" % car_slug),
              "w", encoding="utf-8") as f:
        json.dump(dict(offset=bias_pct - shown, calibrated_at=bias_pct,
                       shown_as=shown), f, ensure_ascii=False, indent=1)


def as_shown(bias_pct: float, offset: float | None) -> str:
    if bias_pct is None:
        return "?"
    if offset is None:
        return "%.2f %%" % bias_pct
    return "%+.2f" % (bias_pct - offset)


# ---------------- Empfehlung ----------------
def advice(rot: float | None, streak: int = 1) -> tuple[str, str] | None:
    """Gibt (Kurztext fuer die Ansage, Begruendung) zurueck oder None.

    `streak` = Zahl der Runden in Folge ausserhalb des Zielbands. Erst ab zwei
    Runden wird etwas gesagt, damit eine einzelne wilde Runde keine Empfehlung
    ausloest.
    """
    if rot is None:
        return None
    # Ein einzelner extremer Ausschlag (z. B. blockierendes Hinterrad) soll
    # sofort melden, nicht erst wenn er sich wiederholt.
    if rot < 2 * TARGET_HI and streak < 2:
        return None
    if rot > TARGET_HI:
        return ("Bremsbalance eine Stufe nach vorn.",
                "Rotationsueberschuss %.0f mrad/s (Ziel %.0f-%.0f) - das Heck "
                "dreht beim Anbremsen staerker ein als die Querkraft hergibt."
                % (rot, TARGET_LO, TARGET_HI))
    if rot < TARGET_LO:
        return ("Bremsbalance eine Stufe nach hinten.",
                "Rotationsueberschuss nur %.0f mrad/s (Ziel %.0f-%.0f) - das Auto "
                "schiebt beim Anbremsen, die Vorderraeder tragen zu viel."
                % (rot, TARGET_LO, TARGET_HI))
    return None
