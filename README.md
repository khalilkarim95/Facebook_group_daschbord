# Facebook Groups Finder – Germany

Findet öffentlich auffindbare Facebook-Gruppen, die für Marketing-Kooperationen
in Deutschland relevant sind. Erster Zielmarkt: syrische und arabische
Communities.

**Phase 1 (umgesetzt):** Import manuell gesammelter Gruppen-URLs, Normalisierung,
Deduplizierung, Klassifikation nach Zielgruppe/Stadt/Kategorie, Priorisierung,
SQLite-Bestand und Excel-/CSV-Export.

## Projektgrenzen

Diese Grenzen sind bewusst gesetzt und im Code verankert:

- Erfasst werden ausschließlich **öffentliche Angaben zur Gruppe selbst**
  (Name, URL, Beschreibungsausschnitt, ungefähre Mitgliederzahl).
- **Keine** Mitglieder- oder Admindaten, keine Profil-URLs, keine Beitragsinhalte,
  keine Kontaktdaten. Das Datenmodell hat dafür keine Felder.
- **Kein** automatisches Posten, **kein** automatisches Messaging.
- **Kein** Zugriff auf facebook.com – weder Scraping noch Login-Automatisierung.
  Phase 1 liest ausschließlich lokale Dateien.
- Die Kontaktaufnahme für Kooperationen erfolgt später manuell.

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Nutzung

```powershell
$env:PYTHONIOENCODING="utf-8"          # nötig für arabische Ausgabe im Terminal
$py = ".\.venv\Scripts\python.exe"

& $py -m fbgroups.cli config-check                 # Konfiguration prüfen
& $py -m fbgroups.cli import-seeds                 # alle Dateien aus data/seeds/
& $py -m fbgroups.cli import-seeds meine.csv       # gezielt eine Datei
& $py -m fbgroups.cli import-seeds --dry-run       # nur anzeigen, nichts speichern
& $py -m fbgroups.cli report --top 20              # Bestand auswerten
& $py -m fbgroups.cli export --format both         # Excel + CSV
& $py -m fbgroups.cli queries --all                # geplante Suchanfragen ansehen
```

Nach `pip install -e .` steht zusätzlich der Befehl `fbgroups` direkt zur Verfügung.

## Seed-Dateien

Ablage in `data/seeds/`. Zwei Formate:

**CSV** – Pflichtspalte `url`, optional `name`, `member_count`, `description`,
`privacy`, `notes`. Deutsche Spaltennamen (`Link`, `Gruppenname`, `Mitglieder`, …)
werden ebenfalls erkannt, Trennzeichen `;` `,` und Tab automatisch.

```csv
url;name;member_count;privacy
https://www.facebook.com/groups/123456789;Syrer in Berlin;12.400;public
```

**TXT** – eine URL pro Zeile, `#` leitet einen Kommentar ein.

Mitgliederzahlen werden in vielen Schreibweisen verstanden: `12500`, `12.500`,
`12,5k`, `3 Mio`, `ca. 4.200 Mitglieder`.

## Validierung, Status und Score

Jede importierte Zeile durchläuft eine Prüfung. Es wird nichts geraten und
nichts ergänzt — fehlende Angaben bleiben `unknown`.

**Validation Status** (Prüfung der URL):

| Wert | Bedeutung |
|---|---|
| `valid` | Kennung wirkt wie eine echte Gruppen-ID |
| `test_data` | offensichtlicher Platzhalter (`123456789…`, `testgruppe`, `example…`) |
| `invalid` | keine verwertbare Kennung |

Die Prüfung ist rein strukturell — es wird **nicht** bei Facebook nachgefragt,
ob die Gruppe existiert. `test_data` ist ein begründeter Verdacht, keine
Existenzaussage. Solche Zeilen werden markiert, nicht gelöscht.

**Status** (Gesamteinordnung, erster zutreffender Fall gewinnt):
`invalid` → `insufficient_data` → `duplicate` → `validated`.

**Data Quality**: `none` (nur URL), `minimal` (1–2 Felder), `partial` (3–4),
`complete` (ab 5).

**Score**: eine Zahl von 0–100 — oder **leer**, wenn die Datenlage nicht reicht.
Einen Ersatzwert gibt es bewusst nicht; die Spalte *Score Reason* nennt für jede
Zeile den Grund. Voraussetzung für eine Bewertung sind ein Gruppenname **und**
mindestens ein weiteres Signal (Zielgruppe, Stadt oder Mitgliederzahl).

Fehlende Bestandteile werden aus der Rechnung ausgeklammert, nicht mit Null
bewertet: Ist die Mitgliederzahl unbekannt, wird über die verbleibenden
Gewichte normiert. Eine fehlende Angabe ist damit weder Bonus noch Strafe.

## Konfiguration

Alles Fachliche liegt in `config/` – Codeänderungen sind dafür nicht nötig:

| Datei | Inhalt |
|---|---|
| `settings.yaml` | Scoring-Gewichte, Dedupe-Schwellen, Pfade |
| `audiences.yaml` | Zielgruppen mit Begriffen in de / ar / translit |
| `cities.yaml` | Städte mit arabischem Namen, Bundesland, Einwohnerzahl |
| `categories.yaml` | Themenkategorien (Jobs, Wohnen, Community …) |
| `queries.yaml` | Suchanfragen für Phase 2 |

**Ausweitung** erfolgt über das Feld `phase`: `phase: 2` → `phase: 1` schaltet
weitere Zielgruppen oder Städte frei.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest              # alle
.\.venv\Scripts\python.exe -m pytest tests\test_urls.py -v
.\.venv\Scripts\python.exe -m pytest -k arabisch  # gezielt
```

Alle Tests laufen offline, ohne Netzwerk und ohne Zugangsdaten.

## Automatische Suche (Phase 2)

```powershell
fbgroups providers                    # Verfügbarkeit prüfen, verbraucht nichts
fbgroups search --dry-run             # Plan und Verbrauch, fragt nichts ab
fbgroups search --limit 5             # höchstens 5 NEUE Anfragen
fbgroups search --provider serper --limit 5
fbgroups search --alle                # bis zur Obergrenze aus providers.yaml
fbgroups search-log                   # Protokoll der bisherigen Anfragen
```

`fbgroups search` **ohne** `--limit` oder `--alle` startet nichts: ein
vollständiger Deutschland-Scan soll nie beiläufig ausgelöst werden.

**Portionsweise arbeiten** — `suche.ps1` fasst einen Durchgang zusammen
(suchen → exportieren → Restanzeige):

```powershell
.\suche.ps1                 # ein Durchgang: höchstens 10 neue Anfragen
.\suche.ps1 -Plan           # zeigt nur, was der nächste Durchgang täte
.\suche.ps1 -Anfragen 5     # kleinere Portion
.\suche.ps1 -Oeffnen        # Excel-Datei danach öffnen
```

Wiederholtes Aufrufen arbeitet die 43 geplanten Anfragen Stück für Stück ab;
bereits beantwortete kosten nie wieder etwas. Das Skript bricht ab, wenn kein
`SERPER_API_KEY` in `.env` steht, und gibt den Schlüssel nie aus.

**Schlüssel einrichten:** `.env.example` nach `.env` kopieren und den Schlüssel
eintragen. Danach in `config/providers.yaml` `active: serper` setzen. Ohne
Schlüssel bleibt `active: fixture` — dann läuft die komplette Pipeline offline
mit gespeicherten Antworten. Schlüssel stehen **ausschließlich** in `.env`,
nie in einer Konfigurationsdatei.

**Schutz vor unbeabsichtigtem Verbrauch** — mehrfach abgesichert:

- Jede erfolgreiche Antwort steht dauerhaft in `data/query_cache.sqlite`.
  Dieselbe Anfrage geht **nie zweimal** an den Dienst — auch nicht nach einem
  Neustart. Ein zweiter Lauf verbraucht nichts.
- `--limit N` begrenzt die **neuen** Anfragen. Bereits gespeicherte Anfragen
  zählen nicht mit, weil sie kein Guthaben kosten.
- `max_queries_per_run` in `config/providers.yaml` ist eine harte Obergrenze;
  der kleinere Wert von beiden gewinnt.
- `--dry-run` beziffert den Verbrauch **vorab** und ruft nichts ab — auch ohne
  hinterlegten Schlüssel.
- `fallback_chain` ist leer: kein automatischer Wechsel auf einen anderen
  Dienst und damit auf ein anderes Guthaben.
- Eine Taktbremse hält den Mindestabstand ein, statt in ein 429 zu laufen.
- Ist das Guthaben aufgebraucht, endet der Lauf **geordnet** mit Protokoll,
  statt abzustürzen.

**Was protokolliert wird** (`data/query_cache.sqlite`, Tabelle `query_log`):
Anfragetext, Zeitpunkt, Provider, Rohantwort, Erfolg oder Fehler und die
Trefferzahl — je Ausführung, auch für Fehlschläge und Speichertreffer.
Fehlschläge werden protokolliert, aber **nicht** zwischengespeichert: sonst
wäre ein einzelner Netzwerkfehler tagelang bindend.

Der Lauf misst zugleich die Qualität der Suchstrategie: Treffsicherheit
(Anteil echter Gruppen-URLs), Ausbeute je Anfrage und eine Aufschlüsselung
nach Sprache — damit ist belegbar, ob Deutsch, Arabisch oder Transliteration
mehr bringt.

## Stand der Search-Provider

Stand August 2026, vor der Anbindung geprüft:

| Dienst | Status | Kostenloses Kontingent |
|---|---|---|
| `serper` | verfügbar | 2.500 Credits einmalig, 6 Monate gültig |
| `brave` | verfügbar | 5 $/Monat, erneuert sich (~1.000 Anfragen) |
| `fixture` | offline | unbegrenzt, keine Anmeldung |
| ~~Google CSE~~ | **für Neukunden geschlossen**, Abschaltung 01.01.2027 | – |
| ~~Bing Web Search~~ | **abgeschaltet** am 11.08.2025 | – |

Bei 43 Anfragen je vollständigem Lauf reichen die Gratis-Kontingente für ~58
Läufe (Serper, einmalig) bzw. ~23 Läufe pro Monat (Brave, wiederkehrend). In
der Praxis liegt der Verbrauch deutlich darunter: ein wiederholter Lauf fragt
nur noch das ab, was neu hinzugekommen ist.

Kein Anbieter ist tragende Säule: `ProviderCapabilities` trägt die Felder
`state` (u. a. `closed_to_new`, `deprecated`) und `sunset_date`, damit ein
Dienst seinen eigenen Lebenszyklus meldet und `fbgroups providers` davor warnt.

Ein neuer Provider erfordert vier Schritte und keine Änderung am übrigen Code:
Klasse implementieren, mit `@register_provider("name")` anmelden, Block in der
Konfiguration ergänzen, gemeinsame Contract-Tests bestehen. Ein Test verhindert
zusätzlich, dass irgendein Modul außerhalb von `providers/` anbieterspezifischen
Code importiert.

**Nicht gebaut und bewusst ausgeschlossen:** Umwege über DuckDuckGo-Bibliotheken
oder SearxNG-Instanzen. Sie sind zwar „kostenlos", funktionieren aber durch
Scraping fremder Suchmaschinen und verstoßen gegen deren Nutzungsbedingungen.
