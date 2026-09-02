"""Importiert eine Session aus der .ibt-Datei in laps/.

Der Live-Mitschnitt aus dem Shared Memory kennt weder GPS-Position noch
Radgeschwindigkeiten - diese Kanaele existieren nur in der Disk-Telemetrie.
Nach einem Stint holt dieses Skript die volle Session aus der .ibt-Datei nach,
damit im Dashboard auch Linienvergleich und Blockierer-Erkennung erscheinen.

    python import_session.py --latest        # neueste Datei
    python import_session.py "<pfad.ibt>"
    python import_session.py --latest --keep # bestehende Runden nicht ersetzen
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ircoach.ibt_reader import read_laps
from ircoach.report import fmt_time
from live_coach import LAPS_DIR, Collector, slug

TELE_DIR = os.path.expanduser(r"~\Documents\iRacing\telemetry")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--keep", action="store_true",
                    help="vorhandene Runden im Ordner behalten")
    ap.add_argument("--no-persist", action="store_true",
                    help="Referenzrunde nicht aktualisieren")
    a = ap.parse_args()

    path = a.path
    if a.latest or not path:
        files = sorted(glob.glob(os.path.join(TELE_DIR, "*.ibt")), key=os.path.getmtime)
        if not files:
            print("Keine .ibt-Dateien gefunden.")
            return 1
        path = files[-1]

    print("Import: %s" % os.path.basename(path))
    laps, meta = read_laps(path)
    if not laps:
        print("Keine vollstaendigen Runden in der Datei.")
        return 1

    out_dir = os.path.join(LAPS_DIR, "%s_%s" % (slug(meta["car"]), slug(meta["track"])))
    os.makedirs(out_dir, exist_ok=True)
    if not a.keep:
        for f in glob.glob(os.path.join(out_dir, "lap_*")):
            os.remove(f)

    c = Collector(persist=not a.no_persist, voice=False, feed=False)
    c.meta = meta
    c.out_dir = out_dir
    c.load_reference()

    n_valid = 0
    for lap in laps:
        lap.lap_no = c.lap_no
        c.report(lap)
        c.lap_no += 1
        n_valid += bool(lap.valid)

    has_pos = all(k in laps[0].data for k in ("Lat", "Lon"))
    print("\n%d Runden importiert (%d gueltig) | %s @ %s"
          % (len(laps), n_valid, meta["car"], meta["track_display"]))
    print("Linienvergleich verfuegbar: %s" % ("ja" if has_pos else "nein"))
    if c.ref:
        print("Referenz: %s" % fmt_time(c.ref.lap_time))
    print("\nDashboard neu laden: http://localhost:8099")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
