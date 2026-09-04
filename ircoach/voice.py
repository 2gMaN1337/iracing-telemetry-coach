"""Baut die kurze Sprachansage fuer die Zieldurchfahrt.

Auf der Start/Ziel-Geraden bleiben nur wenige Sekunden, bevor die erste
Bremszone kommt. Die Ansage nennt deshalb genau eine Kurve und genau eine
Anweisung - keine Zeiten. Alle Zahlen stehen im Report auf dem Bildschirm.
"""
from __future__ import annotations

import json
import os

# Gesprochene Anweisung pro Befund - immer eine Handlung, nie eine Diagnose.
SHORT_FIX = {
    "blockierer": "weicher bremsen",
    "abs": "weicher bremsen",
    "spin": "sanfter aufs Gas",
    "bremspunkt_frueh": "später bremsen",
    "bremspunkt_spaet": "früher bremsen",
    "trail_kurz": "länger von der Bremse rollen",
    "segeln": "eher wieder aufs Gas",
    "gas_spaet": "früher aufs Gas",
    "ausgang": "früher aufs Gas",
    "vollgas_spaet": "eher voll durchziehen",
    "eintritt_langsam": "später verzögern",
    "vmin_niedrig": "mehr Speed mitnehmen",
    "vmin_hoch": "früher verzögern",
    "gang": "anderer Gang",
    "overlap": "Gas und Bremse trennen",
    "lenkkorrekturen": "ruhiger lenken",
    # Der Schluessel nennt die Seite, auf der du bist - die Anweisung die
    # Gegenrichtung.
    "linie_einfahrt_links": "weiter rechts einlenken",
    "linie_einfahrt_rechts": "weiter links einlenken",
    "linie_scheitel_links": "den Scheitelpunkt weiter rechts nehmen",
    "linie_scheitel_rechts": "den Scheitelpunkt weiter links nehmen",
    "linie_ausgang_links": "am Ausgang weiter rechts",
    "linie_ausgang_rechts": "am Ausgang weiter links",
}

# Reihenfolge fuer die Ansage: die handelbare Ursache gewinnt gegen das
# Symptom. Zu langsam am Scheitelpunkt ist meist die Folge von zu hartem
# Bremsen - dann ist "weicher bremsen" der Satz, mit dem der Fahrer etwas
# anfangen kann, nicht "mehr Speed mitnehmen".
VOICE_PRIORITY = [
    "blockierer", "abs", "spin",
    "bremspunkt_frueh", "bremspunkt_spaet", "trail_kurz",
    "segeln", "gas_spaet", "ausgang", "vollgas_spaet",
    "eintritt_langsam", "vmin_niedrig", "vmin_hoch",
    # Linie kommt nach den Pedal-Ursachen: solange Bremsdruck oder Gaszeitpunkt
    # nicht stimmen, bringt eine Linienkorrektur wenig.
    "linie_scheitel_links", "linie_scheitel_rechts",
    "linie_einfahrt_links", "linie_einfahrt_rechts",
    "linie_ausgang_links", "linie_ausgang_rechts",
    "gang", "overlap", "lenkkorrekturen",
]


def load_turn_names(refs_dir: str, track_slug: str) -> dict:
    """Optionale Kurvennamen aus refs/turnnames_<strecke>.json."""
    p = os.path.join(refs_dir, "turnnames_%s.json" % track_slug)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return {int(k): v for k, v in json.load(f).items()}
    except Exception:
        return {}


# Wirkrichtung einer Anweisung: +1 heisst "mehr Tempo hinein, frueher aufs
# Gas", -1 heisst "frueher oder laenger verzoegern". Kippt die Anweisung fuer
# dieselbe Kurve von einer Runde auf die naechste ins Gegenteil, folgt der
# Fahrer beiden - und wandert dabei ueber das Optimum hinaus, statt sich ihm
# zu naehern. Solche Umkehrungen werden verschwiegen.
DIRECTION = {
    "bremspunkt_frueh": +1, "vmin_niedrig": +1, "eintritt_langsam": +1,
    "segeln": +1, "gas_spaet": +1, "ausgang": +1, "vollgas_spaet": +1,
    "bremspunkt_spaet": -1, "vmin_hoch": -1, "trail_kurz": -1,
    "abs": -1, "blockierer": -1, "spin": -1,
}


def _cap(s: str) -> str:
    return s[0].upper() + s[1:] if s else s


def _best_fix(findings) -> str | None:
    """Waehlt aus den Befunden einer Kurve die handelbarste Anweisung."""
    keys = {k for k, _ in findings}
    for key in VOICE_PRIORITY:
        if key in keys:
            return key
    return None


def say_gap(change: dict | None, min_s: float = 0.15) -> str:
    """Kurzer Satz zum Abstand nach vorn. Leer, wenn sich kaum etwas tat."""
    if not change:
        return ""
    d = change["delta"]
    if abs(d) < min_s:
        return ""
    a = abs(d)
    menge = "%d Zehntel" % round(a * 10) if a < 0.95 else             ("eine Sekunde" if round(a, 1) == 1.0 else ("%.1f" % a).replace(".", ","))
    return "%s %s." % (menge, "aufgeholt" if d < 0 else "verloren")


def spoken_line(cmp: dict, tips: list, names: dict | None = None,
                last: tuple | None = None) -> tuple[str, tuple | None]:
    """Eine Kurve, eine Anweisung - ohne Rundenzeit und ohne Delta.

    `last` ist der Schluessel der vorigen Ansage. Wiederholt sich derselbe
    Hinweis, wird auf den naechstgroessten Verlust ausgewichen - eine Ansage,
    die jede Runde identisch ist, wird nicht mehr gehoert.
    """
    if not cmp.get("valid", True):
        return "Runde ungültig.", None

    gap = say_gap(cmp.get("gap_change"))

    names = names or {}

    # Kandidaten: pro Kurve die wichtigste sprechbare Massnahme
    cands = []
    for t in tips:
        key = _best_fix(t["findings"])
        if key:
            cands.append((t["corner"], key))
    if not cands:
        # Es gab Verluste, aber keinen Befund, den man mitten in der Runde
        # umsetzen kann. Dann nur die Kurve nennen - nicht "sauber" melden.
        if tips:
            n = tips[0]["corner"]
            return "%s: kein klarer Fehler." % _cap(names.get(n, "Kurve %d" % n)), None
        return "Sauber, so weiter.", None

    if last is not None:
        # Gegenanweisung zur Vorrunde in derselben Kurve: lieber die
        # naechstgroessere Kurve nennen als den Fahrer hin und her schicken.
        d_last = DIRECTION.get(last[1], 0)
        cands = [c for c in cands
                 if not (c[0] == last[0] and d_last
                         and DIRECTION.get(c[1], 0) == -d_last)] or cands

    pick, repeat = cands[0], False
    if last is not None and cands[0] == last:
        if len(cands) > 1:
            pick = cands[1]
        else:
            repeat = True

    corner, key = pick
    name = _cap(names.get(corner, "Kurve %d" % corner))
    text = "%s%s%s: %s." % (gap + " " if gap else "",
                            "Immer noch " if repeat else "", name, SHORT_FIX[key])
    if cmp.get("warning"):
        text += " Track Limits."
    return text, pick
