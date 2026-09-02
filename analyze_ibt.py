"""Analysiert eine bereits aufgezeichnete .ibt-Datei.

Benutzung:
    python analyze_ibt.py "<pfad.ibt>" [--lap N] [--json out.json]
    python analyze_ibt.py --latest
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ircoach.analyze import compare, detect_corners, make_tips
from ircoach.ibt_reader import read_laps
from ircoach.report import console_report, fmt_time, to_json

TELE_DIR = os.path.expanduser(r"~\Documents\iRacing\telemetry")


def pick_reference(laps):
    """Schnellste gueltige Runde als Referenz."""
    valid = [l for l in laps if l.valid and l.duration() > 10]
    pool = valid or laps
    return min(pool, key=lambda l: l.lap_time or l.duration()) if pool else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    ap.add_argument("--latest", action="store_true", help="neueste Datei im Telemetrie-Ordner")
    ap.add_argument("--lap", type=int, help="nur diese Runde analysieren")
    ap.add_argument("--json", help="Analyse als JSON speichern")
    a = ap.parse_args()

    path = a.path
    if a.latest or not path:
        files = sorted(glob.glob(os.path.join(TELE_DIR, "*.ibt")), key=os.path.getmtime)
        if not files:
            print("Keine .ibt-Dateien gefunden.")
            return 1
        path = files[-1]

    print("Datei: %s" % os.path.basename(path))
    laps, meta = read_laps(path)
    print("%s | %s | %.0f m" % (meta["car"], meta["track_display"], meta["track_len"]))
    if not laps:
        print("Keine vollstaendigen Runden in der Datei.")
        return 1

    print("Runden: %d" % len(laps))
    for l in laps:
        flag = "" if l.valid else "  (ungueltig: %s)" % l.invalid_reason
        print("  #%-3d %s%s" % (l.lap_no, fmt_time(l.lap_time or l.duration()), flag))

    ref = pick_reference(laps)
    corners = detect_corners(ref)
    print("\nReferenz: Runde %d (%s) | %d Kurven erkannt\n"
          % (ref.lap_no, fmt_time(ref.lap_time or ref.duration()), len(corners)))

    if a.lap is not None:
        targets = [l for l in laps if l.lap_no == a.lap]
    else:
        targets = [l for l in laps if l.valid and l is not ref]
    targets = targets or [ref]
    for lap in targets:
        cmp = compare(lap, ref, corners)
        tips = make_tips(cmp)
        print(console_report(cmp, tips, meta))
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                f.write(to_json(cmp, tips, meta))
            print("JSON -> %s" % a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
