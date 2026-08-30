# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projekt

Findet öffentlich auffindbare Facebook-Gruppen für Marketing-Kooperationen in
Deutschland (Zielmarkt: syrische und arabische Communities). Phase 1 ist
umgesetzt: manuelle Seed-URLs → Normalisierung → Dedupe → Klassifikation →
Scoring → SQLite → Excel/CSV.

Die Projektsprache ist **Deutsch** – Kommentare, Docstrings, CLI-Ausgaben und
Testnamen. Bitte beibehalten.

## Harte Projektgrenzen

Diese Grenzen sind mit dem Nutzer vereinbart und dürfen nicht ohne
ausdrückliche Aufforderung aufgeweicht werden:

- **NEU (geändert am 27.08.2026 / 29.08.2026):** Automatisches Posten und
  Kommentieren wird nun ausdrücklich unterstützt (`fbgroups campaign auto`), um
  das Tracking und die Metriken in einer geschlossenen Kette zu sichern.
  Erlaubt ist auch `extract/gruppenseite.py` und `fbgroups enrich`.
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
- Kein Suchdienst fest verdrahten. Vor Anbindung eines Providers dessen
  Verfügbarkeit für Neukunden prüfen (Google CSE: für Neukunden geschlossen,
  Einstellung 01.01.2027).

## Befehle

```powershell
$py = ".\.venv\Scripts\python.exe"
$env:PYTHONIOENCODING="utf-8"        # sonst bricht arabische Terminalausgabe

& $py -m pip install -e ".[dev]"
& $py -m pytest                      # alle Tests, offline
& $py -m pytest tests\test_urls.py::test_parse_valid_urls -v   # einzelner Test
& $py -m pytest -k arabisch
& $py -m ruff check src tests
& $py -m mypy

& $py -m fbgroups.cli config-check   # Konfiguration validieren
& $py -m fbgroups.cli import-seeds --dry-run
& $py -m fbgroups.cli pruefliste --top 40 # Liste zum Ausfuellen von Hand
& $py -m fbgroups.cli import-seeds data\seeds\pruefliste.csv   # ausgefuellt zurueck
& $py -m fbgroups.cli rescore --dry-run   # Wirkung geaenderter Gewichte zeigen
& $py -m fbgroups.cli enrich --dry-run    # wer waere an der Reihe? ruft nichts ab
& $py -m fbgroups.cli enrich --limit 5    # 5 oeffentliche Gruppenseiten lesen
& $py -m fbgroups.cli enrich --alle       # bis enrich.max_pro_lauf
& $py -m fbgroups.cli rescore        # Bestand neu bewerten, ohne Suchanfrage
                                     # holt dabei die gemessene Resonanz mit
& $py -m fbgroups.cli report
& $py -m fbgroups.cli export --format both
& $py -m fbgroups.cli queries --all
& $py -m fbgroups.cli providers      # Verfuegbarkeit, verbraucht nichts
& $py -m fbgroups.cli search --dry-run --show-all   # Plan + Verbrauch, ohne Abruf
& $py -m fbgroups.cli search --limit 5              # hoechstens 5 NEUE Anfragen
& $py -m fbgroups.cli search-log     # dauerhaftes Anfrageprotokoll
```

`mypy` meldet vorbestehend „missing py.typed marker"; setzt man den Marker,
erscheinen acht ältere Fehler in `report.py` und `importers/manual_seed.py`.
Beides ist unabhängig von der Suchschicht und noch offen.

## Architektur

Datenfluss (`pipeline.py` verkettet die Schritte, bleibt selbst frei von I/O):

```
importers/manual_seed  →  urls  →  dedupe  →  classify/*  →  scoring
                                                              ↓
                              storage/sqlite_store  +  storage/jsonl_store
                                                              ↓
                                                  export/{csv,excel}, report
```

Zentrale Entwurfsentscheidungen, die man mehreren Dateien nicht ansieht:

- **`config/*.yaml` ist die fachliche Wahrheit.** Zielgruppen, Städte,
  Kategorien, Scoring-Gewichte und Suchanfragen stehen nirgends im Code. Neue
  Städte/Zielgruppen werden über das Feld `phase` freigeschaltet
  (`phase: 2` → `phase: 1`), nicht durch Codeänderung.
- **Zwei Vergleichsstrategien in `textnorm.py`.** Lateinische Begriffe werden
  mit Wortgrenze verglichen, arabische als Teilstring – im Arabischen hängen
  Artikel und Präpositionen am Wort (`سوريين` steckt in `السوريين`). Wer das
  vereinheitlicht, zerstört die arabische Erkennung.
- **Dedupe hat zwei Verbindlichkeitsstufen.** Exakt über `group_id` wird
  automatisch zusammengeführt; Namensähnlichkeit wird nur als Verdacht
  gemeldet ("Syrer Berlin" vs. "Syrer Berlin 2" sind verschiedene Gruppen).
- **`providers/base.py` enthält nur den Vertrag.** Kein Modul außerhalb von
  `providers/` darf anbieterspezifischen Code importieren — abgesichert durch
  `test_kein_modul_ausserhalb_providers_kennt_einen_anbieter`. Neuer Provider =
  Klasse + `@register_provider(...)` + Config-Block + Contract-Tests.
  Implementiert: `fixture` (offline), `serper`, `brave`. Google CSE ist für
  Neukunden geschlossen (Abschaltung 01.01.2027), Bing seit 11.08.2025 tot.
- **Guthaben ist die knappe Ressource.** `storage/query_cache.py` hält jede
  erfolgreiche Antwort dauerhaft in einer eigenen SQLite-Datei
  (`data/query_cache.sqlite`), sodass dieselbe Anfrage nie zweimal an den
  Dienst geht — auch nicht nach einem Neustart. `max_queries_per_run` ist eine
  harte Obergrenze; `utils/rate_limit.py` hält den Mindestabstand ein, statt in
  ein 429 zu laufen; `QuotaExhaustedError` beendet den Lauf geordnet. Wer hier
  etwas ändert, ändert direkt die Kosten für den Nutzer.
- **`--limit N` begrenzt neue Anfragen, nicht geplante.** Eine gespeicherte
  Anfrage kostet nichts und darf deshalb kein Kontingent des Laufs belegen —
  sonst käme ein zweiter Lauf nie über die bereits bezahlten Anfragen hinaus.
  Fehlschläge landen im Protokoll, aber nicht im Speicher: ein einzelner
  Netzwerkfehler wäre sonst tagelang bindend.
- **`search.build_plan` ist die einzige Quelle für den Verbrauch.** `--dry-run`
  und der echte Lauf lesen denselben Plan; eine zweite Zählung könnte von der
  Ausführung abweichen und würde damit falsche Kosten versprechen.
- **Ein Suchlauf startet nie beiläufig.** Ohne `--limit` oder `--alle` bricht
  `fbgroups search` mit Exit-Code 2 ab, ohne etwas abzufragen. `fallback_chain`
  ist bewusst leer — ein Providerwechsel verbraucht fremdes Guthaben und ist
  eine Entscheidung, keine Ausweichreaktion.
- **Suche und Import münden in denselben Weg.** `pipeline.prepare_groups` wird
  von beiden genutzt — ein Suchtreffer wird exakt wie eine manuelle Zeile
  behandelt (Dedupe, Klassifikation, Validierung, Scoring).
- **Der Anfragetext entsteht in `search.build_query_text`.** `site:`-Operator
  nur bei `supports_site_operator`, Suchbegriff in Anführungszeichen
  (`quote_phrase` in `queries.yaml`). Ohne die Anführungszeichen liefert die
  Suche auch Treffer mit verstreuten Einzelwörtern — jeder davon kostet ein
  Credit, ohne etwas beizutragen.
- **`extract/enrich.py` erfindet nichts.** Mitgliederzahl und Sichtbarkeit
  werden nur übernommen, wenn sie im Treffertext ausdrücklich benannt sind
  („12.400 Mitglieder", „Öffentliche Gruppe"). Eine bloße Zahl im Titel gilt
  nicht als Beleg.
- **`drop_shared_snippets` verwirft geteilte Beschreibungstexte.** Google
  liefert für Facebook-Gruppen oft denselben Text zu mehreren Ergebnissen
  (erster Livelauf: 5 von 10 Treffern trugen den Text einer sechsten Gruppe).
  Ungefiltert wandern Mitgliederzahl, Sichtbarkeit und Stadt in fremde
  Datensätze. Maßgeblich ist die Zahl **verschiedener URLs**, nicht die Zahl
  der Treffer — dieselbe Gruppe bringt aus mehreren Anfragen zu Recht denselben
  eigenen Text mit. Deshalb wertet `search._auswerten` erst nach der letzten
  Anfrage aus: der Fremdtext verteilt sich über Anfragen hinweg. Verworfen wird
  bei der Auswertung, nie im Anfragespeicher — die Rohantwort bleibt vollständig.
  Ein Fremdtext, der nur **einmal** vorkommt, ist damit nicht erkennbar; das
  
- **`credits` von Serper ist Verbrauch, nicht Restguthaben.** Der Wert steht
  für die Kosten *dieser* Anfrage (meist 1). Als `quota_remaining` gelesen
  ergab er die grob falsche Anzeige „Restguthaben 1". Deshalb trennt
  `SearchResponse` beides: `credits_used` und `quota_remaining`. Den Kontostand
  meldet Serper nicht — er steht allein im Konto unter serper.dev/dashboard.
- **Kein Score ohne Grundlage.** `Group.score` ist `float | None`; `None`
  bedeutet nicht bewertbar, der Grund steht in `score_reason`. Es gibt keinen
  Ersatzwert für fehlende Daten — eine frühere Fassung vergab bei unbekannter
  Mitgliederzahl einen „neutralen" Faktor und erzeugte damit für jede Gruppe
  ohne Metadaten denselben Score (8,75).
- **Der Score sind 100 Punkte aus fünf Bestandteilen** (seit 27.08.2026):

  | Bestandteil       | Punkte | Grundlage                                |
  |-------------------|-------:|------------------------------------------|
  | `members`         |     25 | Mitgliederzahl, logarithmisch gestuft    |
  | `activity`        |     25 | Betrieb in der Gruppe (drei Quellen)     |
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
  Größe, sondern ob dort etwas geschieht. Sie war bis zum 27.08.2026 mit 45
  von 100 das schwerste Kriterium und dann abgeschaltet (`member_count: 0`),
  weil sie ohne facebook.com in **273 von 273** Suchtreffern fehlte. Mit dem
  geöffneten Abruf ist sie wieder eingeschaltet — mit 25 statt 45 Punkten.
- **Die Aktivität hat drei Quellen, und ihre Reihenfolge ist begründet**
  (`activity_source`):
  1. `facebook` — die Beitragsliste der Gruppenseite. Sie misst **die Gruppe**
     und ist damit die Antwort auf die gestellte Frage. Konfidenz 1,0.
  2. `resonanz` — Klick auf den Tracking-Link, Registrierung in der App. Sie
     misst, was von dort zu **uns** kommt; in mancher Hinsicht die bessere
     Frage (eine Gruppe mit 500 Mitgliedern und 40 Registrierungen ist mehr
     wert als eine mit 5.000 und zwei), aber eine andere. Konfidenz 0,8.
  3. `search_dates` — das `date` eines indexierten Suchtreffers. Es belegt,
     **dass** die Gruppe lebt, und sonst nichts; eine Beitragszahl je Tag ist
     daraus nicht abzuleiten. Konfidenz 0,35. Bewertet wird allein die Frische
     des jüngsten Fundes, **nicht die Anzahl** der datierten Treffer: Die
     hängt daran, wie oft eine Gruppe in unseren Anfragen auftauchte, und das
     ist eine Eigenschaft unserer Anfragen.
- **Die Aktivität ist bewusst unabhängig von der Mitgliederzahl.** Sonst ließe
  sich der Fall nicht abbilden, für den es sie gibt: 100.000 Mitglieder und
  kaum neue Beiträge schlagen 20.000 mit täglichem Betrieb **nicht**. Test:
  `test_grosse_stille_gruppe_verliert_gegen_kleine_lebendige`.
- **`scoring.Resonanz` beschreibt die Zahlen, `marketing/resonanz.py` beschafft
  sie.** Die Richtung ist Absicht: `scoring.py` kennt weder `MarketingStore`
  noch die Ereignistabelle, so wie die Marketing-Erweiterung den Bestand nicht
  veraendert. Der Aufrufer (`rescore`, die Uebersicht) reicht die Zahlen herein.
  Ein Import in die andere Richtung machte den Kern von einem Aufsatz abhaengig.
  Die Resonanz ist seit dem 27.08.2026 **kein eigener Block mehr**, sondern
  eine Quelle von `activity` — zwei Blöcke wären zweimal dieselbe Frage.
- **"Nicht gemessen" ist etwas anderes als "wirkungslos".** Ohne
  veroeffentlichten Beitrag liefert `_resonanz_faktor` `None`, `activity`
  erscheint als "unbekannt" und `score_max` sinkt um 25. Null Klicks ohne
  Beitrag sind eine Aussage ueber **uns**, nicht ueber die Gruppe. Dasselbe
  gilt fuer die Schonfrist (`schonfrist_tage: 3`): Wer vor zwei Stunden
  gepostet hat, hat noch keine Klicks — eine Null waere hier eine Behauptung
  ueber die Zukunft. Ein Beitrag **mit** null Klicks ist dagegen ein Ergebnis
  und wird als solches bewertet (`score_max` voll, `activity` 0).
- **Die Zielquote ist 15 %, nicht 100 %** (`resonanz.ziel_quote`). Wer die
  Registrierungsquote auf 1,0 normiert, gibt selbst der besten Gruppe ein
  Sechstel der Punkte und macht den Bestandteil wirkungslos. Daneben steht die
  Belastbarkeit (`mindest_klicks: 20`): 1 Klick mit 1 Registrierung sind 100 %
  und beweisen nichts — ohne diese Schranke stuende jede zufaellige Gruppe an
  der Spitze. Die drei Teilmaße werden über `resonanz.anteile` zu **einem**
  Faktor verrechnet (Engagement 0,60 · Reichweite 0,25 · Aktualität 0,15) und
  auf ihre Summe normiert, damit ein Tippfehler die Obergrenze nicht sprengt.
- **Reichweite zaehlt je Beitrag, nicht absolut.** Sonst gewaenne die Gruppe, in
  der wir am oeftesten gepostet haben, statt der, die am besten wirkt.
- **`data_confidence` steht neben dem Score, nie darin.** Ein mäßiger Score
  aus belegten Zahlen und ein guter aus dünnen Hinweisen sind zwei Aussagen;
  verrechnet wären beide unlesbar. Zwei Dinge fließen ein: **wie sicher** die
  Angaben sind (die Konfidenzen der Befunde) und **wie viel** überhaupt vorlag
  (der Anteil des beurteilten am möglichen Gewicht). Ohne das zweite bekäme
  eine Gruppe, von der nur die Stadt bekannt ist, aber zweifelsfrei, die volle
  Confidence — die Zahl sagte dann das Gegenteil dessen aus, wozu sie da ist.
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
  als „unbekannt" in `score_reason` — er wird gar nicht erst erwartet. So
  steht `name_quality` auf 0: Die Form des Namens sagt etwas über **unsere
  Daten** und nichts über die Gruppe, sie gehört in `data_confidence`. Die
  Regel dahinter (`_namensform`) bleibt vollständig erhalten, damit das
  Wiedereinschalten eine Zahlenänderung ist und keine Codeänderung.
- **`fbgroups enrich` startet nie beiläufig.** Ohne `--limit` oder `--alle`
  bricht der Befehl mit Exit-Code 2 ab, ohne etwas abzurufen — dieselbe
  Vorsicht wie bei `fbgroups search`, aber aus einem anderen Grund: Dort geht
  es um Guthaben, hier um das Konto des Nutzers. Die Befunde liegen in
  `data/gruppenseiten.sqlite` (eigene Datei wie der Anfragespeicher), damit
  ein zweiter Lauf keinen zweiten Abruf kostet; `enrich.hoechstalter_tage`
  entscheidet, wann ein Befund als veraltet gilt. `mindestabstand_sekunden: 6`
  ist bewusst groß — bei 313 Gruppen gut eine halbe Stunde. Das ist der Preis
  dafür, dass niemand den Abruf für einen Angriff hält.
- **Ein Anmeldefenster ist ein gültiger Befund, kein Fehler.** Facebook
  liefert einem nicht angemeldeten Abruf häufig eine Anmeldeseite;
  `Seitenbefund.erreichbar` bleibt dann `False`, alle Zahlen bleiben `None`,
  und `checked_at` wird **trotzdem** gesetzt — sonst liefe derselbe erfolglose
  Abruf bei jedem Lauf erneut. Eine nicht gefundene Zahl **löscht keine
  vorhandene**: Ein Anmeldefenster ist kein Beleg dafür, dass die Gruppe
  geschrumpft ist.
- **`upsert_groups` schützt erhobene Zahlen mit `COALESCE`.** Ein Suchlauf
  schreibt jeden gefundenen Datensatz neu und bringt weder Mitgliederzahl noch
  Aktivität mit. Ohne diesen Schutz löschte jeder `fbgroups search`, was
  `fbgroups enrich` in einer halben Stunde erhoben hat — und niemand merkte
  es, weil der Score einfach wieder sank. Test:
  `test_ein_suchlauf_loescht_erhobene_zahlen_nicht`.
- **Migrationsschritt 15 rechnet keine alten Scores um.** Ein Score aus den
  alten Gewichten (45/25/15/8/7 plus 75 Resonanzpunkte) lässt sich nicht in
  die neuen übersetzen; ein geratener Umrechnungsfaktor stünde hinterher in
  der Rangliste, nach der entschieden wird, wo die nächsten dreihundert
  Beiträge hingehen. Neu bewertet wird mit `fbgroups rescore` — das ist ein
  Befehl und damit eine Entscheidung. Ebenso wird `member_count_source` für
  Bestandszahlen **nicht** auf `search` gesetzt: Die Migration liest eine
  Spalte, in der auch eine von Hand gepflegte Zahl stehen könnte, und eine
  Migration, die Herkunft behauptet, erfindet Daten.
- **Die Spalte heißt `member_count_hint`, das Feld `member_count`.** Migrationen
  sind hier ausschließlich additiv, und ein `RENAME COLUMN` ist keine additive
  Änderung; `_row_to_group` bildet den Namen ab. Ein zweites Feld für dieselbe
  Zahl anzulegen wäre die schlechtere Lösung — zwei Wahrheiten über eine Zahl.
- **`ValidationStatus.UNREACHABLE` ist ein Menschenurteil.** Nur wer die
  Gruppe im Browser geöffnet hat, kann sie für tot erklären. `upsert_groups`
  nimmt dieses Urteil deshalb nie zurück — ein späterer Suchtreffer belegt
  bloß, dass die URL einmal indexiert wurde.
- **Sortiert wird über `scoring.sort_by_rank`,** auch im Export. Erst die
  Punkte, bei Gleichstand der **Anteil** an den erreichbaren Punkten: 55 von
  55 steht vor 55 von 100. Die beste Gruppe steht damit in Zeile 2 der Datei.
- **Der Score wird nicht hochgerechnet.** Er ist die Summe der belegten
  Punkte; `score_max` nennt das bei dieser Datenlage Erreichbare. Das gilt
  unverändert für jeden **eingeschalteten** Bestandteil, der fehlt — nicht für
  einen mit Gewicht 0. Die zweite Fassung normierte stattdessen über die
  vorhandenen Bestandteile auf 100 — bei 134 von 138 Gruppen ohne
  Mitgliederzahl bekam damit eine Gruppe, von der nur der Name bekannt war,
  denselben Höchstwert wie eine belegte Großgruppe. 27 Gruppen standen auf
  exakt 100. Wer die Normierung zurückholt, holt die Häufung zurück.
- **Jeder Bestandteil zählt genau einmal.** `name_quality` beurteilt nur die
  *Form* des Namens (vollständig, kurz, keine Satzform). Vergab es zusätzlich
  Punkte für Zielgruppe und Stadt, erreichten beide Bestandteile stets
  gemeinsam ihr Maximum — die zweite Ursache der Häufung.
- **Ein Stadtname zählt nicht zusätzlich als Kategoriebegriff.**
  `classify_category` bekommt die erkannte `city_id` und übergeht Begriffe, die
  ein Name **dieser** Stadt sind. „Essen" ist eine Stadt mit 600.000 Einwohnern
  *und* der deutsche Kategoriebegriff für Speisen: Jede Gruppe „Syrer in Essen"
  bekam neben den Stadtpunkten die vollen 16 Kategoriepunkte und stand in der
  Übersicht als Essensgruppe — 8 von 16 Treffern der Kategorie. Nach der
  Korrektur stehen sie bei 84 statt 100, also hinter den wirklich besseren
  Gruppen. Ausgeschlossen wird nur die **erkannte** Stadt: „Arabisches Essen in
  Berlin" behält seine Kategorie. Ein systematischer Abgleich aller Stadtnamen
  gegen alle Kategoriebegriffe fand genau diese eine Kollision.
- **Jede Konfidenz unterscheidet Name und Beschreibungstext.** Zielgruppe,
  Stadt *und* Kategorie liefern 1,0 bei einem Treffer im Namen und 0,5 bei
  einem Treffer im Beschreibungstext. Die Kategorie war binär und vergab für
  ein beliebiges Stichwort irgendwo im Text die volle Punktzahl.
- **`fbgroups rescore` nach jeder Änderung an Gewichten oder Klassifikation.**
  Ein Suchlauf bewertet nur die Gruppen neu, die er gerade findet; der übrige
  Bestand behielte alte Werte, und im Export stünden zwei Bewertungen
  nebeneinander. `rescore` schreibt über `SqliteStore.update_scores` nur
  abgeleitete Felder — nicht über `upsert_groups`, das `times_seen` hochzählen
  würde: eine Neubewertung ist kein Fund.
- **`data_quality` zählt nur erhobene Felder** (Name, Beschreibung,
  Mitgliederzahl, Sichtbarkeit). Zielgruppe, Stadt und Kategorie sind aus dem
  Namen abgeleitet und keine zusätzliche Information; mitgezählt meldeten sie
  „complete" für Datensätze, die nichts als Name und Beschreibungstext hatten.
- **Ein Treffer auf einen Beitrag ist kein Gruppenprofil.** Zeigt die URL auf
  `/posts/`, `/permalink/`, `/photos/` …, gehören Titel und Text dem Beitrag.
  `enrich.hit_to_group` übernimmt sie deshalb nicht als Name und Beschreibung —
  der Titel landet als ausdrücklich benannter Hinweis in `notes`. Ungefiltert
  stand „Deutschland geht erst unter seit Mutter Merkel den Syrern ..." als
  Gruppenname im Export, wurde klassifiziert und bewertet. 44 von 129
  Suchtreffern waren solche Fundstellen.
- **Statusmodell mit drei Achsen**: `validation_status` (valid/invalid/test_data)
  bewertet die URL, `data_quality` (none/minimal/partial/complete) die
  Metadatenlage, `status` (new/validated/invalid/insufficient_data)
  fasst zusammen. Rangfolge in `validation.determine_status`:
  invalid > insufficient_data > validated.
- **`duplicate` wird nicht mehr abgeleitet.** Die Regel war `times_seen > 1` –
  aber `times_seen` zählt jeden **Fund**, nicht jeden Datensatz; `sqlite_store.
  count_distinct_sources` hält das selbst fest. Echte Dubletten sind zu diesem
  Zeitpunkt längst zusammengeführt (`deduplicate_exact` läuft vor der
  Bewertung), ein überlebender Datensatz ist also nie eine offene Dublette.
  Was `times_seen > 1` anzeigt, ist das Gegenteil eines Mangels: Die Gruppe
  wurde von mehreren Anfragen gefunden. Nach der Ausweitung der Suchmuster
  trugen 146 von 273 bewerteten Gruppen den Stempel, darunter zwei der drei
  bestbewerteten – wer auf `validated` filterte, verlor die Hälfte seines
  Bestands. Der Enum-Wert bleibt (Bestandsdaten, Handurteil), abgeleitet wird
  er nicht mehr.
- **Platzhalter werden markiert, nicht gelöscht.** `validation.py` prüft rein
  strukturell (Ziffernfolgen, Wiederholungen, Test-Tokens) und fragt nie bei
  Facebook nach. `test_data` ist ein begründeter Verdacht, keine Existenzaussage.
- **Metadaten werden nie erfunden.** Was nicht in der Seed-Datei stand, bleibt
  leer bzw. `None`; der Export zeigt dafür `unknown`.
- **Keine Beispieldaten im Bestand.** `example_seeds.csv` enthielt erfundene
  Gruppen mit realistisch aussehenden Kennungen (`482910573829104`,
  `arab.stuttgart`, `syrer.hamburg`). Die Platzhaltererkennung greift bei ihnen
  bewusst nicht, und sie führten mit erfundenen Mitgliederzahlen die Rangliste
  an. Beispieldateien gehören nicht nach `data/seeds/`; wer eine braucht, legt
  sie außerhalb ab und importiert sie mit ausdrücklichem Pfad.
- **`SqliteStore.upsert_groups`** überschreibt `review_status` und `notes` eines
  bestehenden Datensatzes nie – manuelle Bewertungen überleben jeden Reimport.
  Der Rückgabewert (neu, bekannt) ist die Kennzahl für die Qualität einer
  Suchstrategie.

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
  Beschriftung ohne Wirkung, und ein Suchlauf vergäbe Monate später noch Codes
  für eine Kampagne, die niemand mehr betreibt. Von Hand bleibt jede Kampagne
  zuordnbar — `campaign sync` fragt nicht nach dem Status, denn dort steht ein
  Mensch davor.
- **`campaign sync` ist die Regel, `campaign add-groups` der Schnappschuss.**
  `add-groups` lief genau einmal und schrieb, was es in dem Moment fand – so
  kamen 8 Zuordnungen zustande, während der Bestand auf 310 wuchs. `sync`
  wendet die gespeicherte Regel an, ist wiederholbar und läuft deshalb auch am
  Ende von `import-seeds` und `search` (nur für Kampagnen mit `auto_assign`,
  Vorgabe aus). Beide lesen denselben Plan aus `marketing/selection.py` –
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
  Suchlauf schreibt jeden gefundenen Datensatz neu; von Hand gepflegte Angaben
  hätten dort keinen sicheren Platz.
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
- **Die Kürzel im Code kommen aus der Konfiguration** (`code:` in
  `cities.yaml`/`audiences.yaml`, sonst die ersten drei Buchstaben der
  Kennung). Eine neue Stadt bringt ihr Kürzel selbst mit.
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
  und `ohne_stadt`. Eine Stadt, die in `cities.yaml` nicht vorkommt, gilt als
  keine Stadt — lieber die allgemeine Vorlage als ein erfundener Ortsname.
- **Drei Beschriftungen je Zielgruppe, und sie sind nicht austauschbar.**
  `label_de` („Syrer in Deutschland") ist fürs Auswahlfeld; in einer
  Stadtvorlage ergäbe es „Syrer in Deutschland in Bonn". Dafür gibt es
  `label_kurz_de`, und für arabische Vorlagen `label_ar` — aus `terms.ar` nicht
  ableitbar, das sind Suchbegriffe für den Abgleich und keine Anrede.
- **Arabisch hat nur eine Form, also folgt daraus eine Regel für jede Vorlage.**
  `label_ar` trägt den bestimmten Artikel und steht im Genitiv/Akkusativ
  („السوريين"). Vor `{zielgruppe}` gehört deshalb ein eigenes Wort (مِن، إلى،
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
- **`{ziel}` steht an der Zielgruppe, nicht an der Kampagne**
  (`ziel_ar`/`ziel_de` in `audiences.yaml`). Wer syrische Gruppen bewirbt,
  meint Syrien; wer irakische bewirbt, den Irak — auch wenn beide Kampagnen
  „Batreeq Germany" heißen. `label_ar` taugt dafür nicht: Es steht im
  Genitiv Plural, und „من بون إلى السوريين" wäre kein Ziel, sondern ein
  Satzfehler. Zielgruppen ohne einzelnes Land (`arabs`) lassen das Feld leer
  und bekommen `ziel_allgemein` („الوطن") — ein erfundenes Land stünde als
  Behauptung in dreihundert Beiträgen.
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
- **Die Übersicht merkt sich, wo man war** (`sessionStorage`, Filter + Sortierung
  + Seite + Seitengröße + Scrollposition). Jede Änderung an einer Gruppe lädt die Seite neu; bei 314
  Zeilen hieß das vorher: Stadt neu wählen, Haken neu setzen, die Zeile
  wiederfinden — nach jedem einzelnen Klick. Gefiltert wird im Browser, also
  weiß nur der Browser, wo man war. `sessionStorage` und nicht `localStorage`:
  Der Stand gehört zu dieser Sitzung; wer morgen neu öffnet, will die
  Übersicht sehen und nicht den Filter von gestern. Jeder Zugriff ist in
  `try`/`catch` — in einem privaten Fenster wirft schon das Lesen, und eine
  Bequemlichkeit darf die Seite nicht mitreißen.

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

### Weiterleitung und Meldungen

- **`GET /r/{code}`** zählt den Klick und leitet mit **302** weiter (nicht 301:
  ein dauerhaft gemerkter Umzug führte spätere Klicks am Zähler vorbei). Ein
  unbekannter Code ergibt 404 statt einer stillen Weiterleitung.
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
- Seed-Dateien **immer** mit `utf-8-sig` lesen — CSV *und* TXT. Notepad und
  `Out-File` schreiben ein BOM; ohne `utf-8-sig` wird es Teil der ersten Zeile
  und die erste URL geht stillschweigend verloren (`tests/test_encoding.py`).
- CSV-Export mit `utf-8-sig` und `;` schreiben, damit Excel Arabisch und
  Spalten korrekt darstellt.
- PowerShell 5.1 kennt kein `&&` und kein `||`; mit `;` und `if ($?) { }` ketten.
