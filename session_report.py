"""Gesamtauswertung eines Stints ueber eine oder mehrere .ibt-Dateien.

    python session_report.py <datei.ibt> [<datei.ibt> ...] [--json out.json]

Liefert Rundenverlauf, Verlust je Abschnitt, Haeufigkeit der Befunde,
theoretisch beste Runde und den Vergleich erste/zweite Haelfte.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ircoach.analyze import compare, detect_corners, make_tips
from ircoach import corners as corner_set
from ircoach.ibt_reader import read_laps
from ircoach.report import fmt_time
from live_coach import REFS_DIR, slug


def collect(paths):
    laps, meta = [], None
    for p in paths:
        ls, m = read_laps(p)
        meta = meta or m
        laps += [l for l in ls if l.valid and 60 < l.lap_time < 200]
    return laps, meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--json")
    ap.add_argument("--last", type=int, help="nur die letzten N Runden")
    a = ap.parse_args()

    laps, meta = collect(a.paths)
    if a.last:
        laps = laps[-a.last:]
    if len(laps) < 2:
        print("Zu wenige gueltige Runden.")
        return 1

    ref = min(laps, key=lambda l: l.lap_time)
    # Kurvensatz aus allen Runden mitteln, damit die Kurvenzahl nicht von der
    # zufaellig schnellsten Runde abhaengt.
    # Gespeicherter Kurvensatz hat Vorrang: Er gehoert zur Strecke und muss
    # ueber alle Auswertungen hinweg gleich bleiben. Nur wenn es noch keinen
    # gibt, wird einer aus dieser Session gebaut und abgelegt.
    apex = corner_set.load(REFS_DIR, slug(meta["track"]))
    if apex is None:
        apex = corner_set.build(laps)
        if apex:
            corner_set.save(REFS_DIR, slug(meta["track"]), apex, len(laps))
    corners = detect_corners(ref, apex_dists=apex)
    times = np.array([l.lap_time for l in laps])

    per_corner = defaultdict(list)
    per_vmin = defaultdict(list)
    per_abs = defaultdict(list)
    per_line = defaultdict(list)
    findings = defaultdict(Counter)
    finding_total = Counter()

    for lap in laps:
        cmp = compare(lap, ref, corners)
        for c in cmp["corners"]:
            per_corner[c["n"]].append(c["delta"])
            per_vmin[c["n"]].append(c["v_min"])
            if c["abs_pct"] is not None:
                per_abs[c["n"]].append(c["abs_pct"])
            if c.get("line"):
                per_line[c["n"]].append(c["line"]["apex"])
        for t in make_tips(cmp, max_corners=99):
            for key, _ in t["findings"]:
                findings[t["corner"]][key] += 1
                finding_total[key] += 1

    half = len(laps) // 2
    t1, t2 = times[:half], times[half:]

    out = dict(
        car=meta["car"], track=meta["track_display"],
        n_laps=len(laps),
        best=float(times.min()), best_str=fmt_time(float(times.min())),
        median=float(np.median(times)), mean=float(times.mean()),
        std=float(times.std()), worst=float(times.max()),
        times=[round(float(t), 3) for t in times],
        first_half_mean=float(t1.mean()), second_half_mean=float(t2.mean()),
        first_half_std=float(t1.std()), second_half_std=float(t2.std()),
        corners=[], findings_total=dict(finding_total.most_common()),
    )

    theo = 0.0
    for n in sorted(per_corner):
        d = np.array(per_corner[n])
        v = np.array(per_vmin[n])
        theo += float(d.min())
        out["corners"].append(dict(
            n=n,
            d_start=round(float(ref.dist[[c for c in corners if c["n"] == n][0]["i_seg_start"]])),
            mean=float(d.mean()), best=float(d.min()), worst=float(d.max()),
            std=float(d.std()),
            vmin_mean=float(v.mean()), vmin_min=float(v.min()), vmin_max=float(v.max()),
            vmin_ref=float(v[int(np.argmin(times))]),
            abs_mean=float(np.mean(per_abs[n])) if per_abs[n] else None,
            line_mean=float(np.mean(per_line[n])) if per_line[n] else None,
            findings=dict(findings[n].most_common(5)),
        ))
    out["theoretical_best"] = float(ref.lap_time + theo)
    out["theoretical_best_str"] = fmt_time(out["theoretical_best"])

    # ---------- Konsolenfassung ----------
    W = 74
    print("=" * W)
    print("STINT-AUSWERTUNG  %s @ %s" % (out["car"], out["track"]))
    print("=" * W)
    print("%d gueltige Runden | beste %s | Median %s | Streuung %.3f s"
          % (out["n_laps"], out["best_str"], fmt_time(out["median"]), out["std"]))
    print("erste Haelfte %s (s %.3f)   zweite Haelfte %s (s %.3f)"
          % (fmt_time(out["first_half_mean"]), out["first_half_std"],
             fmt_time(out["second_half_mean"]), out["second_half_std"]))
    print("theoretisch beste Runde: %s  (%+.3f s auf die Bestzeit)"
          % (out["theoretical_best_str"], out["theoretical_best"] - out["best"]))
    print("-" * W)
    print("%-5s %6s %8s %8s %7s %8s %7s" %
          ("Kv", "ab m", "Mittel", "Bestfall", "Streu", "vMin", "ABS"))
    for c in sorted(out["corners"], key=lambda x: -x["mean"]):
        print("T%-4d %6d %+8.3f %+8.3f %7.3f %5.0f-%3.0f %6s"
              % (c["n"], c["d_start"], c["mean"], c["best"], c["std"],
                 c["vmin_min"], c["vmin_max"],
                 "%.0f%%" % c["abs_mean"] if c["abs_mean"] is not None else "-"))
    print("-" * W)
    # Ein Befund kann in derselben Runde in mehreren Kurven auftreten -
    # deshalb Nennungen zaehlen, nicht Runden.
    n_slots = out["n_laps"] * len(out["corners"])
    print("Haeufigste Befunde (%d Runden x %d Abschnitte = %d Gelegenheiten):"
          % (out["n_laps"], len(out["corners"]), n_slots))
    for k, v in list(out["findings_total"].items())[:8]:
        print("   %-22s %3d Nennungen  (%.0f%% der Abschnitte)"
              % (k, v, 100 * v / n_slots))
    print("=" * W)

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print("JSON -> %s" % a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
