"""Vorfallspunkte mitschreiben - iRacings eigener Zaehler, nicht geschaetzt.

Der Kanal `PlayerCarMyIncidentCount` steht im Shared Memory und in der
.ibt-Datei und zaehlt die Punkte der laufenden Session. Aus dem Sprung
zwischen zwei Abtastungen laesst sich die Art des Vorfalls ablesen:

    1x  vier Raeder neben der Strecke
    2x  Kontrollverlust
    4x  Kontakt mit einem Auto oder der Mauer

Das ist der einzige belastbare Weg. Ein aus der Telemetrie geschaetzter
Wert - etwa ueber das Surface-Flag - zaehlt Randsteine mit und liegt
schnell um das Dreifache daneben.

Warum das zaehlt: das Safety Rating ergibt sich aus Kurven pro Vorfall.
Ein Kontakt kostet so viel wie vier Ausfluege, weshalb die Punktzahl
aussagekraeftiger ist als die Zahl der Ereignisse.
"""
from __future__ import annotations

ART = {
    1: "vier Raeder neben der Strecke",
    2: "Kontrollverlust",
    4: "Kontakt (Auto oder Mauer)",
}

# Ab hier lohnt die Erwaehnung in der Ansage. Ein einzelner Ausflug ist
# Alltag; ein Kontakt nicht.
SPEAK_MIN = 2


def art(points: int) -> str:
    return ART.get(points, "%d Punkte" % points)


class Tracker:
    """Verfolgt den Zaehler und ordnet jeden Sprung einer Stelle zu."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.count = None        # letzter gelesener Stand
        self.start = None        # Stand beim Sessionbeginn
        self.events: list[dict] = []

    @property
    def total(self) -> int:
        return 0 if self.count is None or self.start is None else self.count - self.start

    def update(self, count, lap_no: int, dist_m: float, speed_kmh: float) -> list[dict]:
        """Neuen Zaehlerstand verarbeiten, neue Ereignisse zurueckgeben."""
        if count is None:
            return []
        count = int(count)
        if self.count is None:
            self.count = self.start = count
            return []
        # Ein Sessionwechsel setzt den Zaehler zurueck - dann neu aufsetzen,
        # sonst entstuende ein negativer Sprung.
        if count < self.count:
            self.count = self.start = count
            return []
        if count == self.count:
            return []
        ev = dict(lap=lap_no, points=count - self.count,
                  dist=round(float(dist_m)), speed=round(float(speed_kmh)))
        self.count = count
        self.events.append(ev)
        return [ev]

    def for_lap(self, lap_no: int) -> list[dict]:
        return [e for e in self.events if e["lap"] == lap_no]

    def hotspots(self, window_m: float = 150.0) -> list[dict]:
        """Stellen, an denen sich Vorfaelle haeufen - nach Punkten sortiert.

        Nicht nach Haeufigkeit: vier Ausfluege an einer Stelle kosten
        zusammen weniger als ein einzelner Kontakt woanders.
        """
        groups: list[dict] = []
        for e in sorted(self.events, key=lambda x: x["dist"]):
            if groups and e["dist"] - groups[-1]["dist"] <= window_m:
                g = groups[-1]
                g["points"] += e["points"]
                g["n"] += 1
                g["dist"] = e["dist"]
            else:
                groups.append(dict(dist=e["dist"], points=e["points"], n=1))
        return sorted(groups, key=lambda g: -g["points"])


def lap_lines(events: list[dict], total: int) -> list[str]:
    """Zeilen fuer den Rundenbericht."""
    if not events:
        return []
    out = ["VORFAELLE in dieser Runde:"]
    for e in events:
        out.append("  %dx bei %d m (%d km/h) - %s"
                   % (e["points"], e["dist"], e["speed"], art(e["points"])))
    out.append("  Session gesamt: %d Punkte" % total)
    return out


def spoken(events: list[dict]) -> str:
    """Kurzer Satz fuer die Ansage - nur bei nennenswerten Vorfaellen.

    Die Ansage nennt die Punktzahl, nicht die Stelle: waehrend der Fahrt
    weiss der Fahrer selbst, wo es passiert ist.
    """
    pts = sum(e["points"] for e in events)
    if pts < SPEAK_MIN:
        return ""
    if any(e["points"] >= 4 for e in events):
        return "%d Punkte, Kontakt." % pts
    return "%d Punkte." % pts
