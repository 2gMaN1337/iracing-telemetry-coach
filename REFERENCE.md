# iRacing Live-Telemetrie-Coach

Liest während der Fahrt die iRacing-Telemetrie aus dem Shared Memory (60 Hz),
schneidet jede Runde an der Start/Ziel-Linie mit und vergleicht sie mit der
schnellsten Runde für diese Auto/Strecken-Kombination.

## Benutzung

```bash
python live_coach.py          # läuft mit, solange iRacing offen ist
python live_coach.py --no-persist   # Referenz nicht dauerhaft speichern
```

Offline auf eine bereits aufgezeichnete Session:

```bash
python analyze_ibt.py --latest              # neueste .ibt-Datei
python analyze_ibt.py "<pfad.ibt>" --lap 7  # eine bestimmte Runde
python replay_ibt.py "<pfad.ibt>"           # alte Session als Referenz einlesen
```

## Ausgabe

| Datei | Inhalt |
|---|---|
| `latest.txt` | Klartext-Report der letzten Runde |
| `session.jsonl` | eine Zeile pro Runde (Live-Feed) |
| `laps/<auto>_<strecke>/lap_NNN.npz` | Rundendaten auf 1500-Punkt-Distanzraster |
| `laps/<auto>_<strecke>/lap_NNN.json` | Analyse als JSON |
| `refs/<auto>_<strecke>.npz` | Referenzrunde (persistente Bestzeit) |

## Wie die Analyse funktioniert

**Kurvenerkennung** — Geschwindigkeits-Minima der Referenzrunde (`scipy.find_peaks`).
Pro Kurve gibt es zwei Fenster:

- *Messfenster* (`i_start`…`i_end`): Bremsbeginn bis Vollgas — hier werden die
  Metriken gemessen.
- *Zeitabschnitt* (`i_seg_start`…`i_seg_end`): von Topspeed zu Topspeed. Diese
  Abschnitte decken die Runde lückenlos ab, ihre Deltas summieren sich exakt auf
  das Rundendelta. Ein schlechter Kurvenausgang wird so der Kurve zugeschrieben
  und nicht der nachfolgenden Geraden.

**Gemessen pro Kurve:** Bremspunkt, Spitzendruck, Länge der Bremsfreigabe
(Trail-Braking), Scheitelpunktgeschwindigkeit, Gasannahme- und Vollgaspunkt,
Segelweg ohne Gas/Bremse, Gas-Brems-Überschneidung, ABS-Anteil, Radblockierer
(Vorderrad- zu Fahrzeuggeschwindigkeit), Antriebsschlupf hinten, Gang am
Scheitelpunkt, Lenkkorrekturen.

Blockierer und Schlupf werden **relativ zur Referenzrunde** bewertet — unter
hartem Bremsen ist etwas Schlupf normal.

## Gültigkeit einer Runde

Ungültig bei: unvollständig (< 90 % der Streckenlänge), Kontakt mit
`PlayerTrackSurface != OnTrack`, Boxengasse, unplausible Zeit. Ungültige Runden
werden gespeichert, aber nicht verglichen und nie als Referenz genommen.

## Fallstricke

- `LapLastLapTime` wird erst **rund 3 Sekunden** nach der Zieldurchfahrt
  aktualisiert — vorher steht dort noch die Zeit der Vorrunde. Der Code misst
  die Dauer selbst aus `SessionTime` (±0.02 s) und prüft `LapLastLapTime` nur
  gegen.
- Runden werden am Rückwärtssprung von `LapDistPct` getrennt, nicht am
  `Lap`-Zähler — der ist in Testsessions unzuverlässig.
- Voraussetzung in `Documents\iRacing\app.ini`: `irsdkEnableMem=1`.

## Kurvennummern

`T1…Tn` ist die Reihenfolge der erkannten Kurven ab Start/Ziel, **nicht**
zwingend die offizielle Streckennummerierung. Die Distanz ab S/F steht in der
Tabelle daneben.

## Dashboard

```bash
python server.py            # http://localhost:8099
python server.py --port 9000
```

Läuft parallel zum Coach; beide teilen sich nur die Dateien in `laps/` und
`refs/`, es gibt keine direkte Kopplung. Die Seite pollt alle 3 s die
Rundenliste und springt auf die neueste Runde, solange "letzte Runde folgen"
aktiv ist. Pfeiltasten blättern durch die Runden.

Enthält: Delta-Verlauf über die Runde, Geschwindigkeit gegen Referenz,
Gas/Bremse, Streckenkarte mit der gefahrenen Linie (nach Zeitgewinn/-verlust
eingefärbt) und die Abschnittstabelle mit allen Befunden.

## Live vs. Disk-Telemetrie — wichtige Einschränkung

Live-Speicher und Disk-Datei enthalten **unterschiedliche** Kanalsätze, nicht
mehr oder weniger: **335 live, 287 in der Datei.** Der Live-Speicher hat mehr,
weil dort die Gegner-Arrays (`CarIdx…`) liegen, die nie auf die Platte kommen.
Nicht live verfügbar sind dagegen:

| Kanal | Folge |
|---|---|
| `Lat`, `Lon`, `Alt` | kein Linienvergleich, keine Streckenkarte |
| `LFspeed`…`RRspeed` | keine Blockierer- und Schlupferkennung |

Alles andere (Delta, Bremspunkte, ABS, Gas/Bremse, Segeln, Gänge,
Lenkkorrekturen) funktioniert live.

Rekonstruktion der Position aus `VelocityX/Y` + `YawNorth` wurde geprüft: die
Weltgeschwindigkeit stimmt auf 0.38 m/s genau, die **integrierte Position
driftet über eine Runde aber um median 4.4 m (max 13 m)** — bei einem
Nutzsignal von 0.5–3 m unbrauchbar. Deshalb kommt die Linie aus der
`.ibt`-Datei:

```bash
python import_session.py --latest     # nach dem Stint
```

Das ersetzt die Runden in `laps/` durch die vollständige Fassung aus der
Disk-Telemetrie; danach zeigt das Dashboard Linienvergleich und
Blockierer-Erkennung.

## Kurvenerkennung

Über die **Querbeschleunigung** (`LatAccel`), nicht über Geschwindigkeitsminima.
Grund: schnelle Knicke, die voll gefahren werden, haben kein Speed-Minimum und
wurden vom alten Verfahren übersehen — am Red Bull Ring drei von zehn (bei
1100 m, 1969 m und 3183 m, alle zwischen 176 und 235 km/h).

Vorzeichen: `LatAccel` positiv = Linkskurve. Gegen den Lenkwinkel geprüft,
Korrelation +0.88.

Das Kurvenfenster läuft von 30 % des Querbeschleunigungs-Maximums vor dem
Scheitel bis 30 % danach, nach vorn erweitert bis zum Bremsbeginn und nach
hinten bis Vollgas steht. Die Zeit-Abschnittsgrenzen liegen weiterhin im
Geschwindigkeitsmaximum zwischen zwei Scheitelpunkten, aber auf den mittleren
60 % der Lücke beschränkt — sonst fällt die Grenze bei einem voll gefahrenen
Knick auf dessen eigenen Scheitelpunkt.

## Falsche Zieldurchfahrten

Ein **Reset-to-Pits oder Abschleppen** setzt `LapDistPct` zurück und sieht damit
aus wie eine Zieldurchfahrt — der Puffer wird als fertige Runde ausgewertet.
Die Rundenzeit ist dann zu kurz und kann fälschlich zur Bestzeit werden.

Zwei Merkmale entlarven das, beide werden geprüft:

- **Das Auto stand**: `min(Speed) < 3 m/s` bei einer Runde mit über 20 m/s
  Spitze. Eine durchgefahrene Runde hat nie einen Stillstand.
- **Messlücke**: weniger als 90 % der bei 60 Hz erwarteten Samples
  (`n < 0.9 · Dauer · 60`). Beim Teleport fehlen typischerweise mehrere Sekunden.

Der bereits vorhandene Schutz gegen Zeitsprünge (`SessionTime` läuft rückwärts)
greift hier nicht, weil ein Tow die Session-Uhr nicht zurücksetzt.
