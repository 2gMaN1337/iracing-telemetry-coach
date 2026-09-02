"""Wertet mehrere .ibt-Dateien gegen die gespeicherte Referenz aus und
fasst zusammen, welche Kurven und welche Fehler wiederkehrend Zeit kosten.

    python aggregate.py <datei.ibt> [<datei.ibt> ...]
"""
from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ircoach.analyze import compare, detect_corners, make_tips
from ircoach.ibt_reader import read_laps
from ircoach.lap import Lap
from ircoach.report import fmt_time
from live_coach import REFS_DIR, slug


def main() -> int:
    paths = sys.argv[1:]
    if not paths:
        print(__doc__)
        return 1

    ref = None
    per_corner = defaultdict(list)
    findings = defaultdict(Counter)
    times = []
    n_laps = 0

    for p in paths:
        laps, meta = read_laps(p)
        if ref is None:
            rp = os.path.join(REFS_DIR, "%s_%s.npz" % (slug(meta["car"]), slug(meta["track"])))
            if not os.path.exists(rp):
                print("Keine Referenz unter %s" % rp)
                return 1
            ref = Lap.load(rp)
            corners = detect_corners(ref)
            print("Referenz: %s | %s @ %s | %d Kurven\n"
                  % (fmt_time(ref.lap_time), meta["car"], meta["track_display"], len(corners)))
        for lap in laps:
            if not lap.valid or lap.lap_time > ref.lap_time * 1.15:
                continue
            n_laps += 1
            times.append(lap.lap_time)
            cmp = compare(lap, ref, corners)
            for c in cmp["corners"]:
                per_corner[c["n"]].append(c["delta"])
            for t in make_tips(cmp, max_corners=99):
                for key, _ in t["findings"]:
                    findings[t["corner"]][key] += 1
        print("  %-46s %3d Runden" % (os.path.basename(p)[:46], len(laps)))

    if not n_laps:
        print("Keine auswertbaren Runden.")
        return 1

    times = np.array(times)
    print("\n%d gueltige Runden | schnellste %s | Median %s | Streuung %.2f s"
          % (n_laps, fmt_time(times.min()), fmt_time(float(np.median(times))),
             float(times.std())))

    print("\nDurchschnittlicher Zeitverlust pro Abschnitt (vs. Bestrunde):")
    rows = sorted(per_corner.items(), key=lambda kv: -float(np.mean(kv[1])))
    for n, vals in rows:
        v = np.array(vals)
        bar = "#" * int(round(max(0.0, float(v.mean())) * 40))
        print("  T%-2d  Mittel %+.3f s   Bestfall %+.3f   Streuung %.3f  %s"
              % (n, v.mean(), v.min(), v.std(), bar))

    print("\nWiederkehrende Fehler (Anteil der Runden):")
    for n, _ in rows[:4]:
        c = findings.get(n)
        if not c:
            continue
        top = ", ".join("%s %d%%" % (k, round(100 * v / n_laps)) for k, v in c.most_common(5))
        print("  T%-2d  %s" % (n, top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
