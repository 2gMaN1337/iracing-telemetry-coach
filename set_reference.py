"""Setzt eine Runde aus einer .ibt-Datei als Referenz - auch eine fremde.

Gedacht fuer heruntergeladene Runden, um gegen eine schnellere
Rundenzeit zu vergleichen und deren Brems- und Gaspunkte im Overlay zu sehen.

    python set_reference.py "<datei.ibt>"            # schnellste gueltige Runde
    python set_reference.py "<datei.ibt>" --lap 7    # bestimmte Runde
    python set_reference.py --list "<datei.ibt>"     # nur anzeigen
    python set_reference.py --restore                # eigene Referenz zurueck

Die bisherige Referenz wird vorher gesichert, damit nichts verloren geht.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ircoach import corners as corner_set
from ircoach.analyze import detect_corners
from ircoach.ibt_reader import read_laps
from ircoach.lap import Lap
from ircoach.livefeed import reference_points
from ircoach.report import fmt_time
from live_coach import REFS_DIR, slug


def ref_path(car: str, track: str) -> str:
    return os.path.join(REFS_DIR, "%s_%s.npz" % (slug(car), slug(track)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    ap.add_argument("--lap", type=int, help="Rundennummer aus der Datei")
    ap.add_argument("--list", action="store_true", help="nur Runden anzeigen")
    ap.add_argument("--restore", action="store_true", help="gesicherte Referenz zurueckholen")
    a = ap.parse_args()

    if a.restore:
        found = False
        for f in os.listdir(REFS_DIR):
            if f.endswith(".own.npz"):
                dst = os.path.join(REFS_DIR, f[:-8] + ".npz")
                shutil.copy(os.path.join(REFS_DIR, f), dst)
                print("zurueckgeholt: %s" % os.path.basename(dst))
                found = True
        if not found:
            print("Keine gesicherte Referenz gefunden.")
        return 0 if found else 1

    if not a.path:
        ap.error("Pfad zur .ibt-Datei fehlt")

    laps, meta = read_laps(a.path)
    if not laps:
        print("Keine vollstaendigen Runden in der Datei.")
        return 1
    print("%s @ %s | %d Runden" % (meta["car"], meta["track_display"], len(laps)))

    valid = [l for l in laps if l.valid]
    for l in laps:
        flag = "" if l.valid else "  (ungueltig: %s)" % l.invalid_reason
        print("   #%-3d %s%s" % (l.lap_no, fmt_time(l.lap_time), flag))
    if a.list:
        return 0
    if not valid:
        print("\nKeine gueltige Runde - Referenz nicht gesetzt.")
        return 1

    if a.lap is not None:
        pick = next((l for l in laps if l.lap_no == a.lap), None)
        if pick is None:
            print("\nRunde %d nicht in der Datei." % a.lap)
            return 1
        if not pick.valid:
            print("\nRunde %d ist ungueltig (%s) - trotzdem gesetzt."
                  % (a.lap, pick.invalid_reason))
    else:
        pick = min(valid, key=lambda l: l.lap_time)

    dst = ref_path(meta["car"], meta["track"])
    if os.path.exists(dst):
        backup = dst[:-4] + ".own.npz"
        if not os.path.exists(backup):
            shutil.copy(dst, backup)
            print("\nbisherige Referenz gesichert: %s" % os.path.basename(backup))
        old = Lap.load(dst)
        print("bisher: %s   neu: %s   (%+.3f s)"
              % (fmt_time(old.lap_time), fmt_time(pick.lap_time),
                 pick.lap_time - old.lap_time))

    pick.save(dst)
    print("\nReferenz gesetzt: %s -> %s" % (fmt_time(pick.lap_time), os.path.basename(dst)))

    # Kontrolle: passen Kurvensatz und Marker zur neuen Referenz?
    apex = corner_set.load(REFS_DIR, slug(meta["track"]))
    cs = detect_corners(pick, apex_dists=apex)
    pts = reference_points(pick, cs)
    nb = sum(1 for p in pts if p["brake"])
    ng = sum(1 for p in pts if p["gas"])
    print("%d Kurven | %d Bremspunkte, %d Gaspunkte fuer das Overlay" % (len(cs), nb, ng))
    has_pos = all(k in pick.data for k in ("Lat", "Lon"))
    print("Positionsdaten fuer den Linienvergleich: %s" % ("ja" if has_pos else "nein"))
    if apex is None:
        print("\nHinweis: Kein gespeicherter Kurvensatz fuer diese Strecke - die "
              "Kurven wurden aus dieser einen Runde erkannt und koennen von "
              "deinen bisherigen Nummern abweichen.")
    print("\nDen Coach neu starten, damit er die neue Referenz laedt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
