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

- Kein Zugriff auf facebook.com – kein Scraping, keine Login-Automatisierung,
  keine Umgehung von Schutzmechanismen. Phase 1 liest nur lokale Dateien.
- Keine Mitglieder-/Admindaten, keine Profil-URLs, keine Beitragsinhalte, keine
  Kontaktdaten. `models.Group` hat dafür bewusst keine Felder – ein Erweitern
  des Modells um solche Felder wäre eine Grenzverletzung.
- Kein automatisches Posten, kein automatisches Messaging.
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
  ist ohne Zugriff auf facebook.com auch nicht auflösbar.
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
- **Die Aktivitaet einer Gruppe wird gemessen, nicht von Facebook geholt.**
  Mitgliederzahl, Beitraege je Woche, aktive Poster und letzter Beitrag stehen
  ausschliesslich auf facebook.com. Die **Meta Groups API wurde am 22.04.2024
  vollstaendig abgeschaltet** (angekuendigt mit Graph API v19 im Januar 2024);
  `groups_access_member_info` und `publish_to_groups` sind ersatzlos entfallen.
  Auch davor haette sie nicht geholfen: Sie verlangte, dass ein **Admin** der
  Gruppe die App dort installiert — bei 307 Gruppen, in denen wir Gast sind,
  war dieser Weg nie offen. Ein Nachbau ueber eine Browsersitzung ist Scraping
  und faellt unter die harten Projektgrenzen.
  Gemessen wird stattdessen die **Resonanz**: Klick auf den Tracking-Link,
  Registrierung in der App. Das beantwortet die eigentliche Frage genauer —
  nicht "wie viel wird dort geredet?", sondern "wie viele Menschen kommen von
  dort zu uns?".
- **`scoring.Resonanz` beschreibt die Zahlen, `marketing/resonanz.py` beschafft
  sie.** Die Richtung ist Absicht: `scoring.py` kennt weder `MarketingStore`
  noch die Ereignistabelle, so wie die Marketing-Erweiterung den Bestand nicht
  veraendert. Der Aufrufer (`rescore`, die Uebersicht) reicht die Zahlen herein.
  Ein Import in die andere Richtung machte den Kern von einem Aufsatz abhaengig.
- **"Nicht gemessen" ist etwas anderes als "wirkungslos".** Ohne
  veroeffentlichten Beitrag liefert `_resonanz_faktoren` `None`, die
  Bestandteile erscheinen als "unbekannt" und `score_max` sinkt auf 100 statt
  175. Null Klicks ohne Beitrag sind eine Aussage ueber **uns**, nicht ueber die
  Gruppe. Dasselbe gilt fuer die Schonfrist (`schonfrist_tage: 3`): Wer vor zwei
  Stunden gepostet hat, hat noch keine Klicks — eine Null waere hier eine
  Behauptung ueber die Zukunft. Ein Beitrag **mit** null Klicks ist dagegen ein
  Ergebnis und wird als solches bewertet (`score_max` 175, Resonanz 0).
- **Die Zielquote ist 15 %, nicht 100 %** (`resonanz.ziel_quote`). Wer die
  Registrierungsquote auf 1,0 normiert, gibt selbst der besten Gruppe ein
  Sechstel der Punkte und macht den Bestandteil wirkungslos. Daneben steht die
  Belastbarkeit (`mindest_klicks: 20`): 1 Klick mit 1 Registrierung sind 100 %
  und beweisen nichts — ohne diese Schranke stuende jede zufaellige Gruppe an
  der Spitze.
- **Reichweite zaehlt je Beitrag, nicht absolut.** Sonst gewaenne die Gruppe, in
  der wir am oeftesten gepostet haben, statt der, die am besten wirkt.
- **Passung (100 Punkte) und Resonanz (75 Punkte) sind zwei Bloecke.**
  `config-check` prueft nur den ersten auf 100; beide zusammen zu pruefen hiesse,
  das Einschalten der Resonanz als Fehler zu melden. Der Score wird weiterhin
  **nicht** auf 100 normiert (Regel 3 in `scoring.py`) — eine Gruppe ohne Beitrag
  erreicht hoechstens 100, und "100 von 100" steht zu Recht hinter "130 von 175".
- **Die Mitgliederzahl ist abgeschaltet (`member_count: 0`).** Sie war mit 45
  von 100 das schwerste Kriterium — für eine Kooperation entscheidet die
  Reichweite. Sie ist aber die einzige Angabe, die das Projekt nicht selbst
  beschaffen kann: In 273 gespeicherten Suchtreffern stand sie **kein einziges
  Mal**, und facebook.com wird nicht aufgerufen. Damit fehlte sie ausnahmslos
  jeder der 132 Gruppen, und die Decke von 55 Punkten unterschied keine Gruppe
  von einer anderen — sie war eine Aussage über das Projekt, nicht über die
  Gruppen. Ihre 45 Punkte liegen jetzt auf `audience_match` (45), `city_match`
  (27), `category_match` (16) und `name_quality` (12).
- **Gewicht `0` schaltet einen Bestandteil ganz ab, `None` heißt unbekannt.**
  Ein abgeschalteter Bestandteil senkt `score_max` nicht und erscheint nicht
  als „unbekannt" in `score_reason` — er wird gar nicht erst erwartet. Ohne
  diesen Filter in `scoring.score_group` setzte `DEFAULT_WEIGHTS` die 45 Punkte
  gegen die Konfiguration wieder ein. Wer die Mitgliederzahl von Hand pflegt
  (`fbgroups pruefliste`), setzt das Gewicht zurück auf > 0 und ruft `rescore`
  auf; der gesamte Mechanismus ist erhalten und durch die Fixture
  `config_mit_mitgliederzahl` weiterhin getestet.
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
& $py -m fbgroups.cli campaign message batreeq-syrian-germany arabinberlin
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
  protokolliert. Der Unterschied ist nicht kosmetisch: Ein Programm, das 300
  Beitraege selbst absetzt, ist genau das, was Facebooks Spam-Erkennung sucht —
  gesperrt wuerde das Konto des Nutzers samt aller Gruppen, in die er
  aufgenommen wurde. `webbrowser.open` ist dabei keine Automatisierung von
  facebook.com: Es passiert dasselbe wie beim Anklicken eines Links, und die
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
- **Der Stand ändert sich nur von Hand.** Kein Befehl beobachtet Facebook, also
  kann keiner erkennen, dass eine Anfrage gestellt wurde. `marketing set` und
  `marketing beitritt` schreiben mit, was ein Mensch im Browser getan hat.
- **Die Beitrittsanfrage ist ein eigener Schritt** (`beitritt_angefragt` →
  `mitglied` → `contacted`). Bei Facebook muss man aufgenommen sein, bevor man
  posten oder die Leitung ansprechen kann. Vorher sprang das Modell von
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

### KI-Anbieter: lokal als Standard

```powershell
& $py -m fbgroups.cli ki status          # Anbieter, Verbindung, Modell, Adresse
& $py -m fbgroups.cli ki test            # eine sehr kurze echte Anfrage
& $py -m fbgroups.cli ki modelle         # was bei Ollama wirklich liegt
& $py -m fbgroups.cli campaign draft <kampagne> --dry-run   # zeigt nur, was liefe
```

- **Ollama ist die Voreinstellung, Anthropic der Sonderfall.** Ollama läuft auf
  dem eigenen Rechner: keine Kosten je Anfrage, und die Angaben über die
  Gruppen verlassen den Rechner nicht. Beides sind Gründe für den Standard,
  nicht für eine Ausweichlösung. `AI_PROVIDER` schlägt
  `marketing.posting.ki.anbieter` — dieselbe Reihenfolge wie bei
  `APP_BASE_URL`. Ein **unbekannter** Wert fällt auf Ollama zurück: Ein
  Tippfehler soll niemanden unversehens bei einem kostenpflichtigen Dienst
  abliefern, und die kostenlose Voreinstellung ist die harmlose Richtung.
- **Es wird nie stillschweigend gewechselt.** Läuft Ollama gerade nicht, wird
  *nicht* auf Anthropic ausgewichen — das verwandelte einen abgeschalteten
  Rechner in eine Rechnung. Dieselbe Überlegung wie bei `fallback_chain` in der
  Suchschicht, die aus genau diesem Grund leer ist.
- **`marketing/ki/` trennt Fachlichkeit von Anbieter.** `basis.py` hält Prompt,
  Prüfung und Entwürfe und kennt **keinen** Anbieter; `ollama.py` und
  `anthropic.py` sind zwei Umsetzungen des Protokolls `Modell`; `factory.py`
  entscheidet. Deshalb ändert ein dritter Anbieter nichts an der Prüfung des
  Tracking-Links — und die Tests laufen ohne Netz, ohne Dienst, ohne Kosten.
- **Ollama braucht keine neue Abhängigkeit.** Es spricht HTTP, und `httpx` ist
  seit jeher Kernabhängigkeit. `[ki]` (das Extra mit `anthropic`) ist
  ausschließlich für den optionalen Weg — ohne es laufen Suche, Zuordnung,
  Warteschlange, Freigabe, Veröffentlichung **und** die Beitragsvorschläge.
- **Eine Anfrage je Fassung, als reiner Text — nicht drei in einem JSON.** Der
  Anthropic-Weg holt drei Fassungen in einem Aufruf mit Schema. Ein kleines
  lokales Modell hält das Schema nicht ein; dann ist nicht eine Fassung
  unbrauchbar, sondern alle drei. Lokal kostet eine dritte Anfrage nichts außer
  Zeit. Die Verschiedenheit kommt aus `temperature` (Ollama nimmt den Parameter,
  Claude Opus 5 lehnt ihn mit 400 ab) und daraus, dass jede Anfrage die bereits
  geschriebenen Fassungen mitbekommt.
- **Genau ein Reparaturversuch.** Ein kleines Modell verfehlt `{link}` deutlich
  öfter als ein großes. Schlägt `pruefe_platzhalter` an, wird **einmal**
  nachgefasst — mit der Regel, an der es lag — und das Ergebnis geht durch
  **dieselbe** Prüfung. Ohne das wäre ein guter Teil der Fassungen unbrauchbar;
  mit mehr als einem Versuch würde daraus eine Schleife, deren Dauer (lokal)
  und Kosten (bei Anthropic) niemand mehr überblickt.
- **Der Statusabruf erzeugt nie etwas** und wird 10 s zwischengespeichert. Die
  Übersicht fragt ihn bei jedem Seitenaufbau; ohne Zwischenspeicher zahlte
  jedes Neuladen die volle Zeitgrenze. `fbgroups ki status` umgeht ihn
  (`frisch=True`) — wer nachsieht, hat womöglich gerade Ollama gestartet.
- **„Verbunden" allein ist eine irreführende Auskunft.** Der häufigste Fehler
  nach der Einrichtung ist ein laufender Dienst **ohne** das Modell. `Status.
  modell_vorhanden` unterscheidet das, und die Meldung nennt `ollama pull`.
  Ebenso getrennt: HTTP 404 bei `/api/generate` heißt „Modell fehlt", ein
  Verbindungsfehler heißt „Dienst läuft nicht" — zwei Fehler, zwei Lösungen.
- **Ohne KI funktioniert alles Übrige vollständig.** Sie ist ein Aufsatz, keine
  Voraussetzung: `sammle_daten` fängt jeden Fehler des Statusabrufs,
  `POST /ki/test` antwortet auch bei totem Ollama mit **200** und `ok: false`
  (ein 500 sähe aus wie ein Fehler des Dienstes statt wie ein abgeschaltetes
  Ollama), und die Übersicht baut sich unverändert.
- **`POST /ki/test` steht hinter `_nur_lokal`** wie jeder schreibende Weg. Er
  erzeugt wirklich etwas — lokal Rechenzeit, bei Anthropic Geld —, und ein Weg,
  den jeder von außen auslösen könnte, wäre bei einem lokalen Modell eine
  Einladung, den Rechner lahmzulegen.

### Der Arbeiter (`worker.py`, `veroeffentlicher/`)

```powershell
& $py -m fbgroups.cli campaign enqueue batreeq-syrian-germany --top 20  # nach Score einreihen
& $py -m fbgroups.cli campaign worker batreeq-syrian-germany --dry-run  # Plan, Grenzen, Zeitplan
& $py -m fbgroups.cli campaign worker batreeq-syrian-germany            # abarbeiten
& $py -m fbgroups.cli campaign tageslauf batreeq-syrian-germany   # retry + enqueue + worker
& $py -m fbgroups.cli campaign zeitplan batreeq-syrian-germany    # taeglich einrichten
& $py -m fbgroups.cli campaign pause batreeq-syrian-germany   # wirkt im laufenden Arbeiter
& $py -m fbgroups.cli campaign retry batreeq-syrian-germany   # ohne die aufgegebenen
```

- **`worker.py` ist Ablaufsteuerung und sonst nichts.** Wie ein Beitrag in eine
  Gruppe kommt, steht dort nirgends — das ist Sache eines `Veroeffentlicher`.
  Dieselbe Trennung wie `providers/base.py` in der Suchschicht und aus
  demselben Grund: Der Teil, der über Tageslimit, Reihenfolge und Abbruch
  entscheidet, muss ohne Netz und ohne Browser prüfbar sein.
- **`veroeffentlicher/basis.py` enthält nur den Vertrag.** Kein Modul außerhalb
  von `veroeffentlicher/` darf eine konkrete Umsetzung importieren —
  abgesichert durch `test_kein_modul_ausserhalb_des_pakets_kennt_einen_adapter`
  und `test_der_arbeiter_kennt_keinen_adapter`. Neuer Adapter = Klasse +
  `@register_veroeffentlicher(...)` + eine Zeile in `__init__.py`; weder
  Arbeiter noch CLI noch Übersicht ändern sich dabei. Implementiert:
  `assistiert` (Text bereit, Gruppe offen, Absenden von Hand).
- **Ein Adapter bekommt nie eine Anmeldung.** Weder Passwort noch Cookie noch
  Token wird durchgereicht; `PostVersuch.browser_session` ist ein *Name* wie
  `standard`. Das Modell hat für eine Anmeldung schlicht kein Feld — geprüft
  von `test_kein_feld_fuer_eine_anmeldung`.
- **Streng nacheinander — kein Thread, kein `asyncio`.** Zwei gleichzeitig
  laufende Beiträge wären nicht doppelt so schnell, sondern der Unterschied
  zwischen einem Menschen, der arbeitet, und einem Programm, das sendet. Die
  Wartezeit dazwischen ist deshalb kein Schönheitsfehler des Ablaufs, sondern
  sein Kern.
- **Der Zustand wird vor jedem Job neu gelesen — und nach jeder Wartezeit.**
  Nur so wirken `pause`, `resume` und `stop` **während** ein Arbeiter läuft:
  Sie werden von einem anderen Prozess geschrieben (CLI oder Übersicht), und
  ein Arbeiter, der seinen Zustand im Speicher hielte, sähe sie nie. Ohne die
  zweite Prüfung wirkte `pause` erst sieben Minuten später — und in der
  Zwischenzeit stünde ein Beitrag in einer Gruppe, den niemand mehr wollte.
- **Das Tageslimit zählt aus `post_versuche`, nicht aus einem Zähler im
  Speicher** (`store.versuche_heute`). Wer um 08:00 zwanzig Beiträge setzt,
  abstürzt und um 14:00 neu startet, sähe sonst einen leeren Zähler und setzte
  zwanzig weitere. Gezählt wird ab **örtlicher** Mitternacht: „20 pro Tag"
  meint den Tag des Menschen, der davorsitzt. Mitgezählt wird **jeder** Versuch,
  auch der fehlgeschlagene — zehn Fehlschläge hintereinander sind ein Grund,
  den Tag zu beenden, kein Grund, es zwanzig weitere Male zu versuchen.
- **Die Reihenfolge entsteht beim `enqueue`, nicht beim Abarbeiten.** Dort
  sortiert `nach_prioritaet` nach Score. Sortierte der Arbeiter selbst,
  entschiede eine Neubewertung mitten im Lauf, welcher Beitrag als nächstes
  hinausgeht — und die Reihenfolge wäre von Tag zu Tag eine andere.
- **Der Versuch wird vor dem Absetzen protokolliert.** `beginne_versuch` läuft,
  bevor der Adapter etwas tut. Bricht der Arbeiter mitten im Absetzen ab,
  bleibt eine Zeile ohne `beendet_am` stehen — unangenehm, aber beantwortbar.
  Ohne sie wüsste niemand, ob in der Gruppe nun ein Beitrag steht.
- **Ein werfender Adapter reißt den Lauf nicht mit.** Der Job stünde sonst für
  immer auf `processing`: weder offen noch fertig, in keiner Liste, nie wieder
  angefasst. Der Grund wandert stattdessen ins Protokoll, und der nächste Job
  kommt dran — eine zickige Gruppe darf nicht den Rest des Tages kosten.
- **`abbrechen` ist ein eigenes Feld, nicht aus `erfolg` abgeleitet.** Ein
  Adapter, der es nicht hätte, liefe nach einem Fehler weiter gegen die Wand;
  einer, der bei jedem Fehler abbräche, verlöre den Tag wegen einer Gruppe. Wer
  so abbricht, verwirft nichts: Der Job geht per `erzwingen` zurück auf
  `queued` (`processing -> queued` fehlt in der Übergangstabelle bewusst, damit
  ein Job nicht unbemerkt zwischen beiden kreist) und ist morgen der nächste.
- **Der Arbeiter schläft nicht bis zur Startzeit.** Ein Prozess, der vierzehn
  Stunden wartet, überlebt keinen Neustart, keine Abmeldung und keinen
  zugeklappten Deckel. `startzeit` in `settings.yaml` ist der Wert für die
  Aufgabenplanung des Systems; `campaign zeitplan` trägt ihn dort ein und
  zeigt ohne `--einrichten` nur, was er täte — etwas, das sich täglich von
  selbst startet, legt man nicht beiläufig an.
- **`campaign tageslauf` ist die Reihenfolge der drei Schritte, nicht ihr
  Ersatz.** Erst `retry`, dann `enqueue`, dann `worker`. Ein gestern
  gescheiterter Beitrag hat Text und Freigabe schon und gehört vor die Gruppen,
  die heute zum ersten Mal drankommen; umgekehrt füllte sich die Warteschlange
  mit Neuem, und der Fehlschlag rutschte Tag um Tag nach hinten. Eingereiht
  wird nur, was ins Tageslimit passt — was darüber hinaus in der Schlange
  stünde, überlebte den Tag und bräche morgen die Score-Reihenfolge.
- **`max_versuche` (Vorgabe 3) wird beim `retry` durchgesetzt.** Die Zahl stand
  seit jeher in der Konfiguration und wirkte nirgends: Jeder Aufruf holte
  dieselbe Gruppe zurück, die aus einem *dauerhaften* Grund scheitert.
  „Erlaubt keine Links" geht beim vierten Mal nicht anders aus als beim ersten,
  kostet aber jedes Mal einen Platz im Tageslimit, den eine erreichbare Gruppe
  gebraucht hätte. Die Aufgegebenen verschwinden nicht — `store.aufgegeben`
  listet sie, und `retry --alle` übergeht die Grenze. Sie warten auf eine
  Entscheidung (anderer Text, Gruppe ausschließen), nicht auf einen vierten
  gleichen Versuch.
- **`POST /kampagnen/{id}/queue` steuert die Warteschlange aus der Übersicht.**
  Hinter `_nur_lokal` wie jeder schreibende Weg. Wer einen Lauf anhalten will,
  sitzt selten vor dem Fenster, in dem er gestartet wurde. Die Antwort nennt,
  wie viele Jobs ein `gestoppt` zurückgestellt hat — „gestoppt" allein ließe
  offen, ob das 3 oder 300 Beiträge waren.

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
  kein Geheimnis, er steht in veröffentlichten Facebook-Beiträgen. Er ist der
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
