"""Gibt pro neuer Runde eine kompakte Zeile aus (fuer den Monitor).

Zaehlt gelesene Zeilen statt Byte-Positionen: wird session.jsonl gekuerzt oder
neu geschrieben, zeigt ein gemerkter Byte-Offset sonst mitten in eine Zeile und
das Parsen schlaegt fehl.
"""
from __future__ import annotations

import json
import os
import sys
import time

FEED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session.jsonl")


def fmt(s: float) -> str:
    m, r = divmod(float(s), 60.0)
    return "%d:%06.3f" % (int(m), r) if m >= 1 else "%.3f" % r


def line_for(d: dict) -> str:
    if not d.get("valid", True):
        return "Runde %d UNGUELTIG (%s)" % (d["lap"], d.get("reason", ""))
    head = "Runde %d | %s | %+.3f s" % (d["lap"], fmt(d["time"]), d["delta"])
    if d.get("new_ref"):
        head += " | NEUE BESTZEIT"
    tips = d.get("tips") or []
    if tips:
        head += " | Verlust: " + ", ".join("T%d %+.2f" % (t["corner"], t["delta"])
                                           for t in tips)
    return head


def read_all() -> list[dict]:
    if not os.path.exists(FEED):
        return []
    out = []
    with open(FEED, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw))
            except Exception:
                continue          # halb geschriebene Zeile - beim naechsten Mal
    return out


def main() -> None:
    seen = len(read_all())        # Bestand beim Start nicht nachtraeglich melden
    while True:
        try:
            rows = read_all()
            if len(rows) < seen:
                seen = len(rows)  # Datei gekuerzt oder ersetzt
            for d in rows[seen:]:
                print(line_for(d), flush=True)
            seen = len(rows)
        except Exception as e:
            print("Watcher-Fehler: %r" % e, flush=True)
        time.sleep(1.0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
