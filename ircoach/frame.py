"""Rendert das Overlay serverseitig als Bild.

Fuer Overlay-Programme, die kein JavaScript ausfuehren. Die Seite besteht dann
nur aus einem <img>, das auf einen MJPEG-Strom zeigt - damit sind fluessige
Brems- und Gasmarker moeglich, ohne dass im Browser Code laufen muss.
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

BG = (10, 12, 15)
TX = (242, 245, 248)
MUT = (138, 148, 162)
LINE = (30, 37, 46)
BRAKE = (255, 59, 47)
GAS = (37, 217, 111)
WARN = (255, 176, 32)
ACC = (76, 201, 212)

_FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
_cache: dict = {}


def font(size: int, bold: bool = False):
    key = (size, bold)
    if key not in _cache:
        for name in (("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf")):
            p = os.path.join(_FONTS, name)
            if os.path.exists(p):
                _cache[key] = ImageFont.truetype(p, size)
                break
        else:
            _cache[key] = ImageFont.load_default()
    return _cache[key]


def _text(d, xy, s, f, fill, anchor="la"):
    d.text(xy, s, font=f, fill=fill, anchor=anchor)


def _width(d, s, f) -> int:
    return int(d.textlength(s, font=f))


def short(t: str) -> str:
    if not t:
        return ""
    return t.split(" - ")[-1].rstrip(".").replace("du ", "", 1)[:44]


def render(lap: dict, marker: dict | None, w: int = 800, h: int = 300) -> Image.Image:
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    pad = int(w * 0.04)
    foot_h = int(h * 0.22)
    top_h = h - foot_h

    # ---------------- oberer Bereich ----------------
    if marker:
        col = BRAKE if marker["type"] == "brake" else GAS
        near = marker["dist"] <= 6
        word = "JETZT" if near else ("BREMSEN" if marker["type"] == "brake" else "GAS")
        fw = font(int(top_h * (0.62 if near else 0.48)), True)
        _text(d, (pad, top_h * 0.42), word, fw, col, "lm")
        wpx = _width(d, word, fw)
        _text(d, (pad + wpx + int(w * 0.03), top_h * 0.46),
              "KURVE %s" % marker["corner"], font(int(top_h * 0.17)), MUT, "lm")
        if not near:
            fd = font(int(top_h * 0.48), True)
            txt = "%d" % round(marker["dist"])
            _text(d, (w - pad, top_h * 0.42), "m", font(int(top_h * 0.17)), MUT, "rm")
            _text(d, (w - pad - int(w * 0.045), top_h * 0.42), txt, fd, col, "rm")
        # Fortschrittsbalken
        span = 300.0 if marker["type"] == "brake" else 100.0
        by0, bh = int(top_h * 0.78), max(6, int(h * 0.05))
        d.rounded_rectangle([pad, by0, w - pad, by0 + bh], radius=bh // 2, fill=(22, 28, 36))
        frac = 1.0 - min(1.0, marker["dist"] / span)
        if frac > 0.01:
            d.rounded_rectangle([pad, by0, pad + int((w - 2 * pad) * frac), by0 + bh],
                                radius=bh // 2, fill=col)
    else:
        bar_w = max(3, int(w * 0.008))
        d.rectangle([pad, int(top_h * 0.18), pad + bar_w, int(top_h * 0.82)], fill=WARN)
        x = pad + bar_w + int(w * 0.028)
        if lap.get("empty"):
            _text(d, (x, top_h * 0.5), "warte auf Runde", font(int(top_h * 0.22), True), MUT, "lm")
        elif not lap.get("valid", True):
            _text(d, (x, top_h * 0.34), "RUNDE UNGÜLTIG", font(int(top_h * 0.17)), WARN, "lm")
            _text(d, (x, top_h * 0.62), str(lap.get("reason", "")).split(",")[0],
                  font(int(top_h * 0.24), True), TX, "lm")
        else:
            head = "KURVE %s" % lap["corner"] if lap.get("corner") else "SAUBER"
            flat0 = h < 0.16 * w
            _text(d, (x, top_h * (0.30 if flat0 else 0.28)), head,
                  font(int(top_h * (0.24 if flat0 else 0.17))), WARN, "lm")
            _text(d, (x, top_h * (0.68 if flat0 else 0.56)),
                  short(lap.get("fix")) or "so weiter",
                  font(int(top_h * (0.40 if flat0 else 0.30)), True), TX, "lm")
            flat = h < 0.16 * w        # sehr flaches Banner: kein Platz fuer Zeile 3
            if not flat and lap.get("corner2") and lap.get("fix2"):
                _text(d, (x, top_h * 0.86),
                      "Kurve %s   %s" % (lap["corner2"], short(lap["fix2"])),
                      font(int(top_h * 0.16)), MUT, "lm")

    # ---------------- Fussleiste ----------------
    y = top_h
    d.line([pad, y, w - pad, y], fill=LINE, width=max(1, int(h * 0.006)))
    fl, fv = font(int(foot_h * 0.30)), font(int(foot_h * 0.46), True)
    cy = y + foot_h * 0.55
    x = pad
    _text(d, (x, cy), "BALANCE", fl, MUT, "lm"); x += _width(d, "BALANCE", fl) + int(w * 0.018)
    bias = lap.get("bias") or "--"
    _text(d, (x, cy), str(bias), fv, TX, "lm"); x += _width(d, str(bias), fv) + int(w * 0.045)
    _text(d, (x, cy), "ROTATION", fl, MUT, "lm"); x += _width(d, "ROTATION", fl) + int(w * 0.018)
    rot = lap.get("rotation")
    if rot is None:
        _text(d, (x, cy), "--", fv, MUT, "lm")
    else:
        col = BRAKE if rot > 22 else (ACC if rot < 12 else GAS)
        _text(d, (x, cy), "%+d" % round(rot), fv, col, "lm")

    adv = lap.get("bias_advice")
    if adv:
        txt = "BALANCE VOR" if "vorn" in adv else "BALANCE ZURUECK"
        fa = font(int(foot_h * 0.34), True)
        tw = _width(d, txt, fa)
        bx0 = w - pad - tw - int(w * 0.022)
        d.rounded_rectangle([bx0, y + foot_h * 0.20, w - pad, y + foot_h * 0.88],
                            radius=int(foot_h * 0.14), fill=WARN)
        _text(d, ((bx0 + w - pad) // 2, cy), txt, fa, BG, "mm")
    return img
