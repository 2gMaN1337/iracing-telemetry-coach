"""Runden-Datenstruktur: Rohsamples -> Distanzraster."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict

import numpy as np

from .channels import GRID_CHANNELS, SURF_ONTRACK

GRID_N = 1500  # Stuetzstellen pro Runde


@dataclass
class Lap:
    lap_no: int
    car: str
    track: str
    session: str
    track_len: float                 # m
    dist: np.ndarray = field(repr=False)          # Distanzraster (m)
    data: dict = field(repr=False)                # Kanal -> Array auf dem Raster
    lap_time: float = 0.0            # offizielle Rundenzeit (s), 0 = unbekannt
    valid: bool = True
    invalid_reason: str = ""
    warning: str = ""          # gueltig, aber erwaehnenswert (z. B. Track Limits)
    tl_suspect: bool = False   # Track Limits: nicht als Referenz verwenden
    n_samples: int = 0

    # ---------- Konstruktion ----------
    @staticmethod
    def from_samples(samples: dict, *, lap_no: int, car: str, track: str,
                     session: str, track_len: float, lap_time: float = 0.0) -> "Lap | None":
        """samples: Kanalname -> Liste von Werten (chronologisch, eine Runde)."""
        n = len(samples.get("SessionTime", []))
        if n < 60:
            return None

        raw = {k: np.asarray(v, dtype=float) for k, v in samples.items()
               if k != "Gear" or True}
        d = raw.get("LapDist")
        if d is None:
            return None

        # LapDist monoton machen (Rauschen / Wrap am Ende abfangen)
        d = np.maximum.accumulate(d)
        t = raw["SessionTime"] - raw["SessionTime"][0]

        if track_len <= 0:
            track_len = float(d[-1])

        covered = float(d[-1] - d[0])
        grid = np.linspace(0.0, track_len, GRID_N)

        data = {}
        src = dict(raw)
        src["t"] = t
        for ch in GRID_CHANNELS:
            if ch in src and len(src[ch]) == n:
                data[ch] = np.interp(grid, d, src[ch])

        lap = Lap(lap_no=lap_no, car=car, track=track, session=session,
                  track_len=float(track_len), dist=grid, data=data,
                  lap_time=float(lap_time), n_samples=n)

        # ---------- Gueltigkeit ----------
        reasons = []
        warnings = []
        if covered < 0.90 * track_len:
            reasons.append("unvollstaendig")
        surf = raw.get("PlayerTrackSurface")
        if surf is not None:
            # Schwelle an beobachteten Daten kalibriert: In Monza hat iRacing
            # Runden mit 1.1 bis 1.4 % ausserhalb nicht gewertet, Runden mit
            # 0.0 bis 0.9 % dagegen schon. Die genaue Regel des Sims ist damit
            # nicht bekannt - 1.0 % liegt zwischen den beobachteten Faellen.
            off = float(np.mean(surf != SURF_ONTRACK) * 100)
            if off > 8.0:
                reasons.append(f"{off:.0f}% neben der Strecke")
            elif off > 1.05:
                # Beobachtet: 1.0 % wurde von iRacing gewertet, 1.1 % nicht.
                # Die Runde bleibt fuer die Analyse gueltig - sie ist ja
                # gefahren worden - taugt aber nicht als Referenz, weil ihre
                # Linie ausserhalb der Strecke verlaeuft.
                lap.tl_suspect = True
                warnings.append(f"Track Limits {off:.1f}% - von iRacing vermutlich nicht gewertet")
            elif off > 0.4:
                warnings.append(f"{off:.1f}% ueber Track Limits - knapp")
        pit = raw.get("OnPitRoad")
        if pit is not None and np.any(pit > 0.5):
            reasons.append("Boxengasse")
        if lap_time and (lap_time < 10 or lap_time > 900):
            reasons.append("Zeit unplausibel")

        # Ein Reset-to-Pits oder Abschleppen setzt LapDistPct zurueck und sieht
        # damit aus wie eine Zieldurchfahrt. Zwei Merkmale verraten es:
        # das Auto stand irgendwo, und es fehlen Messwerte.
        spd = raw.get("Speed")
        if spd is not None and len(spd) and float(np.max(spd)) > 20:
            if float(np.min(spd)) < 3.0:          # < ~11 km/h
                reasons.append("Fahrzeug stand waehrend der Runde")
        if lap_time > 0 and n < 0.90 * lap_time * 60:
            fehlt = lap_time - n / 60.0
            reasons.append(f"{fehlt:.1f}s Messluecke")
        lap.valid = not reasons
        lap.invalid_reason = ", ".join(reasons)
        lap.warning = ", ".join(warnings)
        return lap

    # ---------- Ableitungen ----------
    @property
    def t(self) -> np.ndarray:
        return self.data["t"]

    def duration(self) -> float:
        return float(self.t[-1])

    def time_at(self, distance: float) -> float:
        return float(np.interp(distance, self.dist, self.t))

    # ---------- Persistenz ----------
    def save(self, path) -> None:
        meta = dict(lap_no=self.lap_no, car=self.car, track=self.track,
                    session=self.session, track_len=self.track_len,
                    lap_time=self.lap_time, valid=self.valid,
                    invalid_reason=self.invalid_reason, warning=self.warning,
                    tl_suspect=self.tl_suspect, n_samples=self.n_samples)
        np.savez_compressed(path, __meta__=json.dumps(meta), dist=self.dist,
                            **{f"ch_{k}": v for k, v in self.data.items()})

    @staticmethod
    def load(path) -> "Lap":
        z = np.load(path, allow_pickle=False)
        meta = json.loads(str(z["__meta__"]))
        data = {k[3:]: z[k] for k in z.files if k.startswith("ch_")}
        return Lap(dist=z["dist"], data=data, **meta)
