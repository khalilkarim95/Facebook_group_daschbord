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
  Metadatenlage, `status` (new/validated/invalid/duplicate/insufficient_data)
  fasst zusammen. Rangfolge in `validation.determine_status`:
  invalid > insufficient_data > duplicate > validated.
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
& $py -m fbgroups.cli campaign add-groups batreeq-syrian-germany --top 20
& $py -m fbgroups.cli campaign links batreeq-syrian-germany --export data\exports\links.csv
& $py -m fbgroups.cli campaign message batreeq-syrian-germany arabinberlin
& $py -m fbgroups.cli marketing set arabinberlin --status contacted --kontaktiert-jetzt
& $py -m fbgroups.cli marketing list --erlaubnis approved
& $py -m fbgroups.cli marketing overview
```

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
& $py -m fbgroups.cli marketing referral list --status qualified
& $py -m fbgroups.cli marketing rewards --benutzer user-ahmad
& $py -m fbgroups.cli marketing audit
```

- **`GET /r/{code}`** zählt den Klick und leitet mit **302** weiter (nicht 301:
  ein dauerhaft gemerkter Umzug führte spätere Klicks am Zähler vorbei). Ein
  unbekannter Code ergibt 404 statt einer stillen Weiterleitung.
- **`POST /events`** nimmt die Meldungen der Zielanwendung entgegen.
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
