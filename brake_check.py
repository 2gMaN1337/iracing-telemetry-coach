"""Misst, wie gleichmaessig der Bremsdruck aufgebaut wird.

Zum Vergleichen von Pedaleinstellungen: Einstellung aendern, 10 Runden fahren,
Skript laufen lassen, Zahlen gegen den vorigen Lauf halten. Kleiner ist besser.

    python brake_check.py --latest
    python brake_check.py "<datei.ibt>" [--last 10]

Die beiden aussagekraeftigen Spalten:
  Streuung Spitzendruck  - triffst du Runde fuer Runde denselben Maximaldruck?
  Streuung Aufbauweg     - baust du ihn immer ueber dieselbe Strecke auf?
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ircoach import corners as corner_set
from ircoach.analyze import _smooth, detect_corners
from ircoach.ibt_reader import read_laps
from live_coach import REFS_DIR, slug

TELE_DIR = os.path.expanduser(r"~\Documents\iRacing\telemetry")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--last", type=int, default=18)
    ap.add_argument("--save", metavar="NAME",
                    help="Ergebnis als Vergleichsbasis speichern (z. B. \"vorher\")")
    a = ap.parse_args()

    paths = list(a.paths)
    if a.latest or not paths:
        files = sorted(glob.glob(os.path.join(TELE_DIR, "*.ibt")), key=os.path.getmtime)
        paths = files[-2:]

    laps, meta = [], None
    for p in paths:
        ls, m = read_laps(p)
        meta = meta or m
        laps += [l for l in ls if l.valid and 60 < l.lap_time < 200]
    laps = laps[-a.last:]
    if len(laps) < 5:
        print("Zu wenige gueltige Runden.")
        return 1

    apex = corner_set.load(REFS_DIR, slug(meta["track"])) or corner_set.build(laps)
    corners = detect_corners(laps[0], apex_dists=apex)
    step = float(laps[0].dist[1] - laps[0].dist[0])

    print("%d Runden | %s @ %s\n" % (len(laps), meta["car"], meta["track_display"]))

    # ---- Wo liegt die ABS-Schwelle in Pedalwerten? ----
    b = np.concatenate([l.data["Brake"] for l in laps])
    abs_ch = np.concatenate([l.data["BrakeABSactive"] for l in laps]) \
        if "BrakeABSactive" in laps[0].data else None
    if abs_ch is not None:
        print("Pedalwert   ABS-Regelung")
        thr = None
        for lo in np.arange(0.30, 0.95, 0.05):
            m = (b >= lo) & (b < lo + 0.05)
            if m.sum() < 50:
                continue
            pct = 100 * np.mean(abs_ch[m] > 0.5)
            print("  %.2f-%.2f    %5.1f %%" % (lo, lo + 0.05, pct))
            if thr is None and pct > 50:
                thr = lo
        print("\nhoechster genutzter Pedalwert: %.2f" % b.max())
        if thr:
            print("ab etwa %.2f regelt das ABS ueberwiegend - darueber liegt keine"
                  " Verzoegerung mehr, nur noch Regelung." % thr)
        print()

    # ---- Gleichmaessigkeit je Kurve ----
    print(" Kv    Spitzendruck        Aufbauweg 10->90%%")
    print("       Mittel  Streuung    Mittel   Streuung")
    tot_pk, tot_rise = [], []
    for c in corners:
        lo, hi = c["i_start"], c["i_end"]
        pk, rise = [], []
        for l in laps:
            br = l.data["Brake"][lo:hi]
            if br.max() < 0.15:
                continue
            pk.append(float(br.max()))
            i = np.flatnonzero(br > 0.1 * br.max())
            j = np.flatnonzero(br > 0.9 * br.max())
            if len(i) and len(j) and j[0] > i[0]:
                rise.append((j[0] - i[0]) * step)
        if len(pk) < 5:
            continue
        tot_pk.append(np.std(pk))
        if rise:
            tot_rise.append(np.std(rise))
        print("  T%-3d  %.2f    %.3f       %5.1f m  %5.1f m"
              % (c["n"], np.mean(pk), np.std(pk),
                 np.mean(rise) if rise else 0, np.std(rise) if rise else 0))
    print("-" * 46)
    now = dict(peak_std=float(np.mean(tot_pk)), rise_std=float(np.mean(tot_rise)),
               max_input=float(b.max()), n_laps=len(laps))
    print("  Mittel ueber alle Bremszonen:  Druck %.3f   Weg %.1f m"
          % (now["peak_std"], now["rise_std"]))

    # ---- Vergleich mit gespeicherter Basis ----
    base_path = os.path.join(REFS_DIR, "brake_baseline_%s.json" % slug(meta["track"]))
    if os.path.exists(base_path) and not a.save:
        with open(base_path, encoding="utf-8") as f:
            base = json.load(f)
        print("\n  Vergleich mit \"%s\" (%d Runden):"
              % (base.get("name", "Basis"), base.get("n_laps", 0)))
        for key, label, unit, fmt in (
                ("max_input", "hoechster Pedalwert", "", "%.2f"),
                ("peak_std", "Streuung Spitzendruck", "", "%.3f"),
                ("rise_std", "Streuung Aufbauweg", " m", "%.1f")):
            old = base.get(key)
            if old is None:
                continue
            new, diff = now[key], now[key] - old
            # beim Pedalwert ist mehr besser, bei den Streuungen weniger
            # Unterschiede unterhalb der Anzeigegenauigkeit sind kein Fortschritt
            tol = 0.005 if key == "max_input" else (0.0005 if key == "peak_std" else 0.05)
            if abs(diff) < tol:
                mark = "gleich"
            else:
                better = diff > 0 if key == "max_input" else diff < 0
                mark = "besser" if better else "schlechter"
            print(("    %-22s " + fmt + "%s  ->  " + fmt + "%s   (%+.3f, %s)")
                  % (label, old, unit, new, unit, diff, mark))

    if a.save:
        os.makedirs(REFS_DIR, exist_ok=True)
        now["name"] = a.save
        with open(base_path, "w", encoding="utf-8") as f:
            json.dump(now, f, ensure_ascii=False, indent=1)
        print("\n  als Vergleichsbasis \"%s\" gespeichert." % a.save)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
