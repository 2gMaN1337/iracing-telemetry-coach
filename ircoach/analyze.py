"""Kurvenerkennung, Rundenvergleich und Tipp-Generierung."""
from __future__ import annotations

import numpy as np
from scipy.signal import find_peaks

from .lap import Lap
from .line import corner_line, lateral_offset, line_findings

MS2KMH = 3.6


# --------------------------------------------------------------------------
# Kurvenerkennung (auf der Referenzrunde)
# --------------------------------------------------------------------------
def _smooth(y: np.ndarray, win_m: float, dist: np.ndarray) -> np.ndarray:
    step = float(dist[1] - dist[0])
    n = max(3, int(round(win_m / step)) | 1)
    k = np.ones(n) / n
    return np.convolve(np.pad(y, n // 2, mode="edge"), k, mode="valid")[: len(y)]


def _apexes_by_lat(ref, step, min_g):
    """Scheitelpunkte ueber die Querbeschleunigung - findet auch schnelle
    Knicke ohne Bremspunkt, die kein Geschwindigkeitsminimum haben."""
    lat = _smooth(ref.data["LatAccel"], 35.0, ref.dist) / 9.81
    idx, _ = find_peaks(np.abs(lat), prominence=min_g, distance=int(90 / step))
    return list(idx), lat


def _apexes_by_speed(v, step):
    """Rueckfall, wenn LatAccel fehlt: Geschwindigkeitsminima."""
    span = float(v.max() - v.min())
    idx, _ = find_peaks(-v, prominence=max(2.0, 0.10 * span), distance=int(120 / step))
    return [i for i in idx if v[i] < 0.92 * v.max()]


def detect_corners(ref: Lap, min_g: float = 0.25,
                   apex_dists: list | None = None) -> list[dict]:
    """Findet alle Kurven der Referenzrunde.

    Bevorzugt die Querbeschleunigung: schnelle Knicke, die voll gefahren werden,
    haben kein Geschwindigkeitsminimum und wurden vom reinen Speed-Verfahren
    uebersehen. Positive LatAccel bedeutet Linkskurve (mit dem Lenkwinkel
    gegengeprueft, Korrelation +0.88).

    `apex_dists` gibt den Kurvensatz der Strecke vor (siehe ircoach.corners).
    Ohne ihn wird auf der Referenzrunde allein gesucht - dann schwankt die
    Kurvenzahl je nach Runde, weil eine einzelne Runde zu verrauscht ist.
    """
    d = ref.dist
    step = float(d[1] - d[0])
    v = _smooth(ref.data["Speed"], 40.0, d)
    thr = ref.data.get("Throttle", np.ones_like(v))
    brk = ref.data.get("Brake", np.zeros_like(v))

    lat = None
    if "LatAccel" in ref.data:
        lat = _smooth(ref.data["LatAccel"], 35.0, d) / 9.81
    if apex_dists:
        # Vorgegebener Kurvensatz der Strecke (aus vielen Runden gemittelt) -
        # damit haengt die Kurvenzahl nicht von der Referenzrunde ab.
        apexes = [int(np.argmin(np.abs(d - x))) for x in apex_dists]
    elif lat is not None:
        apexes, _ = _apexes_by_lat(ref, step, min_g)
    else:
        apexes = _apexes_by_speed(v, step)
    if not apexes:
        return []

    corners = []
    for n, apex in enumerate(apexes):
        peak = abs(lat[apex]) if lat is not None else None

        # Kurvenfenster ueber die Querbeschleunigung: von 30 % des Spitzenwerts
        # vor dem Scheitel bis 30 % danach. Fuer langsame Kurven wird nach vorn
        # bis zum Bremsbeginn erweitert, nach hinten bis Vollgas steht.
        if lat is not None:
            lim = 0.30 * peak
            i = apex
            while i > 0 and abs(lat[i]) > lim:
                i -= 1
            j = apex
            while j < len(d) - 1 and abs(lat[j]) > lim:
                j += 1
        else:
            i, j = apex, apex

        while i > 0 and brk[i] > 0.05:
            i -= 1
        start = max(0, i - int(40 / step))
        while j < len(d) - 1 and thr[j] < 0.97:
            j += 1
        end = min(len(d) - 1, j + int(20 / step))

        corners.append(dict(
            n=n + 1, i_start=int(start), i_apex=int(apex), i_end=int(end),
            side=("links" if lat[apex] > 0 else "rechts") if lat is not None else None,
            peak_g=float(peak) if peak is not None else None,
        ))

    # Ueberlappungen der Messfenster aufloesen
    for a, b in zip(corners, corners[1:]):
        if a["i_end"] >= b["i_start"]:
            mid = (a["i_apex"] + b["i_apex"]) // 2
            a["i_end"] = min(a["i_end"], mid)
            b["i_start"] = max(b["i_start"], mid)
    corners = [c for c in corners if c["i_end"] > c["i_start"] + 2]

    # Zeit-Abschnitte: Grenze im Geschwindigkeitsmaximum zwischen zwei
    # Scheitelpunkten, aber nur im mittleren Bereich der Luecke - sonst faellt
    # die Grenze bei einem voll gefahrenen Knick auf dessen eigenen Scheitel.
    apx = [c["i_apex"] for c in corners]
    bounds = [0]
    for p, q in zip(apx, apx[1:]):
        lo = p + max(1, int(0.2 * (q - p)))
        hi = q - max(1, int(0.2 * (q - p)))
        bounds.append(lo + int(np.argmax(v[lo:hi])) if hi > lo else (p + q) // 2)
    bounds.append(len(d) - 1)
    for c, lo, hi in zip(corners, bounds, bounds[1:]):
        c["i_seg_start"], c["i_seg_end"] = int(lo), int(hi)
    return corners


# --------------------------------------------------------------------------
# Metriken pro Kurve
# --------------------------------------------------------------------------
def _first_where(mask: np.ndarray, dist: np.ndarray, lo: int, hi: int):
    seg = mask[lo:hi]
    w = np.flatnonzero(seg)
    return float(dist[lo + w[0]]) if len(w) else None


def corner_metrics(lap: Lap, c: dict) -> dict:
    d, D = lap.dist, lap.data
    lo, apex, hi = c["i_start"], c["i_apex"], c["i_end"]
    step = float(d[1] - d[0])
    v = D["Speed"]
    zeros = np.zeros_like(v)
    thr = D.get("Throttle", zeros)
    brk = D.get("Brake", zeros)
    st = D.get("SteeringWheelAngle", zeros)

    # eigenes Speed-Minimum innerhalb des Fensters
    j = lo + int(np.argmin(_smooth(v, 25.0, d)[lo:hi]))

    m = dict(
        n=c["n"],
        d_start=float(d[lo]), d_apex=float(d[j]), d_end=float(d[hi]),
        t_seg=float(D["t"][hi] - D["t"][lo]),
        v_entry=float(v[lo]) * MS2KMH,
        v_min=float(v[j]) * MS2KMH,
        v_exit=float(v[hi]) * MS2KMH,
        d_brake=_first_where(brk > 0.12, d, lo, hi),
        brake_max=float(brk[lo:hi].max()),
        gear_apex=int(round(float(D.get("Gear", zeros)[j]))),
        steer_max=float(np.abs(st[lo:hi]).max()),
    )
    m["brake_peak_d"] = (float(d[lo + int(np.argmax(brk[lo:hi]))])
                         if m["brake_max"] > 0.05 else None)

    # Segeln (weder Gas noch Bremse) und Ueberschneidung
    coast = (brk[lo:hi] < 0.04) & (thr[lo:hi] < 0.04)
    m["coast_m"] = float(coast.sum() * step)
    m["overlap_m"] = float(((brk[lo:hi] > 0.10) & (thr[lo:hi] > 0.10)).sum() * step)

    # Gasaufbau nach dem Scheitelpunkt
    m["d_throttle"] = _first_where(thr > 0.25, d, j, hi)
    m["d_full"] = _first_where(thr > 0.97, d, j, hi)
    m["thr_at_apex"] = float(thr[j])

    # Bremsfreigabe: Weg vom Druckmaximum bis Bremse frei
    if m["brake_peak_d"] is not None:
        ip = lo + int(np.argmax(brk[lo:hi]))
        rel = _first_where(brk < 0.05, d, ip, hi)
        m["trail_m"] = float(rel - m["brake_peak_d"]) if rel is not None else None
    else:
        m["trail_m"] = None

    # ABS / Blockierer / Spin
    # ABS-Anteil bezogen auf die Bremsphase, nicht auf das ganze Kurvenfenster -
    # sonst haengt der Wert davon ab, wie lang das Fenster gewaehlt wurde.
    abs_ch = D.get("BrakeABSactive")
    m["abs_pct"] = None
    if abs_ch is not None:
        braking = brk[lo:hi] > 0.10
        if braking.sum() >= 3:
            m["abs_pct"] = float(np.mean(abs_ch[lo:hi][braking] > 0.5) * 100)

    lock = None
    if "LFspeed" in D and "RFspeed" in D:
        braking = (brk[lo:hi] > 0.2) & (v[lo:hi] > 12)
        if braking.any():
            front = np.minimum(D["LFspeed"][lo:hi], D["RFspeed"][lo:hi])
            lock = float(np.min(front[braking] / np.maximum(v[lo:hi][braking], 1e-3)))
    m["lock_ratio"] = lock

    spin = None
    if "LRspeed" in D and "RRspeed" in D:
        power = (thr[lo:hi] > 0.5) & (v[lo:hi] > 8)
        if power.any():
            rear = 0.5 * (D["LRspeed"][lo:hi] + D["RRspeed"][lo:hi])
            spin = float(np.max(rear[power] / np.maximum(v[lo:hi][power], 1e-3)))
    m["spin_ratio"] = spin

    # Lenkkorrekturen: Richtungswechsel der Lenkbewegung ueber Rauschschwelle
    ds = np.gradient(_smooth(st[lo:hi], 15.0, d))
    sig = ds[np.abs(ds) > 0.004]
    m["steer_corr"] = int(np.sum(np.diff(np.sign(sig)) != 0)) if len(sig) > 2 else 0
    return m


# --------------------------------------------------------------------------
# Rundenvergleich
# --------------------------------------------------------------------------
def compare(lap: Lap, ref: Lap, corners: list[dict]) -> dict:
    """Delta-Zeit-Verlauf + Kurvenvergleich gegen die Referenz."""
    delta = lap.data["t"] - ref.data["t"]
    offset = lateral_offset(lap, ref)
    res = dict(
        has_line=offset is not None,
        lap_no=lap.lap_no,
        lap_time=lap.lap_time or lap.duration(),
        ref_time=ref.lap_time or ref.duration(),
        ref_lap_no=ref.lap_no,
        valid=lap.valid, invalid_reason=lap.invalid_reason,
        warning=getattr(lap, "warning", ""),
        total_delta=float(delta[-1]),
        corners=[],
    )
    for c in corners:
        lo, hi = c["i_seg_start"], c["i_seg_end"]
        cur = corner_metrics(lap, c)
        cur["ref"] = corner_metrics(ref, c)
        cur["delta"] = float(delta[hi] - delta[lo])
        cur["d_seg_start"] = float(lap.dist[lo])
        cur["d_seg_end"] = float(lap.dist[hi])
        # Aufteilung Kurve / anschliessende Gerade
        mid = c["i_end"]
        cur["delta_corner"] = float(delta[mid] - delta[lo])
        cur["delta_straight"] = float(delta[hi] - delta[mid])
        if offset is not None:
            cur["line"] = corner_line(offset, c)
        res["corners"].append(cur)

    # Runden-Kennzahlen
    D = lap.data
    step = float(lap.dist[1] - lap.dist[0])
    thr, brk = D.get("Throttle"), D.get("Brake")
    if thr is not None and brk is not None:
        res["full_throttle_pct"] = float(np.mean(thr > 0.97) * 100)
        res["coast_pct"] = float(np.mean((thr < 0.04) & (brk < 0.04)) * 100)
        res["brake_pct"] = float(np.mean(brk > 0.05) * 100)
        res["overlap_m"] = float(np.sum((brk > 0.1) & (thr > 0.1)) * step)
    res["delta_trace"] = delta
    return res


# --------------------------------------------------------------------------
# Tipps
# --------------------------------------------------------------------------
def make_tips(cmp: dict, max_corners: int = 3) -> list[dict]:
    """Priorisierte Hinweise fuer die zeitkritischsten Kurven."""
    tips = []
    for c in sorted(cmp["corners"], key=lambda x: -x["delta"]):
        if c["delta"] < 0.03 or len(tips) >= max_corners:
            continue
        r = c["ref"]
        f = []

        # Bremspunkt
        if c["d_brake"] is not None and r["d_brake"] is not None:
            dd = c["d_brake"] - r["d_brake"]
            if dd < -8:
                f.append(("bremspunkt_frueh",
                          "Du bremst %.0f m frueher als in deiner Referenzrunde." % abs(dd)))
            elif dd > 8:
                f.append(("bremspunkt_spaet",
                          "Du bremst %.0f m spaeter - pruefe, ob du dich damit verbremst." % dd))

        # Scheitelpunkt-Geschwindigkeit
        dv = c["v_min"] - r["v_min"]
        if dv < -3:
            f.append(("vmin_niedrig",
                      "Scheitelpunkt %.1f km/h langsamer (%.0f statt %.0f km/h) - zu viel abgebremst."
                      % (abs(dv), c["v_min"], r["v_min"])))
        elif dv > 3:
            f.append(("vmin_hoch",
                      "Scheitelpunkt %.1f km/h schneller, kostet trotzdem Zeit - zu viel Speed rein, dafuer schlechter Ausgang."
                      % dv))

        # Gasaufbau
        if c["d_throttle"] is not None and r["d_throttle"] is not None:
            dd = c["d_throttle"] - r["d_throttle"]
            if dd > 8:
                f.append(("gas_spaet", "Gas kommt %.0f m spaeter." % dd))
        if c["d_full"] is not None and r["d_full"] is not None:
            dd = c["d_full"] - r["d_full"]
            if dd > 15:
                f.append(("vollgas_spaet", "Vollgas erst %.0f m spaeter." % dd))

        # Segeln
        if c["coast_m"] - r["coast_m"] > 12 and c["coast_m"] > 20:
            f.append(("segeln",
                      "%.0f m ohne Gas und ohne Bremse (Referenz %.0f m)." % (c["coast_m"], r["coast_m"])))

        # Bremsverhalten
        # Unter hartem Bremsen ist etwas Schlupf normal - deshalb nur melden,
        # wenn es deutlich schlechter als in der Referenzrunde ist.
        lk, rlk = c["lock_ratio"], r["lock_ratio"]
        if lk is not None and (lk < 0.85 or (rlk is not None and lk < 0.92 and lk < rlk - 0.05)):
            f.append(("blockierer",
                      "Vorderrad blockiert (Raddrehzahl nur %.0f%% der Fahrzeuggeschwindigkeit%s)."
                      % (lk * 100, "" if rlk is None else ", Referenz %.0f%%" % (rlk * 100))))
        if (c["abs_pct"] is not None and r["abs_pct"] is not None
                and c["abs_pct"] - r["abs_pct"] > 8):
            f.append(("abs", "ABS regelt auf %.0f%% des Abschnitts (Referenz %.0f%%) - Bremsdruck zu hoch."
                      % (c["abs_pct"], r["abs_pct"])))
        if (c["trail_m"] is not None and r["trail_m"] is not None
                and c["trail_m"] - r["trail_m"] < -15):
            f.append(("trail_kurz",
                      "Du gehst zu abrupt von der Bremse - laenger und weicher ausrollen lassen."))

        # Traktion / Stabilitaet
        sp, rsp = c["spin_ratio"], r["spin_ratio"]
        if sp is not None and (sp > 1.12 or (rsp is not None and sp > 1.05 and sp > rsp + 0.04)):
            f.append(("spin", "Hinterraeder drehen durch (%.0f%% Schlupf%s) - Gas zu frueh oder zu brutal."
                      % ((sp - 1) * 100,
                         "" if rsp is None else ", Referenz %.0f%%" % ((rsp - 1) * 100))))
        if c["steer_corr"] - r["steer_corr"] >= 3:
            f.append(("lenkkorrekturen",
                      "%d Lenkkorrekturen (Referenz %d) - Auto unruhig, Linie nicht sauber."
                      % (c["steer_corr"], r["steer_corr"])))
        if c["overlap_m"] > 15:
            f.append(("overlap", "%.0f m Gas und Bremse gleichzeitig." % c["overlap_m"]))

        # Gang
        if c["gear_apex"] != r["gear_apex"] and c["gear_apex"] > 0:
            f.append(("gang", "Gang %d am Scheitelpunkt statt %d." % (c["gear_apex"], r["gear_apex"])))

        # Gefahrene Linie gegenueber der Referenz
        if c.get("line"):
            f.extend(line_findings(c["line"]))

        # Verlust auf der anschliessenden Geraden
        if c.get("delta_straight", 0) > 0.05 and c["delta_straight"] > c.get("delta_corner", 0):
            dvx = c["v_exit"] - r["v_exit"]
            if dvx < -2:
                f.append(("ausgang",
                          "%.2f s davon gehen auf der folgenden Geraden verloren - du kommst mit %.0f km/h statt %.0f km/h raus."
                          % (c["delta_straight"], c["v_exit"], r["v_exit"])))
            else:
                f.append(("gerade",
                          "%.2f s gehen auf der folgenden Geraden verloren, obwohl die Ausgangsgeschwindigkeit passt - Schaltpunkte, Linie oder Windschatten pruefen."
                          % c["delta_straight"]))

        f.sort(key=lambda x: PRIORITY.get(x[0], 50))
        tips.append(dict(corner=c["n"], delta=c["delta"],
                         delta_straight=c.get("delta_straight", 0.0),
                         d_start=c["d_start"], d_apex=c["d_apex"],
                         findings=f[:4]))
    return tips


# Reihenfolge, in der Befunde genannt werden: erst Ursachen, dann Symptome.
PRIORITY = {
    "vmin_niedrig": 0, "vmin_hoch": 1, "bremspunkt_frueh": 2, "bremspunkt_spaet": 3,
    "ausgang": 4, "gas_spaet": 5, "vollgas_spaet": 6, "segeln": 7,
    "blockierer": 8, "spin": 9, "abs": 10, "trail_kurz": 11,
    "gang": 12, "lenkkorrekturen": 13, "overlap": 14, "gerade": 15,
    "linie_einfahrt_links": 16, "linie_einfahrt_rechts": 16,
    "linie_scheitel_links": 17, "linie_scheitel_rechts": 17,
    "linie_ausgang_links": 18, "linie_ausgang_rechts": 18,
}
