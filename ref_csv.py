"""Referenzrunden als CSV ein- und ausgeben.

Einlesen - eine fremde Bestrunde wird zur Referenz des Coaches:

    python ref_csv.py import fremde_runde.csv --car "Porsche 911 Cup (992.2)" \
        --track "monza full" --setup open --track-len 5750.8 --lap-time 106.204

Ausgeben - eine eigene Runde fuer VirtualCoach oder ein anderes Werkzeug:

    python ref_csv.py export laps/<ordner>/lap_012.npz meine_runde.csv

Beim Einlesen wird die vorhandene Referenz nicht ueberschrieben, sondern
unter <name>.ext.npz abgelegt. Erst --replace setzt sie als Referenz ein.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ircoach import refcsv
from ircoach.lap import Lap
from ircoach.report import fmt_time

BASE = os.path.dirname(os.path.abspath(__file__))
REFS_DIR = os.path.join(BASE, "refs")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_") or "unknown"


def do_import(a) -> int:
    lap = refcsv.read(a.csv, track_len=a.track_len, lap_time=a.lap_time or 0.0,
                      car=a.car, track=a.track, source="fremd")
    base = "%s_%s_%s" % (slug(a.car), slug(a.track), a.setup)
    target = os.path.join(REFS_DIR, base + (".npz" if a.replace else ".ext.npz"))
    if a.replace and os.path.exists(os.path.join(REFS_DIR, base + ".npz")):
        backup = os.path.join(REFS_DIR, base + ".eigen.npz")
        if not os.path.exists(backup):
            os.replace(os.path.join(REFS_DIR, base + ".npz"), backup)
            print("Bisherige Referenz gesichert: %s" % os.path.basename(backup))
    os.makedirs(REFS_DIR, exist_ok=True)
    lap.save(target)
    print("Gelesen: %d Zeilen, %d Kanaele, Rundenzeit %s"
          % (lap.n_samples, len(lap.data) - 1, fmt_time(lap.lap_time)))
    print("Kanaele: %s" % ", ".join(sorted(k for k in lap.data if k != "t")))
    print("Gespeichert: %s" % target)
    if not a.replace:
        print("\nNoch nicht aktiv. Mit --replace wird sie zur Referenz des Coaches.")
    if not a.lap_time:
        print("\nAchtung: ohne --lap-time ist die Rundenzeit aus dem Tempoverlauf "
              "integriert und weicht typisch um einige Zehntel ab.")
    return 0


def do_export(a) -> int:
    lap = Lap.load(a.npz)
    missing = refcsv.write(lap, a.csv)
    print("Geschrieben: %s (%d Zeilen, Rundenzeit %s)"
          % (a.csv, len(lap.dist), fmt_time(lap.lap_time)))
    if missing:
        print("Nicht vorhanden, mit 0 gefuellt: %s" % ", ".join(missing))
    if "Lat" in missing or "Lon" in missing:
        print("\nOhne Lat/Lon fehlt die Streckenkarte. Diese Kanaele stehen nur "
              "im Shared Memory nicht zur Verfuegung - eine mit import_session.py "
              "nachgeladene Runde hat sie.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("import", help="CSV als Referenz einlesen")
    i.add_argument("csv")
    i.add_argument("--car", required=True)
    i.add_argument("--track", required=True)
    i.add_argument("--setup", default="open", choices=["open", "fixed"])
    i.add_argument("--track-len", type=float, required=True, help="Streckenlaenge in m")
    i.add_argument("--lap-time", type=float, default=0.0, help="Rundenzeit in Sekunden")
    i.add_argument("--replace", action="store_true",
                   help="als Referenz des Coaches einsetzen (Sicherung wird angelegt)")
    i.set_defaults(func=do_import)

    e = sub.add_parser("export", help="eigene Runde als CSV schreiben")
    e.add_argument("npz")
    e.add_argument("csv")
    e.set_defaults(func=do_export)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
