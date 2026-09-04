"""Live-Telemetrie-Coach fuer iRacing.

Liest waehrend der Fahrt die Telemetrie aus dem Shared Memory des Sims,
schneidet jede Runde an der Start/Ziel-Linie mit, vergleicht sie mit der
schnellsten Runde (Session oder gespeicherte Bestzeit fuer Auto+Strecke)
und schreibt direkt nach der Zieldurchfahrt eine Analyse.

    python live_coach.py                # laeuft, bis iRacing beendet wird
    python live_coach.py --no-persist   # Referenz nicht dauerhaft speichern

Ausgabe:
    laps/<auto>_<strecke>/lap_XXX.npz   Rohdaten der Runde (Distanzraster)
    laps/<auto>_<strecke>/lap_XXX.json  Analyse als JSON
    session.jsonl                       eine Zeile pro Runde (Live-Feed)
    latest.txt                          Klartext-Report der letzten Runde
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import irsdk

from ircoach import balance as bal
from ircoach import corners as corner_set
from ircoach import incidents as inc_mod
from ircoach import opponents as opp
from ircoach.analyze import compare, detect_corners, make_tips
from ircoach.channels import CHANNELS
from ircoach.lap import Lap
from ircoach.report import console_report, fmt_time, to_json
from ircoach.speak import DEFAULT_GAIN, DEFAULT_RATE, DEFAULT_VOICE, Speaker
from ircoach.voice import load_turn_names, spoken_line

BASE = os.path.dirname(os.path.abspath(__file__))
LAPS_DIR = os.path.join(BASE, "laps")
REFS_DIR = os.path.join(BASE, "refs")
FEED = os.path.join(BASE, "session.jsonl")
LATEST = os.path.join(BASE, "latest.txt")

TICK_HZ = 60


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_") or "unknown"


class Collector:
    def __init__(self, persist: bool = True, voice: bool = True,
                 voice_name: str = DEFAULT_VOICE, rate: float = DEFAULT_RATE, volume: int = 100,
                 gain: float = DEFAULT_GAIN, feed: bool = True):
        self.ir = irsdk.IRSDK()
        self.inc = inc_mod.Tracker()
        self.persist = persist
        self.feed = feed          # session.jsonl schreiben (im Replay unerwuenscht)
        self.speaker = Speaker(voice=voice_name, rate=rate, volume=volume,
                               gain=gain, enabled=voice)
        if voice and self.speaker.alive:
            print("[coach] Sprachausgabe aktiv (%s, Tempo %.2f, Verstaerkung %.1f)"
                  % (voice_name, rate, gain))
        self.reset_session()

    # ---------------- Session ----------------
    def reset_session(self):
        self.buf: dict[str, list] = {}
        self.laps: list[Lap] = []
        self.ref: Lap | None = None
        self.corners = None
        self.meta = {}
        self.last_pct = None
        self.lap_no = 0
        self.out_dir = None
        self.available = None
        self.turn_names = {}
        self.last_spoken = None
        self.apex = None
        self.last_st = None
        self.bias_offset = None
        self.opp_chans = None      # verfuegbare CarIdx-Kanaele
        self.opp_buf = []          # Abstaende dieser Runde
        self.opp_last = None       # letzter Abtastpunkt (fuer die Bestzeiten)
        self.rival_prev = None     # Abstand nach vorn beim letzten Zieldurchgang
        self.drivers = {}
        self.rot_streak = 0        # Runden in Folge ausserhalb des Zielbands
        self.last_advice = None
        self.inc.reset()           # Vorfallspunkte der Session

    def read_meta(self):
        wi = self.ir["WeekendInfo"] or {}
        di = self.ir["DriverInfo"] or {}
        drv = {}
        try:
            drv = di["Drivers"][di["DriverCarIdx"]]
        except Exception:
            pass
        length = 0.0
        m = re.match(r"([\d.]+)\s*(km|mi)", str(wi.get("TrackLength", "")))
        if m:
            length = float(m.group(1)) * (1000.0 if m.group(2) == "km" else 1609.344)
        # Fixed- und Open-Setup getrennt halten: Bremspunkte, Topspeed und
        # Kurvengeschwindigkeiten unterscheiden sich systematisch, ein
        # gemeinsamer Vergleich waere irrefuehrend.
        fixed = wi.get("WeekendOptions", {}).get("IsFixedSetup")
        setup = "fixed" if str(fixed) == "1" else "open"
        self.meta = dict(
            setup=setup,
            car=drv.get("CarScreenName", "?"),
            track=wi.get("TrackName", "?"),
            track_display=wi.get("TrackDisplayName", wi.get("TrackName", "?")),
            track_len=length,
        )
        self.out_dir = os.path.join(LAPS_DIR, "%s_%s_%s" % (
            slug(self.meta["car"]), slug(self.meta["track"]), self.meta["setup"]))
        os.makedirs(self.out_dir, exist_ok=True)
        print("[coach] Setup: %s" % self.meta["setup"])
        self.turn_names = load_turn_names(REFS_DIR, slug(self.meta["track"]))
        self.bias_offset = bal.load_offset(REFS_DIR, slug(self.meta["car"]))
        self.apex = corner_set.load(REFS_DIR, slug(self.meta["track"]))
        if self.apex:
            print("[coach] Kurvensatz geladen: %d Kurven" % len(self.apex))
        self.load_reference()

    # ---------------- Referenz ----------------
    def ref_path(self) -> str:
        return os.path.join(REFS_DIR, "%s_%s_%s.npz" % (
            slug(self.meta["car"]), slug(self.meta["track"]), self.meta["setup"]))

    def load_reference(self):
        p = self.ref_path()
        if self.persist and os.path.exists(p):
            try:
                self.ref = Lap.load(p)
                self.corners = detect_corners(self.ref, apex_dists=self.apex)
                print("[coach] Referenz geladen: %s (%d Kurven)"
                      % (fmt_time(self.ref.lap_time), len(self.corners)))
            except Exception as e:
                print("[coach] Referenz nicht lesbar (%s) - starte ohne." % e)

    def maybe_update_reference(self, lap: Lap):
        if not lap.valid:
            return False
        if getattr(lap, "tl_suspect", False):
            # Eine Runde mit Track-Limits-Verstoss taugt nicht als Massstab:
            # ihre Brems- und Gaspunkte liegen teils neben der Strecke.
            print("[coach] Runde %d waere schneller, zaehlt aber wegen Track "
                  "Limits nicht als Referenz." % lap.lap_no, flush=True)
            return False
        if self.ref is None or lap.lap_time < self.ref.lap_time - 1e-3:
            self.ref = lap
            self.corners = detect_corners(lap, apex_dists=self.apex)
            if self.persist:
                os.makedirs(REFS_DIR, exist_ok=True)
                lap.save(self.ref_path())
            return True
        return False

    # ---------------- Aufzeichnung ----------------
    def sample_opponents(self):
        """Gegnerabstaende mit ~4 Hz - reicht fuer Verkehr und Positionen."""
        if self.opp_chans is None:
            self.opp_chans = opp.available(self.ir)
            self.drivers = opp.drivers(self.ir)
            if self.opp_chans:
                print("[coach] Gegnerdaten: %d Kanaele, %d Fahrer"
                      % (len(self.opp_chans), len(self.drivers)))
        if not self.opp_chans:
            return
        snap = opp.sample(self.ir, self.opp_chans)
        if not snap:
            return
        self.opp_last = snap
        me = self.ir["PlayerCarIdx"]
        spd = self.ir["Speed"]
        if me is None or spd is None:
            return
        g = opp.gaps(snap, int(me), self.meta.get("track_len") or 1.0, float(spd))
        if g:
            self.opp_buf.append(g)

    def sample(self):
        if self.available is None:
            self.available = [c for c in CHANNELS if self.ir[c] is not None]
            print("[coach] %d Kanaele verfuegbar" % len(self.available))
        for c in self.available:
            v = self.ir[c]
            self.buf.setdefault(c, []).append(float(v) if v is not None else 0.0)

    def finish_lap(self):
        """Wird direkt nach der Zieldurchfahrt aufgerufen."""
        samples, self.buf = self.buf, {}
        n = len(samples.get("SessionTime", []))
        if n < 60:
            return
        dur = samples["SessionTime"][-1] - samples["SessionTime"][0]
        if dur <= 0:
            # Kann nur durch einen Zeitsprung entstehen - keine echte Runde.
            print("[coach] Runde verworfen (Zeitsprung, Dauer %.1f s)" % dur)
            return
        lap = Lap.from_samples(samples, lap_no=self.lap_no, car=self.meta["car"],
                               track=self.meta["track"], session="live",
                               track_len=self.meta["track_len"], lap_time=dur)
        if lap is None:
            return
        self.laps.append(lap)
        self.report(lap)
        self.lap_no += 1
        # Sobald genug gueltige Runden vorliegen, den Kurvensatz der Strecke
        # einmalig aus dem Mittel aller Runden festschreiben.
        if self.apex is None:
            built = corner_set.build(self.laps)
            if built:
                self.apex = built
                corner_set.save(REFS_DIR, slug(self.meta["track"]), built, len(self.laps))
                self.corners = detect_corners(self.ref, apex_dists=built)
                print("[coach] Kurvensatz festgelegt: %d Kurven" % len(built), flush=True)

    def report(self, lap: Lap):
        is_new_ref = self.maybe_update_reference(lap)
        base = os.path.join(self.out_dir, "lap_%03d" % lap.lap_no)
        lap.save(base + ".npz")

        if self.ref is None or self.corners is None:
            print("[coach] Runde %d: %s (noch keine Referenz)"
                  % (lap.lap_no, fmt_time(lap.lap_time)))
            self.speaker.say("Referenzrunde steht. Ab jetzt vergleiche ich.")
            return

        cmp = compare(lap, self.ref, self.corners)
        tips = make_tips(cmp)
        cmp["is_new_reference"] = is_new_ref

        # ---- Bremsbalance ----
        rot = bal.rotation_excess(lap)
        bias = float(np.median(lap.data["dcBrakeBias"])) if "dcBrakeBias" in lap.data else None
        if rot is None or bal.TARGET_LO <= rot <= bal.TARGET_HI:
            self.rot_streak = 0
        else:
            self.rot_streak += 1
        adv = bal.advice(rot, self.rot_streak)
        # Nach einer ausgesprochenen Empfehlung erst wieder melden, wenn sich
        # etwas geaendert hat - sonst wiederholt sie sich jede Runde.
        extreme = rot is not None and abs(rot) > 2 * bal.TARGET_HI
        if adv and adv[0] == self.last_advice and not extreme:
            adv = None
        elif adv:
            self.last_advice = adv[0]
            self.rot_streak = 0
        # ---- Abstand zum Vordermann ----
        change = None
        if self.opp_last is not None:
            me = self.ir["PlayerCarIdx"]
            spd = self.ir["Speed"]
            if me is not None:
                now = opp.rival_gap(self.opp_last, int(me),
                                    self.meta.get("track_len") or 1.0,
                                    float(spd or 30))
                change = opp.gap_change(now, self.rival_prev)
                self.rival_prev = now
        cmp["gap_change"] = change

        # ---- Verkehr ----
        traffic = opp.lap_traffic(self.opp_buf) if self.opp_buf else {}
        self.opp_buf = []
        if traffic and self.opp_last is not None:
            opp.write_lap(os.path.join(self.out_dir, "opponents.jsonl"), lap.lap_no,
                          traffic, opp.best_laps(self.opp_last, self.drivers))
        cmp["traffic"] = traffic

        cmp["rotation"] = rot
        cmp["bias"] = bias
        cmp["bias_shown"] = bal.as_shown(bias, self.bias_offset) if bias else None
        cmp["bias_advice"] = adv

        # Zuerst sprechen: auf der Start/Ziel-Geraden zaehlt jede Zehntelsekunde.
        # ---- Vorfaelle dieser Runde ----
        events = self.inc.for_lap(lap.lap_no)
        cmp["incidents"] = events
        cmp["incidents_total"] = self.inc.total

        spoken, self.last_spoken = spoken_line(cmp, tips, self.turn_names,
                                               self.last_spoken)
        # Vorfallspunkte zuerst: sie kosten Safety Rating und wiegen damit
        # schwerer als eine Zehntelsekunde in irgendeiner Kurve.
        say_inc = inc_mod.spoken(events)
        if say_inc:
            spoken = say_inc + " " + spoken
        if adv:
            spoken = adv[0] + " " + spoken
        self.speaker.say(spoken)

        txt = console_report(cmp, tips, self.meta)
        lines = inc_mod.lap_lines(events, self.inc.total)
        if lines:
            txt += "\n" + "\n".join(lines)
        txt += "\nAnsage: %s" % spoken
        if is_new_ref:
            txt += "\n*** NEUE BESTZEIT - ab jetzt Referenz. ***"
        print(txt, flush=True)

        with open(base + ".json", "w", encoding="utf-8") as f:
            f.write(to_json(cmp, tips, self.meta))
        with open(LATEST, "w", encoding="utf-8") as f:
            f.write(txt)
        line = dict(ts=time.time(), lap=lap.lap_no, time=cmp["lap_time"],
                    ref=cmp["ref_time"], delta=cmp["total_delta"],
                    valid=lap.valid, reason=lap.invalid_reason,
                    new_ref=is_new_ref, car=self.meta["car"],
                    track=self.meta["track_display"], spoken=spoken,
                    rotation=round(rot, 1) if rot is not None else None,
                    traffic=traffic.get("traffic_pct"),
                    ahead_s=traffic.get("ahead_min_s"),
                    gap_delta=change.get("delta") if change else None,
                    gap_to_rival=change.get("gap") if change else None,
                    bias=cmp["bias_shown"], bias_advice=adv[0] if adv else None,
                    incidents=sum(e["points"] for e in events),
                    incidents_total=self.inc.total,
                    tips=[dict(corner=t["corner"], delta=round(t["delta"], 3),
                               findings=[x[1] for x in t["findings"]]) for t in tips])
        if self.feed:
            with open(FEED, "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")

    # ---------------- Hauptschleife ----------------
    def run(self):
        connected = False
        last_try = 0.0
        period = 1.0 / TICK_HZ
        print("[coach] warte auf iRacing ...", flush=True)
        while True:
            t0 = time.perf_counter()
            if self.ir.is_initialized and self.ir.is_connected:
                if not connected:
                    connected = True
                    self.reset_session()
                    self.read_meta()
                    print("[coach] verbunden: %s @ %s (%.0f m)"
                          % (self.meta["car"], self.meta["track_display"],
                             self.meta["track_len"]), flush=True)
                self.ir.freeze_var_buffer_latest()

                # Reset-to-Pits, Tow oder Session-Wechsel setzen SessionTime
                # zurueck. Der angefangene Puffer gehoert dann zu einer anderen
                # Zeitbasis und wuerde eine unsinnige Runde ergeben.
                st = self.ir["SessionTime"]
                if st is not None:
                    if self.last_st is not None and st < self.last_st - 0.5:
                        print("[coach] Zeitsprung erkannt - Puffer verworfen.", flush=True)
                        self.buf = {}
                        self.last_pct = None
                    self.last_st = st

                pct = self.ir["LapDistPct"]
                if pct is not None and pct >= 0:
                    if self.last_pct is not None and pct - self.last_pct < -0.5:
                        try:
                            self.finish_lap()
                        except Exception as e:
                            print("[coach] Analysefehler: %r" % e, flush=True)
                            self.buf = {}
                    self.last_pct = pct
                    self.sample()
                    # Vorfallszaehler bei jedem Tick pruefen: nur so laesst
                    # sich der Sprung der Stelle zuordnen, an der er passiert.
                    for e in self.inc.update(self.ir["PlayerCarMyIncidentCount"],
                                             self.lap_no,
                                             pct * (self.meta.get("track_len") or 0.0),
                                             (self.ir["Speed"] or 0.0) * 3.6):
                        print("[coach] %dx bei %d m - %s (Session: %d)"
                              % (e["points"], e["dist"], inc_mod.art(e["points"]),
                                 self.inc.total), flush=True)
                    # Gegnerdaten seltener abtasten als die eigene Telemetrie
                    self.opp_tick = getattr(self, "opp_tick", 0) + 1
                    if self.opp_tick % 15 == 0:
                        self.sample_opponents()
            else:
                if connected:
                    print("[coach] Verbindung verloren.", flush=True)
                    connected = False
                    self.last_pct = None
                # Nur einmal pro Sekunde neu verbinden, nicht bei jedem Tick.
                if t0 - last_try > 1.0:
                    last_try = t0
                    self.ir.shutdown()
                    self.ir.startup()
            dt = period - (time.perf_counter() - t0)
            if dt > 0:
                time.sleep(dt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-persist", action="store_true",
                    help="Bestzeit nicht dauerhaft als Referenz speichern")
    ap.add_argument("--no-voice", action="store_true", help="Sprachansage aus")
    ap.add_argument("--voice", default=DEFAULT_VOICE,
                    help="Stimme: Katja, Stefan oder Hedda")
    ap.add_argument("--rate", type=float, default=DEFAULT_RATE,
                    help="Sprechtempo; 1.0 = normal, 0.9 = etwas langsamer")
    ap.add_argument("--volume", type=int, default=100, help="SAPI-Lautstaerke (0..100)")
    ap.add_argument("--gain", type=float, default=DEFAULT_GAIN,
                    help="Nachverstaerkung; 1 = aus, 4 = Standard, 8 = sehr laut")
    ap.add_argument("--say", help="nur diesen Text sprechen und beenden (Test)")
    a = ap.parse_args()
    os.makedirs(LAPS_DIR, exist_ok=True)

    if a.say:
        sp = Speaker(voice=a.voice, rate=a.rate, volume=a.volume, gain=a.gain)
        sp.say(a.say)
        sp.close()
        return

    c = Collector(persist=not a.no_persist, voice=not a.no_voice,
                  voice_name=a.voice, rate=a.rate, volume=a.volume, gain=a.gain)
    try:
        c.run()
    except KeyboardInterrupt:
        print("\n[coach] beendet.")
    finally:
        c.speaker.close()


if __name__ == "__main__":
    main()
