"""Sprachausgabe ueber die Windows-Sprachsynthese (SAPI), mit Nachverstaerkung.

SAPI ist bei Volume 100 gegen Motorensound zu leise. Deshalb wird die Ansage
nicht direkt ausgegeben, sondern in eine WAV-Datei gerendert, normalisiert und
weich begrenzt (Soft-Clipping) und dann abgespielt. Das hebt vor allem die
leisen Stellen an und macht die Ansage deutlich durchsetzungsfaehiger, ohne
hart zu uebersteuern.

Ein PowerShell-Prozess bleibt warm, damit die Ansage ohne Startverzoegerung
direkt nach der Zieldurchfahrt kommt.
"""
from __future__ import annotations

import os
import queue
import subprocess
import tempfile
import threading
import wave

import numpy as np

PS1 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "speaker.ps1")
DEFAULT_VOICE = "Katja"      # OneCore-Stimme, klingt natuerlicher als Hedda
DEFAULT_GAIN = 10.0
DEFAULT_RATE = 1.0           # WinRT-Tempo: 1.0 = normal, <1 langsamer


def _boost(path_in: str, path_out: str, gain: float) -> bool:
    """Normalisieren + weich begrenzen. Gibt False zurueck, wenn es nicht klappt."""
    try:
        with wave.open(path_in, "rb") as w:
            params = w.getparams()
            if w.getsampwidth() != 2:
                return False
            frames = w.readframes(w.getnframes())
        x = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
        if x.size == 0:
            return False

        peak = float(np.abs(x).max())
        if peak < 1e-6:
            return False
        x /= peak                                   # auf Vollaussteuerung ziehen
        if gain > 1.0:
            # tanh staucht Spitzen, statt sie abzuschneiden - laut, aber ohne
            # das Knacken von hartem Clipping.
            x = np.tanh(x * gain) / float(np.tanh(gain))
        x = np.clip(x * 0.98, -1.0, 1.0)

        with wave.open(path_out, "wb") as w:
            w.setparams(params)
            w.writeframes((x * 32767.0).astype("<i2").tobytes())
        return True
    except Exception:
        return False


class Speaker:
    def __init__(self, voice: str = DEFAULT_VOICE, rate: float = DEFAULT_RATE,
                 volume: int = 100, gain: float = DEFAULT_GAIN,
                 enabled: bool = True):
        self.proc = None
        self.gain = float(gain)
        self.q: queue.Queue = queue.Queue()
        self.tmp = tempfile.mkdtemp(prefix="ircoach_tts_")
        self.seq = 0
        self.thread = None
        if not enabled:
            return
        self._cmd = ["powershell", "-NoProfile", "-NonInteractive",
                     "-ExecutionPolicy", "Bypass", "-File", PS1,
                     "-Voice", voice, "-Rate", str(rate)]
        try:
            self.proc = subprocess.Popen(
                self._cmd,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                bufsize=1, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as e:
            print("[coach] Sprachausgabe nicht verfuegbar: %r" % e)
            self.proc = None
            return
        self._start_reader()
        # Eigener Thread: die 60-Hz-Schleife darf nie auf die Sprachausgabe warten.
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # ---------------- oeffentlich ----------------
    def say(self, text: str) -> None:
        if text and self.alive:
            self.q.put(" ".join(str(text).split()))

    def close(self) -> None:
        if not self.alive:
            return
        try:
            # Erst die Warteschlange leerlaufen lassen - sonst wird die letzte
            # Ansage abgeschnitten, bevor sie ueberhaupt gespielt wurde.
            self.q.put(None)
            if self.thread is not None:
                self.thread.join(timeout=60)
            self.proc.stdin.close()
            self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass

    # ---------------- intern ----------------
    def _start_reader(self) -> None:
        """Antworten des Sprechers in eine Queue lesen.

        Nur so laesst sich mit Zeitlimit darauf warten. Ein blockierendes
        readline() wuerde die Ansage bei einem haengenden Sprecher dauerhaft
        und ohne jede Meldung verstummen lassen.
        """
        self.answers: queue.Queue = queue.Queue()
        proc = self.proc

        def read():
            try:
                for line in proc.stdout:
                    line = line.strip()
                    # PowerShell gibt gelegentlich Leerzeilen aus. Wuerden die
                    # als Antwort zaehlen, haelt der naechste Aufruf sie faelsch-
                    # licherweise fuer seine eigene Rueckmeldung.
                    if line in ("OK", "ERR"):
                        self.answers.put(line)
            except Exception:
                pass
            self.answers.put(None)          # Sprecher ist weg

        threading.Thread(target=read, daemon=True).start()

    def _respawn(self) -> bool:
        """Sprecher neu starten, nachdem er nicht mehr geantwortet hat."""
        # Alte Leitungen ausdruecklich schliessen - sonst meckert der
        # Garbage Collector spaeter beim Aufraeumen der toten Pipes.
        for stream in ("stdin", "stdout"):
            try:
                getattr(self.proc, stream).close()
            except Exception:
                pass
        try:
            self.proc.kill()
        except Exception:
            pass
        try:
            self.proc = subprocess.Popen(
                self._cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                bufsize=1, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self._start_reader()
            print("[coach] Sprachausgabe neu gestartet.", flush=True)
            return True
        except Exception as e:
            print("[coach] Sprachausgabe endgueltig ausgefallen: %r" % e, flush=True)
            self.proc = None
            return False

    def _render(self, text: str, timeout: float = 8.0) -> str | None:
        """Laesst PowerShell in eine WAV-Datei sprechen und gibt den Pfad zurueck."""
        self.seq += 1
        raw = os.path.join(self.tmp, "say_%d.wav" % self.seq)
        # Reste aus einem vorigen Zeitueberlauf verwerfen - sonst holt dieser
        # Aufruf die verspaetete Antwort des vorigen ab und haelt sie fuer seine.
        while True:
            try:
                self.answers.get_nowait()
            except queue.Empty:
                break
        try:
            self.proc.stdin.write("%s\t%s\n" % (raw, text))
            self.proc.stdin.flush()
        except Exception:
            self._respawn()
            return None
        try:
            ans = self.answers.get(timeout=timeout)
        except queue.Empty:
            print("[coach] Sprecher antwortet seit %.0f s nicht - Neustart."
                  % timeout, flush=True)
            self._respawn()
            return None
        if ans is None:
            print("[coach] Sprecher hat sich beendet - Neustart.", flush=True)
            self._respawn()
            return None
        return raw if ans == "OK" and os.path.exists(raw) else None

    def _play(self, path: str) -> None:
        import winsound
        winsound.PlaySound(path, winsound.SND_FILENAME)

    def _worker(self) -> None:
        while True:
            text = self.q.get()
            if text is None or not self.alive:
                return
            try:
                raw = self._render(text)
                if raw is None:
                    continue
                loud = raw.replace(".wav", "_loud.wav")
                self._play(loud if _boost(raw, loud, self.gain) else raw)
                for p in (raw, loud):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            except Exception as e:
                print("[coach] Fehler in der Sprachausgabe: %r" % e, flush=True)
                if not self._respawn():
                    return
