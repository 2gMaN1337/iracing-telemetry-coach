---
name: iracing-telemetry-coach
description: Live-Telemetrie-Coaching für iRacing - schneidet jede Runde aus dem Shared Memory mit, vergleicht sie mit der Bestrunde und gibt nach der Zieldurchfahrt eine gesprochene Fahranweisung. Dazu Web-Dashboard, VR-Overlay mit Brems- und Gasmarkern, Bremsbalance-Bewertung, Linienvergleich und Stint-Auswertungen. Nutze dies, wenn jemand iRacing-Telemetrie auswerten, beim Fahren gecoacht werden, Rundenzeiten oder Fahrfehler analysieren, Pedaleinstellungen vergleichen oder ein Renn-Overlay bauen will.
---

# iRacing Telemetrie-Coach

Wertet iRacing-Telemetrie aus und coacht während der Fahrt. Alles läuft lokal,
es werden keine Daten verschickt.

## Zuerst prüfen

```bash
python -m pip install pyirsdk numpy scipy pyyaml pillow
```

In `Documents\iRacing\app.ini` müssen stehen:

```
irsdkEnableMem=1        ; Live-Telemetrie (Pflicht)
irsdkEnableDisk=1       ; .ibt-Dateien
irsdkAutoLogDisk=1      ; automatisch aufzeichnen
irsdkLogSetup=1         ; Setup mitschreiben (für Setup-Vergleiche)
```

Ohne `irsdkEnableMem=1` gibt es kein Live-Coaching, ohne die Disk-Optionen
keinen Linienvergleich.

## Die drei Prozesse

Im Hintergrund starten, sie laufen bis iRacing beendet wird:

```bash
python live_coach.py --voice Katja --rate 0.95 --gain 10   # Mitschnitt + Ansage
python server.py --port 8099                                # Dashboard + Overlay
python watch_feed.py                                        # eine Zeile pro Runde
```

`live_coach.py` verbindet sich automatisch, sobald der Sim läuft, und
überlebt Sessionwechsel.

## Adressen

| URL | wofür |
|---|---|
| `http://127.0.0.1:8099` | Dashboard: Diagramme, Streckenkarte, Rundenliste |
| `http://127.0.0.1:8099/vr` | **VR-Overlay** (Bildstrom, braucht kein JavaScript) |
| `http://127.0.0.1:8099/overlay` | Overlay mit JavaScript, für normale Browser |
| `http://127.0.0.1:8099/panel` | Rückfall ohne JavaScript und ohne Bildstrom |

**Immer `127.0.0.1` verwenden, nicht `localhost`** - unter Windows kostet die
Namensauflösung sonst rund 200 ms pro Abfrage und der Live-Marker ruckelt.

Overlay-Programme wie RaceLab führen teils **kein JavaScript** aus. Zeigt die
Seite nur ihren Starttext, ist `/vr` die richtige Adresse: Dort rendert der
Server fertige Bilder und schiebt sie als MJPEG-Strom.

Größe anpassen: `/vr?w=1280&h=140&fps=12`

## Nach einem Stint

```bash
python import_session.py --latest    # .ibt nachladen: Linie, Radgeschwindigkeiten
python session_report.py --latest    # Stint-Auswertung
python brake_check.py --latest       # Pedalarbeit, Vergleich mit gespeicherter Basis
```

Pedaleinstellungen vergleichen: `brake_check.py --latest --save "vorher"`,
dann Einstellung ändern, 10 Runden fahren, `brake_check.py --latest` - es
zeigt die Veränderung gegen die gespeicherte Basis.

## Eine fremde Runde als Referenz nehmen

Die Referenz muss nicht die eigene beste Runde sein. Jede `.ibt`-Datei geht -
etwa eine heruntergeladene Runde aus Garage61 oder die eines Teamkollegen.
Dann zeigen Delta, Report und die Overlay-Marker, wo dessen Runde anders
aussieht als die eigene.

```bash
python set_reference.py --list "<datei.ibt>"    # erst anschauen, welche Runden drin sind
python set_reference.py "<datei.ibt>"           # schnellste gültige Runde setzen
python set_reference.py "<datei.ibt>" --lap 7   # oder eine bestimmte
python set_reference.py --restore               # zurück zur eigenen
```

Die bisherige Referenz wird beim ersten Setzen automatisch gesichert
(`.own.npz`), es geht also nichts verloren. Danach den Coach neu starten.

**Worauf zu achten ist:**

- **Setup muss passen.** Flügel, Bremsbalance und Tankstand verschieben
  Bremspunkte systematisch. Fixed gegen Open zu vergleichen führt in die Irre.
  Das Setup steht in der `.ibt` und lässt sich vorher prüfen.
- **Nicht zu schnell wählen.** Eine Runde 1 bis 1,5 s über dem eigenen Niveau
  ist übertragbar. Bei 3 s Unterschied zeigt der Vergleich vor allem, dass man
  langsamer ist - das weiß man schon.
- **Apex-Geschwindigkeiten und Gaspunkte übertragen sich besser als
  Bremspunkte.** Wo jemand bremst, hängt stark an seinem Setup und seinem Mut;
  wie schnell er durch die Kurve kommt, ist allgemeiner.
- **Track Limits prüfen.** Eine fremde Runde mit Verstößen taugt nicht als
  Referenz - ihre Linie verläuft teilweise neben der Strecke. Das Skript meldet
  Kurvenzahl und Positionsdaten nach dem Setzen.

Der Kurvensatz der Strecke bleibt dabei unangetastet, die Nummerierung ändert
sich also nicht.

## Was der Coach nach jeder Runde meldet

Gesprochen wird **eine Kurve und eine Anweisung**, keine Zeiten - auf der
Start/Ziel-Geraden bleiben nur wenige Sekunden. Beispiel:

> „Vier Zehntel aufgeholt. Kurve 3: weicher bremsen."

Im Report stehen zusätzlich alle Zahlen: Delta je Abschnitt, Bremspunkte,
ABS-Anteil, Bremsbalance, Verkehr, Linienversatz.

## Auswertung im Browser und Overlay einrichten

Der Server (`server.py`) liefert vier verschiedene Ansichten aus derselben
Datenquelle. Welche passt, hängt davon ab, wo sie angezeigt werden soll.

### Am Monitor: das Dashboard

`http://127.0.0.1:8099` - zum Anschauen zwischen den Stints, nicht während
der Fahrt. Enthält:

- Rundenliste links, mit Pfeiltasten durchblätterbar
- Delta-Verlauf über die Runde, rot wo verloren, grün wo gewonnen
- Geschwindigkeit, Gas und Bremse gegen die Referenzrunde
- Streckenkarte mit der gefahrenen Linie, nach Zeitgewinn eingefärbt
  (nur nach `import_session.py`, siehe Kanal-Fallstrick oben)
- Abschnittstabelle mit allen Befunden im Klartext
- Gemeinsames Fadenkreuz über alle Diagramme

Aktualisiert sich alle 3 s und springt auf neue Runden, solange "letzte Runde
folgen" aktiv ist.

### In VR: das Overlay einrichten

Overlay-Programme, die eine frei wählbare URL anzeigen können (RaceLab,
OpenKneeboard, SimHub, OBS als Browserquelle), bekommen:

```
http://127.0.0.1:8099/vr
```

Schrittweise:

1. `python server.py` starten, bevor das Overlay-Programm die Seite lädt
2. Im Overlay-Programm eine Custom-URL-Quelle anlegen und die Adresse eintragen
3. Fenstergröße im Overlay auf ein flaches Banner ziehen
4. Passt die Darstellung nicht, per Parameter setzen:
   `?w=1280&h=140` - `fps=12` ist der Standard, `fps=8` bei Rucklern

Das Overlay zeigt zwischen den Kurven den Coaching-Hinweis der letzten Runde
und beim Anfahren einer Bremszone einen Countdown in Metern (rot = bremsen,
grün = Gas), darunter Bremsbalance und Rotationswert.

Vorhaltemaß gegen die Anzeigeverzögerung: `?lead=8` zieht den Countdown um
8 m vor. Mit 0 anfangen und vorsichtig erhöhen - zu viel Vorhalt bringt
einen falschen Bremspunkt bei.

### Wenn das Overlay leer bleibt

| Beobachtung | Ursache | Lösung |
|---|---|---|
| nur der Starttext, nichts aktualisiert sich | Programm führt kein JavaScript aus | `/vr` statt `/overlay` |
| Marker läuft sichtbar hinterher | `localhost` statt `127.0.0.1` | Adresse wechseln |
| Marker ruckelt trotzdem | Bilder stauen sich | `?fps=8` |
| gar nichts lädt | Server läuft nicht | URL im normalen Browser testen |

Zur Diagnose `http://127.0.0.1:8099/overlay?debug=1` im normalen Browser
öffnen - unten links steht dann, was schiefgeht.

### Ohne laufenden Sim testen

```
http://127.0.0.1:8099/api/sim?on=1     # Bestrunde in Echtzeit abspielen
http://127.0.0.1:8099/api/sim?on=0     # wieder aus
```

Die Referenzrunde läuft dann in Dauerschleife durch das Overlay, damit sich
Größe und Position in Ruhe einrichten lassen. **Vor dem Fahren ausschalten**,
sonst zeigt das Overlay die Aufzeichnung statt der echten Position.

## Reifegrad - was erprobt ist und was nicht

Dieses Werkzeug ist an **einem** Fahrzeug (Porsche 911 Cup 992.2) und **zwei**
Strecken entstanden. Vieles ist gemessen und geprüft, einiges ist ausdrücklich
Versuchsstadium.

**Belastbar** - mehrfach gegen echte Daten geprüft:

- Rundenschnitt, Delta je Abschnitt, Bremspunkte, ABS-Anteil
- Kurvenerkennung über die Querbeschleunigung (stabil ab acht Runden)
- Linienvergleich (Selbsttest Referenz gegen sich selbst ergibt exakt 0.00 m)
- Sprachausgabe und Dashboard

**Versuchsstadium** - funktioniert, aber wenig erprobt:

- **VR-Overlay.** Lief erst nach mehreren Anläufen; hängt stark davon ab, was
  das Overlay-Programm kann. Der Bildstrom (`/vr`) ist der robusteste Weg,
  aber nur mit einem Programm getestet.
- **Brems- und Gasmarker.** Die Positionen stimmen auf wenige Meter, die
  Anzeigeverzögerung liegt je nach Betrachter bei 50 bis 150 ms. Ob ein
  Bremsmarker überhaupt nützt, hängt vom Fahrer ab - wessen Bremspunkte
  ohnehin nur wenige Meter streuen, gewinnt dadurch nichts.
- **Gegnerdaten und Abstandsansage.** Zwei Rennen getestet. Die Zuordnung des
  Rivalen über `CarIdxPosition` kann bei Überrundungen danebenliegen.
- **Bremsbalance-Zielband (12-22 mrad/s).** Aus einem Fahrzeug/Strecken-Paar
  abgeleitet. Bei anderen Kombinationen erst prüfen, ob die Grenzen passen -
  auf einer zweiten Strecke lagen dieselben Einstellungen deutlich anders.
- **Track-Limits-Schwelle (1.05 %).** An genau einem Fall kalibriert: 1.0 %
  wurde von iRacing gewertet, 1.1 % nicht. Die tatsächliche Regel des Sims
  ist nicht bekannt.
- **Sprachqualität.** Nutzt die OneCore-Stimmen über WinRT, mit Rückfall auf
  System.Speech. Auf anderen Windows-Installationen können andere oder keine
  deutschen Stimmen vorhanden sein.

**Nicht gelöst:**

- Kein Linienvergleich und keine Blockierer-Erkennung live - die Kanäle fehlen
  im Shared Memory, es braucht `import_session.py` nach dem Stint
- Kurvennummern sind Zählreihenfolge ab Start/Ziel, **nicht** die offizielle
  Streckennummerierung
- Nur Windows (Sprachausgabe und Schriftarten)

Wer damit arbeitet, sollte Zahlen aus den Versuchsteilen als Hinweis behandeln
und vor Schlussfolgerungen gegenprüfen.

## Fallstricke, die dieses Werkzeug bereits behandelt

Diese Punkte sind im Code gelöst - wer daran etwas ändert, sollte sie kennen.

**Live-Speicher und `.ibt` enthalten unterschiedliche Kanäle.** Live fehlen
`Lat`/`Lon`/`Alt` und die Radgeschwindigkeiten, dort gibt es dafür die
Gegner-Arrays (`CarIdx…`), die nie auf die Platte kommen. Linienvergleich und
Blockierer-Erkennung brauchen deshalb `import_session.py`.

**Positionsrekonstruktion aus `VelocityX/Y` + `YawNorth` funktioniert nicht.**
Die Weltgeschwindigkeit stimmt auf 0.38 m/s, die integrierte Position driftet
über eine Runde aber um median 4.4 m - bei 0.5 bis 3 m Nutzsignal unbrauchbar.

**`LapLastLapTime` hinkt rund 3 Sekunden nach.** Direkt nach der
Zieldurchfahrt steht dort noch die Zeit der Vorrunde. Der Code misst die Dauer
selbst aus `SessionTime`.

**Reset-to-Pits sieht aus wie eine Zieldurchfahrt.** `LapDistPct` springt
zurück. Erkannt an Stillstand während der Runde und an fehlenden Messwerten.

**Kurvenerkennung braucht mehrere Runden.** Aus einer einzelnen Runde schwankt
die Kurvenzahl je nach Referenz erheblich. Der Kurvensatz wird deshalb aus dem
Mittel von mindestens acht Runden bestimmt und pro Strecke gespeichert.
Erkannt wird über die Querbeschleunigung, nicht über Geschwindigkeitsminima -
sonst fehlen alle voll gefahrenen Knicke.

**Track Limits kosten die Wertung, nicht die Zeit.** Runden über etwa 1 %
außerhalb wertet iRacing nicht. Solche Runden bleiben hier für die Analyse
gültig, dürfen aber nicht Referenz werden - ihre Brems- und Gaspunkte lägen
teils neben der Strecke.

**Fixed- und Open-Setup getrennt halten.** Flügel und Bremsbalance verschieben
Bremspunkte systematisch. Referenzen tragen den Setup-Typ im Namen, der
Kurvensatz bleibt gemeinsam.

## Analysemethoden

**Zeit-Abschnitte** laufen von Topspeed zu Topspeed, nicht von Kurve zu Kurve.
So decken sie die Runde lückenlos ab, ihre Deltas summieren sich exakt auf das
Rundendelta, und ein schlechter Kurvenausgang wird der Kurve zugeschrieben
statt der Geraden danach.

**Bremsbalance** wird über den Rotationsüberschuss bewertet: Stationär gilt
`a_quer = v · Drehrate`; was darüber hinausgeht, ist Übersteuern. Gegen
Radgeschwindigkeiten geprüft (Korrelation +0.73 mit Heckblockierern).
Zielband 12 bis 22 mrad/s - dieser Wert stammt aus einem Fahrzeug/Strecken-Paar
und sollte bei anderen Kombinationen überprüft werden.

**ABS-Anteil** wird auf die Bremsphase bezogen, nicht auf das Kurvenfenster -
sonst hängt der Wert von der Fensterwahl ab.

**Vorsicht bei Kausalaussagen.** Ein hoher ABS-Anteil bedeutet nicht
automatisch Zeitverlust; in Messungen korrelierte mehr ABS teils mit *weniger*
Verlust. Vor solchen Behauptungen die Korrelation an den Daten prüfen.

## Bei Auswertungen

Rundenzeiten allein führen in die Irre. Immer mitprüfen:

- **Verkehr** (`opponents.jsonl`) - eine langsame Runde ist oft Hinterherfahren
- **Zwischenfälle** - Dreher und Ausflüge verzerren jeden Mittelwert
- **Setup** - im `.ibt` steht das komplette Setup, Vergleiche sonst wertlos
- **Track Limits** - eine „Bestzeit" über der Schwelle zählt nicht

Zwischenfälle erkennt man an einer Mindestgeschwindigkeit weit unter dem
sonstigen Kurvenminimum. Für faire Vergleiche herausfiltern und das
ausdrücklich dazusagen.
