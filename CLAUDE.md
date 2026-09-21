# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projekt

Verwaltet Facebook-Gruppen für Marketing-Kooperationen in Deutschland
(Zielmarkt: syrische und arabische Communities). Der Bestand wird **gepflegt**,
nicht gesucht: Gruppe, Stadt, Kategorie und Zielgruppe stehen in der Datenbank.
Darauf setzt die Marketing-Erweiterung auf — Kampagnen, Tracking-Codes,
Textvorlagen, Arbeitsseite und Kommentarautomatik.

Die Projektsprache ist **Deutsch** – Kommentare, Docstrings, CLI-Ausgaben und
Testnamen. Bitte beibehalten.

## Die Entdeckungsschicht ist entfernt (20.09.2026)

Das war die größte Entfernung des Projekts, und sie ist der Grund, warum viele
ältere Notizen weiter unten von Dateien sprechen, die es nicht mehr gibt.

**Weg sind:** `providers/` (Serper, Brave, fixture), `query/` (Anfrageplan),
`search.py`, `classify/` (Zielgruppe, Stadt, Kategorie aus Begriffslisten),
`importers/` (Seed-Import), `extract/` (Gruppenseiten-Abruf, Aktivität aus
Treffern), `pipeline.py`, `dedupe.py`, `report.py`, `export/` und
`storage/query_cache.py`. Dazu die CLI-Befehle `import-seeds`, `pruefliste`,
`rescore`, `enrich`, `report`, `export`, `queries`, `providers`, `search` und
`search-log`, das Skript `suche.ps1` sowie `SERPER_API_KEY`/`BRAVE_API_KEY`.

**Aus `config/` weg:** `audiences.yaml`, `cities.yaml`, `categories.yaml`,
`queries.yaml`, `providers.yaml`. Übrig bleiben `settings.yaml` (Pflicht),
`textvorlagen.yaml` (optional) und `rewards.yaml` (liest `marketing/rewards.py`
selbst). `config.py` kennt nur noch die ersten beiden — `AppConfig` hat die
Felder `audiences`, `cities`, `categories` und `queries` nicht mehr, und die
Klassen `Audience`, `City` und `Category` gibt es nicht mehr.

**Was das im Betrieb ändert** — vier Stellen, und jede ist bewusst so:

- **`{zielgruppe}` ist immer `anrede_allgemein`** („الأصدقاء"). Die drei
  Beschriftungen je Zielgruppe (`label_de`, `label_kurz_de`, `label_ar`) sind
  mit `audiences.yaml` weg. Der blosse Tag („syrians") taugt nicht als Anrede
  in einem arabischen Satz, und ein Wort zu erfinden ist das Gegenteil dessen,
  was dieses Projekt unter Vorlagen versteht.
- **`{ziel}` ist immer `ziel_allgemein`**, und das steht seit dem 20.09.2026
  auf **„سوريا"** statt „الوطن". Der Platzhalter kommt in 24 Vorlagen vor;
  „الوطن" wäre an jeder dieser Stellen eine Abschwächung des Satzes, den der
  Nutzer geschrieben hat. Wer einen Bestand mit zwei Zielen bewirbt, braucht
  zwei Kampagnen mit eigenen Vorlagen — nicht eine Tabelle neben dem Text.
- **Tracking-Codes entstehen aus dem Bestand.** `FB-SYR-BER-001` setzt sich aus
  den ersten drei Buchstaben von `audience_tags[0]` und `city` zusammen; das
  optionale Feld `code:` aus den beiden gelöschten Dateien gibt es nicht mehr.
  Ohne Tag bzw. ohne Stadt bleibt es bei `GEN` und `DE`. **Vergebene Codes
  ändern sich dadurch nicht** — sie stehen in veröffentlichten Beiträgen.
- **Die Zielpriorität beurteilt den Namen nicht mehr.** `Regeln` hat die
  Felder `kategoriebegriffe`, `audiencebegriffe` und `staedte` nicht mehr;
  maßgeblich sind `kategorie`, `nebenkategorien`, `audiences` und `stadt` **im
  Bestand**. Eine Gruppe „Syrer in Berlin – Reisen nach Damaskus", die als
  `community` im Bestand steht, ist damit **B** und nicht mehr A. Wer sie als
  Reisegruppe behandelt haben will, trägt `reise` am Datensatz ein — eine
  Angabe, die bleibt, statt bei jedem Lauf neu geraten zu werden.

**Was nicht verschwunden ist, sondern umgezogen:**

- Die **deutschen Städtenamen** aus `cities.yaml` stehen jetzt in
  `settings.yaml` unter `marketing.zielprioritaet.herkunft`. Ohne sie wäre
  „مشاوير برلين - بيروت - دمشق" eine Gruppe mit „Land unbekannt" und stünde
  hinter einer libanesischen. Gemeint ist nur der Deutschlandbeleg — welche
  Stadt es ist, entscheidet dort nichts.
- `parse_member_count` steht in `automation/actions.py` (einziger Aufrufer).
- `rescoring.bewerte_neu` **klassifiziert nicht mehr**, es bewertet nur. Die
  drei Felder werden gepflegt; eine Neubewertung, die sie überschriebe, wäre
  ein stilles Zurücksetzen dieser Handarbeit.
- `scoring.py` ist **unverändert**: Seine Bestandteile lasen nie eine der
  gelöschten Dateien, sondern die gespeicherten Felder der Gruppe plus Zahlen
  aus `settings.yaml`. Entfallen ist allein die dritte Aktivitätsquelle
  (`ActivitySource.SEARCH_DATES`) — sie las `last_post_at`, und das schreibt
  seither nichts mehr. Der Enum-Wert bleibt: Bestandsdaten können ihn tragen.

**Der Weg in den Bestand heißt jetzt `import-mitglieder`** (20.09.2026, siehe
eigenen Abschnitt). `import-seeds` war der einzige davor und ist entfernt.

## Harte Projektgrenzen

Diese Grenzen sind mit dem Nutzer vereinbart und dürfen nicht ohne
ausdrückliche Aufforderung aufgeweicht werden:

- **NEU (geändert am 27.08.2026 / 29.08.2026):** Automatisches Posten und
  Kommentieren wird ausdrücklich unterstützt (`fbgroups campaign auto`), um
  das Tracking und die Metriken in einer geschlossenen Kette zu sichern.
  Unverändert verboten bleiben: **kein stiller/headless Login und keine
  Umgehung von Sperren** (kein Proxywechsel, keine wechselnden Kennungen,
  kein nachgeahmter Browser). Die Automatisierung muss sichtbar für den
  Nutzer ablaufen (Headless=False). Die Tests
  `test_es_wird_kein_browser_nachgeahmt` und `test_es_gibt_keinen_login_weg`
  halten die verbliebenen Grenzen fest.
- Keine Mitglieder-/Admindaten, keine Profil-URLs, keine Beitragsinhalte, keine
  Kontaktdaten. `models.Group` hat dafür bewusst keine Felder – ein Erweitern
  des Modells um solche Felder wäre eine Grenzverletzung. **Diese Grenze ist
  nicht mitgeöffnet worden**: Vom Abruf übernommen werden Mitgliederzahl,
  Sichtbarkeit, Name und Beitrags*zeitpunkte* — nie ein Beitragstext, nie ein
  Mensch.
  **NEU (Entscheidung für automatisiertes Kommentieren):** Um den besten Beitrag
  für einen Kommentar zu finden, dürfen Beitrags-Metriken (URL, Zeitstempel,
  Anzahl der Reaktionen/Kommentare) gelesen und gespeichert werden.
  **Streng verboten bleibt weiterhin das Lesen oder Speichern von Beitragsinhalten
  (Text) oder Autorennamen.**
- Kein Suchdienst fest verdrahten. Seit dem 20.09.2026 ist das gegenstandslos:
  Es gibt keine Suchschicht mehr. Wer eine zurückholt, holt die Regel mit.

## Befehle

```powershell
$py = ".\.venv\Scripts\python.exe"
$env:PYTHONIOENCODING="utf-8"        # sonst bricht arabische Terminalausgabe

& $py -m pip install -e ".[dev,web]"
& $py -m pytest                      # alle Tests, offline
& $py -m pytest tests\test_urls.py::test_parse_valid_urls -v   # einzelner Test
& $py -m pytest -k arabisch
& $py -m ruff check src tests
& $py -m mypy

& $py -m fbgroups.cli config-check   # Konfiguration validieren
& $py -m fbgroups.cli auth login     # Browser-Sitzung anlegen
& $py -m fbgroups.cli import-mitglieder data\from_lokal\liste.csv --dry-run
& $py -m fbgroups.cli import-mitglieder data\from_lokal\liste.csv
& $py -m fbgroups.cli serve --port 3000
```

Alles Weitere sind Unterbefehle von `campaign` und `marketing` — siehe unten.

`mypy` meldet vorbestehend „missing py.typed marker"; setzt man den Marker,
erscheinen ältere Fehler in Modulen, die davon unberührt sind. Das ist offen
und war es schon vor dem Ausbau der Entdeckungsschicht.

## Architektur

```
config/{settings,textvorlagen}.yaml
        │
        ▼
   config.AppConfig ──► scoring ──► storage/sqlite_store (groups)
                                            │
                                            ▼
                          marketing/  ──► store (Kampagnen, Codes, Ereignisse)
                                      ──► vorlagen, beitrag, kurzcode
                                      ──► lauf, automatik, arbeit, grenzen
                                      ──► web, dashboard, arbeitsseite
                                            │
                                      automation/ (Playwright, sichtbar)
```

Zentrale Entwurfsentscheidungen, die man mehreren Dateien nicht ansieht:

- **`config/settings.yaml` ist die fachliche Wahrheit.** Scoring-Gewichte,
  Grenzen je Aktion, Zielpriorität und Pfade stehen nirgends im Code.
  Zielgruppen, Städte und Kategorien stehen **nicht** dort, sondern am
  Datensatz der Gruppe.
- **Zwei Vergleichsstrategien in `textnorm.py`.** Lateinische Begriffe werden
  mit Wortgrenze verglichen, arabische als Teilstring – im Arabischen hängen
  Artikel und Präpositionen am Wort (`سوريين` steckt in `السوريين`). Wer das
  vereinheitlicht, zerstört die arabische Erkennung.
- **Kein Score ohne Grundlage.** `Group.score` ist `float | None`; `None`
  bedeutet nicht bewertbar, der Grund steht in `score_reason`. Es gibt keinen
  Ersatzwert für fehlende Daten — eine frühere Fassung vergab bei unbekannter
  Mitgliederzahl einen „neutralen" Faktor und erzeugte damit für jede Gruppe
  ohne Metadaten denselben Score (8,75).
- **Der Score sind 100 Punkte aus fünf Bestandteilen** (seit 27.08.2026):

  | Bestandteil       | Punkte | Grundlage                                |
  |-------------------|-------:|------------------------------------------|
  | `members`         |     25 | Mitgliederzahl, logarithmisch gestuft    |
  | `activity`        |     25 | Betrieb in der Gruppe (zwei Quellen)     |
  | `category`        |     20 | Haupt- und Nebenkategorien               |
  | `location`        |     15 | Stadt, sonst Bundesland, sonst Land      |
  | `target_audience` |     15 | erkannte Zielgruppen                     |

  **Reichweite und Betrieb tragen zusammen die Hälfte.** Das ist die fachliche
  Vorgabe und keine Feinheit: Eine thematisch perfekte Gruppe, in der nichts
  geschieht, ist kein guter Platz für einen Beitrag. `config-check` prüft
  beides — die Summe 100 *und* die 50 für `members` + `activity`; ohne die
  zweite Prüfung verschiebt sich das Verhältnis beim nächsten Feintuning
  unbemerkt.
- **`scoring.BESTANDTEILE` ist die einzige Quelle der Bestandteile.**
  `score_group` läuft über die Registry und nennt keinen Bestandteil beim
  Namen. Einen ergänzen heißt: eine Funktion schreiben, sie mit
  `@bestandteil(name, label, gewicht)` eintragen, ein Feld in `ScoreBreakdown`
  ergänzen und in `settings.yaml` ein Gewicht setzen. Ein Gewicht für einen
  Namen, den es nicht gibt, ist ein **Tippfehler und keine Erweiterung** —
  `config-check` meldet ihn, sonst liefe der gemeinte Bestandteil still mit
  seiner Vorgabe weiter.
- **Ein Bestandteil liefert `Befund` oder `None` — nie eine 0.** `None` heißt
  „keine Grundlage": Der Bestandteil senkt `score_max`, statt eine Null zu
  behaupten. „Keine Aktivität" ist ein Urteil über die Gruppe, „Aktivität
  unbekannt" eines über unsere Daten, und der Unterschied entscheidet die
  Rangfolge. Der `Befund` trägt neben dem Faktor auch **Konfidenz und
  Herkunft** — deshalb steht im Export „Mitglieder 22 (facebook)" und nicht
  nur „22".
- **Die Mitgliederzahl wächst logarithmisch** (`member_count_buckets`, neun
  Stufen). Eine Gruppe mit 100.000 Mitgliedern ist nicht zehnmal so wertvoll
  wie eine mit 10.000: Oberhalb einiger tausend entscheidet nicht mehr die
  Größe, sondern ob dort etwas geschieht.
- **Die Aktivität hat zwei Quellen, und ihre Reihenfolge ist begründet**
  (`activity_source`):
  1. `facebook` — die erhobene Zahl. Sie misst **die Gruppe** und ist damit
     die Antwort auf die gestellte Frage. Konfidenz 1,0.
  2. `resonanz` — Klick auf den Tracking-Link, Registrierung in der App. Sie
     misst, was von dort zu **uns** kommt; in mancher Hinsicht die bessere
     Frage (eine Gruppe mit 500 Mitgliedern und 40 Registrierungen ist mehr
     wert als eine mit 5.000 und zwei), aber eine andere. Konfidenz 0,8.

  Eine dritte gab es bis zum 20.09.2026: `search_dates`, die Frische des
  jüngsten indexierten Suchtreffers. Sie ist mit der Suchschicht entfallen —
  `last_post_at` schreibt nichts mehr.
- **Die Aktivität ist bewusst unabhängig von der Mitgliederzahl.** Sonst ließe
  sich der Fall nicht abbilden, für den es sie gibt: 100.000 Mitglieder und
  kaum neue Beiträge schlagen 20.000 mit täglichem Betrieb **nicht**. Test:
  `test_grosse_stille_gruppe_verliert_gegen_kleine_lebendige`.
- **`scoring.Resonanz` beschreibt die Zahlen, `marketing/resonanz.py` beschafft
  sie.** Die Richtung ist Absicht: `scoring.py` kennt weder `MarketingStore`
  noch die Ereignistabelle, so wie die Marketing-Erweiterung den Bestand nicht
  verändert. Der Aufrufer reicht die Zahlen herein. Ein Import in die andere
  Richtung machte den Kern von einem Aufsatz abhängig.
- **„Nicht gemessen" ist etwas anderes als „wirkungslos".** Ohne
  veröffentlichten Beitrag liefert `_resonanz_faktor` `None`, `activity`
  erscheint als „unbekannt" und `score_max` sinkt um 25. Null Klicks ohne
  Beitrag sind eine Aussage über **uns**, nicht über die Gruppe. Dasselbe
  gilt für die Schonfrist (`schonfrist_tage: 3`): Wer vor zwei Stunden
  gepostet hat, hat noch keine Klicks — eine Null wäre hier eine Behauptung
  über die Zukunft. Ein Beitrag **mit** null Klicks ist dagegen ein Ergebnis
  und wird als solches bewertet (`score_max` voll, `activity` 0).
- **Die Zielquote ist 15 %, nicht 100 %** (`resonanz.ziel_quote`). Wer die
  Registrierungsquote auf 1,0 normiert, gibt selbst der besten Gruppe ein
  Sechstel der Punkte und macht den Bestandteil wirkungslos. Daneben steht die
  Belastbarkeit (`mindest_klicks: 20`): 1 Klick mit 1 Registrierung sind 100 %
  und beweisen nichts. Die drei Teilmaße werden über `resonanz.anteile` zu
  **einem** Faktor verrechnet (Engagement 0,60 · Reichweite 0,25 · Aktualität
  0,15) und auf ihre Summe normiert, damit ein Tippfehler die Obergrenze nicht
  sprengt.
- **Reichweite zählt je Beitrag, nicht absolut.** Sonst gewänne die Gruppe, in
  der wir am öftesten gepostet haben, statt der, die am besten wirkt.
- **`data_confidence` steht neben dem Score, nie darin.** Ein mäßiger Score
  aus belegten Zahlen und ein guter aus dünnen Hinweisen sind zwei Aussagen;
  verrechnet wären beide unlesbar. Zwei Dinge fließen ein: **wie sicher** die
  Angaben sind (die Konfidenzen der Befunde) und **wie viel** überhaupt vorlag
  (der Anteil des beurteilten am möglichen Gewicht).
- **Der Ort kennt vier Stufen, und die unterste ist nicht null**
  (`location_stufen`): Stadt (voll, mal Konfidenz) → Bundesland (0,45) → Land
  (0,20) → nichts erkannt (`None`, unbekannt). „Deutschland allgemein" ist
  eine schwächere Passung als „Bonn", aber immer noch eine.
- **Nebenkategorien heben die Hauptkategorie an, gedeckelt**
  (`kategorie_nebenbonus` 0,08 je Thema, höchstens 0,24). Eine Gruppe, die
  drei gesuchte Themen bedient, ist mehr wert als eine, die eines bedient —
  ohne Deckel gewänne aber die Gruppe mit dem längsten Namen.
- **Gewicht `0` schaltet einen Bestandteil ganz ab, `None` heißt unbekannt.**
  Ein abgeschalteter Bestandteil senkt `score_max` nicht und erscheint nicht
  als „unbekannt" in `score_reason`. So steht `name_quality` auf 0: Die Form
  des Namens sagt etwas über **unsere Daten** und nichts über die Gruppe, sie
  gehört in `data_confidence`. Die Regel dahinter (`_namensform`) bleibt
  vollständig erhalten, damit das Wiedereinschalten eine Zahlenänderung ist
  und keine Codeänderung.
- **`upsert_groups` schützt erhobene Zahlen mit `COALESCE`.** Ein Schreiblauf,
  der weder Mitgliederzahl noch Aktivität mitbringt, darf nicht löschen, was
  mühsam erhoben wurde — und niemand merkte es, weil der Score einfach wieder
  sänke. Test: `test_ein_suchlauf_loescht_erhobene_zahlen_nicht`.
- **Migrationsschritt 15 rechnet keine alten Scores um.** Ein Score aus den
  alten Gewichten (45/25/15/8/7 plus 75 Resonanzpunkte) lässt sich nicht in
  die neuen übersetzen; ein geratener Umrechnungsfaktor stünde hinterher in
  der Rangliste, nach der entschieden wird, wo die nächsten dreihundert
  Beiträge hingehen. Neu bewertet wird beim Kampagnenlauf.
- **Die Spalte heißt `member_count_hint`, das Feld `member_count`.** Migrationen
  sind hier ausschließlich additiv, und ein `RENAME COLUMN` ist keine additive
  Änderung; `_row_to_group` bildet den Namen ab. Ein zweites Feld für dieselbe
  Zahl anzulegen wäre die schlechtere Lösung — zwei Wahrheiten über eine Zahl.
- **`ValidationStatus.UNREACHABLE` ist ein Menschenurteil.** Nur wer die
  Gruppe im Browser geöffnet hat, kann sie für tot erklären. `upsert_groups`
  nimmt dieses Urteil deshalb nie zurück.
- **Sortiert wird über `scoring.sort_by_rank`.** Erst die Punkte, bei
  Gleichstand der **Anteil** an den erreichbaren Punkten: 55 von 55 steht vor
  55 von 100.
- **Der Score wird nicht hochgerechnet.** Er ist die Summe der belegten
  Punkte; `score_max` nennt das bei dieser Datenlage Erreichbare. Das gilt
  unverändert für jeden **eingeschalteten** Bestandteil, der fehlt — nicht für
  einen mit Gewicht 0. Eine frühere Fassung normierte stattdessen über die
  vorhandenen Bestandteile auf 100 — bei 134 von 138 Gruppen ohne
  Mitgliederzahl bekam damit eine Gruppe, von der nur der Name bekannt war,
  denselben Höchstwert wie eine belegte Großgruppe. 27 Gruppen standen auf
  exakt 100. Wer die Normierung zurückholt, holt die Häufung zurück.
- **Jeder Bestandteil zählt genau einmal.** `name_quality` beurteilt nur die
  *Form* des Namens (vollständig, kurz, keine Satzform). Vergab es zusätzlich
  Punkte für Zielgruppe und Stadt, erreichten beide Bestandteile stets
  gemeinsam ihr Maximum — die zweite Ursache der Häufung.
- **`data_quality` zählt nur erhobene Felder** (Name, Beschreibung,
  Mitgliederzahl, Sichtbarkeit). Zielgruppe, Stadt und Kategorie sind
  abgeleitet und keine zusätzliche Information; mitgezählt meldeten sie
  „complete" für Datensätze, die nichts als Name und Beschreibungstext hatten.
- **Statusmodell mit drei Achsen**: `validation_status` (valid/invalid/test_data)
  bewertet die URL, `data_quality` (none/minimal/partial/complete) die
  Metadatenlage, `status` (new/validated/invalid/insufficient_data)
  fasst zusammen. Rangfolge in `validation.determine_status`:
  invalid > insufficient_data > validated.
- **`duplicate` wird nicht mehr abgeleitet.** Die Regel war `times_seen > 1` –
  aber `times_seen` zählt jeden **Fund**, nicht jeden Datensatz. Der Enum-Wert
  bleibt (Bestandsdaten, Handurteil), abgeleitet wird er nicht mehr.
- **Platzhalter werden markiert, nicht gelöscht.** `validation.py` prüft rein
  strukturell (Ziffernfolgen, Wiederholungen, Test-Tokens) und fragt nie bei
  Facebook nach. `test_data` ist ein begründeter Verdacht, keine Existenzaussage.
- **Metadaten werden nie erfunden.** Was nicht erhoben wurde, bleibt leer bzw.
  `None`; die Übersicht zeigt dafür `unknown`.
- **`SqliteStore.upsert_groups`** überschreibt `review_status` und `notes` eines
  bestehenden Datensatzes nie – manuelle Bewertungen überleben jeden Reimport.

### Die Mitgliederliste ist der Weg in den Bestand (`mitglieder.py`)

```
fbgroups import-mitglieder <datei.csv> [--dry-run] [--ohne-status]
```

Seit dem 20.09.2026 der **einzige** Weg, Gruppen in die Datenbank zu bekommen.
Was hereinkommt, hat ein Mensch von Hand gesammelt: Gruppen, in denen wir
bereits Mitglied sind.

- **Jede Zeile bedeutet „wir sind Mitglied"**, und genau das wird vermerkt
  (`MarketingStatus.MEMBER`). Eine Beitrittsanfrage an eine Gruppe, in der wir
  schon stehen, wäre ein Handgriff ohne Zweck — und eine der wenigen
  Handlungen, die bei Facebook auffallen. `--ohne-status` lässt den
  Arbeitsstand unberührt.
- **Ein erreichter Stand wird nie zurückgedreht.** Wer laut
  `MARKETING_FORTSCHRITT` schon weiter ist (Leitung angesprochen,
  Zusammenarbeit läuft), bleibt dort. Ein zweiter Lauf über dieselbe Datei
  ändert damit nichts — dieselbe Regel wie bei `marketing beitritt`.
- **Das Dateiformat ist eine fremde Tabelle, nicht unser Datenmodell.** Drei
  Spalten bedeuten etwas anderes, als ihr Name verspricht; das ist der
  eigentliche Inhalt des Moduls:

  | Spalte | Was drinsteht | Was daraus wird |
  |---|---|---|
  | `category` | Anzeigename („Reise & Transport") | Kennung (`reise`) über `KATEGORIEN` |
  | `city` | **Reiseziel** („Damaskus", „دمشق") | **nichts** — Hinweis in `notes` |
  | `country` | Raum („Deutschland / Europa") | `Group.country` |
  | `activity` | Kopf der Gruppenseite | Sichtbarkeit, Mitgliederzahl, Beiträge/Tag |

- **`category` muss übersetzt werden, sonst greift die Zielpriorität nie.**
  `marketing.zielprioritaet.kategorien` nennt `reise` und `versand`; stünde
  „Reise & Transport" im Bestand, träfe die Regel keine einzige Gruppe — und
  die Kampagne arbeitete wieder in den Gemeinschaftsgruppen, ohne dass
  irgendwo eine Fehlermeldung entstünde. Ein Wert, den `KATEGORIEN` nicht
  kennt, wird **gemeldet und nicht geraten**.
- **`city` wird verworfen, und das ist der Kern.** `Group.city` trägt 15
  Score-Punkte und belegt in `zielgruppe.bestimme_region` `Region.DE`. Mit
  „Damaskus" darin gälte eine syrische Zielangabe als deutscher Sitz: Die
  Gruppe stünde vor den tatsächlich deutschen und bekäme Punkte, die sie nicht
  verdient hat. Verloren ist die Angabe nicht — sie steht ausdrücklich benannt
  in `notes` („Reiseziel laut Liste: Damaskus"), dieselbe Zurückhaltung wie
  bei einem Beitragstitel, der kein Gruppenname ist.
- **„25 ungelesene Beiträge" ist keine Beitragszahl.** Der teuerste Fehlgriff,
  den der Seitenkopf anbietet: Es ist **unser eigener Postfachstand**. Als
  Rate gelesen ergäbe er 25 Beiträge am Tag und damit den vollen
  Aktivitätsfaktor — 25 von 100 Punkten für eine Gruppe, über deren Betrieb
  wir nichts wissen. `_BEITRAEGE_RE` verlangt deshalb das „pro Tag".
- **`الإشعارات` ist kein Gruppenname.** Es heißt „Benachrichtigungen" und ist
  die Überschrift *neben* der Gruppe, beim Sammeln mitgenommen. Ungefiltert
  stünde es als Name im Bestand, würde bewertet und erschiene über einem
  Beitrag — dieselbe Falle wie ein Beitragstitel, der früher als Gruppenname
  im Export landete. `KEIN_NAME` fängt es ab; der Name bleibt **leer**, die
  Gruppe bleibt erhalten (die URL ist echt, und Mitglied sind wir auch).
- **Der Import zeigt die Zielpriorität gleich mit.** Gerechnet, nicht
  gespeichert — über dieselbe `zielgruppe.aus_group`, die der Lauf benutzt.
  Sie steht dort, weil sie mehr entscheidet als der Score:
  `Gruppenfortschritt.bearbeitbar` schließt Klasse **D ganz aus**. Eine Gruppe
  mit 480.000 Mitgliedern, die als D hereinkommt, wird nie bearbeitet — und
  ohne diese Tabelle sähe niemand, warum.
- **`parse_member_count` steht in `textnorm.py`.** Zwei Wege lesen dieselbe
  Zeichenkette — den Kopf einer Gruppenseite: der Browser live
  (`automation/actions.py`), dieses Modul aus einer CSV-Spalte. Zwei Parser
  wären zwei Wahrheiten über dieselbe Zahl, und ein CSV-Leser soll dafür nicht
  Playwright laden müssen.
- **Der Import trägt keine Daten auf den Server.** `ausrollen.sh` überträgt
  ausschließlich `src config pyproject.toml`. Wer den Bestand des Servers
  füllen will, kopiert die Datei dorthin und lässt den Befehl **dort** laufen
  — nach der Regel, dass alles, was den Bestand ändert, auf den VPS gehört.

## Marketing-Erweiterung (`marketing/`)

Aufsatz auf den Gruppenbestand, ohne ihn zu verändern. Verwaltet **nur die
eigene Vorbereitung** — es wird nichts veröffentlicht und nichts verschickt;
`campaign message` gibt Text zum Kopieren aus, mehr nicht.

```powershell
& $py -m fbgroups.cli campaign new "Batreeq Syrian Germany" --zielgruppe syrians --stadt berlin
& $py -m fbgroups.cli campaign target batreeq-syrian-germany       # Regel anzeigen
& $py -m fbgroups.cli campaign target batreeq-syrian-germany --alle --auto-assign
& $py -m fbgroups.cli campaign sync batreeq-syrian-germany --dry-run
& $py -m fbgroups.cli campaign sync batreeq-syrian-germany   # Regel auf den Bestand anwenden
& $py -m fbgroups.cli campaign add-groups batreeq-syrian-germany --top 20  # einmaliger Griff
& $py -m fbgroups.cli campaign links batreeq-syrian-germany --export data\exports\links.csv
& $py -m fbgroups.cli campaign kurzlinks            # kurze Adressen nachtragen (Bestand)
& $py -m fbgroups.cli campaign kurzlinks batreeq-syrian-germany
& $py -m fbgroups.cli campaign text batreeq-syrian-germany --aus-vorlage --ja
                                             # Texte je Gruppe erzeugen -
                                             # Beitrag UND Kommentar
& $py -m fbgroups.cli campaign text batreeq-syrian-germany --aus-vorlage --typ post --ja
                                             # nur eine Art
& $py -m fbgroups.cli campaign text batreeq-syrian-germany --aus-vorlage --ueberschreiben --ja   # nach Aenderung an config/textvorlagen.yaml
& $py -m fbgroups.cli campaign message batreeq-syrian-germany arabinberlin
& $py -m fbgroups.cli campaign message batreeq-syrian-germany arabinberlin --typ kommentar
& $py -m fbgroups.cli campaign queue batreeq-syrian-germany   # was steht noch aus?
& $py -m fbgroups.cli campaign next batreeq-syrian-germany    # Gruppe fuer Gruppe
& $py -m fbgroups.cli campaign fortschritt batreeq-syrian-germany
& $py -m fbgroups.cli campaign posted arabinberlin --fehler "erlaubt keine Links"
& $py -m fbgroups.cli campaign retry batreeq-syrian-germany   # nur die Fehlschlaege
& $py -m fbgroups.cli marketing set arabinberlin --status contacted --kontaktiert-jetzt
& $py -m fbgroups.cli marketing list --erlaubnis approved
& $py -m fbgroups.cli marketing overview
```

- **`campaign next` bereitet vor, es veroeffentlicht nicht.** Je Gruppe legt
  es den fertigen Text in die Zwischenablage und oeffnet die Gruppe im
  Browser; einfuegen und absenden tut ein Mensch, und der Ausgang wird sofort
  protokolliert.
 : Es passiert dasselbe wie beim Anklicken eines Links, und die
  harte Projektgrenze bleibt unangetastet.
- **Der Beitragsstand gehoert zum Paar aus Kampagne und Gruppe**, nicht zur
  Gruppe (`campaign_groups`, nicht `group_marketing`). Dieselbe Gruppe kann in
  zwei Kampagnen stehen und traegt dann zwei Beitraege mit zwei Codes. An der
  Gruppe gespeichert meldete der eine Beitrag den anderen als erledigt — und
  die Arbeitsliste verschwiege eine offene Aufgabe.
- **Bestehende Zuordnungen starten auf `offen`.** Migrationsschritt 8 uebernimmt
  `group_marketing.last_posted_at` ausdruecklich **nicht**: Das Feld gilt fuer
  die Gruppe, der neue Stand fuer das Paar. Eine Gruppe zu viel in der Liste
  kostet einen Blick, eine zu wenig kostet einen Beitrag. Tracking-Codes, URLs
  und Ereignisse werden dabei nicht angefasst — der Schritt ist rein additiv.
- **`posted_at` wird nur beim ersten Erfolg gesetzt.** Die Klicks eines Codes
  gehen auf den Beitrag zurueck, der zuerst stand; ein Datum, das bei jedem
  erneuten Posten mitwandert, machte die Frage "seit wann laeuft dieser Link?"
  unbeantwortbar. `post_attempts` zaehlt dagegen **jeden** Ausgang mit, auch den
  Erfolg — es beantwortet "wie oft angefasst?", nicht "wie oft schiefgegangen?".
- **`uebersprungen` ist kein Fehlschlag.** "Passt nicht" ist ein Urteil ueber
  die Gruppe; `campaign retry` holt allein die fehlgeschlagenen zurueck. Deshalb
  gibt es den Ausgang auch nicht als Knopf in der Uebersicht (`POST /beitrag`
  nimmt nur `veroeffentlicht` und `fehlgeschlagen`): Ein Urteil gehoert an die
  Stelle, an der man die Gruppe ohnehin betrachtet.
- **Ein Erfolg loescht den alten Fehlergrund.** Sonst stuende neben einem
  veroeffentlichten Beitrag der Grund, aus dem er beim vorletzten Mal nicht ging.
- **Der Beitragstext entsteht ausschliesslich in `beitrag.beitragstext`.**
  `campaign message`, `queue`, `next` und die Uebersicht lesen alle dort. Eine
  zweite Fassung koennte abweichen, und der Unterschied fiele erst auf, wenn ein
  Beitrag mit dem falschen Code in einer Gruppe steht — zurueckholen laesst er
  sich dann nicht mehr. Kein Tracking-Code steht im Programm; jeder kommt aus
  der Zuordnung, ob es drei Gruppen sind oder dreihundert.
- **In der Arbeitsliste stehen die besten Gruppen oben** (`sort_by_rank`). Bei
  300 Gruppen bringt niemand die Liste an einem Tag zu Ende; wer abbricht, soll
  die wertvollsten Beitraege geschrieben haben und nicht die alphabetisch
  ersten. Gruppen ohne Datensatz wandern ans Ende statt zu verschwinden — ein
  Beitrag, der nicht in der Liste steht, wird nie geschrieben.
- **Ausgeschlossene Gruppen (`bearbeiten = 0`) stehen nicht in der Liste**, ihr
  Tracking-Code bleibt aber gueltig. Deshalb weicht "8 offen" in den Zaehlern
  von "9 in der Arbeitsliste" ab; `campaign fortschritt` nennt beide Zahlen,
  sonst wundert man sich ueber die Differenz.
- **Beschreibung und Auswahlregel einer Kampagne sind zwei Dinge.**
  `campaigns.audiences`/`cities` sagen, *wen* die Kampagne bewirbt; die
  `target_*`-Spalten sagen, *welche Gruppen* einen Tracking-Code bekommen.
  Solange beides dasselbe Feld war, liess sich eine Kampagne namens „Batreeq
  Syrian Germany" nicht auf den ganzen Bestand weiten, ohne ihre fachliche
  Beschreibung zu verfälschen. Bei jedem `target_*`-Feld heisst **leer: keine
  Einschränkung** – „alle Gruppen" ist damit ein Normalfall der Regel und kein
  Sonderweg. Auf der Kommandozeile hebt der Wert `alle` eine Einschränkung
  wieder auf (`--stadt alle`); ohne so ein Wort gäbe es keinen Weg zurück, weil
  eine leere Liste von „nicht angegeben" nicht zu unterscheiden ist.
- **Anlegen vergibt keine Codes.** `POST /kampagnen` erzeugt einen Entwurf mit
  `auto_assign` aus und **null** Zuordnungen; die Codes kommen erst über
  `POST /kampagnen/{id}/sync`, und der antwortet mit `dry_run: true` als
  Vorgabe. Ein Tracking-Code ist endgültig — er steht später in
  veröffentlichten Beiträgen und wird nie zurückgenommen; ein Formular, das
  beim Speichern still 400 Codes vergäbe, wäre ein Knopf mit unumkehrbarer
  Wirkung. Vorschau und Ernstfall lesen denselben `selection.baue_plan`: Eine
  zweite Zählung könnte abweichen, und der Mensch bestätigte dann eine Zahl und
  bekäme eine andere. Die Auswahlregel einer neuen Kampagne beginnt als Abbild
  ihrer Beschreibung, nicht leer — leer hieße „keine Einschränkung", also der
  ganze Bestand.
- **`auto_assign` greift nur bei `status: active`.** Sonst wäre „pausiert" eine
  Beschriftung ohne Wirkung, und ein Zuordnungslauf vergäbe Monate später Codes
  für eine Kampagne, die niemand mehr betreibt. Von Hand bleibt jede Kampagne
  zuordnbar — `campaign sync` fragt nicht nach dem Status, denn dort steht ein
  Mensch davor.
- **`campaign sync` ist die Regel, `campaign add-groups` der Schnappschuss.**
  `add-groups` lief genau einmal und schrieb, was es in dem Moment fand – so
  kamen 8 Zuordnungen zustande, während der Bestand auf 310 wuchs. `sync`
  wendet die gespeicherte Regel an, ist wiederholbar und läuft deshalb auch am
  Ende jedes Laufs, der neue Gruppen bringt (nur für Kampagnen mit
  `auto_assign`, Vorgabe aus). Beide lesen denselben Plan aus `marketing/selection.py` –
  `--dry-run` und Ernstfall können nicht auseinanderlaufen.
- **Drei Wege zur Zuordnung, und der dritte schließt eine Lücke.** `campaign
  sync` beschreibt die Auswahl als **Regel**, das Feld in der Kampagnenspalte
  greift **eine** Gruppe heraus — wer aber genau diese zwölf meint, müsste sie
  erst als Regel formulieren, und eine Regel, die zwölf trifft und keine
  dreizehnte, ist meist gar nicht formulierbar. `POST /kampagnen/{id}/gruppen`
  nimmt deshalb die angehakten Zeilen entgegen, als **Liste** wie
  `POST /bearbeiten`: Zwölf Zeilen sind ein Zug, keine zwölf Klicks — und nur
  so entstehen die Codes aus **einem** `CodeAllocator`. Die Reihenfolge kommt
  aus `selection.vergabereihenfolge` (deshalb ist der Schlüssel öffentlich),
  nicht aus der Reihenfolge der Haken: Sonst bekäme dieselbe Gruppe eine andere
  Nummer, je nachdem, wie die Tabelle gerade sortiert war. Bestätigt wird mit
  der **Zahl**, nicht nur mit dem Namen — wie viele Codes gleich entstehen, ist
  die Angabe, die man vorher gegenliest. Anders als das Feld in der Zeile nennt
  die Liste **alle** Kampagnen: Hinter der Auswahl stehen viele Gruppen mit
  verschiedenen Ständen, und der Server überspringt die bereits zugeordneten
  und sagt hinterher, wie viele es waren.
- **Zugeordnet wird nur hinzugefügt, nie entfernt.** Passt eine Gruppe später
  nicht mehr zur Regel, behält sie ihren Code und erscheint als
  `nicht_mehr_passend` im Bericht. Der Code steht möglicherweise in einem
  veröffentlichten Beitrag.
- **Die Codevergabe folgt `first_seen_at`, nicht dem Score.** Vorher lief sie
  in `sort_by_rank`-Reihenfolge – damit bekam dieselbe Gruppe nach jedem
  `rescore` eine andere Nummer, und „deterministisch" galt nur, solange niemand
  neu bewertete. `CodeAllocator` merkt sich je Kürzelpaar die höchste vergebene
  Nummer und zählt weiter: Der Aufwand hängt an der Zahl der **neuen** Codes,
  nicht am Quadrat der vorhandenen (bei 1000 Gruppen im selben Paar wären das
  sonst eine halbe Million Vergleiche). Eine frei gewordene Nummer wird
  bewusst nicht wieder ausgegeben.
- **`MarketingStore` holt fehlende Migrationsschritte selbst nach.** `GET
  /r/{code}` und `POST /events` öffnen **nur** diesen Speicher. Ohne den Schritt
  fehlte auf einem Server, dessen Datei aus einer älteren Fassung stammt, genau
  die neu hinzugefügte Spalte – und die Weiterleitung stürbe an einer Stelle,
  an der niemand eine Migration vermutet. Eine hier frisch angelegte Datei
  bekommt ausserdem ihre `user_version`; ohne sie hielte der nächste
  `SqliteStore` sie für eine Datei aus grauer Vorzeit.
- **„Bearbeiten wir sie?" ist eine eigene Achse.** `GroupMarketing.bearbeiten`
  steht neben `marketing_status`, nicht darin. Dort bedeuten `active`/`inactive`
  das Ende des Kooperationswegs (Zusammenarbeit läuft / ist beendet); wer damit
  auch „nicht bearbeiten" ausdrückte, löschte beim Ausschließen die Angabe, dass
  er in der Gruppe bereits **Mitglied** ist — und finge beim Wiederaufnehmen bei
  `not_contacted` an. **Der Tracking-Code bleibt bei einem Ausschluss gültig**
  (`test_ausschliessen_laesst_den_tracking_code_gueltig`): Er steht
  möglicherweise in einem veröffentlichten Beitrag, und ein Klick darauf muss
  ankommen und gezählt werden. `POST /bearbeiten` nimmt bewusst eine **Liste**
  entgegen — beim ersten vollen Bestand waren 144 von 413 Datensätzen ohne
  verwertbare Daten; das auszusortieren ist ein Zug, keine 144 Klicks.
  Migrationsschritt 7 schließt genau diese einmalig aus (`ON CONFLICT DO
  NOTHING` schützt jede von Hand gepflegte Zeile — eine Migration überstimmt
  kein Menschenurteil).
- **Der Arbeitsstand steht in `group_marketing`, nicht in `groups`.** Ein
  Schreiblauf über `upsert_groups` schreibt den ganzen Datensatz neu; von Hand
  gepflegte Angaben hätten dort keinen sicheren Platz.
- **, also
  kann keiner erkennen, dass eine Anfrage gestellt wurde. `marketing set` und
  `marketing beitritt` schreiben mit, was ein Mensch im Browser getan hat.
- **Die Beitrittsanfrage ist ein eigener Schritt** (`beitritt_angefragt` →
  `mitglied` → `contacted`). . Vorher sprang das Modell von
  `not_contacted` direkt auf `contacted` — und traf damit den häufigsten
  Schritt nicht: Beitrittsanfragen betreffen jede Gruppe im Bestand, das
  Ansprechen der Leitung eine Handvoll. `join_requested_at` ist ein eigenes
  Feld, nicht `last_contacted_at`: Facebook lässt Beitrittsanfragen oft
  wochenlang offen.
- **`marketing beitritt` dreht keinen erreichten Stand zurück.** Der
  Sammelbefehl überspringt jede Gruppe, die laut `MARKETING_FORTSCHRITT` schon
  weiter ist, und meldet sie. Auch `beitritt_abgelehnt` bleibt stehen — eine
  Ablehnung ist ein Ergebnis; sie erneut anzufragen ist eine Entscheidung von
  Hand über `marketing set`.
- **Migrationsschritte dürfen an einer schon vorhandenen Spalte nicht
  scheitern.** Die Marketing-Tabellen entstehen selbst in Schritt 3 – aus dem
  *aktuellen* Schema. Eine Datei, die diesen Schritt nachholt, bekommt sie
  deshalb bereits mit allen später ergänzten Spalten, und ein späterer
  `ALTER … ADD COLUMN` läuft ins Leere. `_migrate` übergeht genau
  `duplicate column name`, sonst nichts.
- **Ein vergebener Tracking-Code ändert sich nie.** Er steht in
  veröffentlichten Beiträgen. `add_link` lässt eine bestehende Zuordnung
  unangetastet, `refresh-urls` erneuert nur den Vorspann der Links. Der Code
  ist über **alle** Kampagnen eindeutig — sonst wäre ein eingehender Klick
  nicht zuzuordnen.
- **Die Kürzel im Code kommen aus dem Bestand** — die ersten drei Buchstaben
  von `audience_tags[0]` und `city`. Bis zum 20.09.2026 ging ein optionales
  Feld `code:` aus `cities.yaml`/`audiences.yaml` vor; beide Dateien sind mit
  der Entdeckungsschicht entfernt. Wer ein bestimmtes Kürzel will, schreibt
  den Tag bzw. den Stadtnamen entsprechend — eine zweite Tabelle dafür wäre
  eine zweite Wahrheit über dieselbe Gruppe.
- **`APP_BASE_URL` (Umgebung) schlägt `marketing.app_base_url`.** Der Wechsel
  von localhost auf die echte Domain berührt die Codes nicht.
- **Dieselbe Datenbank, dasselbe Migrationsverfahren** (`user_version`,
  additive Schritte in `storage/sqlite_store.py`). Kein zweites
  Datenbanksystem — das wäre eine zweite Wahrheit über dieselben Gruppen.
### Redirect-Dienst, Empfehlungen, Prämien

```powershell
& $py -m pip install -e ".[web]"     # FastAPI/uvicorn, nur fuer den Dienst
& $py -m fbgroups.cli serve --port 3000
& $py -m fbgroups.cli marketing analytics --top 10
& $py -m fbgroups.cli marketing code FB-SYR-KLN-002 --benutzer   # ein Code allein
& $py -m fbgroups.cli marketing referral list --status qualified
& $py -m fbgroups.cli marketing rewards --benutzer user-ahmad
& $py -m fbgroups.cli marketing audit
```

### Keine KI (entfernt)

Bis zum 26.08.2026 gab es `marketing/ki/`: Ollama als Standard auf dem eigenen
Rechner, Anthropic als kostenpflichtiger Sonderfall, dazu Entwürfe
(`post_entwuerfe`), ein „Mit KI anpassen" auf der Arbeitsseite, `campaign
draft` und `fbgroups ki status|test|modelle`. **Alles davon ist entfernt** —
Paket, Wege, Befehle, Konfiguration, Umgebungsvariablen, das `[ki]`-Extra und
der Rückwärtstunnel auf Port 11434.

Was davon bleibt und warum:

- **`vorlagen.pruefe_platzhalter`** (früher `ki.basis`): genau ein `{link}`,
  keine ausgeschriebene Adresse, kein codeähnliches Muster. Sie galt nie nur
  für Modellantworten — sie gilt für **jeden** Text, auch für einen von Hand
  geschriebenen. Mit der KI hatte sie nur zufällig zusammengewohnt.
- **`TextQuelle.KI` und `JobStatus.AI_GENERATED`** stehen weiter in den
  Aufzählungen. Sie werden nicht mehr vergeben, aber die Spalten tragen sie
  für Zuordnungen von damals; entfernt wären diese Datensätze nicht mehr
  ladbar.
- **`post_entwuerfe`** verschwindet aus dem Schema, aber **nicht** aus einer
  bestehenden Datei: Migrationen sind hier ausschließlich additiv, und ein
  `DROP` löschte Zeilen, die einmal Arbeit waren. Eine frisch angelegte Datei
  bekommt die Tabelle nicht mehr; niemand liest oder schreibt sie.

Der Grund für den Ausbau ist nicht, dass die KI schlecht gearbeitet hätte,
sondern dass sie den Ablauf nicht getragen hat: Die Vielfalt der 310 Texte kam
ohnehin aus `config/textvorlagen.yaml`, und was dort nicht passte, schreibt ein
Mensch auf der Arbeitsseite schneller, als ein lokales Modell einen Vorschlag
liefert.

### Textherstellung (`marketing/vorlagen.py`, `config/textvorlagen.yaml`)

```
Kampagne → Gruppen → je Gruppe und Einsatzzweck (POST | KOMMENTAR):
Sprache → mit_stadt / ohne_stadt → deterministische Wahl → füllen → speichern
   → Arbeit → [Kopieren] [Text bearbeiten → Speichern] → [Veröffentlicht]
```

Beide Texte gehören zum **Paar aus Kampagne und Gruppe**, nicht zur Kampagne:
`campaign_groups` hat je Zweck eigene Spalten (`post_text` / `kommentar_text`,
dazu `*_generated`, `*_vorlage_key`, `*_quelle`, `*_generiert_am`).

- **Die Abwechslung kommt aus dem Vorrat, nicht aus dem Modell.** Vorher gab es
  eine Vorlage je Kampagne, und `beitragstext` ersetzte nur `{link}`,
  `{tracking_code}`, `{landing_page}` — der einzige Unterschied zwischen 310
  Beiträgen war also der Link. Genau gleichlautende Beiträge in vielen Gruppen
  
  Fassungen je Sprache und Topf in `config/textvorlagen.yaml`, und **kein**
  Sprachmodell im Regelweg: Ein kleines Modell, das aus dem Nichts schreiben
  soll, erfindet Produkt, Anlass und Zahlen.
- **Der Text entsteht in zwei Stufen, und die Trennung ist der Kern.**
  `vorlagen.fuelle` ersetzt `{zielgruppe}` und `{stadt}` — Angaben über *diese*
  Gruppe, die sich nicht mehr ändern — und **speichert** das Ergebnis.
  `beitrag.beitragstext` bleibt unverändert die einzige Stelle, an der
  `{link}` aufgelöst wird, und tut das erst beim Lesen. Nur deshalb darf der
  gespeicherte Text einem Sprachmodell vorgelegt werden: Was das Modell nie
  bekommt, kann es nicht verfälschen. Test:
  `test_das_modell_sieht_den_tracking_code_nie`.
- **`generated_text` steht neben `post_text`, nicht darin.** Das erste ist, was
  die Vorlage ergeben hat; das zweite, was wirklich hinausgeht („current_text"
  im Aufbau). Mit einem Feld gäbe es nach einer KI-Überarbeitung keinen Weg
  zurück, und niemand könnte mehr sagen, wie viel des Textes aus der Vorlage
  stammt. `POST /arbeit/{k}/vorschlag/zuruecksetzen` ist der Ausweg, der erst
  dadurch möglich ist. Er hat **seit dem 28.08.2026 keinen Knopf mehr** — die
  Arbeitsseite bietet an seiner Stelle „Gruppe bei Facebook oeffnen" an; der
  Weg selbst bleibt, und wer eine überarbeitete Fassung verwerfen will, hat
  daneben noch vier andere Nummern. Der erzeugte Text wird auch dann
  aufgefrischt, wenn der laufende stehenbleibt — eine veraltete
  Vergleichsgröße ist schlechter als keine.
- **Die Wahl der Fassung folgt `blake2b(group_id)`, nicht `hash()`.** Der
  eingebaute `hash` ist für Zeichenketten je Prozess gesalzen; dieselbe Gruppe
  bekäme nach jedem Neustart des Dienstes eine andere Vorlage — und der Text
  änderte sich unter demjenigen, der ihn gerade freigegeben hat. Derselbe
  Gedanke wie bei der Codevergabe, die `first_seen_at` folgt und nicht dem
  Score. Test: `test_die_wahl_ueberlebt_einen_neustart` (eigener Prozess mit
  anderem `PYTHONHASHSEED`).
- **`vorlage_key` wird gespeichert, nicht neu berechnet** (`"ar/mit_stadt/3"`).
  Nur so bekommt dieselbe Gruppe beim nächsten Füllen wieder dieselbe Fassung —
  etwa nachdem eine Stadt nachgetragen wurde. Die Nummer ist der **Index** in
  der Liste: hinten anhängen ist gefahrlos, in der Mitte einfügen verschiebt
  alle folgenden.
- **Zwei Töpfe statt eines Platzhalters, der leer bleiben darf.** 152 von 313
  Gruppen haben keine erkannte Stadt, 115 keine Zielgruppe. Eine Vorlage mit
  „in {stadt}" zerbricht damit bei der Hälfte des Bestands, deshalb `mit_stadt`
  und `ohne_stadt`. Seit dem 20.09.2026 gilt jede **eingetragene** Stadt:
  Es gibt keine Liste mehr, gegen die geprüft werden könnte, und `Group.city`
  wird von Hand gepflegt. Leer bleibt leer, und dann greift `ohne_stadt`.
- **Drei Beschriftungen je Zielgruppe gab es bis zum 20.09.2026.** `label_de`
  („Syrer in Deutschland") war fürs Auswahlfeld; in einer Stadtvorlage ergäbe
  es „Syrer in Deutschland in Bonn", dafür gab es `label_kurz_de`, und für
  arabische Vorlagen `label_ar`. Alle drei standen in `audiences.yaml` und
  sind mit ihr entfallen. `{zielgruppe}` kommt jetzt aus `anrede_allgemein`
  in `textvorlagen.yaml` und lautet für **jede** Gruppe gleich.
- **Arabisch hat nur eine Form, also folgt daraus eine Regel für jede Vorlage.**
  Die Anrede trägt den bestimmten Artikel und steht im Genitiv/Akkusativ
  („الأصدقاء"; bis zum 20.09.2026 je Zielgruppe „السوريين"). Vor `{zielgruppe}` gehört deshalb ein eigenes Wort (مِن، إلى،
  أهلنا، مجتمع) — nie ein angehängtes Präfix und nie „يا": „لـ" verschmilzt mit
  dem Artikel („لـالسوريين" gibt es nicht), und „يا" verträgt keinen Artikel.
  Beides stand im ersten Wurf drin und fiel erst in der Rauchprobe auf.
- **Die eigene Vorlage einer Kampagne geht dem Vorrat vor, ist aber der
  Sonderfall.** `campaign.message_template` gilt dann für **alle** Gruppen der
  Kampagne — dann klingen wieder alle Beiträge gleich. Leer ist deshalb im
  Formular der Normalfall und kein Mangel. Personalisiert wird sie trotzdem:
  `{zielgruppe}` und `{stadt}` wirken darin genauso.
- **Was die Vorlage nicht trifft, schreibt ein Mensch** — direkt auf der
  Arbeitsseite, im Textfeld unter dem jeweiligen Text. Hier stand einmal die
  KI-Überarbeitung; sie ist entfernt. Der Weg dorthin ist
  `POST /arbeit/{k}/text` mit `texttyp`, und er geht durch **dieselbe**
  `vorlagen.pruefe_platzhalter` wie jeder andere Text.
- **Beitrag und Kommentar teilen sich keine Vorlage.** Ein Beitrag eröffnet: Er
  darf begrüßen, erklären und mit einem Aufruf enden. Ein Kommentar steht unter
  einem Beitrag, den jemand anderes geschrieben hat — er bringt seinen Anlass
  nicht mit, sondern reagiert auf einen vorhandenen. Ein gekürzter Beitrag als
  Kommentar liest sich wie eingeworfene Werbung, und genau danach sucht die
  Spam-Erkennung. Deshalb `vorlagen: <sprache>: <post|kommentar>: <topf>` mit
  je fünf Fassungen und ein eigenes Feld am Datensatz.
- **Der Kommentar hängt am selben Paar — vier Spalten, keine zweite Tabelle.**
  Er hat denselben Schlüssel, dieselbe Lebensdauer und verschwindet mit
  derselben Zuordnung; eine Tabelle daneben wäre eine Kopie mit dem Risiko,
  auseinanderzulaufen (dieselbe Überlegung wie bei den Job-Feldern).
  Migrationsschritt 13 ist rein additiv und schreibt **keine** Abschrift des
  Beitrags in `kommentar_text`: Einen Beitrag als Kommentar auszugeben wäre die
  Behauptung, er tauge dafür.
- **Der Beitrag trägt den Ablauf, der Kommentar wird kopiert.** `JobStatus`,
  Freigabe, Warteschlange und `post_versuche` gehören dem Beitrag; der
  Kommentar hat davon nichts. Eine zweite Warteschlange daneben wäre eine
  zweite Zählweise für dieselben Beiträge.
- **Jede Kampagne führt Kommentare** (seit 28.08.2026). Die Frage „brauchen
  wir hier Kommentare?" gibt es nicht mehr: kein Haken in der Kampagnenzeile,
  kein Feld im Anlegeformular, kein `POST /kampagnen/{id}/texte`, kein
  `Campaign.kommentare`. Sie stand in jeder Zeile der Übersicht und musste in
  keiner beantwortet werden — wer in einer Gruppe postet, kommentiert dort
  auch; ein Kommentar zu viel kostet einen Blick, ein fehlender einen
  Handgriff. `campaign text` erzeugt deshalb ohne `--typ` **beide** Arten, und
  `--typ` schränkt einen Lauf ein, statt eine zweite Art freizuschalten.
  Die Spalte `campaigns.kommentare` bleibt im Schema (Migrationen sind hier
  additiv) und wird nicht mehr gelesen; geschrieben wird konstant `1`, damit
  eine ältere Fassung des Programms dieselbe Datei nicht anders liest.
  Migrationsschritt 13 bleibt unverändert stehen — eine Datei, die ihn schon
  ausgeführt hat, führt ihn nie wieder aus.
- **Der Vorlagenschlüssel trägt eine Kennung, keine Nummer**
  (`ar/post/mit_stadt/alltag`). Vorher stand dort die Position, und wer eine
  Vorlage in der Mitte einfügte, verschob alle folgenden — eine Gruppe trug
  dann einen Schlüssel, hinter dem ein anderer Text stand. Umsortieren ist
  damit gefahrlos; eine Kennung zu **ändern** ist dasselbe wie die Vorlage zu
  löschen, und das ist die ehrlichere Beschreibung. Ein Schlüssel aus drei
  Teilen (`ar/mit_stadt/3`) stammt aus der Zeit davor, meint einen Beitrag und
  wird weiterhin gelesen — sonst bekämen 310 Gruppen beim nächsten Füllen eine
  andere Vorlage, ohne dass jemand etwas geändert hat.
- **Die Wahl zieht den Zweck mit ein** (`blake2b("<group_id>|<zweck>")`). Sonst
  fände dieselbe Gruppe in beiden Töpfen dieselbe Stelle — eine unnötige
  Regelmäßigkeit in etwas, das gerade nicht regelmäßig aussehen soll. Stabil
  bleibt es trotzdem: dieselbe Gruppe, derselbe Zweck, dieselbe Fassung.
- **Ein Platzhalter, den niemand ersetzt, ist ein Fehler und keine Freiheit.**
  Erlaubt sind `{zielgruppe}`, `{stadt}`, `{ziel}`, `{gegenstand}`, `{gruppe}`
  (dieses Modul) sowie `{link}`, `{tracking_code}`, `{landing_page}` und
  `{datum}` (`beitrag.mit_link`). Alles
  andere wirft `UnbekannterPlatzhalter` — eine Unterklasse von `VorlageFehlt`,
  damit jeder Aufrufer, der eine lückenhafte Konfiguration schon behandelt,
  auch diesen Fall behandelt: Für *diese* Gruppe entsteht kein Text, und der
  Grund steht im Bericht. `config-check` meldet es vorher.
- **Der Texttyp der Meldung entscheidet, welches Feld geschrieben wird.**
  `POST /arbeit/{k}/text` und `/zuruecksetzen` tragen ihn mit (Vorgabe
  `post`); ohne ihn landete ein Kommentar im Beitrag, und das fiele erst auf,
  wenn er in der Gruppe steht.
- **Der arabische Vorrat `mit_stadt` sind die zehn Vorlagen des Nutzers**
  (28.08.2026, fünf Beiträge + fünf Kommentare). Drei Zusicherungen stehen in
  jeder von ihnen und sind keine Formulierung, sondern die Grenze der App: sie
  **vermittelt nur**, sie kennt **keinen Preis**, sie wickelt **keine Zahlung**
  ab — und sie verspricht weder Zustellung noch Versicherung. Wer eine sechste
  Fassung schreibt, schreibt das mit. Die Vorlagen sprechen die Zielgruppe
  **nicht mehr an**; sie nennen stattdessen ihr Reiseziel (`{ziel}`). Die
  Anrede bleibt abgeleitet und bleibt geprüft — nur eben an der
  `Personalisierung` und nicht mehr am fertigen Text.
- **`ohne_stadt` behält die alten Fassungen.** Die zehn Vorlagen nennen fast
  alle eine Stadt; sie mechanisch auf „aus Deutschland" umzuschreiben wäre eine
  Vorlage, die niemand hingeschrieben hat. Damit tragen die 152 Gruppen ohne
  erkannte Stadt weiterhin den früheren Tonfall — sichtbar in der
  Arbeitsseite, und eine bewusste Entscheidung des Nutzers, keine Lücke.
- **`{ziel}` steht seit dem 20.09.2026 in `textvorlagen.yaml`**, unter
  `ziel_allgemein`, und lautet dort **„سوريا"**. Vorher stand es je Zielgruppe
  in `audiences.yaml` (`ziel_ar`/`ziel_de`): Wer syrische Gruppen bewarb,
  meinte Syrien; wer irakische bewarb, den Irak. Mit der Datei ist die
  Unterscheidung entfallen — der Bestand dieses Projekts sind Syrien-Strecken,
  und „الوطن" wäre an 24 Vorlagenstellen eine Abschwächung des Satzes, den der
  Nutzer geschrieben hat. Wer einen Bestand mit zwei Zielen bewirbt, braucht
  zwei Kampagnen mit eigenen Vorlagen — nicht eine Tabelle neben dem Text.
  Eine Anrede taugt dafür nach wie vor nicht: „من بون إلى الأصدقاء" wäre kein
  Ziel, sondern ein Satzfehler.
- **`{gegenstand}` ist ein Wort und kein zweiter Vorrat.** In den Vorlagen
  steht „`{gegenstand}` صغير"; jede Fassung müsste dieselbe Genus- und
  Numerusform haben („أمانة صغير" gibt es nicht). Fünf Wörter für einen
  Unterschied, den niemand liest, wären der falsche Ort für Abwechslung — die
  kommt aus der Zahl der Vorlagen.
- **`{datum}` wird beim Lesen ersetzt, nicht beim Erzeugen.** Es trägt den
  laufenden Monat. Beim Füllen eingesetzt und mitgespeichert stünde in einem
  Beitrag, der drei Wochen später hinausgeht, der Monat von damals — eine
  Frage nach Reisenden im **letzten** Monat ist schlicht falsch. Es steht
  deshalb in `beitrag.mit_link` neben `{link}`, und `config` ist dort
  **verpflichtend**: Ein Aufrufer, der es vergessen dürfte, ließe `{datum}` in
  geschweiften Klammern im Beitrag stehen. Die Monatsnamen sind levantinisch
  (آب, أيلول) und nicht die ägyptisch-arabische Reihe — in einer syrischen
  Gruppe klingt „سبتمبر" nach Fremdsprache. Sie stehen in
  `textvorlagen.yaml`, `config-check` zählt sie (zwölf oder Fehler).
- **Das Kampagnenformular hat weder „Eigene Textvorlage" noch den
  Kommentar-Haken** (entfernt am 28.08.2026). Die eigene Vorlage galt für
  **alle** Gruppen der Kampagne und war damit der häufigste Griff daneben —
  ausgerechnet im Formular, in dem eine Kampagne entsteht; sie bleibt über
  `campaign set <k> --vorlage ...` erreichbar, und `POST /kampagnen` nimmt
  `message_template` unverändert entgegen: Was fehlt, ist der Knopf, nicht der
  Weg. Der Kommentar-Haken hat dagegen keinen Weg mehr — es gibt nichts mehr
  zu schalten, siehe „Jede Kampagne führt Kommentare".
- **Das Kampagnenformular hat keine Vorlagenvorschau mehr** (entfernt am
  27.08.2026 samt `GET /vorlagen/vorschau` und `vorlagen.vorschau`). Sie zeigte
  eine Fassung mit den gewählten Beschriftungen, und „Andere Vorlage" lief im
  Kreis durch den Topf — eine Antwort auf eine Frage, die sich beim Anlegen
  nicht stellt: Welche Fassung eine **Gruppe** bekommt, entscheidet ihre
  Kennung, und das steht erst beim Zuordnen fest. Wer die Texte sehen will,
  sieht sie dort, wo mit ihnen gearbeitet wird — auf `/arbeit/{kampagne}`
  stehen alle fünf Fassungen nebeneinander, mit dem Vorlagenschlüssel über
  jeder. Der Hinweis am Feld „Eigene Textvorlage" bleibt: Er ist die
  eigentliche Warnung vor dem häufigsten Griff daneben.

### Kein Arbeiter, keine Wartezeit, kein Tageslimit (entfernt)

Ebenfalls entfernt: `worker.py` (die Schleife, die Beiträge nacheinander
absetzte), das Paket `veroeffentlicher/` samt Adapter `assistiert`, die
Befehle `campaign worker`, `campaign tageslauf` und `campaign zeitplan`, die
Wartezeit zwischen zwei Beiträgen samt Countdown auf der Sperrseite
(`pause_sekunden_min/max`, `startzeit`, `max_pro_lauf`) und
`store.letzter_versuch`.

- **Die Warteschlange bleibt, der Taktgeber geht.** Reihenfolge nach Score,
  `JobStatus`, Freigabe, Einreihen, `pause`/`resume`/`stop`, `retry` — alles
  unverändert. Was fehlt, ist die Uhr: Nach einem gemeldeten Ausgang kommt
  **sofort** die nächste Gruppe (303 auf `/arbeit/{kampagne}`, und die holt
  den nächsten Auftrag).
- **Warum die Wartezeit weg ist.** Sie zog drei bis sieben Minuten zwischen
  zwei Beiträge und sollte einen menschlichen Takt nachbilden. Sie hat den
  Ablauf mehr aufgehalten als geschützt: Wer dreißig Gruppen abarbeitet, saß
  damit drei Stunden vor einem Countdown — und der Takt entsteht ohnehin von
  selbst, weil jeder Beitrag von Hand eingefügt und abgesendet wird.
- **Auch das Tageslimit ist weg** (27.08.2026: `max_pro_tag`,
  `arbeit.Grenzen`, `lade_grenzen`, `Grund.TAGESLIMIT`,
  `store.versuche_heute`). Es war die letzte Bremse aus der Zeit des Arbeiters:
  zwanzig Beiträge am Tag, ab örtlicher Mitternacht, über alle Kampagnen. Gegen
  eine Schleife, die selbst abschickt, war das eine Bremse; gegen einen
  Menschen, der jeden Beitrag von Hand einfügt, war es eine Sperre, die
  ausgerechnet den traf, der gerade arbeitet — wer dreißig Gruppen vor sich
  hatte, stand vor der einundzwanzigsten. **Damit gibt es keine gezählte Grenze
  mehr**; was bleibt, ist `pause`/`stop` je Kampagne, also ein Entschluss statt
  einer Zahl. Wer eine Obergrenze zurückholt, holt sie an genau dieser einen
  Stelle zurück (`melde_vorschlag`); der Test
  `test_es_gibt_keine_gezaehlte_tagesgrenze_mehr` hält den Zustand fest.
- **`Ergebnis` steht jetzt in `arbeit.py`.** Es war der Rückgabewert eines
  Adapters; die gibt es nicht mehr, gemeldet wird von Hand.

### Arbeiten auf dem Server (`arbeit.py`, `arbeitsseite.py`)

```
https://go.b-tarikak.de → Übersicht → Kampagne → [Arbeiten]
   → /arbeit/{kampagne}          bereitet selbst vor, dann der laufende Beitrag
   → /arbeit/{kampagne}?nr=N     der N-te - nur ansehen, nichts wird begonnen
```

- **Der Bestand lebt auf dem Server, Zwischenablage und Browser stehen auf
  dem Arbeitsrechner.** Die Arbeit dorthin zu holen hieße, in eine **zweite**
  Datenbank zu schreiben — genau davor warnt `docs/plan-go-subdomain.md`.
  Aufgelöst wird das, indem die *Arbeit* dorthin kommt, wo der Bestand steht:
  Der Server bereitet vor und zählt, der Browser des Menschen kopiert und
  öffnet. Der Server braucht keine Zwischenablage — der Browser hat eine.
- **`arbeit.py` hält den Schritt, den beide Wege teilen.** Die Schleife des
  Arbeiters ist nur *eine* Art, `hole_auftrag`/`melde_ergebnis` aufzurufen; die
  Weboberfläche ist die andere. Eine zweite Fassung der Regeln für das Web wäre
  eine zweite Zählweise für dieselben Beiträge.
- **Es wird nicht gewartet und nichts gezählt.** Nach einem gemeldeten Ausgang
  kommt sofort die nächste Gruppe. Wartezeit *und* Tageslimit sind entfernt —
  siehe „Kein Arbeiter, keine Wartezeit". Was den Ausgang noch anhalten kann,
  ist allein `pause`/`stop` an der Kampagne.
- **Der Auftrag ist begonnen, wenn er herausgeht.** `hole_auftrag` setzt
  `processing` und schreibt die Protokollzeile, *bevor* jemand den Text sieht.
  Wer den Reiter schließt, bekommt beim nächsten Aufruf **denselben** Auftrag
  zurück, statt dass ein zweiter angefangen wird — ohne das blutete die
  Warteschlange bei jedem geschlossenen Fenster einen Beitrag aus. Ein
  zurückgegebener Auftrag ist derselbe Auftrag - kein zweiter Versuch.
- **Eine Sperre fasst nichts an.** Pausiert, gestoppt, leer — in keinem dieser
  Fälle entsteht ein Versuch. Sonst zählte Nachsehen als Arbeit.
  Die Reihenfolge der Prüfungen ist dabei nicht beliebig: Wer pausiert hat,
  will lesen, dass er pausiert hat, und nicht, dass die Schlange leer ist.
- **Der Text geht nur hinaus, nie zurück.** Das Formular meldet den Ausgang und
  die `versuch_id`; der Beitragstext ist kein Feld darin. Ein manipuliertes
  Formular kann damit keinen anderen Text in einen Beitrag bringen als den, den
  der Server vorbereitet hat. Die Antwort ist **303** — ein Neuladen soll den
  Ausgang nicht ein zweites Mal melden.
- **Der dritte Knopf ist der nächste Schritt, nicht der vorige** (28.08.2026).
  Unter jedem Text steht [Speichern] [… kopieren] [Gruppe bei Facebook
  oeffnen] — der Handgriff in seiner Reihenfolge. Dort stand „Zurueck zur
  Vorlage": eine Frage, die sich beim Schreiben selten stellt, ausgerechnet an
  der Stelle, an der man nach dem Kopieren weitergeht. Der Link steht in
  **beiden** Spalten; derselbe Link im Kopf der Seite bleibt, weil er zur
  Gruppen-Navigation gehört und auch beim bloßen Blättern gebraucht wird.
- **Vier Knöpfe, eine Reihe, zwei Ausgänge.** Kopieren, Gruppe öffnen,
  „Veröffentlicht", „Fehlgeschlagen" stehen zusammen unter dem Beitrag — es ist
  **ein** Handgriff, und getrennt lagen seine beiden Hälften eine halbe Seite
  auseinander: Wer den Beitrag abgesetzt hatte, scrollte an „Text anpassen"
  vorbei, um den Ausgang zu melden. „Passt nicht" und „Schluss für heute"
  haben deshalb keinen Knopf mehr — für das Urteil über eine Gruppe ist die
  Übersicht der Ort, und wer aufhört, schließt die Seite. `POST
  /arbeit/{k}/ergebnis` nimmt beide Ausgänge **weiterhin** an: Ein Job, den ein
  Skript zurückgelegt hat, wäre sonst nicht mehr zu melden. Damit steht das
  Textfeld für den Fehlergrund jetzt vor den Absendeknöpfen, und Enter darin
  sendete mit dem **ersten** ab — „Veröffentlicht". Wer gerade den Fehlergrund
  tippt, meldete so das Gegenteil dessen, was er meint; ein `keydown`-Handler
  leitet Enter auf „Fehlgeschlagen".
- **Kein `python-multipart`.** Das Formular trägt vier kurze Textfelder und
  keine Datei; `parse_qsl` aus der Standardbibliothek genügt. `request.form()`
  verlangte ein Paket mehr, und das `[web]`-Extra soll klein bleiben — dieselbe
  Überlegung wie bei `webbrowser` und der Zwischenablage in `beitrag.py`.
- **„Zurueckholen" ist der Ausgang aus „Passt nicht".** Ohne ihn war der Knopf
  eine Sackgasse mit Ansage: `uebersprungen` setzt `job_status` auf
  `cancelled`, und ein `cancelled` **mit Text** fällt durch jeden Schritt der
  Werkbank — die Textschritte nehmen nur Textlose, `approve` nur
  draft/ai_generated/pending_review, `enqueue` nur approved. Wer sich
  verklickte, sah vier Knöpfe, von denen keiner etwas tat, und nichts sagte
  ihm warum; der einzige Weg zurück war `campaign reset` auf der
  Kommandozeile — ausgerechnet ein Befehl, um eine Fehlbedienung der
  Oberfläche zu heilen. Ziel ist `draft`, und nicht aus Bequemlichkeit: Es ist
  laut `UEBERGAENGE` der einzige erlaubte Ausgang aus `cancelled`. **Der Text
  bleibt stehen** — „Passt nicht" ist ein Urteil über die Gruppe, nicht über
  den Text, und ihn beiläufig zu löschen nähme ein zweites Urteil vorweg, das
  niemand gefällt hat. Wer doch den Text meinte, überschreibt ihn im
  Textfeld darunter. Der Knopf trägt bewusst **keine
  Nummer**: Er gehört nicht in die Kette, er nimmt eine Fehlbedienung zurück.
  Seit „Passt nicht" keinen Knopf mehr hat, kommt ein `cancelled` nur noch von
  der Kommandozeile — der Weg zurück bleibt trotzdem, denn die Bestandsdaten
  bleiben.
- **`/arbeit/…` steht hinter `_nur_lokal`** wie jeder schreibende Weg, und der
  Knopf „Arbeiten" erscheint nur im bedienbaren Zugang. Anders als die übrigen
  Knöpfe wird er **entfernt** statt per CSS versteckt: Er ist ein einfacher
  Link, den das Skript nicht sucht — die Begründung fürs Verstecken
  (mehrere Knöpfe werden beim Start gesucht) trifft auf ihn nicht zu.
- **`?nr=N` blättert, ohne etwas anzufangen.** `hole_vorschau` liest nur; kein
  `processing` und keine Protokollzeile. Vorher war der
  einzige Weg zum übernächsten Beitrag, den nächsten zu **melden** — und
  „veröffentlicht" oder „passt nicht" sind Aussagen über einen Beitrag, den es
  in dem Moment noch gar nicht gibt. `Vorschau` ist deshalb ein eigener Typ
  neben `Auftrag` und hat **keine `versuch_id`**: Ohne sie lässt sich kein
  Ausgang melden, und die Seite kann die Knöpfe gar nicht erst zeigen.
  Gezählt wird ab 1, und 1 ist der laufende Beitrag — eine Zählweise, nicht
  zwei. Test: `test_blaettern_faengt_keinen_versuch_an`.
- **Ein Schieber, keine 300 Nummern.** Er springt bei `change`, nicht bei
  `input`: Sonst lädt die Seite auf dem Weg von 1 nach 40 neununddreißig Mal.
  Hinter das Ende geblättert führt auf den laufenden Beitrag (303) statt auf
  eine Fehlerseite — die Schlange wird kürzer, während man darin liest.
- **Die Merkmale stehen über dem Beitrag.** Stadt, Zielgruppe, Kategorie,
  Score, Vorlage, Textquelle. Wer 300 Beiträge hintereinander schreibt, sieht
  sonst immer denselben Bildschirm und muss in einem anderen Fenster
  nachsehen, um wen es diesmal geht — dabei steht alles davon im Bestand.
  `config` geht nur für die Beschriftungen hinein: `audience_tags` hält
  Kennungen, und „syrians" ist keine Auskunft.
- **Der Editor ist die eine Ausnahme von „der Text geht nur hinaus".** Und er
  ist ein **eigener Weg** (`POST /arbeit/{k}/text`), kein Feld im
  Ergebnisformular — genau darin liegt die Begründung: Das Formular, das einen
  Beitrag abschließt, trägt weiterhin nur Ausgang und `versuch_id`; ein
  Textfeld darin wäre ein Kanal, den niemand geöffnet haben wollte. Hier ist
  das Ändern die Handlung selbst, ausdrücklich und hinter `_nur_lokal`. Test:
  `test_das_ergebnisformular_traegt_weiterhin_keinen_text`.
- **Das Textfeld steht offen, nicht zugeklappt.** Solange der Regelfall aus
  der Vorlage kam und ein Modell den Rest machte, war „Text von Hand
  bearbeiten" hinter einem `<details>` richtig. Seit der Mensch den Text hier
  **schreibt**, ist Zuklappen ein Weg mehr zu dem, was ohnehin die Hauptsache
  ist. Es liegt **außerhalb** des Ergebnisformulars, in derselben Karte wie
  der Text, den es ändert.
- **Der Editor zeigt den gespeicherten Text mit `{link}`,** nicht den
  angezeigten mit eingesetztem Link, und geht durch **dieselbe**
  `vorlagen.pruefe_platzhalter` wie jeder andere Text: genau ein `{link}`,
  keine ausgeschriebene Adresse, kein codeähnliches Muster. Ein von Hand
  hineingeschriebener Link ergäbe einen Beitrag, der richtig aussieht und
  dessen Gruppe nie einen Klick gutgeschrieben bekommt.
- **„Arbeiten" bereitet selbst vor.** Ist die Warteschlange leer, laufen
  `text`, `approve` und `enqueue` beim Aufruf von `/arbeit/{k}` **ohne
  Rückfrage** — vorher stand dort eine Knopfreihe, die nacheinander zu drücken
  kein Entschluss war, sondern eine Wegstrecke. Zulässig ist das, weil keiner
  der drei etwas veröffentlicht und keiner einen vorhandenen Text
  überschreibt; das Schlimmste, was ein überflüssiger Lauf anrichtet, ist ein
  Eintrag im Protokoll. Bleibt die Schlange danach leer, steht der **Bericht**
  auf der Seite (`0 Texte · 3 freigegeben · 0 eingereiht`) — die Zahl vorn,
  nicht der Satz: „Freigegeben." lässt offen, ob es zwölf waren oder keine,
  und genau das ist dann die Frage. Der häufigste Fall bekommt Klartext:
  *„Diese Kampagne hat keine Gruppen zugeordnet."*
- **Nach dem automatischen Lauf fehlen die drei nummerierten Knöpfe.** Übrig
  bleiben die Schritte, die die Kette *nicht* enthält: ein „Passt nicht"
  zurücknehmen, vorhandene Texte neu erzeugen. Die drei noch
  einmal anzubieten wäre eine Einladung, etwas zu wiederholen, das gerade
  nachweislich nichts bewirkt hat.
- **Zwei Texte, eine Seite.** Unter dem Beitrag steht der Kommentar derselben
  Gruppe — eigene Vorlagenzeile, eigener Kopierknopf (`data-kopieren`) und
  ein **offenes Textfeld**. Es ist dieselbe Gruppe und derselbe Handgriff; sie
  auf zwei Seiten zu verteilen hieße, zweimal durch die Warteschlange zu
  blättern. Die Kommentarkarte steht **auch ohne Text**: Sie ist die Stelle,
  an der ein Kommentar entsteht, und eine Stelle, die es nur gibt, wenn schon
  etwas dasteht, ist keine. Das **Ergebnisformular bleibt beim Beitrag**: Der
  Kommentar hat keinen Ausgang zu melden. Woher ein Text stammt, steht seit dieser Trennung über dem jeweiligen
  Text und nicht mehr in der Merkmalszeile — eine gemeinsame Zeile ließe offen,
  welcher von beiden gemeint ist.
- **Kopiert wird der angezeigte Text.** Er trägt den eingesetzten
  Tracking-Link; `{link}` bekommt der Mensch nie zu sehen und damit auch nie in
  die Zwischenablage.
- **Die Beitragsspalte der Übersicht zeigt den Stand, sonst nichts.** Zuerst
  fielen „Gruppe", „steht" und „ging nicht", am 27.08.2026 auch „Text": Alle
  vier stammen aus der Zeit vor der Arbeitsseite und boten denselben Ablauf ein
  zweites Mal an — nur schmaler und ohne die Merkmale der Gruppe. Zwei Wege zum
  selben Beitrag heißen zwei Zählweisen. Gearbeitet wird unter
  `/arbeit/{kampagne}`; `POST /beitrag` bleibt als programmatischer Weg
  bestehen, hat aber keinen Knopf mehr. **Der fertige Beitragstext steht damit
  nicht mehr in der Nutzlast** der Übersicht: Er lag dort nur, damit der
  Kopierknopf ohne zweiten Aufruf auskam — bei 310 Gruppen sind das 310 Texte
  im Dokument für einen Knopf, den es nicht mehr gibt.
- **Die Tabelle blättert ab 25 Zeilen** (10/25/50/100/alle wählbar). Bei 314
  Zeilen lag die Kampagnenliste hinter dreihundert Zeilen. Gefiltert und
  sortiert wird über den **ganzen** Bestand, geschnitten erst danach —
  andersherum zeigte Seite 1 die ersten fünfundzwanzig Zeilen der Datei statt
  die fünfundzwanzig besten. Ein Filterwechsel springt auf Seite 1: Seite 7
  eines anderen Ergebnisses ist keine sinnvolle Fortsetzung.
- **Die Übersicht zeigt den Bestand, nicht einen Ausschnitt davon**
  (14.09.2026). `nur bewertete` und `nur bearbeitete` standen im HTML auf
  `checked`; beim ersten Aufruf fehlten damit jede Gruppe ohne Score und jede
  ausgeschlossene — **471 von 621**, ohne dass irgendwo stand, warum. Wer
  danach eine bearbeitete Gruppe suchte, hielt sie für verschwunden. Der eigene
  Haken überlebt die Sitzung weiterhin; anders ist allein, womit die Seite
  anfängt. Eine Seite, die „Gruppen" heißt, zeigt die Gruppen.
- **Der Zähler nennt den Grund und den Weg zurück.** „4 von 621" sagt, *dass*
  etwas fehlt, und verschweigt, warum — bei dreizehn Filtern ist das keine
  Auskunft, sondern ein Rätsel. Daneben steht jetzt
  „150 ausgeblendet durch Stand: nichts getan" und ein Knopf
  „Filter zurücksetzen". Er nennt die Beschriftung, die im Feld steht, nicht
  den Feldnamen: Gesucht wird nach dem, was man gelesen hat.
- **Ein Wortschatz für den Stand.** Die Spalte zeigte „nicht gesendet" /
  „Anfrage gesendet", das Filterfeld nannte dieselben Werte „nichts getan" /
  „Beitritt angefragt" — zwei Namen für ein Feld. Wer den Text der Spalte im
  Filter suchte, fand ihn nicht und griff zum nächstbesten Eintrag; die Gruppe
  war weg. Beide lesen jetzt `status_label`, dieselbe Quelle, aus der auch die
  Schnittstelle ihre Beschriftungen nimmt.
- **Ein Beitrag ändert den Stand nicht.** `marketing_status` („wo stehen wir im
  Kooperationsweg?") und `post_status` („ist der Beitrag hinaus?") bleiben
  getrennt — deshalb findet man eine bearbeitete Gruppe weiterhin über ihren
  Stand, und wer sie über ihren Beitrag sucht, nimmt den Filter
  `Beitrag: veröffentlicht`. Zusammengelegt ließe sich die eine Frage nicht
  mehr lesen, ohne die andere zu verändern. Test:
  `test_ein_veroeffentlichter_beitrag_setzt_den_stand_nicht`.
- **Die Übersicht merkt sich, wo man war** (`sessionStorage`, Filter + Sortierung
  + Seite + Seitengröße + Scrollposition). Jede Änderung an einer Gruppe lädt die Seite neu; bei 314
  Zeilen hieß das vorher: Stadt neu wählen, Haken neu setzen, die Zeile
  wiederfinden — nach jedem einzelnen Klick. Gefiltert wird im Browser, also
  weiß nur der Browser, wo man war. `sessionStorage` und nicht `localStorage`:
  Der Stand gehört zu dieser Sitzung; wer morgen neu öffnet, will die
  Übersicht sehen und nicht den Filter von gestern. Jeder Zugriff ist in
  `try`/`catch` — in einem privaten Fenster wirft schon das Lesen, und eine
  Bequemlichkeit darf die Seite nicht mitreißen.

### Kommentarautomatik (`marketing/automatik.py`, `marketing/lauf.py`)

- **Zehn Kommentare je Gruppe aus fünf Vorlagen** (`ZIEL_JE_GRUPPE = 10`,
  `VORLAGEN_JE_TOPF = 5`). Die beiden Zahlen sind nicht dasselbe: Fassung 6
  trägt wieder Vorlage 1, aber `bisherige_post_urls` sorgt dafür, dass zwei
  gleiche Texte nie unter denselben Beitrag geraten.
- **Das Ziel wird beim Start eingefroren** (`automatik_lauf.ziel_je_gruppe`,
  Spaltenvorgabe 5). Ein laufender Vorgang behält deshalb die Zahl, die beim
  Einfrieren galt — wer `ZIEL_JE_GRUPPE` erhöht, sieht die neue Zahl erst im
  **nächsten** Lauf. Das ist derselbe Gedanke wie bei der eingefrorenen
  Kampagnenliste: Eine Bedingung, die unter einem laufenden Vorgang
  wegwandert, macht ihn unabschließbar. Der Fortschritt geht dabei nicht
  verloren — er wird aus `campaign_group_texte.status` **gelesen** und nicht
  im Lauf geführt.
- **Die Mitgliedschaft ist eine Vorbedingung mit Schalter**
  (`automatik.mitgliedschaft_pflicht`, seit 01.09.2026 auf `false`). Die Regel
  entstand am 31.08.2026: Von 36 zugeordneten Gruppen stand **keine** auf
  `mitglied`, und ein Lauf hätte 180 sicher scheiternde Versuche aus einem
  Konto gemacht — genau das Muster, das zur Sperre führt. Dagegen steht die
  Beobachtung des Nutzers: In `Betaraqiq-Test Syrer in Berlin` standen drei
  **veröffentlichte** Kommentare, während der Stand auf `beitritt_angefragt`
  stand. Der Vermerk beschreibt also unseren Arbeitsstand, nicht die Frage, ob
  Facebook dort schreiben lässt. Ausgeschaltet wird die Gruppe **versucht**,
  nicht als Mitglied behauptet: `Gruppenfortschritt.mitglied` bleibt die
  beobachtete Angabe, und was den Lauf jetzt begrenzt, sind
  `MAX_VERSUCHE_JE_FASSUNG` (3) und danach `erschoepft`. Die Vorgabe **im
  Code** bleibt `True` — der Schutz gilt, solange niemand etwas sagt; gesagt
  wird es in `settings.yaml`.
- **In jeder Gruppe zuerst der Beitrag, dann die Kommentare** (seit
  10.09.2026). Die Reihenfolge ist der Zweck: Der eigene Beitrag ist der
  Anlass und steht in der Gruppe; ein Kommentar haengt an einem fremden
  Beitrag. `lauf.naechster_schritt` gibt deshalb `Texttyp.POST` heraus,
  solange `campaign_groups.post_status` auf `offen` steht und es einen
  Beitragstext gibt (`store.fassungen_mit_text`) - danach die zehn
  Kommentare. Ein von Hand auf der Arbeitsseite abgesetzter Beitrag wird
  damit nicht ein zweites Mal abgesetzt: Es ist dieselbe Spalte.
- **Der Beitrag hat genau einen Anlauf je Lauf, der Kommentar drei.** Der
  Unterschied ist kein Versehen. Scheitert ein Kommentar, liegt das meist an
  *diesem* Beitrag (schon kommentiert, Feld nicht geladen) - der naechste
  kann gehen. Scheitert der Beitrag, liegt es an der Gruppe (keine Links
  erlaubt, Formular gesperrt), und ein zweiter Anlauf aus demselben Konto
  wiederholte nur, was gerade nachweislich nicht ging. `melde_vorschlag`
  setzt das Paar auf `fehlgeschlagen`, der Lauf geht **sofort** zu den
  Kommentaren weiter, und zurueckgeholt wird mit `campaign retry`. Tests:
  `test_ein_gescheiterter_beitrag_haelt_den_lauf_nicht_auf`,
  `test_in_jeder_gruppe_zuerst_der_beitrag_dann_die_kommentare`.
- **Fehlender Text heisst beim Beitrag etwas anderes als beim Kommentar.**
  Ohne Kommentartext ist die Gruppe erschoepft - es gibt nichts, was dort
  hingehen koennte. Ohne Beitragstext faellt nur der Beitrag aus; die zehn
  Kommentare haengen nicht daran. Beides in `kommentar_erschoepft` zu
  schreiben hiesse, wegen eines fehlenden Beitragstextes auf alle Kommentare
  zu verzichten (`automatik._ohne_text`).
- **Eine leere Kampagnenliste wird nie eingefroren.** Ein Lauf ohne
  Kampagnen kann nie `fertig` werden, bleibt auf `angehalten` stehen, und
  `offener_lauf` bietet ihn bei jedem Start wieder an. Am 10.09.2026 ist
  genau das passiert: Die letzte aktive Kampagne wurde beim Abschluss auf
  `completed` gesetzt, der naechste Start fror nichts ein und meldete
  "Kampagnen 0 / 0" - und von da an tat die Automatik nichts mehr.
  `hole_oder_starte_lauf` liefert dafuer `(0, False)`, und der Aufrufer sagt
  den Grund. Test: `test_ohne_aktive_kampagne_entsteht_kein_lauf`.
- **Eine leere Kampagne ist erledigt, aber nicht erfolgreich**
  (`Kampagnenfortschritt.leer`). Wird eine Kampagne geloescht, waehrend ein
  Lauf offen ist, bleibt ihre Kennung in der eingefrorenen Liste stehen,
  waehrend ihre Zuordnungen per CASCADE verschwinden - derselbe Lauf haenge
  sonst fuer immer bei "0 / 1". `leer` beendet den **Lauf**, setzt die
  Kampagne aber nicht auf `completed`: Sie hat nie etwas bekommen. Die
  Abschlussmeldung nennt beides getrennt. Test:
  `test_eine_leere_kampagne_haelt_den_lauf_nicht_auf`.
- **Beitragsadressen werden zweistufig gesucht** (`urls.beitragslinks`,
  `actions.fetch_top_posts`). Zuerst je Artikel (`div[role='article']`), denn
  nur dort stehen Reaktionen und Kommentarzahlen - nach ihnen wird der beste
  Beitrag ausgewaehlt. Findet das nichts, wird die **ganze Seite** nach
  Beitragsverweisen abgesucht, mit Kennzahlen 0: Am 10.09.2026 meldete eine
  Gruppe mit zwei sichtbaren Beitraegen "keine Beitraege zum Kommentieren
  gefunden" - die Artikel waren da, nur passte kein Verweis darin auf das
  Muster. Eine geratene Kennzahl waere schlimmer als eine fehlende, deshalb 0
  und nicht ein Schaetzwert.
- **Eingesammelt wird waehrend des Scrollens, nicht danach.** Facebooks
  Beitragsstrom ist virtualisiert: Was aus dem Blick geraet, wird wieder aus
  dem DOM ausgehaengt. Die erste Fassung scrollte viermal um 1200 Pixel und
  suchte **dann** - in einer Gruppe mit zwei Beitraegen stand man damit hinter
  dem Ende des Stroms, und es war nichts mehr da: "Found 0 articles.",
  waehrend das Warten auf `div[role='article']` kurz zuvor noch angeschlagen
  hatte. Jetzt wird nach jedem kleinen Schritt gelesen und das Gefundene
  behalten; der Lauf hoert auf, sobald genug beisammen ist.
- **`canonical_post_url` schneidet `__cft__` und `__tn__` ab.** Die Parameter
  aendern sich bei jedem Laden; blieben sie stehen, saehe derselbe Beitrag in
  jedem Durchgang neu aus - und ein bereits kommentierter galte als
  unkommentiert (`bisherige_post_urls` vergleicht Zeichenketten). Aus
  demselben Grund werden auch nicht-numerische Kennungen (`pfbid...`)
  uebernommen: Sie fielen vorher durch die Erkennung und blieben als
  Rohadresse mit Parametern stehen.
- **Der Schalter wirkt dort, wo der Bestand liegt.** Beim Fernbetrieb
  (`campaign automatik --server`) liest der **Server** den Stand über
  `/automatik/naechster`; die Konfiguration des Arbeitsrechners ist dabei
  belanglos. Eine Änderung an `settings.yaml` wirkt erst nach
  `bash ./ausrollen.sh`.

### Zielpriorität: welche Gruppe zuerst (`marketing/zielgruppe.py`)

```
A  Reise- und Versandgruppen mit genanntem Ziel   → zuerst, Beitritt, alles
B  syrische/arabische Gemeinschaft in Deutschland → danach, Beitritt, nur bei Bezug
C  irgendein Bezug, aber keiner von beiden        → nur der Einzelfall, kein Beitritt
D  kein erkennbarer Bezug                         → gar nicht
```

Seit dem 14.09.2026 ordnet **innerhalb** jeder Klasse eine zweite Achse: erst
Deutschland, dann das übrige Europa, dann die Gruppen ohne genanntes Land.
Siehe „Deutschland zuerst, dann Europa".

Seit 13.09.2026. Der Anlass steht in einem Satz: Bis dahin sortierte der Lauf
allein nach `sort_by_rank`, also nach dem Score. Der beantwortet „welche Gruppe
ist gut?" — **nicht** „welche Gruppe ist die richtige". Eine
Gemeinschaftsgruppe mit 40.000 Mitgliedern stand damit vor einer Reisegruppe
mit 900, und die 900 sind genau die Menschen, die einen Mitnehmer suchen.

- **Gerechnet, nicht gespeichert.** Keine Spalte hält die Klasse; sie ergibt
  sich bei jedem Lesen aus Name, Beschreibungstext, Kategorie, Zielgruppe und
  Stadt. Das ist die Antwort auf die ausdrückliche Anforderung *„nicht nur ein
  Feld `priority` speichern, das später keine Wirkung hat"*: Ein Feld könnte
  veralten, eine Rechnung kann es nicht. Dieselbe Überlegung wie bei
  `qualifikation.beurteile` und beim Lauffortschritt.
- **Sie wirkt an drei Stellen, und nur deshalb gibt es sie.**
  `Kampagnenfortschritt.arbeitsliste` sortiert **zuerst** nach ihr (dann
  `vorrang`, dann Score — stabil, damit der Score innerhalb einer Klasse weiter
  entscheidet); `Gruppenfortschritt.bearbeitbar` schließt `D` aus;
  `beitritt_offen` lässt nur `A` und `B` durch.
- **Beitreten ist „aktiv bearbeiten", und das ist bei `C` ausgeschlossen.** In
  einer allgemeinen Gruppe passt höchstens einmal ein einzelner Beitrag; dafür
  beizutreten wäre die riskanteste Handlung des Projekts für die schwächste
  Aussicht. Kommentiert werden darf dort trotzdem — mit der strengsten
  Schwelle.
- **`A` verlangt Thema **und** Ziel.** „Reisegruppe" allein kann Thailand
  meinen, „Syrien" allein ist eine Nachrichtenseite. Erst beides zusammen
  beschreibt den Markt der App. Das Thema muss dabei **im Namen** stehen oder
  als Kategorie erkannt sein: Ein Beitrag über ein Paket macht aus einer
  Wohnungsgruppe keine Versandgruppe — dieselbe Gewichtung wie überall
  (`name_confidence` 1,0 gegen `snippet_confidence` 0,5).
- **Die Nebenkategorien zählen wie die Hauptkategorie.** `classify_category`
  kürt **einen** Sieger, und bei zwei gleich starken Treffern gewinnt der, der
  bei der Klassifikation weiter oben stand. Welches von beiden das ist, war
  eine Eigenschaft der Datei und keine der Gruppe. Seit dem 20.09.2026 steht
  die Kategorie im Bestand und wird gepflegt statt gekürt.
- **Der Unterschied zwischen `B` und `C` ist der Deutschlandbezug.** Eine
  Gruppe über Syrien, die Deutschland nie erwähnt, ist keine Gemeinschaft
  *hier*. Erkannt wird er an `Merkmale.stadt` (aus dem Bestand) oder an einem
  Herkunftswort aus `settings.yaml` — eine Gemeinschaftsgruppe nennt beides fast immer, denn sie
  heißt danach.
- **Die Vorgabe ohne Angabe ist `B`, nicht `D`.** Wer einen `Gruppenfortschritt`
  ohne Einstufung baut — ein Test, ein älterer Aufrufer —, hat nichts über die
  Gruppe gesagt, und daraus einen Ausschluss zu machen wäre dieselbe Art Fehler
  wie eine Erlaubnis aus ungelesenen Regeln. `B` ist genau die Behandlung, die
  vor dem 13.09.2026 jede Gruppe bekam.
- **`D` heißt übersprungen, nicht gelöscht.** Die Gruppe bleibt im Bestand,
  behält ihren Tracking-Code und wird im nächsten Lauf neu beurteilt — dieselbe
  Behandlung wie bei einer gesperrten Gruppe.
- **Die Begriffe stehen seit dem 20.09.2026 in einer Datei**: `settings.yaml`
  unter `marketing.zielprioritaet`. Kategorie- und Zielgruppenbegriffe gab es
  daneben in `categories.yaml`/`audiences.yaml`; mit ihnen ist der Abgleich
  gegen den **Namen** einer Gruppe entfallen — maßgeblich sind jetzt
  `kategorie`, `nebenkategorien` und `audiences` aus dem Bestand. `regeln_aus_config` ist die
  **einzige** Stelle des Moduls, die Konfiguration liest; alles darunter ist
  ohne sie prüfbar (wie `grenzen.einstellungen`). Eine Kategorie, die im
  **Bestand** nicht vorkommt, meldet `config-check` — sonst verschwände die
  Klasse A still, und die Kampagne arbeitete wieder in den
  Gemeinschaftsgruppen. Bei leerem Bestand wird nicht gewarnt: Sonst meldete
  ein frisches Projekt jeden richtigen Eintrag als Tippfehler.
- **`versand` und `reise` sind die beiden Kategorien der Klasse A.** Zwei
  getrennte, weil wer **schickt** einen Reisenden sucht und wer **reist** etwas
  mitnehmen kann — die beiden Hälften desselben Marktplatzes. Sie standen bis
  zum 20.09.2026 ganz oben in `categories.yaml`, weil die Reihenfolge dort bei
  Gleichstand entschied: „Syrer in Berlin – Reisen nach Damaskus" traf
  `community` und `reise` mit je einem Namenstreffer, und hinten stehend
  gewänne die Gemeinschaft. Diese Kürung gibt es nicht mehr — die Kategorie
  steht im Bestand und wird gepflegt. Die Gruppe aus dem Beispiel ist damit
  das, was jemand eingetragen hat, und nicht das, was eine Dateireihenfolge
  ergab. Genannt werden die beiden Kennungen in `settings.yaml` unter
  `marketing.zielprioritaet.kategorien`; `config-check` gleicht sie gegen den
  Bestand ab.

### Deutschland zuerst, dann Europa (`zielgruppe.Region`)

```
A · DE          Reise/Versand nach Syrien, Deutschland genannt   → zuerst
A · EU          dasselbe aus Österreich, Schweden, ...           → danach
A · unbekannt   dasselbe ohne genanntes Land                     → danach
B  · …          Gemeinschaftsgruppen                             → erst dann
C  · ausserhalb „نقل من لبنان إلى سورية" — nicht unsere Strecke
```

Seit 14.09.2026. Der Anlass steht in einer Zeile aus dem Bestand:
`FB-SYR-DE-024 · نقل من لبنان إلى سورية · Versand & Mitnahme` — eine
lupenreine Versandgruppe mit genanntem Ziel, und eine Strecke, auf der diese
App niemandem hilft: Ihre Nutzer sitzen in Europa. Nach Klasse allein
sortiert stand sie vor den deutschen Gruppen.

- **Eine zweite Achse, keine neuen Klassen.** `A-DE`, `A-EU`, `A-sonst` wären
  drei Werte für zwei Fragen; jede Auswertung, jeder Filter und jede
  Beschriftung müsste sie wieder auseinandernehmen. Dieselbe Überlegung wie
  bei `GroupMarketing.bearbeiten` neben `marketing_status`.
- **Die Klasse steht vor der Region.** `Zielbefund.rang` ist ein **Paar**
  `(Klasse, Region)` und keine Summe: Eine deutsche Gemeinschaftsgruppe kommt
  nicht vor eine österreichische Versandgruppe, nur weil sie in Deutschland
  ist. Das Land ordnet innerhalb einer Klasse, es hebt keine Klasse an.
  Daraus folgt genau die geforderte Reihenfolge — erst **alle** A in
  Deutschland, dann **alle** A in Europa, und erst danach B. Ein niedriger
  Score einer A-Gruppe bringt nie eine B-Gruppe nach vorn; der Score
  entscheidet erst auf der vierten Stufe.
- **`UNBEKANNT` steht vor `AUSSERHALB`, und das ist der Punkt.** Eine Gruppe,
  die kein Land nennt, ist keine Gruppe außerhalb Europas — sie ist eine, über
  deren Land wir nichts wissen. Die meisten deutschen Gruppen heißen
  „شحن الى سوريا" und schreiben Deutschland nirgends hin; sie hinter eine
  libanesische Versandgruppe zu stellen hieße, sie für eine Auslassung zu
  bestrafen. Dieselbe Unterscheidung wie beim Score, bei der Aktivität und
  bei der Resonanz: „nicht gemessen" ist etwas anderes als „gemessen und
  schlecht".
- **Nur `AUSSERHALB` ändert die Klasse** — nach `C`, nicht nach `D`. Die
  Gruppe ist nicht bezuglos: Ein einzelner Beitrag, in dem jemand aus
  Deutschland schreibt, bleibt erreichbar. Was sie verliert, ist die
  Beitrittsanfrage (`BEITRITT_WERT`) und der Vortritt — und genau darum geht
  es. Als `D` wäre sie dauerhaft draußen, und das ist ein Urteil, das die
  Datenlage nicht trägt.
- **Ein europäisches Wort schlägt ein außereuropäisches.** „مشاوير برلين -
  بيروت - دمشق" ist eine Berliner Gruppe; sie wegen des Zwischenstopps
  herabzustufen hieße, eine richtige Gruppe an ein Detail zu verlieren. Nur
  wer **allein** die andere Seite nennt, meint die andere Strecke.
- **Die deutschen Städtenamen zählen als Deutschlandbeleg**, zusätzlich zu
  `Merkmale.stadt`. Jene ist die *erkannte* Stadt aus dem Bestand und der
  bessere Beleg; diese fängt den Fall ab, in dem nie eine eingetragen wurde.
  Sie standen bis zum 20.09.2026 in `cities.yaml` und stehen seither in
  `settings.yaml` unter `marketing.zielprioritaet.herkunft` — mitgenommen
  statt verloren: „مشاوير برلين - بيروت - دمشق" ist eine Berliner Gruppe, und
  ohne „برلين" stünde sie als „Land unbekannt" hinter einer libanesischen.
- **Syrien steht in `ziele` und in keiner Länderliste.** Stünde es in
  `ausserhalb`, fiele der ganze Zielmarkt aus Klasse A — jede Gruppe darin
  nennt ihr Ziel. `config-check` meldet die Kollision als Fehler, ebenso ein
  Wort, das in `europa` **und** `ausserhalb` steht (Europa gewinnt, der
  zweite Eintrag wirkt nie).
- **Eine leere `ausserhalb`-Liste ist der stillste Fehler hier.** Es sieht
  alles richtig aus, nur fällt keine Gruppe mehr aus dem Zielmarkt heraus.
  `config-check` zählt deshalb beide Listen, statt sie nur zu nennen.
- **Sie wirkt an denselben drei Stellen wie die Klasse.**
  `Kampagnenfortschritt.arbeitsliste` sortiert danach (Klasse → Region →
  Vorrang → Score), `beitritt_kandidaten` folgt der Arbeitsliste, und
  `arbeit.arbeitsreihenfolge` (die Seite des Menschen) liest denselben
  `Zielbefund.rang` — zwei Rangfolgen für dieselbe Kampagne hießen, dass der
  Mensch an einer anderen Gruppe arbeitet als die Automatik.
- **Gerechnet, nicht gespeichert** — wie die Klasse selbst. Keine Spalte hält
  die Region; sie ergibt sich bei jedem Lesen aus Name, Beschreibungstext und
  erkannter Stadt.
- In der Übersicht steht sie **in derselben Zelle** wie die Klasse
  (`A DE`, `A EU`, `C ✗`), dazu ein eigener Filter „Jedes Land". `unbekannt`
  bekommt bewusst kein Zeichen: Ein Vermerk, der in zwei Dritteln der Zeilen
  steht, sagt nichts mehr. Die Kachel nennt `A – Deutschland / Europa`
  getrennt neben `A – gesamt`: „20 A-Gruppen" beantwortet nicht, ob genug
  davon in Deutschland stehen.

### Erst die Regeln lesen, dann beitreten (`Schrittart.REGELN`)

Seit 13.09.2026. Vorher ging die Beitrittsanfrage an jede Gruppe, und ob dort
überhaupt kommentiert werden darf, stellte sich Tage später heraus — nach der
Aufnahme, nach dem ersten Versuch, nach dem ersten Fehlschlag.

- **Der Regelschritt steht vor der Anfrage und kostet am wenigsten.** Ein
  Seitenabruf, keine Handlung in der Gruppe: Niemand sieht ihn, nichts wird
  geschrieben, kein Kontingent verbraucht. Genau deshalb gehört er nach vorn —
  er entscheidet am meisten und kostet am wenigsten.
- **Er fragt `beitritt_frei` nicht.** Regeln zu lesen kostet keine
  Beitrittsanfrage. Wer das an die Tagesmenge der Anfragen bände, läse an einem
  erschöpften Tag gar nichts — und stünde am nächsten Morgen wieder vor
  derselben ungelesenen Gruppe.
- **Je Gruppe, nicht als Vorlauf über den Bestand.** `regeln_offen` liefert
  höchstens **zwei** Gruppen: die nächste, an die eine Anfrage geht, und die
  nächste, in der gearbeitet wird. Der Ablauf ist damit verzahnt — Regeln von
  Gruppe 1 lesen, in Gruppe 1 posten und kommentieren, Regeln von Gruppe 2
  lesen, und so fort.
  Zuerst stand hier die vollständige Liste, und die Phase galt der ganzen
  Kampagne. Bei einer Kampagne mit **254 Gruppen** las der Lauf damit eine
  halbe Stunde, bevor der erste Beitrag hinausging. Verlangt war aber „vor der
  Beitrittsanfrage die Regeln prüfen" — je Gruppe. Derselbe Abruf, dieselbe
  Reihenfolge, nur verzahnt statt gestapelt. Test:
  `test_die_regeln_werden_je_gruppe_gelesen_nicht_als_vorlauf`.
- **Gefragt wird nur für Gruppen, die der Lauf anfasst** (A oder B beim
  Beitritt, `bearbeitbar` bei der Arbeit). Die Regeln einer Gruppe zu lesen,
  in der weder gearbeitet noch beigetreten wird, kostet einen Seitenabruf für
  nichts.
- **`beitritt_kandidaten` steht neben `beitritt_offen`.** Jenes sind die
  Gruppen, die eine Anfrage verdienen; dieses die, bei denen sie jetzt
  hinausgehen darf. Die Regelprüfung liegt dazwischen — ohne die Trennung
  wäre die Gruppe, deren Regeln noch zu lesen sind, gar nicht auffindbar.
- **Ein nicht gelesener Befund schreibt nichts** (`store.merke_regeln`). Eine
  Anmeldewand ist kein Beleg dafür, dass eine früher gelesene Regel weg ist —
  dieselbe Überlegung wie bei `upsert_groups` mit `COALESCE`. Damit dieselbe
  Gruppe trotzdem nicht bei jedem Durchgang wiederkommt, wird sie für **diesen**
  Lauf übersprungen: ein Übersprung, kein Urteil.
- **`beitritt.regeln_zuerst: false` ist der Ausweg**, und er hat einen echten
  Anlass: Lässt sich eine Gruppenseite nie lesen, bliebe `regeln_gelesen_am`
  leer, und die Anfrage ginge nie hinaus. Die Vorgabe **im Code** ist `True` —
  der Schutz gilt, solange niemand etwas sagt; gesagt wird es in
  `settings.yaml`. Dieselbe Aufteilung wie bei `automatik.mitgliedschaft_pflicht`.
- **Ohne Leser keine Regelschritte.** Fehlt `regeln_lesen` (ein Treiber ohne
  Browser), setzt `fuehre_lauf_aus` die Pflicht aus — dieselbe Überlegung wie
  bei `beitreten is None`. Sonst bliebe jede Gruppe mit ungelesenen Regeln
  liegen, und der Lauf täte gar nichts, obwohl an ihm nichts fehlt außer einer
  Fähigkeit, die er nie hatte.
- **Eine Gruppe, deren Regeln alles ausschließen, bekommt keine Anfrage**
  (`Qualifikation.UNGEEIGNET`). Ein Beitritt zu einer Gruppe, in der nichts
  stehen darf, wäre ein Handgriff ohne Zweck.
- **Im Fernbetrieb wertet der Arbeitsrechner aus, nicht der Server.**
  `POST /automatik/regeln/ergebnis` nimmt den **Befund** entgegen (vier
  Wahrheitswerte), nicht die Seite: Die Seite über den Tunnel zu schicken hieße,
  ein halbes Megabyte HTML zu übertragen, damit der Server vier Flags daraus
  liest. Ausgewertet wird mit derselben reinen Funktion (`lies_regeln`) — zwei
  Auswertungen könnten abweichen.

### Der Text folgt dem Beitrag, nicht der Gruppe (`inhalt.Anlass`)

Seit 13.09.2026. Bis dahin stand der Kommentartext fest, **bevor** ein Beitrag
gelesen war: fünf Fassungen je Gruppe, gewählt nach der Gruppenkennung. Er
passte deshalb auf jeden Beitrag gleich gut, also auf keinen.

- **Acht Anlässe, und `KEINER` ist einer davon.** `sucht_reisenden`,
  `geschenk`, `gegenstand`, `versandweg`, `platz_im_koffer`, `bietet_mitnahme`,
  `medikamente` — und der Rest. Der Anlass ist die Brücke zwischen Beitrag und
  Vorlage: Er sagt, *welche* der vorbereiteten Antworten hier etwas beiträgt.
- **Er ist zugleich die Schranke.** „Ich fliege nächste Woche nach Syrien"
  ergibt **keinen** Anlass und damit keinen Kommentar; „… und habe noch Platz
  im Koffer" ergibt `PLATZ_IM_KOFFER`. Derselbe Satz, ein Halbsatz mehr, und
  erst der macht aus einer Reiseankündigung eine Gelegenheit. Abschaltbar über
  `marketing.anlass_pflicht` — die Erkennung ist eng gefasst, und wer feststellt,
  dass zu wenig hinausgeht, ändert eine Zahl und keinen Code.
- **Die Reihenfolge der Erkennung ist die Genauigkeit.** „Ich reise am Dienstag
  und kann Medikamente mitnehmen" trägt drei Anzeichen; die Antwort soll die
  zum Medikament sein — sie ist die einzige, die etwas sagt, was die anderen
  nicht auch sagen (und sie trägt den Satz mit, dass über die Einfuhr nicht die
  App entscheidet).
- **Ein Anlass entsteht nur bei Versand- oder Reisethema.** Ohne das ist ein
  Geschenk ein Geburtstag und ein Dokument ein Behördengang. Die einzige
  Ausnahme ist die ausdrückliche Suche nach einem Reisenden („مين مسافر ع
  الشام") — wer das schreibt, hat sein Thema mitgeliefert, auch wenn die
  Themenerkennung es anders einsortiert.
- **Die Texte stehen in `textvorlagen.yaml` unter `anlaesse:`**, einem eigenen
  Zweig neben `vorlagen:`. Jene gehören einer **Gruppe** und stehen fest, bevor
  ein Beitrag gelesen wurde; diese gehören einem **Anlass**. Ein gemeinsamer
  Topf hätte zwei Dinge vermischt, die zu verschiedenen Zeitpunkten gewählt
  werden. Es sind die elf Fassungen des Nutzers (13.09.2026), unverändert.
- **Kein Vorrat zu einem Anlass heißt: kein Kommentar**, nicht „irgendeine
  andere Fassung". Eine Ersatzfassung aus einem anderen Topf wäre eine Antwort
  auf eine andere Frage. Gezählt wird es als `kein_anlass` — ein Ergebnis, kein
  Fehlversuch, und damit weder gegen die Fassung noch gegen die Gruppe.
- **`{link}` wird aufgelöst, bevor der Text hinausgeht — und das war der
  teuerste vergessene Schritt des Projekts** (14.09.2026). Der Anlasstext
  ersetzt seit dem 13.09.2026 den vorbereiteten Text; der vorbereitete war
  durch `beitrag.mit_link` gegangen, der neue nicht. Zwischen „Text wählen"
  und „Text absenden" fehlte die Ersetzung ganz, und in einer Gruppe stand:

  ```
  فيك تنشر طلبك على بطريقك وتشوف إذا في مسافر مناسب.
  {link}
  ```

  Der Kommentar sah richtig aus, und seine Gruppe bekam nie einen Klick
  gutgeschrieben. Aufgelöst wird jetzt in `entscheide_und_kommentiere`,
  unmittelbar vor dem Absenden, mit `beitrag.setze_adresse` — **derselben**
  Ersetzung, die auch `mit_link` benutzt.
- **Die Adresse reist getrennt vom Text** (`link_url`). Sie steht neben
  `text` in der Nutzlast und nicht darin: Wählt der Arbeitsrechner gleich
  einen anderen Text, ist die im vorbereiteten eingesetzte Adresse nicht mehr
  erreichbar. Gespeichert wird weiterhin die **Fassung mit Platzhalter** —
  aufgelöst wird beim Lesen, nie beim Ablegen; sonst stünde der Tracking-Code
  in der Datenbank.
- **Ein offener Platzhalter verhindert den Kommentar** (`beitrag.
  offene_platzhalter`). Lieber kein Kommentar als ein kaputter: Ein Text mit
  `{link}` sieht richtig aus und ist wertlos, und zurückholen lässt er sich
  nicht. Gebucht wird das als `kein_anlass` — es ist ein Fehler bei uns und
  kein Urteil über die Gruppe. Tests:
  `test_der_abgesetzte_text_traegt_die_adresse_und_nicht_den_platzhalter`,
  `test_ohne_adresse_wird_lieber_nicht_kommentiert`.
- **Der wirklich abgesetzte Text wird in die Fassung zurückgeschrieben**
  (`store.merke_verwendeten_text`, **vor** `melde_vorschlag`). Sonst stünden
  zwei Texte für denselben Vorgang im Bestand, und die Übersicht zeigte den,
  der nie hinausging. `melde_vorschlag` spiegelt den Text bei Erfolg ins Paar —
  danach einzutragen wäre zu spät.
- **Der Rückfall auf den vorbereiteten Text greift nur, wenn es zu diesem
  Anlass gar keinen Vorrat gibt** — etwa in einer deutschen Kampagne, für die
  `anlaesse.de` noch fehlt. Ohne ihn stünde eine Sprache ohne Anlassvorrat
  still, und das wäre eine Änderung, die niemand angeordnet hat.

### Link-Modus: kein Link ist die Vorgabe (`entscheidung.Linkmodus`)

```
NO_LINK         nicht einmal der Name   → helpful_reply, private_contact
APP_NAME_ONLY   „بطريقك", ohne Adresse  → contextual_app_mention   (Regelfall)
TRACKING_LINK   mit {link}              → direct_app_recommendation
```

- **Es ist keine eigene Entscheidung, sondern die Kehrseite von `Antwortart`.**
  Wer die App nicht nennt, trägt auch keinen Link; wer sie mit Link empfiehlt,
  nennt sie erst recht. Eine zweite Entscheidung daneben könnte von der ersten
  abweichen — und dann stünde ein Link in einer Antwort, die gar nichts von uns
  tragen sollte.
- **Der Vorrat trägt keinen Link.** `anlasstext` hängt `{link}` in einer
  eigenen Zeile an, wo die Gruppe ihn erlaubt — mechanisch, nicht erfunden.
  Aufgelöst wird er weiterhin erst in `beitrag.mit_link`: Der gespeicherte Text
  trägt den Platzhalter, nie den Code.
- **`NO_LINK` hat keinen Vorrat, und das ist Absicht.** Für die bloße Hilfe und
  den privaten Hinweis hat der Nutzer keine Texte geschrieben; sie zu erfinden
  wäre das Gegenteil dessen, was „keine KI" bedeutet. Dort wird deshalb nicht
  kommentiert. Wer das ändern will, schreibt Fassungen — keinen Code.
- **`mit_link` ist ohnehin die engste Bedingung im Haus**: Sie verlangt
  gelesene Regeln ohne Linkverbot, keine `OHNE_LINKS`-Beobachtung, belegten
  Bezug (`HOCH`) und ein Thema, das die App betrifft. Erst seit es einen Vorrat
  **ohne** Link gibt, wirkt sie überhaupt — vorher trug jede Kommentarfassung
  einen, und eine linkscheue Gruppe fiel damit ganz aus.

### Was ein Beitrag hergeben muss, hängt am Ort (`entscheidung.Anspruch`)

| Klasse | Mindestrelevanz | zusätzlich          |
|--------|-----------------|---------------------|
| `A`    | `mittel`        | –                   |
| `B`    | `hoch`          | –                   |
| `C`    | `hoch`          | genannte **Strecke** |
| `D`    | —               | es wird nicht geantwortet |

In einer Reisegruppe ist die Frage nach einem Koffer das Thema des Hauses; in
einer Gemeinschaftsgruppe ist sie eine unter fünfzig, und in einer allgemeinen
Gruppe ist sie ein Zufall. Dieselbe Antwort ist an der einen Stelle hilfreich
und an der anderen eingeworfene Werbung — der Unterschied liegt nicht am Satz,
sondern am Ort. Die Werte stehen in `settings.yaml`
(`marketing.zielprioritaet.mindestrelevanz`).

`entscheide` nennt die beiden Fälle getrennt: „kein Bezug zum Angebot" ist ein
Urteil über den Beitrag, „Bezug zu schwach für diese Gruppe" eines über den
Ort. Wer beides gleich benennt, sieht später im Protokoll nicht, ob die Gruppe
nichts hergibt oder ob wir dort nur strenger sind.

### Grenzen und Duplikate

- **`limits.comments.je_gruppe_taeglich` schützt die Gruppe, `daily` das
  Konto.** Fünf Kommentare am Tag, alle in derselben Gruppe, sind für deren
  Leser dasselbe Bild wie fünfzig. Sie greift **vor** der Vorlagenwahl: Ist die
  Zahl erreicht, wird die Gruppe für heute übersprungen und **nicht** als
  erschöpft vermerkt — das wäre ein Urteil über sie statt über unseren Takt.
- **`0` heißt hier ohne Schranke**, anders als bei `daily` (dort „gar nicht").
  Das ist Absicht: Eine Aktion abzuschalten ist eine Entscheidung, eine
  Schranke wegzulassen heißt nur, dass die Tagesmenge allein zählt.
- **Werte seit 13.09.2026** (Wunsch des Nutzers): 5 Kommentare am Tag, höchstens
  1 je Gruppe, 20–60 Minuten Abstand, 3 Beitrittsanfragen. Ziel ist
  ausdrücklich *wenige, aber relevante* Kommentare.
- **Duplikatkontrolle auf drei Ebenen.** Beitrag: `bisherige_post_urls` (nie
  zweimal unter denselben Beitrag). Text: `store.verwendete_vorlagen` über
  **alle** Kampagnen (nie zweimal derselbe Satz in derselben Gruppe) —
  `anlasstext` geht dann der Reihe nach weiter. Person: **gibt es nicht**, und
  zwar nicht aus Versehen — sie bräuchte Autorennamen, und die speichert dieses
  Projekt nicht (harte Projektgrenze). Was an ihrer Stelle wirkt, ist die
  Tagesmenge je Gruppe.

### Was Facebook geantwortet hat (`qualifikation.Ablehnungsgrund`)

`Ausgangsart` beantwortet die Frage, die der **Lauf** stellt: Zählt das gegen
die Gruppe, gegen das Konto oder gegen gar nichts? `Ablehnungsgrund`
beantwortet die Frage, die ein **Mensch** stellt, wenn er hinterher in die
Übersicht sieht: Was ist da eigentlich passiert?

- **Zwei Aufzählungen für zwei Fragen, nicht eine für beide.** Der Lauf darf
  nicht feiner unterscheiden, als er handeln kann — sonst hätte jede neue
  Meldung von Facebook eine neue Verzweigung zur Folge. Der Bericht darf und
  soll es. `grund` baut auf `klassifiziere` auf und läuft ihr nicht davon.
- **Gespeichert in `post_versuche.grund`** (Migrationsschritt 22), **neben**
  dem Fehlertext und nicht statt seiner: Der Text ist der Beleg, die Einstufung
  die Auskunft. Alte Zeilen bleiben leer — sie nachträglich einzustufen hieße,
  ein Urteil über Antworten zu fällen, die niemand mehr nachlesen kann
  (dieselbe Zurückhaltung wie bei Schritt 15 und den alten Scores).
- **Gerechnet wird in `beende_versuch`**, nicht beim Aufrufer: Jeder Weg, der
  einen Versuch beendet — Arbeitsseite, Automatik, Fernbetrieb —, kommt dort
  durch, und eine Einstufung, die an drei Stellen gerechnet wird, ist drei
  Einstufungen.
- **`UNKNOWN_FACEBOOK_RESPONSE` ist der ehrliche Rest.** `klassifiziere`
  endet im Zweifel bei `TECHNISCH`, und das hat zwei Gründe: Entweder stand ein
  technisches Wort darin, oder gar keines. Für die Handlung ist das dasselbe,
  für den Bericht nicht — das eine ist eine Auskunft, das andere ein
  Eingeständnis.
- **Ein technischer Fehler wird nie zu einer Gruppenregel.** Die teuerste
  Verwechslung, die dieses Projekt gemacht hat: Am 11.09.2026 ließ ein
  geschlossenes Browserfenster dreißig Fassungen scheitern, und danach galten
  45 Gruppen als erschöpft.

### Die Übersicht bekommt eine vierte Achse

„Wo stehen wir?" (`marketing`), „arbeiten wir daran?" (`bearbeiten`), „darf
hier etwas stehen?" (`qualifikation`) — und seit dem 13.09.2026 **„gehört
diese Gruppe zum Zielmarkt?"** (`zielprioritaet`). Vier Fragen, vier Spalten.

- Die Spalte „Ziel" zeigt nur den Buchstaben; Beschriftung, Grund und die
  Facebook-Antworten stehen im Titel. Sie steht in jeder der 314 Zeilen, und
  „A – Reise & Versand" wäre dort breiter als der Gruppenname.
- Das Filterfeld „Jede Priorität" ist der häufigste Griff: *zeig mir die
  A-Gruppen*. Es wird im `sessionStorage` mitgemerkt wie die übrigen Filter.
- Drei Kacheln nennen die Verteilung (A / B / C+D). Sie beantworten die Frage,
  wegen der es die Einstufung gibt: Haben wir überhaupt genug Gruppen im
  Zielmarkt? „20 A-Gruppen" ist eine Auskunft, „314 Gruppen" ist keine. Sie
  entstehen aus **denselben** Befunden wie die Zeilen — ein zweiter Lauf über
  die Gruppen könnte davon abweichen.

### Der Kampagnenablauf und seine Reihenfolge (`marketing/lauf.py`)

```
Kampagne waehlen → Gruppenregeln lesen → Beitrittsanfragen → Neubewertung
   → beste Gruppen zuerst (A vor B vor C) → Beitrag, dann Kommentare
   → naechste Kampagne
```

und ausdrücklich **nicht** „alle Gruppen irgendwie bearbeiten, später
beitreten, irgendwann qualifizieren". Seit 12.09.2026; der Regelschritt und
die Zielprioritaet kamen am 13.09.2026 dazu.

- **Die Reihenfolge steht an genau einer Stelle: `lauf.naechster_schritt`.**
  Kommandozeile, Dienst und Fernbetrieb fragen alle dort; keiner von ihnen
  kennt die Reihenfolge, jeder führt nur aus, was herauskommt. Eine zweite
  Fassung für das Web wäre eine zweite Reihenfolge — und dann arbeitete der
  Fernbetrieb anders als der örtliche Lauf, ohne dass es jemandem auffiele.
  Deshalb ist auch die **Neubewertung ein Schritt** (`Schrittart.BEWERTEN`)
  und kein Nebenbei: Sie braucht keinen Browser, aber sie hat einen Platz in
  der Folge, und der gehört dorthin, wo die Folge steht.
- **`--neu` ist der Ausweg aus der eingefrorenen Liste.** Ein Lauf behält
  seine Kampagnen (Punkt 16) — und genau das wird zum Hindernis, sobald
  Kampagnen **dazukommen**: Sie kämen nie dran, und von außen sieht das aus,
  als täte die Automatik nichts. `campaign automatik --neu` schließt den
  offenen Lauf ab und friert eine frische Liste ein; im Fernbetrieb trägt der
  **erste** Aufruf von `/automatik/naechster` das Feld `neu` (jeder weitere
  schlösse sonst den Lauf, den er gerade abarbeitet — ein Lauf, der sich
  selbst neu startet, wird nie fertig). Verloren geht dabei nur die Liste:
  Der Fortschritt steht in den Fassungen und wird gelesen, nicht geführt.
  Zurückgesetzt wird allein die Übersprungsliste dieses Laufs — und das ist
  der Sinn der Sache. `campaign automatik --status` nennt deshalb die
  eingefrorenen Kampagnen **namentlich** und sagt, welche aktiven fehlen: Die
  Zahl „Kampagnen 0 / 1" beantwortet die Frage nicht.
- **Beitrittsanfragen gehören der Kampagne, nicht dem Bestand.**
  `store.beitrittskandidaten(campaign_id)` fragt nach den Gruppen **dieser**
  Kampagne; `gruppen_ohne_anfrage` (der ganze Bestand, für `campaign
  beitritt`) bleibt daneben stehen. Ohne die Einschränkung gingen die fünfzig
  Anfragen des Tages an die bestbewerteten Gruppen überhaupt — und die
  Kampagne, an der gerade gearbeitet wird, bekäme keine einzige.
- **Der Takt hält die Anfrage an, nicht den Lauf** (seit 13.09.2026). Ist
  eine Anfrage fällig, aber der Mindestabstand noch nicht um, geht es mit der
  **Arbeit** weiter; die Anfrage folgt, sobald ihr Abstand um ist. Nur wenn
  es sonst nichts zu tun gibt, liefert `naechster_schritt` `None` und der
  Treiber wartet (`Lauffortschritt.wartet_auf_beitritt`).

  Bis dahin stand dort ein `return None`, und die Begründung war: Sonst wäre
  die geforderte Reihenfolge eine Empfehlung, die jeder Mindestabstand
  aushebelt. Das stimmt für kleine Abstände und kippt bei großen. Im Betrieb
  stand „Beitrittstakt: noch 31 Min" auf dem Schirm, und bei 50 offenen
  Anfragen ergab das **23,8 Stunden Schlaf für einen einzigen Kommentar**.
  Damit war zweierlei gebrochen: Punkt 16 der Anforderung („eine Gruppe, die
  auf Aufnahme wartet, darf die anderen nicht aufhalten") und der Grundsatz,
  für den es `grenzen.py` überhaupt gibt („wer wegen einer gebremsten Aktion
  alles anhält, verliert die Arbeit dort, wo nichts dagegen spricht") —
  Beitrag und Kommentar hielten sich längst gegenseitig daran, der Beitritt
  als einziger nicht.

  **Die Reihenfolge bleibt:** Lässt der Takt die Anfrage zu, geht sie vor
  jeder Arbeit hinaus. Was entfällt, ist allein das Warten. Tests:
  `test_der_takt_haelt_die_anfrage_an_aber_nicht_die_arbeit` und
  `test_ohne_arbeit_wartet_der_lauf_weiterhin_auf_den_takt`.

  Ein **erschöpftes Tageskontingent** hält ohnehin nichts auf: „heute nicht
  mehr" ist etwas anderes als „noch nicht". `limits.join_requests.daily: 0`
  nimmt die Anfragen ganz aus dem Lauf.
- **Der Takt hält auch die Arbeit an, statt den Lauf zu beenden**
  (14.09.2026). Den Warteweg gab es nur für die Beitrittsanfrage; stand der
  Takt der **Kommentare** im Weg, meldete der Server „nichts mehr zu tun", und
  der Lauf beendete sich — im Betrieb bei `31 / 2700` Kommentaren und
  *„kommentar: Abstandsregel - noch 2 Min"*. Zwei Minuten.
  `Lauffortschritt.wartet_auf_takt` ist die allgemeine Fassung von
  `wartet_auf_beitritt`; beide Treiber (örtlich und fern) fragen sie, bevor
  sie aufhören.
- **Gewartet wird nur, wenn Warten etwas bringt.** `wartet_auf_takt` sucht den
  Schritt ein zweites Mal — mit den gebremsten Aktionen als frei
  (`dataclasses.replace`, nicht mit einer zweiten Rechnung daneben: Die
  Reihenfolge steht in `naechster_schritt` und nirgends sonst). Kommt dann
  einer heraus, war der Takt der einzige Grund; kommt keiner, ist der Lauf
  wirklich durch, und ein Schlaf wäre eine Viertelstunde für nichts.
- **`Lage.nur_takt` trennt die eigene Vorsicht von der fremden Ansage.** Beide
  tragen eine Wartezeit, und trotzdem sind sie nicht dasselbe: Der Takt
  (`delays`, 8–20 Min) ist unser eigener Abstand — ihn abzuwarten ist genau
  sein Zweck. Die Bremse der Gegenseite (`gesperrt_bis`, 60 Min bis 24 h) ist
  eine Ansage von Facebook; sie zu **verschlafen** hieße, eine Stunde vor dem
  Bildschirm zu sitzen und danach genau das Muster fortzusetzen, das zur
  Bremsung geführt hat. Der Lauf endet, der Backoff überlebt den Neustart. Die
  erste Fassung unterschied das nicht und ließ
  `test_eine_bremse_haelt_nur_ihre_eigene_aktion_an` eine Stunde lang nicht zu
  Ende laufen — der Test hat den Entwurfsfehler gefunden, nicht der Betrieb.
- **Der Schlaf ist gedeckelt** (`automatik.wartesekunden`, eine Viertelstunde).
  Danach wird neu gefragt, statt einer Zahl zu vertrauen, die niemand
  vorhergesagt hat.
- **Tagesmenge und Takt gehören zusammen gelesen.** Die eine Zahl allein
  ändert nichts: Bei 20–60 Minuten Abstand passen 36 Kommentare in einen Tag,
  gleich was unter `limits.comments.daily` steht. Am 13.09.2026 auf 100
  Kommentare (8–20 Min) und 50 Beitrittsanfragen (3–8 Min) gesetzt — ein Tag
  ergibt damit 50 Anfragen in 4,6 Stunden und danach rund 83 Kommentare.
- **Die Neubewertung läuft einmal je Kampagne und Lauf**
  (`automatik_lauf_kampagnen.bewertet_am`, Migrationsschritt 21). Sie steht
  zwischen Beitritt und Arbeit, weil sie die Rangfolge bestimmt, nach der
  gearbeitet wird — ohne sie arbeitete der Lauf nach den Zahlen von
  vorgestern. Bei jedem Schritt erneut wäre sie die teuerste Zeile des Laufs.
  Der Vermerk wird **auch nach einem Fehlschlag** gesetzt: Eine Bewertung,
  die jedes Mal scheitert, hielte die Kampagne sonst für immer vor der Arbeit
  fest. Gerechnet wird in `rescoring.bewerte_neu` — derselbe Weg, den
  auch die Übersicht liest. Zwei Fassungen wären zwei Ranglisten: eine für den
  Bericht, eine für die Arbeit, und niemand könnte sagen, welche gilt. Einen
  eigenen Befehl `fbgroups rescore` gibt es seit dem 20.09.2026 nicht mehr;
  neu bewertet wird im Lauf. **Klassifiziert wird dabei nicht** — die drei
  Felder werden gepflegt, und eine Neubewertung, die sie überschriebe, wäre
  ein stilles Zurücksetzen dieser Handarbeit.
  `--limit N` zählt die Bewertung **nicht** mit; sie begrenzt, was nach außen
  geht.
- **Die Rangfolge hat drei Stufen: erst wohin es gehört, dann was möglich
  ist, dann wie gut.** `Zielprioritaet` (A vor B vor C) steht seit dem
  13.09.2026 vorn, danach `Gruppenfortschritt.vorrang` — 0 (Beitrag **und**
  Kommentare möglich), 1 (eines von beiden), 2 (nichts) —, danach der Score.
  `Kampagnenfortschritt.arbeitsliste` sortiert **stabil** danach. Stabil ist
  der Punkt: Die Liste kommt bereits score-sortiert herein (`sort_by_rank`),
  und wer hier neu ordnete, hätte zwei Rangfolgen — die Anzeige zeigte dann
  eine andere Gruppe als die, an der gearbeitet wird.
  **Die Zielpriorität steht vor dem Vorrang**, nicht dahinter: Dass eine
  Gruppe Beitrag und Kommentare nimmt, macht sie nicht zur richtigen Gruppe.
  „Was ist möglich?" ist die zweite Frage; die erste ist „wo gehört es hin?".
  `arbeit.arbeitsreihenfolge` (die Arbeitsseite des Menschen) sortiert seit
  demselben Tag genauso — zwei Rangfolgen für dieselbe Kampagne hießen, dass
  der Mensch an einer anderen Gruppe arbeitet als die Automatik.
- **Klasse 2 heißt übersprungen, nicht ungeeignet.** Die Gruppe bleibt im
  Bestand, behält ihren Tracking-Code und wird im nächsten Lauf neu
  beurteilt. „Gibt nichts mehr her" (`kommentar_erschoepft`) wäre ein Urteil
  über die Gruppe; hier liegt eine Entscheidung von uns vor, und sie kann
  sich ändern — durch eine Aufnahme, einen gelesenen Regelsatz, einen
  umgelegten Schalter.
- **Die Regeln der Gruppe binden ohne Schalter** (`qualifikation.
  darf_nach_regeln`). Kein Link, wo Links verboten sind; kein Kommentar, wo
  Kommentare wiederholt abgelehnt wurden; gar nichts, wo die Regeln Werbung
  verbieten. `qualifikation.pflicht` entscheidet seit dem 12.09.2026 nur noch
  über die **Beitrittsstufen** (`BEITRITTSSTUFEN`) — also über die Frage, ob
  der Vermerk „kein Mitglied" sperrt, und die beantwortet daneben schon
  `automatik.mitgliedschaft_pflicht`. Ein Schalter, der die Regeln der Gruppe
  aufhöbe, wäre ein Schalter zum Regelbruch.
- **Technische Fehler sind weder Erlaubnis noch Verbot.** `Beobachtung` zählt
  nur `MODERATION`; ein abgestürzter Browser erscheint dort gar nicht erst.
  Seit dem 12.09.2026 gilt dasselbe für `gescheiterte_kommentarfassungen`:
  Ein technischer Fehlschlag zählt **nicht** gegen eine Fassung, also
  erschöpft ein geschlossenes Browserfenster keine Gruppe mehr. Die Lücke war
  teuer — am 11.09.2026 galten danach 45 Gruppen als erschöpft, am
  12.09.2026 noch einmal 78: vier Kampagnen standen auf „fertig" mit **null**
  Kommentaren. Was die Gruppe selbst sagt („abgelehnt", „Spam", „Link in
  Kommentar"), zählt unverändert weiter — sonst wäre die Grenze abgeschafft
  statt präzisiert.
- **Fünf technische Fehlschläge hintereinander beenden den Lauf**
  (`_Technikwaechter`, `ABBRUCH_TECHNIK`). Das ist **kein** Fall für die
  Fehlerisolierung je Gruppe: Es liegt nicht an dieser Gruppe und nicht an
  der nächsten, sondern am Rechner — dieselbe Überlegung wie bei
  `MAX_NETZFEHLER` im Fernbetrieb. Ohne diese Grenze arbeitete sich der Lauf
  durch 121 Gruppen und vermerkte überall dasselbe. Ein Erfolg **oder** eine
  Ablehnung durch die Gruppe setzt die Zählung zurück: Dann arbeitet der
  Browser ja.
- **`campaign retry --alle --kommentare` ist der Weg zurück**, wenn es doch
  passiert ist — und es gilt **ohne Kennung für alle Kampagnen**: Der Fall,
  für den man es braucht, ist nie einer. Ein geschlossenes Fenster lässt
  nicht eine Kampagne scheitern, sondern die, an der gerade gearbeitet wurde,
  und jede danach; sieben einzeln aufzuzählen ist derselbe Handgriff siebenmal
  und beim achten vergisst man eine. Mit Kennung bleibt die Einschränkung
  möglich — sonst wäre sie keine Wahl mehr. Es nimmt Beitrag *und* Fassungen
  zurück und löscht die Erschöpfung aus diesen Versuchen —
  **veröffentlichte Fassungen bleiben unberührt**. Genau darin liegt der Unterschied zu `campaign reset`, das die
  ganze Kampagne auf Anfang stellt, auch das bereits Hinausgegangene: In einer
  Kampagne mit acht veröffentlichten Kommentaren stünden sie danach ein
  zweites Mal in denselben Gruppen. Eine vom Lauf auf `completed` gesetzte
  Kampagne braucht außerdem wieder `campaign status <k> active` — sonst friert
  der nächste Lauf sie nicht mit ein.

### Der Wächter (`marketing/watchdog.py`, 15.09.2026)

```
fbgroups campaign watchdog --server http://127.0.0.1:8090
   → alle 5 Min: hält jemand die Sperre?
        ja   → nichts tun
        nein → Dienst erreichbar?
                 nein → SSH-Tunnel aufmachen, kurz warten
                          Port da?  nein → warten
                 ja   → campaign automatik starten
```

- **Er enthält keine Kampagnenlogik**, und das ist die Bedingung dafür, dass
  er ungefährlich ist. Keine Warteschlange, keine Rangfolge, kein Takt, keine
  Entscheidung über eine Gruppe. Ein Wächter, der selbst entscheiden könnte,
  was gepostet wird, wäre ein zweiter Runner mit eigener Zählweise — und zwei
  Zählweisen für dieselben Kommentare hat dieses Projekt mehrfach teuer
  bezahlt. Test: `test_der_waechter_kennt_keine_kampagnenlogik` (kein Import
  von `lauf`, `automatik`, `store`, `vorlagen`, `entscheidung`).
- **`baue_befehl` ist die Stelle, an der die Zusagen nachprüfbar sind.** Kein
  `--neu` (sonst begänne er die Kampagne alle fünf Minuten von vorn, und die
  übersprungenen Gruppen kämen jedes Mal zurück), kein `--kampagne`, kein
  `--limit`. Was dort nicht dransteht, kann nicht geschehen — also legt er
  keine Kampagne an und setzt keine auf `completed`.
- **Die Sperre gehört dem Lauf, nicht dem Wächter.** `campaign automatik`
  nimmt sie selbst. Das ist der Unterschied zwischen „der Wächter startet
  keinen zweiten" und „es *gibt* keinen zweiten": Auch ein von Hand
  gestarteter Lauf hält sie. Merkte der Wächter sich nur seine eigenen
  Kinder, bliebe ein Lauf im zweiten Fenster unsichtbar, und zwei Browser
  arbeiteten in derselben Gruppe.
- **Eine Sperre mit toter Kennung gilt nicht.** Sonst sperrte ein Absturz den
  nächsten Lauf für immer aus. Auf Windows genügt `OpenProcess` dafür
  **nicht**: Solange irgendwo ein Handle offen ist, bleibt die Kennung
  gültig, obwohl der Prozess längst tot ist — geprüft wird deshalb der
  Ausgangswert (`GetExitCodeProcess`, `STILL_ACTIVE`). Ohne das startete der
  Wächter nach einem Absturz nie wieder; gefunden in einem End-zu-End-Lauf
  mit einem echten Kindprozess, nicht im Einheitentest.
- **Ein geschlossener Tunnel ist kein Fehlschlag der Kampagne.** Antwortet
  der Port nicht, wird gewartet statt gestartet — ein Lauf ohne Tunnel
  scheiterte an der ersten Anfrage, ohne etwas zu buchen. Geprüft wird die
  **TCP-Verbindung**, nicht `/healthz`: Ob der Weg antwortet, hängt an nginx
  und an Schlüsseln; ob der Tunnel steht, hängt nur am Port.
- **Seit dem 20.09.2026 macht er den Tunnel selbst auf** (`watchdog.tunnel`
  in `settings.yaml`, `Tunnel`/`Tunnelwart`/`baue_tunnelbefehl`). Ein
  Wächter, der einen abgestürzten Lauf neu startet, aber vor einem
  geschlossenen Tunnel die Hände hebt, löst die Hälfte des Problems —
  nachts die falsche: Im Protokoll stand „Läuft der SSH-Tunnel?", und die
  Antwort darauf war jedes Mal ein Mensch mit einem zweiten Fenster. Das
  ist **keine** Kampagnenlogik: ein Port und ein Ziel, keine Gruppe und
  keine Zählung — den Port prüfte er ohnehin schon, neu ist nur, dass er
  ihn aufmachen darf. `baue_tunnelbefehl` ist dafür dieselbe nachprüfbare
  Stelle wie `baue_befehl`.
- **Gefragt wird der Port, nicht der eigene Prozess.** Steht der Tunnel aus
  einem anderen Fenster, ist alles gut und es wird nichts gestartet; ein
  zweites `ssh` auf denselben Port scheiterte ohnehin
  (`ExitOnForwardFailure`) und schriebe einen Fehler ins Protokoll, an dem
  nichts liegt. Läuft **unser** `ssh` und der Port antwortet trotzdem
  nicht, sagt die Meldung genau das — die Leitung steht, die Weiterleitung
  nicht.
- **`-N`, und das ist kein Detail.** Der Befehl von Hand öffnet nebenbei
  eine Kommandozeile auf dem Server; eine, die tagelang offensteht und der
  niemand zusieht, ist der Zugang, den man am ehesten vergisst. Dazu
  `ServerAliveCountMax=3`: Ohne das hinge `ssh` ewig an einer Verbindung,
  die es nicht mehr gibt, und der Wächter hielte den Tunnel für stehend.
- **Das Kennwort des Schlüssels steht in keiner Datei dieses Projekts** —
  nicht in `settings.yaml`, nicht in einer `.env`, und `sshpass` gibt es
  hier nicht. `ssh` nimmt es ohnehin nicht auf der Kommandozeile entgegen,
  und ein Kennwort neben dem Bestand wäre ein Schlüssel ohne Schloss. Zwei
  Wege bleiben, beide Sache des Rechners: einmal `ssh-add <schlüssel>` (der
  Agent von Windows behält ihn über den Neustart), sonst fragt `ssh` im
  Terminal des Wächters — deshalb läuft er im Vordergrund und behält seine
  Ströme. Test: `test_im_tunnelbefehl_steht_kein_kennwort`.
- **Nach dem Aufmachen wird kurz gewartet** (`TUNNEL_FRIST`, 10 s), nicht
  einen ganzen Blickabstand: Sonst verstrichen fünf Minuten für nichts.
  Kommt der Port in dieser Frist — der Regelfall mit hinterlegtem Schlüssel
  —, startet derselbe Blick den Lauf.
- **Ein laufender Prozess geht dem Dienst vor.** Läuft schon einer, ist alles
  gut — auch wenn der Tunnel gerade wackelt. Ihn deswegen als „Dienst weg" zu
  melden wäre eine Auskunft über den falschen Gegenstand.
- **`enabled: false` heißt aus, nicht leiser.** Der Wächter endet dann, statt
  alle fünf Minuten nachzusehen, ob er wieder an ist. Der Abstand hat eine
  Untergrenze von 30 Sekunden — sie schützt vor einem Tippfehler, nicht vor
  einer Entscheidung.

### Ein Limit für eine Aktion ist keines für die andere (15.09.2026)

Der Anlass war ein Verdacht, der sich nicht bestätigt hat — und zwei Fehler,
die beim Nachsehen daneben lagen. Die Abschlussmeldung

```
Beitraege:  1 / 24      Kommentare: 9 / 240
Heute nicht mehr moeglich:  post: Abstandsregel - noch 137 Min
```

las sich, als habe der Beitragstakt die Kommentare eingefroren. Das hatte er
nicht: Die neun Kommentare gingen **während** der Sperre hinaus. Die Zeile
nennt, was gerade nicht geht — nicht den Grund für das Ende des Laufs.
`naechster_schritt` prüft `darf_post` und `darf_kommentar` getrennt und fällt
bei gesperrtem Beitrag auf den Kommentar durch. Test:
`test_bei_getaktetem_beitrag_geht_der_kommentar_sofort_hinaus`.

Zwei echte Fehler in derselben Gegend, beide gegen dieselbe Zusage:

- **Die Kommentar-Tagesmenge sperrte die ganze Gruppe.**
  `limits.comments.je_gruppe_taeglich` wird aus **Kommentar**-Versuchen
  gezählt, stand aber in `Gruppenfortschritt.bearbeitbar` — und sperrte damit
  auch den **Beitrag** derselben Gruppe. Bei `1` hieß das: Der erste
  Kommentar kostete der Gruppe ihren Beitrag für denselben Tag. Jetzt
  `not tageslimit_erreicht or post_offen`, und der Kommentarzweig fragt
  selbst.
- **Eine volle Gruppe hielt die nächste auf.** Der Folgefehler der Korrektur:
  Seit eine Gruppe an ihrer Tagesmenge `bearbeitbar` bleibt, konnte
  `naechste_gruppe` genau sie liefern — und stünde ihr Beitrag unter Takt,
  bliebe der Lauf vor ihr stehen, obwohl die nächste Gruppe kommentieren
  könnte. Deshalb wählt der Kommentarzweig seine Gruppe neu:
  `naechste_kommentargruppe`. **Beide lesen `arbeitsliste`** — eine zweite
  Rangfolge wäre zwei; die Frage ist eine andere („wer darf heute noch
  kommentieren?"), die Reihenfolge dieselbe.
- **`wartet_auf_takt` meldete eine Wartezeit, obwohl Arbeit dalag.** Der
  Treiber fragt es nur bei leerem Schritt, also blieb es folgenlos — aber
  eine Eigenschaft, die „noch 137 Min" sagt, während ein Kommentar
  bereitliegt, ist für sich genommen falsch, und die nächste Stelle, die sie
  liest, glaubte es. Sie gibt jetzt `""` zurück, sobald ein Schritt
  verfügbar ist.
- **Kein Beitragsgrund beendet eine Kampagne.** Weder der Takt noch die
  erschöpfte Tagesmenge noch `posts.daily: 0` sagen etwas über die
  Kommentare oder über das Ende — `abgeschlossen` verlangt weiterhin, dass
  jede Gruppe **voll** ist.

### Ein Fehlschlag kostet einen Beitrag, nicht die Gruppe (15.09.2026)

```
Beitrag 1 → kein Kommentarfeld → technisch → Beitrag 2
Beitrag 2 → kein Kommentarfeld → technisch → Beitrag 3
Beitrag 3 → kein Kommentarfeld → Gruppe fuer DIESEN Lauf beiseite
          → naechste Gruppe (Rangfolge unveraendert)
```

Der Anlass ist ein Lauf, der in einer knappen Stunde **null** Kommentare
schrieb: viermal derselbe Beitrag, dazwischen je eine Viertelstunde Schlaf,
dann „Abgebrochen: 5 technische Fehlschlaege". Drei Fehler wirkten zusammen.

- **Derselbe Beitrag wurde wiederholt.** `bisherige_post_urls` trägt nur,
  worunter wirklich etwas steht; ein Fehlschlag ändert daran nichts, und
  `waehle_gelegenheit` ist deterministisch — also fiel die Wahl jedes Mal
  gleich aus. `entscheide_und_kommentiere` geht jetzt im **selben Schritt**
  zum nächsten Beitrag (`MAX_BEITRAEGE_JE_SCHRITT` = 3), genau wie ein
  Mensch. **Nur bei technischen Ausgängen**: Eine Ablehnung der Gruppe gilt
  für den nächsten Beitrag genauso, und sie dreimal zu wiederholen hieße,
  gegen die Gruppe zu arbeiten.
- **Das Gruppenlimit reist als Flagge, nicht als Satz.** `_ausgang` setzt
  `gruppe_beiseite` aus `Kommentarausgang.gruppenlimit` — ob weiterversucht
  wird, darf nicht an Facebooks Wortwahl hängen.
- **Gewartet wurde auf die falsche Aktion.** `wartet_auf_takt` hob alle
  Bremsen auf einmal auf und nahm die Aktion des Schrittes, der dabei
  herauskam; `naechster_schritt` prüft aber den **Beitrag vor dem
  Kommentar**. Also gewann immer der Beitragstakt (120–240 Min) —
  „Takt: noch 58 Min", während der Kommentar in 11 Minuten frei gewesen
  wäre. Jetzt wird **jede Bremse einzeln** gefragt („bringt sie allein einen
  Schritt?"), und die **kürzeste** gewinnt.
- **Fünf technische Fehlschläge beendeten den Lauf.** „Kein Kommentarfeld"
  ist in fünf Gruppen fünfmal eine Aussage über **einen Beitrag** — nicht
  über den Rechner. Eine aktive Kampagne endete damit an einem gewöhnlichen
  Tag. `_Technikwaechter` trennt jetzt:
  * **Sitzungsfehler** (Fenster zu, Anmeldung weg — `ist_sitzungsfehler`)
    halten **sofort** an. Beim ersten Mal so eindeutig wie beim fünften, und
    vier weitere Anläufe kosten vier Gruppen einen Vermerk.
  * **Gewöhnliche technische Fehlschläge** legen ihre **Gruppe** beiseite;
    der Lauf geht weiter. Die gezählte Notbremse (`GRENZE` = 12) stand hier
    bis zum 20.09.2026 daneben — siehe „Kein technischer Fehlschlag beendet
    den Lauf mehr".
  * Ein Erfolg **oder** eine Ablehnung setzt zurück — dann arbeitet der
    Browser ja.
- **`gruppe_beiseite` steht neben `kein_anlass`.** Beide legen die Gruppe für
  diesen Lauf beiseite, beide sind **kein** Urteil über sie — aber im
  Protokoll muss der Unterschied stehen, sonst sieht „kein Anlass" aus wie
  „Browser tot". Der Ausgang wird bei `gruppe_beiseite` **gebucht** (er
  gehört ins Protokoll), bei `kein_anlass` nicht (dort ist nichts versucht
  worden).
- **Die Kampagne bleibt aktiv.** Kein Abbruchgrund setzt sie auf
  `completed`; beide Abschlusstexte sagen das ausdrücklich. Beendet wird eine
  Kampagne durch den Menschen, durch `campaign status`, oder wenn sie
  wirklich durch ist (`Kampagnenfortschritt.abgeschlossen`).
- **Mehrere Kampagnen waren nie das Problem.** `hole_oder_starte_lauf` friert
  **alle** aktiven ein, `naechste_kampagne` überspringt eine ohne Arbeit, und
  Fortschritt und Warteschlange liegen je Kampagne. „Kampagnen: 0 / 1" heißt
  nur: Eine war auf `active`. Welche das sind, nennt
  `campaign automatik --status` namentlich.

### Fehlerisolierung: ein Fehler kostet eine Gruppe, nicht den Lauf

- **`automatik_lauf_uebersprungen` ist der Kern** (Migrationsschritt 21). Was
  in einem Schritt hochkommt — ein abgestürzter Browser, eine Vorlage, die
  wirft, eine Zuordnung, die verschwunden ist —, legt die Gruppe für **diesen
  Lauf** beiseite; der Lauf geht zur nächsten Gruppe und, wenn die Kampagne
  nichts mehr hergibt, zur nächsten Kampagne. Die Zeile hängt an der
  `lauf_id`: Ein Fehler von heute ist kein Urteil über morgen. In
  `kommentar_erschoepft` geschrieben wäre ein geschlossenes Browserfenster
  dauerhaft ein Urteil über die Gruppe — genau die Verwechslung, an der am
  11.09.2026 45 Gruppen als erschöpft galten.
- **Der erste Grund bleibt stehen.** `ueberspringe_gruppe` überschreibt einen
  vorhandenen Eintrag nicht (`ON CONFLICT DO NOTHING`): Was nach dem ersten
  Fehler kommt, sind meist dessen Folgen.
- **Eine Kampagne darf für sich scheitern.** `lies_fortschritt` liest je
  Kampagne (`_lies_kampagne`); wirft eine, kommt sie leer und
  `gescheitert` in die Liste, und `leer` hält den Lauf nicht auf. Vorher nahm
  eine kaputte Zuordnung dreihundert Beiträge in anderen Kampagnen mit.
- **`break` war der eigentliche Fehler.** `fuehre_lauf_aus` beendete den
  ganzen Lauf, wenn zu **einer** Gruppe Kampagne oder Zuordnung fehlte. Das
  ist jetzt ein Übersprung; der Rest des Laufs merkt nichts davon.
- **Der Schleifenwächter ist die Notbremse.** Jeder bekannte Weg schreibt
  seinen Ausgang in den Bestand, und damit steht beim nächsten Durchgang ein
  anderer Schritt an — aber „jeder bekannte Weg" ist genau die Annahme, die
  eine Endlosschleife widerlegt. Kommt derselbe Schritt viermal, wird seine
  Gruppe beiseitegelegt: Ein Lauf, der nichts mehr tut und trotzdem nicht
  aufhört, ist schlimmer als eine Gruppe weniger.
- **Eine gescheiterte Beitrittsanfrage hinterlässt nichts im Bestand** —
  `beitritt_angefragt` zu setzen wäre die Behauptung, es sei etwas
  abgeschickt worden. Damit dieselbe Gruppe trotzdem nicht bei jedem
  Durchgang wiederkommt, wird sie für diesen Lauf übersprungen. Deshalb trägt
  `POST /automatik/beitritt/ergebnis` seit dem 12.09.2026 ein optionales
  `campaign_id`: Der Server hält den Stand, also muss er auch wissen, dass
  diese Gruppe schon drankam. Der Einzelbefehl `campaign beitritt` lässt das
  Feld leer — dort gibt es keinen Lauf.
- **Netzfehler sind kein Fall für die Isolierung.** Antwortet der Dienst
  nicht, betrifft das nicht eine Gruppe, sondern alle:
  `fuehre_lauf_fern_aus` versucht es `MAX_NETZFEHLER` (5) mal und hört dann
  auf. Ein **nicht buchbarer Ausgang** beendet den Lauf sofort (nach einem
  zweiten Anlauf): Der Server böte denselben Kommentar erneut an, und
  derselbe Text stünde zweimal in derselben Gruppe. Weiterzulaufen hieße, im
  Browser zu arbeiten, während nirgends etwas gezählt wird.

### Kontextbezogener Runner: Inhalt, Entscheidung, Grenzen (12.09.2026)

```
Gruppe → Mitgliedschaft → Regeln → moegliche Aktionen → Beitraege lesen
   → Thema und Bezug beurteilen → beste Handlung waehlen → konservativ
   ausfuehren → Ergebnis speichern → spaeter neu bewerten
```

und ausdrücklich **nicht** „Gruppe gefunden → sofort posten → sofort
kommentieren → nächstes Ziel". Drei neue reine Module tragen das; sie kennen
weder Netz noch Datenbank noch Playwright, wie `kaltmodus.py` und
`qualifikation.py` vor ihnen.

- **`inhalt.py` liest den Beitrag, behält ihn aber nicht.** Die harte Grenze
  („kein Beitragstext, nie ein Mensch") ist in ihrem **Speicherteil**
  unangetastet: Der Text geht durch das Modul und endet dort; gespeichert
  wird das Urteil — ein Schlagwort (`versand`, `wohnung`) und eine Einstufung
  (`hoch`, `keine`). Technisch abgesichert und nicht nur vorgenommen:
  `GroupPost` hat kein Textfeld, `upsert_group_posts` könnte einen Text gar
  nicht ablegen (Test: `test_der_beitragstext_wird_nicht_gespeichert`).
  Der Vorläufer stand schon im Haus — `actions._artikel_auswerten` liest seit
  jeher `inner_text()`, um Reaktionen zu zählen, und behält davon nichts.
- **Elf Themen, nicht eines.** Eine Gemeinschaftsgruppe redet über Wohnungen,
  Jobs, Behörden und Autos; ein Runner, der nur „Versand nach Syrien" kennt,
  hält alles Übrige fälschlich für seine Gelegenheit. Die **Absicht** steht
  daneben (`sucht`/`bietet`/`fragt`): „Ich suche eine Wohnung" und „Ich biete
  eine Wohnung" haben dasselbe Thema und verlangen das Gegenteil voneinander.
- **`behoerde` steht vor `job` in der Themenliste.** Der lateinische Abgleich
  erlaubt die Wortfortsetzung („arab" trifft „araber"), also trifft `job`
  auch `jobcenter` — und „Wer kennt einen Termin beim Jobcenter?" stünde als
  Stellenangebot im Protokoll. Dieselbe Kollisionsart wie zwischen der Stadt
  „Essen" und dem Kategoriebegriff für Speisen; ein Abgleich aller Listen
  gegeneinander fand genau diese eine.
- **`entscheidung.py` kennt fünf Ausgänge, und `NO_REPLY` ist einer davon.**
  `HELPFUL_REPLY` trägt nichts von uns, `PRIVATE_CONTACT_SUGGESTION`
  verlagert das Gespräch, `CONTEXTUAL_APP_MENTION` nennt die App,
  `DIRECT_APP_RECOMMENDATION` empfiehlt sie mit Link. Je näher an der
  Werbung, desto mehr muss dafür sprechen. „Nicht jede gefundene Gelegenheit
  muss genutzt werden" ist damit kein Vorsatz, sondern ein Rückgabewert — er
  wird gezählt, nicht als Ausfall gebucht.
- **`Erlaubnis` hat vorsichtige Vorgaben** (`links=False`, `werbung=False`).
  Wer sie ohne Angaben baut, hat nichts über die Gruppe gelesen — und daraus
  eine Erlaubnis zu machen wäre genau der Fehler, den die Anforderung mit
  „UNKNOWN bedeutet nicht erlaubt" benennt. Ungelesene Regeln führen deshalb
  zur **vorsichtigeren** Handlung (privater Kontakt statt App-Nennung), nicht
  zum Stillstand: Aus nichts entsteht keine Beobachtung, und eine Gruppe, in
  der nie etwas versucht wird, bliebe für immer unbewertet.
- **`UNGEEIGNET` schließt beides aus.** Ein Werbeverbot der Gruppe macht über
  `beurteile` ein `UNGEEIGNET`, und dort bleibt auch die „bloß hilfreiche"
  Antwort aus — geschrieben hätten wir sonst trotzdem. Die Stufe
  `HELPFUL_REPLY` bleibt erreichbar, wo jemand die Erlaubnis genauer kennt
  als der Regeltext.
- **Der passendste Beitrag schlägt den lautesten.** `waehle_und_kommentiere`
  ordnet nach (Nähe der Antwortart, dann Reaktionen+Kommentare). Vorher
  entschied allein `interactions + comments`: Der Kommentar über
  Paketmitnahme stand dann unter dem Wohnungsgesuch mit hundert Reaktionen —
  also ausgerechnet dort, wo ihn die meisten sehen.
- **„Kein Anlass" ist kein Fehlversuch.** `Schrittergebnis.kein_anlass` führt
  nicht in die Buchung (das zählte gegen die Fassung und irgendwann gegen die
  Gruppe), sondern in die Übersprungsliste **dieses Laufs**. Morgen stehen in
  derselben Gruppe andere Beiträge.
- **Ohne einen einzigen lesbaren Text gilt die alte Regel.** Der Rückfall
  steht an der ehrlichen Stelle: Wir wissen dann nichts über die Beiträge —
  das ist etwas anderes als „sie passen nicht".

### Grenzen je Aktion (`marketing/grenzen.py`)

- **Ein Limit für Kommentare ist keines für Beiträge.** Jede Aktion hat ihre
  eigene Tagesmenge, ihren eigenen Takt und ihre eigene Sperre (`Aktion`:
  Beitritt, Post, Kommentar). Wer wegen einer gebremsten Aktion alles anhält,
  verliert die Arbeit dort, wo nichts dagegen spricht — und erfährt nichts
  darüber, was tatsächlich gesperrt war. `lauf.naechster_schritt` fragt
  deshalb je Schrittart, nicht einmal für alles.
- **Eine Antwort zählt als Kommentar.** `aus_texttyp` bildet beides auf
  `KOMMENTAR` ab; eine eigene Menge für Replies wäre eine Umgehung der
  eigenen Grenze, und zwar eine, die man sich selbst erlaubt hat.
- **`limits`/`delays` in `settings.yaml` sind Planungswerte dieses
  Programms**, keine Grenzen von Facebook — die veröffentlicht niemand. Sie
  beschreiben, wie vorsichtig wir sein wollen. Vorgaben: 4 Beitritte, 6
  Kommentare, 3 Beiträge am Tag; 30–90 Minuten Abstand, beim Beitrag
  120–240. `daily: 0` schaltet eine Aktion ganz ab — nicht „unbegrenzt",
  sondern „gar nicht", wie Gewicht 0 im Scoring.
- **Damit ist die gezählte Tagesgrenze zurück**, die am 27.08.2026 entfernt
  wurde („Kein Arbeiter, keine Wartezeit, kein Tageslimit"). Der Grund von
  damals bleibt richtig und gilt heute nicht mehr: Die Bremse traf einen
  **Menschen**, der jeden Beitrag von Hand einfügte. Sie trifft jetzt eine
  Schleife, die selbst absetzt — und für die war sie immer gedacht. Der Test
  `test_es_gibt_keine_gezaehlte_tagesgrenze_mehr` prüft weiterhin die Stelle,
  an der sie **nicht** steht (`arbeit.melde_vorschlag`, die Arbeitsseite des
  Menschen).
- **`beitritt.einstellungen` liest hier.** Zwei Zahlen für dieselbe Frage
  wären zwei Wahrheiten: Eine Änderung an einer von beiden hätte die andere
  still überstimmt, je nachdem, welcher Aufrufer gerade fragt. Die alten
  Schlüssel (`beitritt.anfragen_pro_tag`) bleiben als **Rückfall** gültig —
  eine bestehende `settings.yaml` auf dem Server verhält sich durch ein
  Update nicht anders als gestern.
- **`Ausgangsart.RATE_LIMIT` ist der vierte Ausgang** neben Erfolg,
  Moderation und Technik — und wird **zuerst** geprüft: „vorübergehend
  gesperrt" enthält „gesperrt", und wer das als Moderation liest, verurteilt
  eine Gruppe für **unsere** Eile. Eine Bremse zählt deshalb weder gegen die
  Gruppe (wie Technik) noch führt sie zum Weitermachen (anders als Technik):
  Sie pausiert ihre Aktion.
- **Der Backoff verdoppelt sich und überlebt den Neustart** (60, 120, 240 …
  bis 24 h; gespeichert in `marketing_meta` als `sperre:<aktion>:bis` und
  `:stufe`). Ohne die gespeicherte Stufe finge er nach jedem Start wieder bei
  einer Stunde an — und genau das Muster, das zur Bremsung geführt hat,
  begänne von vorn. Ein Erfolg **oder** eine echte Ablehnung setzt zurück:
  Dann arbeitet der Browser ja.

### Was aus einem abgeschickten Kommentar wird (12.09.2026)

- **`comment_on_post` meldete jeden Versuch als Erfolg.** Enter drücken, drei
  Sekunden warten, `return True` — die Seite wurde danach nie angesehen. Am
  12.09.2026 fiel auf, was das verschweigt: In einer Gruppe mit
  Freigabepflicht standen unsere Kommentare als **„Ausstehend"** und waren für
  niemanden sichtbar, während der Lauf sie als veröffentlicht zählte. Die
  Funktion liefert jetzt einen `Kommentarausgang` statt eines `bool`.
- **Drei unterscheidbare Ausgänge.** Abgeschickt und sichtbar; abgeschickt,
  aber auf Freigabe wartend (Erfolg **mit Ansage** — der Tracking-Link ist
  heraus, aber bis zur Freigabe klickt ihn niemand, und eine Null bei den
  Klicks ist dann kein Urteil über die Gruppe); gar nicht angenommen.
- **`Ausgangsart.GRUPPENLIMIT` ist der fünfte Ausgang.** „Du hast das Limit
  für freizugebende Inhalte in **dieser Gruppe** erreicht" ist weder ein
  Urteil über den Text (Moderation) noch eine Sperre des Kontos
  (`RATE_LIMIT`): Dort warten genug Beiträge von uns auf einen Moderator, und
  in der nächsten Gruppe geht es sofort weiter. Beides gleich zu behandeln
  hieße, wegen einer vollen Warteschlange sechs Kampagnen anzuhalten. Sie
  wird **vor** `RATE_LIMIT` geprüft — die Meldung enthält „Limit" und
  „erreicht", und beide anderen Listen würden sie falsch einordnen.
- **Das Gruppenlimit verbraucht keine Fassung.** Sonst wäre eine volle
  Freigabe-Warteschlange nach drei Läufen eine „erschöpfte" Gruppe — dieselbe
  Verwechslung wie beim geschlossenen Browserfenster, nur langsamer.
- **Wo kein Kommentarfeld ist, wird erst die Gruppe gefragt.** Blendet
  Facebook das Feld wegen des Limits aus, wäre „Kommentarfeld nicht
  gefunden" eine Aussage über **unsere Suche** statt über die Gruppe.
- **Die eingereichte `kommentieren`-Funktion darf weiterhin `bool` liefern.**
  Im Test zählt sie, statt zu kommentieren; `_ausgang` bringt beides auf eine
  Form. Ohne das wäre die Auswahlregel nur noch mit Browser prüfbar — genau
  das soll die Aufteilung verhindern.

### SUCCESS zaehlt, FAILED nicht (20.09.2026)

```
SUCCESS         -> Tagesmenge +1, Gruppenmenge +1, Takt abwarten
FAILED/SKIPPED  -> nichts zaehlen, kein Takt, sofort zur naechsten Gruppe
```

Zehn Regeln des Nutzers, und ihr Kern ist eine einzige Unterscheidung: **Ein
Kommentar zaehlt erst, wenn er wirklich in der Gruppe steht.** Vier davon
waren bereits erfuellt (10 je Gruppe, Gruppe ueberspringen, naechste Kampagne,
nie den ganzen Lauf beenden); vier waren verletzt, alle an derselben Wurzel.

- **`versuche_heute` und `versuche_heute_je_gruppe` zaehlen `erfolg = 1`.**
  Bis dahin zaehlte jeder Versuch ausser dem technischen Fehlschlag, und die
  Begruendung lautete: Ein abgelehnter Kommentar sei trotzdem einer gewesen,
  den die Gruppe gesehen hat. Das trifft auf die **Moderation** zu — auf einen
  Kommentar, den Facebook gar nicht erst angenommen hat, trifft es nicht: Er
  stand dort nie. Die Tagesmenge einer Gruppe dafuer zu verbrauchen ist
  dieselbe Verwechslung, an der am 14.09.2026 24 Gruppen mit je einem
  Kommentar im Bericht standen.
- **`letzter_versuch` liefert nur den juengsten *erfolgreichen* Versuch.** Das
  war der teuerste Leerlauf des Laufs: Ein Kommentar, den Facebook nicht
  annahm, hielt den naechsten volle zehn bis zwanzig Minuten auf, obwohl
  nichts hinausgegangen war. Eine Gruppe, in der gerade nichts geht, kostete
  so eine Viertelstunde je Fehlschlag. Der Takt ist der Abstand zwischen zwei
  Dingen, die **in einer Gruppe stehen**; wo nichts steht, gibt es nichts
  abzuwarten.
- **Der Schutz ist nicht weg, er wandert.** Der alte Einwand („nach zwanzig
  Fehlschlaegen mit voller Portion weiterzumachen ist leichtsinnig") bleibt
  richtig und ist woanders aufgehoben: `Ausgangsart.RATE_LIMIT` pausiert die
  Aktion mit einem Backoff, der sich verdoppelt und den Neustart ueberlebt,
  sobald Facebook selbst sagt, dass es zu viel wird; `_Technikwaechter`
  beendet den Lauf, wenn der Rechner nicht mehr mitmacht; und was die Gruppe
  ablehnt, beschraenkt sie ueber `qualifikation.Beobachtung`. Was entfaellt,
  ist allein das **stille** Verbrauchen der Tagesmenge durch etwas, das nie in
  einer Gruppe stand.
- **Eine Ablehnung legt die Gruppe fuer diesen Lauf beiseite** (Regel 5).
  Vorher kam sie gleich wieder an die Reihe, nur mit einer anderen Fassung: In
  einer Gruppe, die gerade nichts annimmt, verbrauchte der Lauf so eine
  Fassung nach der anderen, bis sie als **erschoepft** galt — ein dauerhaftes
  Urteil aus einer einzigen Stunde. `gruppe_beiseite` ist kein Urteil: Es gilt
  fuer diesen Lauf, morgen wird die Gruppe neu beurteilt. Technische
  Fehlschlaege wurden schon vorher so behandelt; neu ist, dass die Ablehnung
  denselben Weg geht — nur der Grund im Protokoll ist ein anderer.
- **Was das bedeutet, wenn der Tag schlecht laeuft:** Ein Lauf mit 200
  Fehlschlaegen hat danach immer noch seine vollen 100 Kommentare uebrig, und
  keiner der 200 hat ihn gebremst. Das ist ausdruecklich so gewollt — die
  Bremse kommt jetzt von der Gegenseite (`RATE_LIMIT`) und nicht mehr von der
  eigenen Buchhaltung.
- **Die Zahlen stehen in `settings.yaml`**: `limits.comments.daily: 100`
  (harte Obergrenze, Regel 2/10), `limits.comments.je_gruppe_taeglich`,
  `delays.comment`. Zehn je Gruppe und Kampagne ist dagegen
  `lauf.ZIEL_JE_GRUPPE` und zaehlt **veroeffentlichte** Fassungen aus
  `campaign_group_texte.status` — ein Fehlschlag bringt eine Gruppe damit nie
  naeher an ihre zehn.
- Festgehalten in `tests/test_kommentarregeln.py`, eine Regel je Test.

### Ein geloeschter Beitrag ist kein Fehler der Gruppe (20.09.2026)

Im Browser stand unter der Adresse **„هذا المحتوى غير متوفر حاليًا"** — den
Beitrag gibt es nicht mehr. Kein Kommentarfeld, weil es den Beitrag nicht
gibt. Ohne eigene Erkennung endete das als „Kommentarfeld nicht gefunden",
also als Aussage ueber die **Gruppe**: Der Lauf steuerte dieselbe tote Adresse
Dutzende Male an, und am Ende bezahlte die Gruppe dafuer.

- **`BEITRAG_WEG` in `actions.py`** wird geprueft, **bevor** nach dem
  Kommentarfeld gesucht wird. Die Reihenfolge ist der Punkt: „kein Feld
  gefunden" ist sonst eine Aussage ueber unsere Suche statt ueber die Adresse
  — dieselbe Ueberlegung wie beim Gruppenlimit.
- **`Kommentarausgang.beitrag_weg` ist ein eigener Ausgang**, weder Erfolg
  noch Moderation noch Technik. Er sagt nichts ueber die Gruppe, nichts ueber
  unseren Text und nichts ueber das Konto — nur, dass diese eine Adresse ins
  Leere zeigt.
- **Der Schritt geht sofort zum naechsten Beitrag** (`continue`), wie bei
  einem technischen Ausgang — aber ohne dessen Folgen: Er zaehlt **nicht**
  gegen den `_Technikwaechter` und macht aus der Gruppe kein Urteil.
- **Kein Ausschluss.** `_fuehre_schritt_aus` prueft `not ergebnis.beitrag_weg`,
  bevor es `schliesse_gruppe_aus` ruft. Die Gruppe kann voellig in Ordnung
  sein; ihre Beitragsliste ist bloss aelter als unser Bestand. Sie dafuer
  auszuschliessen hiesse, die falsche Stelle zu bestrafen — dieselbe
  Verwechslung wie „Technik ist kein Urteil", nur eine Ebene tiefer.
- **Sind alle versuchten Adressen tot**, wird die Gruppe fuer diesen Lauf
  beiseitegelegt (damit der naechste Schritt zur naechsten Gruppe geht) und
  ausdruecklich **nicht** ausgeschlossen.

### Ein technischer Fehlschlag schliesst die Gruppe aus (20.09.2026)

```
technischer Fehlschlag  ->  1. Uebersprung fuer DIESEN Lauf (braucht lauf_id)
                            2. bearbeiten = 0  (braucht sie NICHT)
```

Der Anlass ist ein Lauf, der in **einer** Gruppe im Kreis lief — derselbe
Beitrag, dieselbe Fassung 1, Dutzende Male — und mit *„12 technische
Fehlschlaege in Folge, ueber verschiedene Gruppen hinweg"* endete, obwohl es
nie eine zweite Gruppe gab.

- **Der Ausschluss haengt nicht mehr an der Lauf-Kennung.** In `web.py` stand
  `if meldung.gruppe_beiseite and meldung.lauf_id:` — beides in **einer**
  Bedingung. Kommt die Kennung nicht mit, fiel damit jede Folge des
  Fehlschlags weg, und der Server bot dieselbe Gruppe sofort wieder an. Jetzt
  sind es zwei Schritte: Der **Uebersprung** braucht die Kennung (er gilt fuer
  genau diesen Lauf), der **Ausschluss** nicht.
- **`store.schliesse_gruppe_aus` ist die eine Stelle**, die beide Wege
  benutzen — oertlich (`automatik._fuehre_schritt_aus`) und fern
  (`POST /automatik/ergebnis`). Zwei Fassungen waeren zwei Regeln.
- **Das hebt „Technik ist kein Urteil" teilweise auf, und das ist bewusst.**
  Die alte Regel entstand am 11.09.2026, als ein geschlossenes Browserfenster
  45 Gruppen als erschoepft gelten liess. Sie bleibt richtig fuer
  `kommentar_erschoepft` — aber „kein Kommentarfeld" in derselben Gruppe beim
  dreissigsten Anlauf ist keine Eigenschaft des Browsers mehr, sondern eine
  der Gruppe.
- **Ausgeschlossen, nicht geloescht** — und darin liegt der Schutz:
  `bearbeiten = 0` ist die Achse „arbeiten wir daran?", nicht
  `marketing_status`; dass wir Mitglied sind, bleibt stehen. Der
  **Tracking-Code bleibt gueltig** (er steht moeglicherweise in einem
  veroeffentlichten Beitrag). Der Grund steht daneben und ist in der
  Uebersicht zu lesen; zuruecknehmen ist ein Haken, kein Befehl.
- **Ein Menschenurteil wird nie ueberschrieben.** Steht die Gruppe schon auf
  ausgeschlossen, bleibt ihr Grund stehen — „passt thematisch nicht" ist die
  bessere Auskunft als „automatisch: ...".
- Wirksam wird der Ausschluss ueber `links_zum_bearbeiten`, das
  `COALESCE(gm.bearbeiten, 1) = 1` filtert: Die Gruppe faellt aus der
  Arbeitsliste der Kampagne, und der naechste Schritt greift zur naechsten.

### Die Anmeldung wird vor dem Lauf geprüft (21.09.2026)

```
Lauf startet  ->  facebook.com laden  ->  Anmeldewand?
                                            ja   -> nichts tun, Exit 2
                                            nein -> erster Schritt
Anmeldewand mitten im Lauf -> Sitzungsfehler -> Lauf haelt an, kein Ausschluss
```

Der Anlass ist eine Frage des Nutzers nach einem Lauf, der nichts tat —
und dahinter stand eine Lücke, die teurer ist als dieser eine Lauf: Eine
**abgemeldete** Browsersitzung findet in *jeder* Gruppe kein Kommentarfeld
und kein Beitragsformular. Beides endete als „nicht gefunden", also als
technischer Fehlschlag — und der nimmt seit dem 20.09.2026 die Gruppe aus
der Kampagne. Ein abgelaufener Anmeldestand hätte damit eine Kampagne nach
der anderen leergeräumt, mit einem Grund an jeder Gruppe, an dem nichts
liegt.

- **Die Vorbedingung wird vorher gefragt, nicht hinterher gelernt**
  (`actions.ist_angemeldet`, `cli._sitzung_pruefen`). Ein Seitenabruf gegen
  die **Startseite** — eine Gruppenseite kann aus vielen Gründen nicht
  laden, die Startseite nur aus einem. Beide Treiber fragen sie, örtlich wie
  fern, und zwar **vor** dem ersten Schritt: Danach wäre es zu spät, denn
  der erste Fehlschlag trägt die Gruppe schon aus der Kampagne.
- **Ein Abruffehler heißt nicht „abgemeldet".** Ein Netzfehler ist kein
  Beleg für eine abgelaufene Sitzung; es wird gesagt und trotzdem versucht —
  dieselbe Zurückhaltung wie bei `merke_regeln`, das aus einer ungelesenen
  Seite keine Regel macht.
- **`ANMELDEWAND` steht in `actions.py` neben `BEITRAG_WEG`** und wird in
  beiden Wegen **zuerst** geprüft: Sie sagt nichts über diese eine Adresse
  und nichts über die Gruppe, sondern alles über uns. Die Muster sind so
  gewählt, dass sie nur abgemeldet vorkommen („Neues Konto erstellen",
  „Passwort vergessen", „إنشاء حساب جديد") — ein Fehlalarm hielte den Lauf
  mitten in der Arbeit an.
- **Der Text beginnt mit `NICHT_ANGEMELDET`, und das ist die Schnittstelle.**
  `automatik._SITZUNG` erkennt ihn (`nicht angemeldet`), damit die Wand als
  **Sitzungsfehler** gilt: Der Lauf hält sofort an, statt sich durch die
  Kampagne zu arbeiten. Wer die Konstante ändert, muss dort nachsehen —
  deshalb ist es eine Konstante und kein Satz im Code.
- **Kein Ausschluss bei einem Sitzungsfehler.** `_fuehre_schritt_aus` und
  `POST /automatik/ergebnis` prüfen es beide, neben `beitrag_weg`. Zwei
  Fassungen wären zwei Regeln, und die zweite fände man erst an einer
  leergeräumten Kampagne.
- **Die Meldung nennt den Weg zurück** (`fbgroups auth login`) und sagt
  ausdrücklich, dass nichts versucht und nichts vermerkt wurde. Die Sitzung
  liegt im Browserprofil (`data/browser_state`) und überlebt den Neustart —
  angemeldet wird einmal, nicht je Lauf.
- Festgehalten in `tests/test_anmeldung.py`: Erkennung in drei Sprachen,
  kein Fehlalarm auf einer gewöhnlichen Gruppenseite, Anhalten statt
  Weitermachen, kein Ausschluss, und beide Treiber fragen vorher.

### Kein technischer Fehlschlag beendet den Lauf mehr (20.09.2026)

```
technischer Fehlschlag  ->  Gruppe beiseite + aus der Kampagne, naechste Gruppe
Sitzungsfehler          ->  Lauf anhalten (der einzige verbliebene Grund)
```

Der Anlass ist derselbe Lauf wie im Abschnitt davor, einen Schritt später:
Er endete wieder mit *„12 technische Fehlschlaege in Folge, ueber
verschiedene Gruppen hinweg"* — und wieder gab es nur **eine** Gruppe. Die
Anweisung des Nutzers dazu ist wörtlich: nicht abbrechen, sondern die Gruppe
aus der Kampagne nehmen und es mit der nächsten versuchen.

- **Die Ursache lag im Rückfall ohne gelesene Texte**
  (`automatik._ohne_urteil_kommentieren`). Findet die Gruppenseite ihre
  Artikel nicht, kommen die Beiträge **ohne Text** herein („Keine Artikel im
  Aufbau gefunden - es wird trotzdem gesucht"); dann greift der Rückfall, und
  der nahm **einen** Beitrag: den lautesten. Die Rangfolge ist
  deterministisch, ein Fehlschlag ändert nichts an ihr — also fiel die Wahl
  jedes Mal auf dieselbe gelöschte Adresse. Der Weg **mit** gelesenen Texten
  ging seit dem 15.09.2026 zum nächsten Beitrag weiter; dieser hier nicht,
  und weil die Artikel im Betrieb oft fehlen, ist er keineswegs der
  Sonderfall. Jetzt gelten dort dieselben drei Regeln: tote Adresse →
  nächster Beitrag, technischer Fehlschlag → nächster Beitrag, Ablehnung der
  Gruppe → Gruppe beiseite. Der gemeinsame Ausgang steht in `_abschluss` —
  zwei Fassungen wären zwei Regeln für denselben Fall.
- **Die gezählte Notbremse ist weg.** `_Technikwaechter.melde` liefert `True`
  nur noch beim **Sitzungsfehler**; `ABBRUCH_TECHNIK` und `GRENZE` gibt es
  nicht mehr. Gezählt wird weiter (`folge`), aber die Zahl beendet nichts —
  sie steht im Protokoll. Was einen technischen Fehlschlag jetzt beantwortet,
  ist der Ausschluss aus der Kampagne (Abschnitt davor), und der trifft die
  Gruppe, um die es geht, statt den Lauf.
- **Der Einwand dagegen bleibt richtig und ist die Kehrseite:** Geht der
  Browser auf eine Art kaputt, die `ist_sitzungsfehler` nicht kennt, nimmt
  der Lauf eine Gruppe nach der anderen aus der Kampagne. Deshalb steht der
  Grund an jeder Zeile (`automatisch: ...`), deshalb ist der Ausschluss ein
  Haken und kein Befehl, und deshalb bleibt der Tracking-Code gültig.
- **`beitrag_weg` reist jetzt auch im Fernbetrieb mit.** Der örtliche Lauf
  las es seit dem 20.09.2026, bevor er ausschließt; `POST /automatik/ergebnis`
  bekam es gar nicht erst zu sehen — der Server sah nur `gruppe_beiseite` und
  konnte „hier nimmt niemand einen Kommentar an" nicht von „diese drei
  Beiträge gibt es nicht mehr" unterscheiden. Eine gelöschte Adresse hätte
  damit im Regelfall (Fernbetrieb) eine gesunde Gruppe ausgeschlossen.

### Eine Gruppe ohne passenden Beitrag ruht (20.09.2026)

```
kein Anlass / tote Adresse  ->  Ruhezeit (30 Min), dann wieder in der Reihe
technischer Fehlschlag      ->  Schlussstrich fuer diesen Lauf + aus der Kampagne
ruht noch eine Gruppe       ->  der Lauf wartet, statt sich fertig zu melden
```

Der Anlass ist ein Lauf über zwölf Gruppen, der nach **zwölf Schritten**
aufhörte: In neun davon stand „kein passender Beitrag", und jede war damit
für den ganzen Lauf weg — 11 von 120 Kommentaren, Meldung „nicht vollständig
abgeschlossen". Verlangt war das Gegenteil: zwischen den Gruppen hin und her
gehen, bis jede ihre zehn Kommentare hat, und niemals aufhören.

- **`automatik_lauf_uebersprungen.wiederholen_ab`** (Migrationsschritt 24,
  rein additiv) macht aus dem Schlussstrich eine **Ruhezeit**. `NULL` heißt
  unverändert „für diesen Lauf erledigt" — die Bedeutung, die jede bestehende
  Zeile hatte. Die Dauer steht in `settings.yaml`
  (`automatik.ruhe_minuten`, 30).
- **Zwei Aussagen, die vorher dieselbe Zeile schrieben.** „Hier ging es
  nicht" hängt der Gruppe an; „hier steht gerade nichts Passendes" hängt dem
  Augenblick an. Nur das Zweite ruht — in einer halben Stunde stehen dort
  andere Beiträge. Dreißig Minuten sind die Abwägung: kürzer holt der Lauf
  dieselbe Gruppenseite neu, ohne dass sich dort etwas geändert hätte;
  länger steht eine Kampagne mit wenigen Gruppen still.
- **Der erste Grund bleibt, die Ruhezeit wird fortgeschrieben.** Bliebe auch
  sie stehen (`ON CONFLICT DO NOTHING`), käme dieselbe Gruppe sofort wieder,
  scheiterte wieder und liefe im Kreis. Eine Ruhezeit macht aus einem
  Schlussstrich **nie** einen zeitlichen: „für diesen Lauf erledigt" ist die
  stärkere Aussage, sonst holte ein späteres „kein Anlass" eine
  ausgeschlossene Gruppe zurück.
- **`store.naechste_rueckkehr` entscheidet über das Ende des Laufs.** Ruht
  auch nur eine Gruppe, ist er nicht durch: Beide Treiber warten (örtlich
  `fuehre_lauf_aus`, fern über `warten` aus `/automatik/naechster`) — derselbe
  Weg wie beim Takt. `None` heißt: Hier kommt nichts mehr von selbst,
  aufhören ist richtig.
- **Der Schlaf ist gedeckelt wie jeder hier** (`automatik.ruhesekunden`, eine
  Viertelstunde, nach unten 30 Sekunden). Danach wird neu gefragt, statt
  einer Zahl zu vertrauen, die vor einer halben Stunde gerechnet wurde.
- **Ein Schlaf setzt den Schleifenwächter zurück** (`_Schleifenwaechter.
  vergiss`). Er sucht einen Schritt, der sich **ohne Fortschritt**
  wiederholt; eine Ruhezeit ist Fortschritt — die Gruppe war zwischen den
  beiden Malen gar nicht an der Reihe. Ohne das legte er bei einer Kampagne
  mit einer einzigen Gruppe nach vier Ruhezeiten endgültig weg.
- **Der technische Fehlschlag ruht ausdrücklich nicht.** Dort ist der
  Schlussstrich richtig: Die Gruppe fällt ohnehin aus der Kampagne (siehe
  „Ein technischer Fehlschlag schliesst die Gruppe aus"). Eine **tote
  Adresse** dagegen ruht — sie sagt nichts über die Gruppe.
- Festgehalten in `tests/test_ruhezeit.py`; die beiden Treibertests zeigen
  den Unterschied im Betrieb: Mit „kein Anlass" kommt die erste Gruppe ein
  zweites Mal dran, mit „kein Kommentarfeld" nicht.

### Kampagnenzustände: „gerade geht nichts" ist nicht „fertig"

- **`Kampagnenfortschritt.abgeschlossen` steht neben `fertig`.** `fertig`
  heißt „der Lauf versucht hier nichts mehr" und zählt eine erschöpfte Gruppe
  mit; `abgeschlossen` heißt „erreicht". Nur das Zweite setzt eine Kampagne
  dauerhaft auf `completed`. Am 12.09.2026 stand der Unterschied im Bild:
  vier Kampagnen auf „FERTIG", 78 von 78 Gruppen durch, **null**
  veröffentlichte Kommentare. Fertig war daran nur der Lauf.
- **Drei neue Laufzustände** (`KampagnenLaufStatus`): `WARTET_AUF_BEITRITT`
  (zugeordnet, aber noch nicht drin), `ERSCHOEPFT_VORERST` (heute nichts,
  morgen wieder), `VORERST_GEBREMST` (Tagesmenge, Takt oder Bremse). Sie sind
  der Gegenentwurf zu einem `FERTIG`, das nur bedeutete: Der Lauf hat
  aufgehört, es hier zu versuchen.

### Qualifikation (`marketing/qualifikation.py`) — die Frage vor dem Text

```
Entdecken → Regeln lesen → Beitreten → Warten → Bewerten → Arbeiten
```

und ausdrücklich **nicht** „Entdecken → überall posten". Seit 11.09.2026.

```powershell
& $py -m fbgroups.cli marketing regeln --limit 10   # Gruppenseiten lesen (Browser)
& $py -m fbgroups.cli campaign qualifikation essen-1109
& $py -m fbgroups.cli campaign qualifikation essen-1109 --stufe ohne_links
```

- **Gerechnet, nicht gespeichert.** `qualifikation.beurteile` bekommt
  Mitgliedschaft, Gruppenregeln und Versuchsprotokoll herein und liefert
  `Befund(qualifikation, grund)`. Keine Spalte hält die Einstufung — ein
  gespeichertes Urteil neben seinen eigenen Grundlagen läuft von ihnen weg,
  sobald sich eine ändert. Derselbe Gedanke wie beim Lauffortschritt.
  Gespeichert ist allein, was einen Abruf kostet: der **Regelbefund**
  (`group_marketing.regeln_gelesen_am` + vier Flags, Migrationsschritt 20).
- **Die Regeln der Gruppe binden, die Beobachtung schränkt nur ein.** Dass ein
  Link einmal durchging, heißt nicht, dass er erlaubt war. Eine Gruppe, deren
  Regeln Links verbieten, bleibt `OHNE_LINKS` — auch nach fünf geglückten
  Versuchen. Umgekehrt gilt es sehr wohl: Wer ohne verbietende Regel
  wiederholt ablehnt, wird eingeschränkt. Test:
  `test_die_regeln_der_gruppe_binden_auch_gegen_die_beobachtung`.
- **„Nicht gelesen" ist etwas anderes als „nichts verboten."**
  `Regelbefund.gelesen` trennt beides; ohne diese Unterscheidung wäre die
  Abwesenheit einer Regel eine Erlaubnis, die niemand erteilt hat. Ein
  ungelesener Befund **schreibt nichts** (`merke_regeln`) — eine Anmeldewand
  ist kein Beleg dafür, dass eine frühere Regel weg ist. Dieselbe Regel wie
  `upsert_groups` mit `COALESCE`.
- **Technik ist kein Urteil.** `klassifiziere` trennt `MODERATION` von
  `TECHNISCH`, und im Zweifel gilt `TECHNISCH`: Eine geratene Ablehnung
  verurteilte eine Gruppe, gegen die nichts vorliegt. Genau das ist am
  11.09.2026 passiert — ein geschlossenes Browserfenster ließ dreißig
  Fassungen scheitern, und danach galten 45 Gruppen als erschöpft. Technische
  Fehlschläge landen in **keinem** Feld von `Beobachtung`.
- **Kommentare werden nach Link getrennt gezählt.** „Mit Link abgelehnt, ohne
  Link durchgegangen" ist das Muster, das `OHNE_LINKS` trägt; in einer
  gemeinsamen Zahl wäre es unsichtbar. Ob eine Fassung einen Link trug, sagt
  ihr Text — `store.beobachtungen` verbindet `post_versuche` mit
  `campaign_group_texte` über `(campaign_id, group_id, texttyp, nummer)`. Eine
  ausdrückliche Ablehnung („Link in Kommentar") genügt allein; sonst braucht
  es `MINDEST_BEOBACHTUNGEN` (2), weil eine einzelne Ablehnung alles sein kann.
- **Die drei Einschränkungen lassen jeweils das andere zu.** `OHNE_LINKS` nimmt
  denselben Kommentar ohne Link, `OHNE_KOMMENTARE` nimmt den Beitrag,
  `OHNE_BEITRAEGE` nimmt die Kommentare. Nur `UNGEEIGNET` und die
  Beitrittsstufen lassen gar nichts. **`darf` und `darf_nach_regeln` trennen
  genau hier**: Jenes beantwortet beide Fragen, dieses nur die nach der
  Gruppe — und nur dieses bindet ohne Schalter. **Der Beitrag trägt immer einen Link**
  (`pruefe_platzhalter` verlangt ihn) und fällt in einer linkscheuen Gruppe
  deshalb mit aus — die Regeln der Gruppe zu umgehen ist kein Ziel.
- **`BEWERTUNG` erlaubt ausdrücklich.** Aus nichts entsteht keine Beobachtung;
  eine Gruppe, in der nie etwas versucht wird, bliebe für immer unbewertet.
  Der erste Versuch *ist* die Bewertung.
- **Eine gesperrte Gruppe ist nicht erschöpft.** „Gibt nichts mehr her" wäre
  ein Urteil über die Gruppe; hier liegt eine Entscheidung von uns vor, und
  sie kann sich ändern — durch eine Aufnahme, einen gelesenen Regelsatz, einen
  umgelegten Schalter. Als erschöpft vermerkt wäre sie dauerhaft draußen.
- **`gesperrt` fragt nach den vorhandenen Fassungen, nicht nach der
  Möglichkeit.** „Diese Gruppe nähme einen Kommentar ohne Link" hilft nicht,
  wenn alle zehn Fassungen einen tragen: Der Lauf bliebe vor ihr stehen, und
  die nächste käme nie dran.
- **`qualifikation.pflicht` steht auf `false`** (`settings.yaml`) — und
  bedeutet seit dem 12.09.2026 nur noch, ob die **Beitrittsstufen** sperren.
  Was die Gruppe selbst erlaubt, bindet ohne Schalter (`darf_nach_regeln`,
  siehe „Der Kampagnenablauf und seine Reihenfolge"). Der Rest der Begründung
  gilt unverändert: Am 11.09.2026 stand keine einzige Gruppe auf `geeignet`,
  weil noch nie jemand ihre Regeln gelesen hat — hätte die Mitgliedschaft
  sofort gesperrt, stünde jede laufende Kampagne still, bis eine volle Runde
  aus Beitritt und Bewertung durch ist.
- **`marketing regeln` startet nie beiläufig** (Exit-Code 2 ohne `--limit`
  oder `--alle`) und läuft nur mit angemeldetem Browser: Über `httpx` kommt
  eine Anmeldewand, und ein Weg, der zuverlässig nichts findet, trüge
  „nichts verboten" in den Bestand ein.
- **Facebook bleibt Channel, nicht Kern.** Das Modul ist rein — kein Netz,
  keine Datenbank, kein Playwright; der Abruf steht in `automation/actions.py`,
  die Auswertung hier. Kein `if facebook` außerhalb. `lies_regeln` steht
  bewusst hier und nicht in `extract/`: Was eine Gruppe erlaubt, ist eine
  Frage des Marketing-Kanals, und `extract` dient dem Suchweg.

### Der Trichter und seine Zuordnung

- **Die Stufen sind unabhängig, die Zuordnung ist die einzige Klammer.**
  `click`, `landing_visit`, `registration`, `download`, `activation`,
  `qualified`, `conversion` werden jede für sich gezählt; `FUNNEL_ORDER` ordnet
  die *Anzeige*, nicht den Ablauf. Eine Registrierung ohne Download ist ein
  gültiger Zustand, ein Download ohne Registrierung ebenso, und
  „10 Registrierungen, 3 Downloads" heißt nicht, dass sieben Registrierungen
  fehlerhaft sind. Kein Ereignis setzt ein anderes voraus, keines erzeugt ein
  anderes mit — die einzige gemeinsame Frage ist: *welcher Facebook-Gruppe ist
  dieses Ereignis zu verdanken?*
- **`user_identities` überbrückt den Kennungswechsel.** Ein Mensch heißt auf
  dem Weg durch den Trichter nacheinander verschieden: erst `anon-…` (die
  Kennung, die sich die Web-App im Browser gibt), ab der Registrierung
  `user-8472`. Die Zuordnung hängt aber am **ersten Besuch** — dort steht der
  Tracking-Code. Genau an dieser Naht ist die vorige Fassung gescheitert:
  `erste_zuordnung` suchte nur unter der eigenen Kennung, fand für
  `user-8472` nichts und schrieb jeden Download ohne Gruppe fort. Meldet eine
  Anfrage **beide** Kennungen (`user_ref` + `anon_ref`), hält
  `verknuepfe_kennung` fest, dass sie derselbe Mensch sind; gelesen wird
  danach über alle Kennungen der Identität. Die Benutzerkennung gewinnt als
  gemeinsame Identität — sie ist die bestehende, die anonyme verschwindet mit
  dem Browserspeicher.
- **Verknüpfen ändert kein gespeichertes Ereignis.** Die Zeilen behalten die
  Kennung, unter der sie gemeldet wurden; zusammengeführt wird beim Lesen. Eine
  Zuordnung, die einmal in der Datenbank steht, ist die Grundlage von Zahlen,
  die jemand schon gesehen hat — sie nachträglich umzuschreiben hieße, eine
  Auswertung von gestern unbemerkt ungültig zu machen.
- **Ohne erkennbaren Menschen bleibt ein Ereignis ohne Zuordnung.** Ein
  Download ohne Vorgeschichte wird gezählt, aber keiner Gruppe zugeschlagen.
  Eine geratene Zuordnung wäre schlimmer als eine fehlende: Sie schöbe eine
  Gruppe in einer Rangliste nach oben, nach der sich entscheidet, wo die
  nächsten 300 Beiträge geschrieben werden. Aus demselben Grund verwirft
  `POST /events` einen **unbekannten** Tracking-Code (Tippfehler, alter
  Beitrag) und erbt stattdessen — ein erfundener Code bekäme sonst eine eigene
  Spalte in jeder Auswertung.
- **`download` zählt je Mensch einmal** (`EINMAL_JE_MENSCH` in `models.py`).
  Ein Download ist von Natur aus wiederholbar — neu laden, zweites Gerät,
  Neuinstallation. Ohne diese Schranke überholte ein einzelner Mensch mit fünf
  Versuchen eine Gruppe, die vier Menschen gebracht hat. `registration`,
  `activation`, `qualified` und `conversion` stehen bewusst **nicht** darin:
  Sie sind seit dem 18.08.2026 im Betrieb, und ihre Bedeutung stillschweigend
  zu ändern machte alte und neue Zahlen unvergleichbar. Die zweite Meldung
  bekommt `200` mit `gezaehlt: false` — sie ist angekommen, sie hat nur nicht
  gezählt.
- **„Download" ist nicht „installiert".** Drei verschiedene Dinge lassen sich
  zählen: ein Knopfdruck, ein tatsächlich beginnender Transfer und eine App,
  die auf einem Gerät liegt. Die ersten beiden kann nur die ausliefernde Stelle
  unterscheiden (dieses Projekt ruft nichts ab und sieht keine Datei);
  `download` heißt deshalb **ausgelöst**, so spät gemessen wie die Plattform es
  erlaubt. Der Beleg, dass die App wirklich angekommen ist, ist `activation` —
  und den kann allein die App selbst liefern. Deshalb sind es zwei Stufen und
  nicht eine.
- **Die Antwort nennt den Code.** `POST /events` gibt `tracking_code` zurück —
  
  Beleg für die meldende Anwendung: Ein leerer Wert heißt „ohne Gruppe
  gespeichert" und ist das erste sichtbare Zeichen dafür, dass die Verknüpfung
  nicht stattfindet.
- **`marketing code <CODE>` beantwortet die Frage, die eine Summe nicht
  beantwortet.** Mit `--benutzer` steht je Mensch daneben, unter welchen
  Kennungen er auftrat und welche Stufen auf ihn entfallen — die Begründung
  dafür, dass dieser Download zu diesem Code gehört, statt sie glauben zu
  müssen.
- **Das Geheimnis gehört nicht in die Web-App.** Was der Browser lesen kann,
  kann ein Besucher lesen. `landing_visit` und `download` meldet die Web-App
  deshalb an die eigene API (`POST /api/v1/tracking/events`), die mit dem
  `EVENTS_TOKEN` weiterreicht. Der Weg nimmt **nur** diese beiden Stufen und
  liest `user_ref` niemals aus dem Rumpf, sondern aus dem Zugangstoken:
  `qualified` und `conversion` verschieben Empfehlungsstände und schalten
  Prämien frei — ein öffentlicher Weg, der sie annähme, wäre ein Prämienhahn.

### Der oeffentliche Kurzcode (`marketing/kurzcode.py`)

```
Beitrag       go.b-tarikak.de/r/8wa6dja      <- der Deckname
Weiterleitung loest auf  ->  FB-SYR-DUE-004  <- der innere Code
Auswertung    FB-SYR-DUE-004                 <- unveraendert
```

Seit 14.09.2026. Der Anlass steht in einem Satz: `FB-SYR-DUE-004-B` nennt
jedem Leser Kanal, Zielgruppe, Stadt und laufende Nummer — das ist die
Buchhaltung der Kampagne, und sie stand bis dahin in jedem Beitrag.

- **Es ist kein zweites Tracking, sondern ein Deckname.** Gezaehlt,
  gespeichert und ausgewertet wird unter dem **inneren** Code; `aufloesen` ist
  die eine Stelle, an der aus einer Adresse ein Datensatz wird, und sie gibt
  ihn immer zurueck. Landete der Deckname in `tracking_events`, zerfiele jede
  Auswertung in zwei Haelften — eine fuer Beitraege von vorher, eine fuer die
  von nachher —, und der Tabelle saehe man es nicht an. Test:
  `test_gespeichert_wird_der_innere_code`.
- **Nach aussen ueberall, nach innen nirgends.** Den Decknamen tragen: der
  Link im Beitrag (`{link}`), `{tracking_code}`, `?ref=` an der Landingpage
  und der Play-`referrer`. `POST /events` loest ihn wieder auf — die Web-App
  liest ihn ja aus `?ref=` und kennt den inneren gar nicht.
- **Abgeleitet und trotzdem gespeichert.** `blake2b`-artig aus Code und einem
  Geheimnis in `marketing_meta`: Geht die Spalte verloren, kommt derselbe Code
  wieder. Gespeichert wird er, weil die Weiterleitung ihn rueckwaerts braucht
  und 300 Codes je Klick durchzurechnen die teuerste Zeile des Dienstes waere.
  Das Geheimnis ist kein Passwort — es verhindert, dass ein Leser aus einem
  Beitrag die Adressen der Nachbargruppen ausrechnet.
- **Das Alphabet hat 29 Zeichen, und die fehlenden sind der Grund.** Kein
  `0/o`, kein `1/l/i`, kein `u/v`: Wer die Adresse abtippt, verwechselt genau
  die — und eine verwechselte Stelle ist kein Tippfehler mit Fehlermeldung,
  sondern ein Klick, der einer **anderen** Gruppe gutgeschrieben wird.
- **Ein vergebener Kurzcode aendert sich nie**, wie der Tracking-Code selbst.
  Er steht moeglicherweise in einem veroeffentlichten Beitrag. Neue
  Zuordnungen bekommen ihn in `add_link`; der Bestand ueber
  `campaign kurzlinks` (wiederholbar, fasst Vorhandenes nie an) und beiliegend
  beim Einfrieren eines Laufs.
- **Der alte lange Link bleibt gueltig.** Er steht in Beitraegen, die seit
  Wochen in Gruppen stehen; zurueckholen laesst sich keiner davon. `aufloesen`
  sucht deshalb in **vier** Spalten. Test:
  `test_der_alte_lange_link_funktioniert_weiter`.
- **Fehlt der Kurzcode, geht die lange Adresse hinaus.** Der Rueckfall ist
  Absicht: Ein Datensatz aus der Zeit davor soll einen Beitrag bekommen, der
  funktioniert, und nicht einen ohne Link. **Seit dem 20.09.2026 sagt der Lauf
  es aber** (`automatik._fuehre_schritt_aus`): Genau dieser Rueckfall ist der
  einzige Weg, auf dem "FB-SYR-BER-010-B" in eine Gruppe kommt - also die
  lange rohe Adresse, die dort nichts zu suchen hat. Ohne die Meldung faellt
  es erst auf, wenn der Beitrag steht; nachgetragen wird es mit
  `campaign kurzlinks`.
- **Der Vorspann kommt aus der gespeicherten Tracking-Adresse**, nicht aus
  `app_base_url`: Beide Adressen desselben Paares sollen auf denselben Dienst
  zeigen. `refresh-urls` stellt deshalb alle **vier** um — nur die Haelfte
  hiesse, dass ein Beitrag je nach Alter auf zwei Dienste zeigt, und gemerkt
  haette man es an einer Zahl, die nicht mehr steigt.
- **Auf dem Bildschirm des Bearbeiters steht er weiter.** `campaign next`
  zeigt den Tracking-Code in der Ueberschrift, `campaign links` in der ersten
  Spalte — dort ist "um welche Zuordnung geht es?" die erste Frage. Die CSV
  traegt `public_url` **hinten angehaengt**, damit eine bestehende Tabelle
  weiter passt.

### Die Vorschaukarte traegt keine Meta-Weiterleitung

`_vorschauseite` hatte bis zum 14.09.2026 ein
`<meta http-equiv='refresh'>` auf ihr Ziel. **Facebooks Abrufer folgt dem und
beschreibt, was er am Ende findet** — bei einem Store-Code also
`play.google.com`. Im Beitrag stand die Karte von Google Play, mit fremdem
Namen und fremdem Bild, und die sorgfaeltig gesetzten Angaben darueber las
niemand. Weil das Ziel je Fassung wechselt (ungerade Browser, gerade Store),
geschah es nur bei jedem zweiten Beitrag — daher „manchmal".

- **Weitergeleitet wird jetzt mit einer Zeile JavaScript.** Genau darin liegt
  der Unterschied: Ein Browser fuehrt sie aus, ein Abrufer nicht. Der Mensch,
  der diese Seite doch sieht (ein Browser mit ungewoehnlicher Kennung), kommt
  weiter; die Plattform bleibt bei dem stehen, was fuer sie geschrieben ist.
- **`og:site_name` ist dazugekommen** (`marketing.vorschau.seitenname`). Ohne
  ihn zeigt Facebook unter der Ueberschrift die Domain, und
  „go.b-tarikak.de" liest sich wie eine technische Adresse und nicht wie eine
  App.
- **`og:url` bleibt die eigene Adresse.** Zeigte sie auf das Ziel, fuehrte die
  Karte an der Zaehlung vorbei.
- **Facebook merkt sich die Karte je Adresse** (unveraendert). Ein bereits
  veroeffentlichter Beitrag bekommt die neue Karte deshalb **nicht**
  nachtraeglich — nur neue Beitraege. Pruefen laesst sich eine Adresse
  gefahrlos im Sharing Debugger: Dessen Abruf traegt `facebookexternalhit` und
  zaehlt nicht als Klick.

### Die Adresse baut die Karte und geht dann aus dem Text

`post_to_group` fuegt den vollen Text ein, wartet auf die Vorschaukarte,
**nimmt die nackte Adresse wieder heraus** und sendet dann ab — der Handgriff,
den ein Mensch macht. Die Karte bleibt anklickbar und fuehrt weiterhin auf
`/r/{code}`, der Klick wird also weiterhin gezaehlt; was verschwindet, ist
allein die Adresse im Text.

- **Kein stiller Erfolg.** Haelt Facebook die Karte ohne die Adresse nicht,
  wird der volle Text wiederhergestellt und gepostet — und das gesagt:
  `Beitragsausgang.link_sichtbar` und `hinweis` wandern ins Protokoll.
  `post_to_group` liefert deshalb kein `bool` mehr, dieselbe Lehre wie bei
  `comment_on_post` am 12.09.2026. Ein Beitrag ohne Link waere schlimmer als
  einer mit sichtbarer Adresse: Seine Gruppe bekaeme nie einen Klick
  gutgeschrieben.
- **`trenne_adresse` ist rein und traut sich wenig zu.** Entfernt wird nur bei
  **genau einer** Adresse (bei mehreren wuessten wir nicht, welche die Karte
  gebaut hat) und nur, wenn sie **am Zeilenende** steht — mitten im Satz
  hinterliesse das Entfernen eine Luecke darin. Ein Text, der nur aus dem Link
  bestand, bleibt unangetastet: Ein leerer Beitrag ist kein Beitrag.
- **Ersetzt wird der ganze Entwurf** (`Strg+A`), nicht die Adresse einzeln.
  Wo genau sie im Feld steht, weiss nur Facebooks Editor; ein Markieren trifft
  immer.
- **Nur mit Karte.** Ohne sie naehme das Entfernen dem Beitrag seinen Link
  ersatzlos. `link_verbergen=False` ist der Ausweg, falls Facebook den
  Handgriff einmal nicht mehr mitmacht.
- **Der Kommentar bleibt unberuehrt.** Unter einem fremden Beitrag gibt es
  keine Anlage, die eine geloeschte Adresse ueberlebt — dort steht der Link
  weiterhin im Text (und in linkscheuen Gruppen gar nicht, siehe
  `Linkmodus`).

### Weiterleitung und Meldungen

- **`GET /r/{code}`** zählt den Klick und leitet mit **302** weiter (nicht 301:
  ein dauerhaft gemerkter Umzug führte spätere Klicks am Zähler vorbei). Ein
  unbekannter Code ergibt 404 statt einer stillen Weiterleitung.
- **`GET /r/{code}` beantwortet Vorschau-Abrufe selbst** (seit 10.09.2026,
  `marketing.vorschau` in `settings.yaml`). Facebook, WhatsApp und Telegram
  holen einen frisch geposteten Link ab und bauen daraus die Karte; ohne sie
  steht im Beitrag die nackte Adresse - `go.b-tarikak.de/r/FB-SYR-BER-010-B`
  liest sich wie ein Code und nicht wie eine App. Vorher folgte der Abruf der
  Weiterleitung und nahm, was am Ziel stand: beim Store-Code die Karte von
  Google, beim Browser-Code die der Landingpage - zwei Gesichter fuer eine
  App. `og:url` bleibt dabei die **eigene** Adresse; zeigte sie auf das Ziel,
  fuehrte die Karte an der Zaehlung vorbei. Ohne `vorschau.bild` bleibt es
  bei der Weiterleitung: Eine Karte ohne Bild zeigt Facebook ohnehin nicht.
  Gezaehlt wird ein Vorschau-Abruf weiterhin nicht (`_ist_linkvorschau`).
- **Facebook merkt sich die Karte je Adresse.** Jeder Tracking-Code ist eine
  eigene Adresse, also wird sie je Beitrag genau einmal gebaut - beim ersten
  Posten. Ein bereits veroeffentlichter Beitrag bekommt sie nicht nachtraeglich;
  pruefen laesst sich eine Adresse gefahrlos im Sharing Debugger, denn dessen
  Abruf traegt `facebookexternalhit` und zaehlt deshalb nicht als Klick.
- **`post_to_group` wartet auf die Vorschaukarte, bevor es absendet**
  (`_warte_auf_vorschau`, bis 15 s). Sie entsteht erst, nachdem Facebook den
  Link im Entwurf entdeckt und selbst abgerufen hat. Der erste automatisch
  gesetzte Beitrag ging ohne sie hinaus, weil zwischen Einfuegen und Absenden
  nur wenige Sekunden lagen. Erkannt wird sie am Knopf zum Entfernen der
  Karte oder an einem Bild mehr im Dialog - nicht am Text des Entwurfs, denn
  die Adresse steht ohnehin darin. Kommt keine, wird trotzdem gepostet.
- **`POST /events`** nimmt die Meldungen der Zielanwendung entgegen.
- **Hinter einem Reverse Proxy braucht uvicorn `--proxy-headers
  --forwarded-allow-ips 127.0.0.1`.** Ohne das ist `request.client.host` für
  jeden Besucher `127.0.0.1`; der `visitor_hash` entsteht dann fast nur noch aus
  dem User-Agent, und zwei Besucher mit demselben Browser am selben Tag zählen
  als **ein** Klick. nginx muss dazu `X-Forwarded-For $remote_addr` setzen
  (überschreiben, nicht anhängen – sonst ist der Wert vom Client beeinflussbar).
  Derselbe Schalter hält auch `_nur_lokal` intakt: ohne ihn hielte der Dienst
  jeden Aufruf für lokal und gäbe die Arbeitsliste heraus.
- **Die Übersicht zeigt die Trichterzahlen je Gruppe in derselben Zeile** wie
  Score und Arbeitsstand – nicht als zweite Bestenliste daneben. Sonst gäbe es
  einen zweiten Satz Zahlen, den man getrennt filtern und sortieren müsste.
  Sind Links vergeben, aber kein einziger Klick da, erscheint ein Hinweis: Das
  ist fast immer eine Domain, die auf eine andere Anwendung zeigt, und es fällt
  sonst nicht auf, weil der Besucher ja eine Seite sieht.
- **Die Übersicht hat zwei Zugänge mit verschiedenen Rechten.** Vom selben
  Rechner (SSH-Tunnel) ist sie bedienbar; von außen zeigt nginx sie hinter
  Basic Auth **nur lesend** (`UEBERSICHT_TOKEN`, Kopfzeile
  `X-Uebersicht-Token`, `location = /uebersicht`). Der Unterschied ist kein
  Rest, sondern der Punkt: `campaign sync` vergibt Tracking-Codes, und ein
  vergebener Code wird nie zurückgenommen — er steht später in
  veröffentlichten Beiträgen. Ein abhandengekommenes Passwort soll Zahlen
  zeigen können und sonst nichts. Die Absicherung liegt dabei **im Dienst**
  (`_nur_lokal` bewacht jeden schreibenden Weg unverändert weiter), nicht in
  der nginx-Regel `limit_except`; die ist der zweite Riegel. Das Ausblenden
  der Knöpfe (`render(..., nur_lesen=True)`) sichert gar nichts — es ist
  Aufrichtigkeit: Ein Knopf, dessen Weg mit 404 antwortet, sieht aus wie ein
  Fehler der Seite. Ausgeblendet wird per CSS statt entfernt, weil das Skript
  mehrere dieser Knöpfe beim Start sucht und sonst die ganze Seite leer bliebe.
- **Das Geheimnis ist geheim, obwohl nginx die Kopfzeile überschreibt.**
  `proxy_set_header` macht einen vom Besucher mitgeschickten Wert wirkungslos —
  aber nur in dem Block, der es aufruft. Ohne eigenen Wert hinge der Schutz
  daran, dass jeder künftige `location`-Block das Überschreiben nicht vergisst.
  Ein Weg, der Auskunft gibt, bringt seinen Schutz selbst mit.
- **Die Tests laufen nicht gegen die `.env` des Rechners.** `load_config` hebt
  sie über `load_dotenv` in die Prozessumgebung; im Test entschied damit der
  Arbeitsrechner mit. Wer einen `EVENTS_TOKEN` eingetragen hatte — der
  Normalfall —, sah neun Tests scheitern, die ausdrücklich den Fall „kein
  Schlüssel gesetzt" prüfen. Die autouse-Fixture `_ohne_env_datei` in
  `tests/conftest.py` setzt die betroffenen Werte je Test auf **leer**, nicht
  gelöscht: `load_dotenv` läuft mit `override=False` und trüge einen gelöschten
  beim nächsten `load_config` wieder ein. Wer einen Schlüssel braucht, setzt
  ihn im Test — dann ist er eine Angabe des Tests und keine Eigenschaft des
  Rechners.
- **Keine IP-Adressen im Bestand.** Gegen Doppelklicks entsteht aus IP,
  User-Agent und **Tagesdatum** ein HMAC-Prüfwert (16 Zeichen, Zufallsschlüssel
  in `marketing_meta`). Er ist nicht zurückrechenbar und wechselt täglich —
  gerade genug fürs Entdoppeln, zu wenig zum Verfolgen. Test:
  `test_keine_ip_adresse_im_bestand`.
- **`user_ref` ist eine undurchsichtige Kennung** aus der Zielanwendung. Das
  Modell hat keine Felder für Namen, E-Mail oder Telefon — mitgeschickte Werte
  könnten gar nicht gespeichert werden.
- **Spätere Ereignisse erben die erste Zuordnung des Benutzers.** Beim
  Qualifizieren kennt die App nur noch ihren Benutzer, nicht den Tracking-Code.
  Ohne diese Erbschaft stünden genau die interessanten Stufen ohne Gruppe da.
  Maßgeblich ist der **erste** Fund — er hat den Menschen gebracht.
- **Referral-Regeln liefern immer eine Begründung** (`referral.Entscheidung`).
  Kein Selbst-Werben, ein Werber je Geworbenem (Datenbank-Unique), Empfehlung
  erst nach der Registrierung. Verdächtiges wird auf `review` gesetzt, nicht
  gesperrt — ein falsch abgewiesener echter Nutzer kostet mehr. Jeder Ausgang
  steht im Audit-Log, auch die Ablehnung.
- **Ein Status fällt nie von selbst zurück.** Eine nachklappernde Meldung darf
  aus `qualified` nicht wieder `registered` machen; Herabstufen ist Handarbeit
  über `review`.
- **`config/rewards.yaml` hält die Schwellen.** `qualified` zählt auch, was
  schon `converted` ist — sonst verlöre jemand seine Prämie, indem er
  weiterkommt. Eine Regel je Benutzer nur einmal (Unique in der Datenbank).
  Bewusst kein Geldbetrag als Prämientyp.
- **`conversion_rate` ist `None` bei null Klicks**, nicht 0,0 % — eine Quote
  ohne Grundgesamtheit gibt es nicht.
- **FastAPI ist optional** (`[web]`-Extra) und wird auf Modulebene importiert:
  Wegen `from __future__ import annotations` löst FastAPI Typangaben erst beim
  Bauen der Routen auf; stünden die Namen nur in `create_app`, beantwortete der
  Dienst jeden Aufruf mit 422.

## Windows-Fallstricke

- Dateien **immer** mit `encoding="utf-8"` öffnen; die Plattformvorgabe ist
  cp1252 und zerstört arabische Begriffe.
- Von Hand erstellte CSV-/TXT-Dateien **immer** mit `utf-8-sig` lesen. Notepad
  und `Out-File` schreiben ein BOM; ohne `utf-8-sig` wird es Teil der ersten
  Zeile und der erste Datensatz geht stillschweigend verloren. Der Seed-Import
  und `tests/test_encoding.py`, an denen diese Regel hing, sind am 20.09.2026
  entfallen — die Regel gilt für den nächsten Importweg genauso.
- CSV mit `utf-8-sig` und `;` schreiben, damit Excel Arabisch und Spalten
  korrekt darstellt.
- PowerShell 5.1 kennt kein `&&` und kein `||`; mit `;` und `if ($?) { }` ketten.
