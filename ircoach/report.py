"""Konsolen- und JSON-Ausgabe der Rundenanalyse."""
from __future__ import annotations

import json

import numpy as np


def fmt_time(s: float) -> str:
    if not s or s <= 0:
        return "--:--.---"
    m, rest = divmod(float(s), 60.0)
    return "%d:%06.3f" % (int(m), rest) if m else "%.3f" % rest


def fmt_delta(s: float) -> str:
    return "%+.3f" % s


def console_report(cmp: dict, tips: list[dict], meta: dict) -> str:
    L = []
    W = 72
    L.append("=" * W)
    head = "RUNDE %s   %s" % (cmp["lap_no"], fmt_time(cmp["lap_time"]))
    if cmp["ref_time"]:
        head += "   vs. Referenz %s   %s s" % (fmt_time(cmp["ref_time"]),
                                               fmt_delta(cmp["total_delta"]))
    L.append(head)
    if not cmp.get("valid", True):
        L.append("!! UNGUELTIG: %s - kein Vergleich." % cmp.get("invalid_reason", ""))
        L.append("=" * W)
        return "\n".join(L)
    ch = cmp.get("gap_change")
    if ch:
        L.append("Vordermann (P%d): %.2f s Abstand   %+.2f s gegenueber der Vorrunde"
                 % (ch["pos"], ch["gap"], ch["delta"]))
    t = cmp.get("traffic") or {}
    if t.get("traffic_pct") is not None:
        L.append("Verkehr: %.0f%% der Runde naeher als 1.5 s   naechstes Auto %.2f s%s"
                 % (t["traffic_pct"], t.get("ahead_min_s") or 0,
                    "   (freie Fahrt)" if t.get("clean") else ""))
    if cmp.get("rotation") is not None:
        line = "Bremsbalance %s   Rotationsueberschuss %+.0f mrad/s" % (
            cmp.get("bias_shown") or "?", cmp["rotation"])
        adv = cmp.get("bias_advice")
        L.append(line + ("   >>> %s" % adv[0] if adv else "   (im Zielband 12-22)"))
        if adv:
            L.append("    %s" % adv[1])
    if cmp.get("warning"):
        L.append("Hinweis: %s" % cmp["warning"])
    L.append("=" * W)

    if "full_throttle_pct" in cmp:
        L.append("Vollgas %.0f%%   Bremse %.0f%%   Segeln %.0f%%   Ueberschneidung %.0f m"
                 % (cmp["full_throttle_pct"], cmp["brake_pct"],
                    cmp["coast_pct"], cmp.get("overlap_m", 0.0)))
        L.append("-" * W)

    # Kurven-Tabelle (Abschnitt = Anbremsen bis Ende der folgenden Geraden)
    line = cmp.get("has_line")
    L.append("%-4s %6s %7s %7s %6s %6s %7s %7s%s"
             % ("Kv", "ab m", "Delta", "davon", "vMin", "Ref", "Bremse", "Ref",
                "   Linie" if line else ""))
    L.append("%-4s %6s %7s %7s %6s %6s %7s %7s%s"
             % ("", "", "gesamt", "Gerade", "km/h", "km/h", "m", "m",
                "  Scheitel" if line else ""))
    for c in cmp["corners"]:
        r = c["ref"]
        bp = "%.0f" % c["d_brake"] if c["d_brake"] is not None else "-"
        rbp = "%.0f" % r["d_brake"] if r["d_brake"] is not None else "-"
        mark = " <<<" if c["delta"] >= 0.05 else ""
        ln = ""
        if line and c.get("line"):
            ln = "  %+6.1f m" % c["line"]["apex"]
        L.append("T%-3d %6.0f %7s %7s %6.0f %6.0f %7s %7s%s%s"
                 % (c["n"], c.get("d_seg_start", c["d_start"]), fmt_delta(c["delta"]),
                    fmt_delta(c.get("delta_straight", 0.0)),
                    c["v_min"], r["v_min"], bp, rbp, ln, mark))
    L.append("-" * W)

    if not tips:
        L.append("Keine nennenswerten Verluste - saubere Runde.")
    else:
        L.append("GROESSTE ZEITVERLUSTE")
        for t in tips:
            L.append("")
            L.append("  T%d  (%s s, ab %.0f m)" % (t["corner"], fmt_delta(t["delta"]), t["d_start"]))
            if not t["findings"]:
                L.append("    - Zeit verloren, aber kein einzelner Fehler dominant"
                         " (Linie / Fahrzeugbalance pruefen).")
            for _, txt in t["findings"]:
                L.append("    - " + txt)
    L.append("=" * W)
    return "\n".join(L)


def to_json(cmp: dict, tips: list[dict], meta: dict) -> str:
    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items() if k != "delta_trace"}
        if isinstance(o, (list, tuple)):
            return [clean(x) for x in o]
        if isinstance(o, (np.floating, np.integer)):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
        return o

    return json.dumps(dict(meta=meta, summary=clean(cmp), tips=clean(tips)),
                      ensure_ascii=False, indent=1)
