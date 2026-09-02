"""Liest .ibt-Dateien (iRacing Disk-Telemetrie) und zerlegt sie in Runden."""
from __future__ import annotations

import numpy as np
import yaml

import irsdk

from .channels import CHANNELS
from .lap import Lap


def _session_yaml(ibt: "irsdk.IBT") -> dict:
    h = ibt._header
    raw = ibt._shared_mem[h.session_info_offset:h.session_info_offset + h.session_info_len]
    txt = raw.decode("utf-8", "ignore").split("\x00")[0]
    return yaml.safe_load(txt) or {}


def _track_length_m(info: dict) -> float:
    s = str(info.get("WeekendInfo", {}).get("TrackLength", "")).strip()
    try:
        val, unit = s.split()
        val = float(val)
        return val * 1000.0 if unit.lower().startswith("km") else val * 1609.344
    except Exception:
        return 0.0


def read_laps(path: str, min_speed: float = 5.0) -> tuple[list[Lap], dict]:
    """Gibt (Runden, Session-Info) zurueck. Runden werden am S/F-Wrap getrennt."""
    ibt = irsdk.IBT()
    ibt.open(path)
    try:
        info = _session_yaml(ibt)
        track_len = _track_length_m(info)
        wi = info.get("WeekendInfo", {})
        di = info.get("DriverInfo", {})
        drv = {}
        try:
            drv = di["Drivers"][di["DriverCarIdx"]]
        except Exception:
            pass
        meta = dict(track=wi.get("TrackName", "?"),
                    track_display=wi.get("TrackDisplayName", wi.get("TrackName", "?")),
                    car=drv.get("CarScreenName", "?"),
                    track_len=track_len)

        cols = {}
        for ch in CHANNELS:
            vals = ibt.get_all(ch)
            if vals is not None:
                cols[ch] = np.asarray(vals, dtype=float)
        if "LapDistPct" not in cols:
            return [], meta
        if track_len <= 0:
            track_len = float(np.nanmax(cols.get("LapDist", [0])))
            meta["track_len"] = track_len

        pct = cols["LapDistPct"]
        # S/F-Ueberfahrten: deutlicher Rueckwaertssprung im Rundenanteil
        cuts = np.flatnonzero(np.diff(pct) < -0.5) + 1
        bounds = list(zip(np.r_[0, cuts], np.r_[cuts, len(pct)]))

        laps = []
        lap_ch = cols.get("Lap")
        last_time_ch = cols.get("LapLastLapTime")
        for k, (a, b) in enumerate(bounds):
            if b - a < 60:
                continue
            if "Speed" in cols and np.nanmax(cols["Speed"][a:b]) < min_speed:
                continue
            seg = {ch: cols[ch][a:b] for ch in cols}
            lap_no = int(lap_ch[a]) if lap_ch is not None else k
            # Gemessene Dauer ist auf ~0.02 s genau. LapLastLapTime wird erst
            # rund 3 s nach der Zieldurchfahrt aktualisiert - vorher steht dort
            # noch die Zeit der Vorrunde. Deshalb spaet lesen und gegenpruefen.
            lt = float(cols["SessionTime"][b - 1] - cols["SessionTime"][a])
            if last_time_ch is not None:
                cand = float(last_time_ch[min(b + 200, len(last_time_ch) - 1)])
                if abs(cand - lt) < 0.5:
                    lt = cand
            lap = Lap.from_samples(seg, lap_no=lap_no, car=meta["car"],
                                   track=meta["track"], session=path,
                                   track_len=track_len, lap_time=lt)
            if lap is not None:
                laps.append(lap)
        return laps, meta
    finally:
        ibt.close()
