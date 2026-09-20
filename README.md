# Facebook Groups Finder – Germany

Verwaltet Facebook-Gruppen für Marketing-Kooperationen in Deutschland.
Zielmarkt: syrische und arabische Communities.

Der Bestand wird **gepflegt**, nicht mehr gesucht: Gruppe, Stadt, Kategorie und
Zielgruppe stehen in der Datenbank und werden von Hand oder über die Übersicht
gesetzt. Darauf setzt die Marketing-Erweiterung auf — Kampagnen, Tracking-Codes,
Textvorlagen, Arbeitsseite und Kommentarautomatik.

> **Entfernt am 20.09.2026 — die Entdeckungsschicht.** Suche, Suchanbieter,
> Anfragepläne, Klassifikation aus Begriffslisten, Seed-Import, Gruppenseiten-
> Abruf, Bericht und Export sind aus dem Projekt genommen, ebenso die fünf
> Konfigurationsdateien, an denen sie hingen (`audiences.yaml`, `cities.yaml`,
> `categories.yaml`, `queries.yaml`, `providers.yaml`). Was davon noch gebraucht
> wurde, ist in `settings.yaml` bzw. in den Bestand gezogen — nichts wurde
> nachgebaut.

## Projektgrenzen

Diese Grenzen sind bewusst gesetzt und im Code verankert:

- Erfasst werden ausschließlich **öffentliche Angaben zur Gruppe selbst**
  (Name, URL, Beschreibungsausschnitt, ungefähre Mitgliederzahl, Sichtbarkeit).
- **Keine** Mitglieder- oder Admindaten, keine Profil-URLs, keine
  Beitragsinhalte, keine Kontaktdaten. Das Datenmodell hat dafür keine Felder;
  `GroupPost` hat kein Textfeld, und `upsert_group_posts` könnte einen Text gar
  nicht speichern.
- Automatisches Posten und Kommentieren wird über Playwright unterstützt und
  läuft **sichtbar** (`headless=False`). Kein stiller Login, keine Umgehung von
  Sperren.

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,web]"
.\.venv\Scripts\playwright install chromium
```

## Nutzung

```powershell
$env:PYTHONIOENCODING="utf-8"          # nötig für arabische Ausgabe im Terminal
$py = ".\.venv\Scripts\python.exe"

& $py -m fbgroups.cli config-check                 # Konfiguration prüfen
& $py -m fbgroups.cli auth login                   # Browser-Sitzung anlegen
& $py -m fbgroups.cli serve --port 3000            # Übersicht und Tracking-Links
& $py -m fbgroups.cli campaign --help              # Kampagnen
& $py -m fbgroups.cli marketing --help             # Arbeitsstand, Auswertung
```

Nach `pip install -e .` steht zusätzlich der Befehl `fbgroups` direkt zur
Verfügung. Die Kampagnenbefehle sind in `CLAUDE.md` vollständig beschrieben.

## Validierung, Status und Score

Es wird nichts geraten und nichts ergänzt — fehlende Angaben bleiben `unknown`.

**Validation Status** (Prüfung der URL):

| Wert | Bedeutung |
|---|---|
| `valid` | Kennung wirkt wie eine echte Gruppen-ID |
| `test_data` | offensichtlicher Platzhalter (`123456789…`, `testgruppe`, `example…`) |
| `invalid` | keine verwertbare Kennung |
| `unreachable` | ein Mensch hat die Gruppe im Browser als tot befunden |

Die Prüfung ist rein strukturell — es wird **nicht** bei Facebook nachgefragt,
ob die Gruppe existiert. `test_data` ist ein begründeter Verdacht, keine
Existenzaussage. Solche Zeilen werden markiert, nicht gelöscht.

**Data Quality**: `none` (nur URL), `minimal` (1–2 Felder), `partial` (3–4),
`complete` (ab 5). Gezählt werden nur **erhobene** Felder.

**Score**: eine Zahl von 0–100 — oder **leer**, wenn die Datenlage nicht reicht.
Einen Ersatzwert gibt es bewusst nicht; `score_reason` nennt für jede Zeile den
Grund, und `score_max` das bei dieser Datenlage Erreichbare. Fehlende
Bestandteile senken `score_max`, statt mit Null bewertet zu werden — und es
wird **nicht** hochgerechnet.

| Bestandteil | Punkte | Grundlage |
|---|---:|---|
| `members` | 25 | Mitgliederzahl, logarithmisch gestuft |
| `activity` | 25 | erhobene Zahl, sonst gemessene Resonanz |
| `category` | 20 | Haupt- und Nebenkategorien aus dem Bestand |
| `location` | 15 | Stadt, sonst Bundesland, sonst Land |
| `target_audience` | 15 | Zielgruppen-Tags aus dem Bestand |

Neu bewertet wird beim Kampagnenlauf (`rescoring.bewerte_neu`); einen eigenen
`rescore`-Befehl gibt es nicht mehr.

## Konfiguration

Alles Fachliche liegt in `config/` – Codeänderungen sind dafür nicht nötig:

| Datei | Inhalt | Pflicht |
|---|---|---|
| `settings.yaml` | Scoring-Gewichte, Pfade, Grenzen je Aktion, Zielpriorität | ja |
| `textvorlagen.yaml` | Beitrags- und Kommentarvorlagen, Anlasstexte | nein |
| `rewards.yaml` | Prämienregeln (liest `marketing/rewards.py` selbst) | nein |

Zielgruppen, Städte und Kategorien stehen **nicht mehr** in der Konfiguration,
sondern am Datensatz der Gruppe (`audience_tags`, `city`, `category`). Wer eine
lesbare Beschriftung will, schreibt sie dorthin — eine Tabelle daneben wäre
eine zweite Wahrheit über dieselbe Gruppe.

Schlüssel (`APP_BASE_URL`, `EVENTS_TOKEN`, `UEBERSICHT_TOKEN`) stehen
**ausschließlich** in `.env`, nie in einer Konfigurationsdatei. Vorlage:
`.env.example`.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest              # alle
.\.venv\Scripts\python.exe -m pytest tests\test_urls.py -v
.\.venv\Scripts\python.exe -m pytest -k arabisch  # gezielt
```

Alle Tests laufen offline, ohne Netzwerk und ohne Zugangsdaten.
