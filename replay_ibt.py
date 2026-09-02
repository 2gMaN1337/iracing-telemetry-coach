"""Spielt eine .ibt-Datei durch den Live-Code ab.

Dient zum Testen der Live-Kette (Referenzwahl, Report, session.jsonl) ohne
laufenden Sim - und um eine Referenzrunde aus einer alten Session anzulegen.

    python replay_ibt.py "<pfad.ibt>"
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ircoach.ibt_reader import read_laps
from live_coach import Collector, LAPS_DIR, slug


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--no-persist", action="store_true")
    ap.add_argument("--voice", action="store_true", help="Ansagen mitsprechen")
    a = ap.parse_args()

    laps, meta = read_laps(a.path)
    if not laps:
        print("Keine Runden gefunden.")
        return 1

    c = Collector(persist=not a.no_persist, voice=a.voice, feed=False)
    c.meta = meta
    c.out_dir = os.path.join(LAPS_DIR, "%s_%s" % (slug(meta["car"]), slug(meta["track"])))
    os.makedirs(c.out_dir, exist_ok=True)
    c.load_reference()

    print("Replay: %d Runden | %s @ %s\n" % (len(laps), meta["car"], meta["track_display"]))
    for lap in laps:
        lap.lap_no = c.lap_no
        c.laps.append(lap)
        c.report(lap)
        c.lap_no += 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
