# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projekt

Verwaltet Facebook-Gruppen für Marketing-Kooperationen in Deutschland
(Zielmarkt: syrische und arabische Communities; beworben wird die App
„بطريقك" — Reisende nehmen Kleinigkeiten nach Syrien mit). Der Bestand wird
**gepflegt**, nicht gesucht: Der einzige Weg hinein ist die Mitgliederliste
(`import-mitglieder`). Darauf setzen Kampagnen, Tracking-Codes, Textvorlagen,
Arbeitsseite und Kommentarautomatik auf.

Die Projektsprache ist **Deutsch** – Kommentare, Docstrings, CLI-Ausgaben und
Testnamen. Bitte beibehalten.

Gearbeitet wird **direkt auf `main` im Hauptcheckout** — `ausrollen.sh` packt
den Code aus dem Verzeichnis, in dem es läuft; ein Branch oder Worktree rollt
den alten Stand aus. Alles, was den Bestand ändert, läuft auf dem Server
(Befehle: `run_command.md`).

## Harte Projektgrenzen

Mit dem Nutzer vereinbart; nicht ohne ausdrückliche Aufforderung aufweichen.

- **Automatisches Posten und Kommentieren ist erlaubt**
  (`campaign automatik`), aber **sichtbar** (Headless=False). **Kein
  stiller/headless Login, keine Umgehung von Sperren** (kein Proxywechsel,
  keine wechselnden Kennungen, kein nachgeahmter Browser, keine
  Cookie-Uebernahme). Die Sitzung entsteht von Hand (`auth login`) im
  Profil `data/browser_state`. Test: `tests/test_projektgrenzen.py`.
- **Keine Personendaten**: keine Mitglieder-/Admindaten, Profil-URLs,
  Kontaktdaten, Autorennamen. `models.Group` hat dafür keine Felder.
- **Beitragstexte werden gelesen, nie gespeichert.** Erlaubt zu speichern
  sind Beitrags-Metriken (URL, Zeitstempel, Reaktionen/Kommentare) und
  **Urteile** über den Text (Thema, Anlass, Bezüge als Schlagwörter).
  `GroupPost` hat kein Textfeld; `beitrag_bezuege` hält nur Schlagwörter.
- Kein Suchdienst fest verdrahten (es gibt keine Suchschicht mehr — wer eine
  zurückholt, holt die Regel mit).
- **Keine KI im Regelweg.** Texte kommen aus `config/textvorlagen.yaml` oder
  von einem Menschen auf der Arbeitsseite; Urteile aus nachlesbaren
  Schlagwörtern. Ein Modell erfindet Zusammenhänge.

## Befehle

```powershell
$py = ".\.venv\Scripts\python.exe"
$env:PYTHONIOENCODING="utf-8"        # sonst bricht arabische Terminalausgabe

& $py -m pip install -e ".[dev,web]"
& $py -m pytest                      # alle Tests, offline (~6 Min)
& $py -m pytest tests\test_bezuege.py -v
& $py -m pytest -k arabisch
& $py -m ruff check src tests
& $py -m mypy                        # meldet vorbestehend "missing py.typed marker"

& $py -m fbgroups.cli config-check
& $py -m fbgroups.cli auth login     # Browser-Sitzung anlegen (sichtbar, von Hand)
& $py -m fbgroups.cli import-mitglieder data\from_lokal\liste.csv --dry-run
& $py -m fbgroups.cli serve --port 3000
& $py -m fbgroups.cli campaign --help
& $py -m fbgroups.cli marketing --help
```

In einem Git-Worktree `PYTHONPATH=src` voranstellen — das venv hat den
Hauptcheckout editierbar installiert und liefe sonst mit dessen Code.

## Architektur

```
config/settings.yaml, textvorlagen.yaml, rewards.yaml
        │
mitglieder.py ──► storage/sqlite_store (groups) ──► scoring, rescoring
                                │
                  marketing/store (Kampagnen, Codes, Texte, Versuche, Ereignisse)
                                │
   Text:     vorlagen, beitrag, kurzcode
   Urteil:   inhalt (Thema/Anlass/Relevanz), bezug (Bezüge), entscheidung,
             qualifikation (Regeln/Beobachtung), grenzen (Tagesmengen/Takt)
   Ablauf:   lauf (Reihenfolge), automatik (Treiber), arbeit (Arbeitsseite),
             watchdog
   Dienst:   web (FastAPI), dashboard, arbeitsseite, tracking, referral, rewards
                                │
                  automation/ (Playwright, sichtbar: actions, browser)
```

Die reinen Module (`inhalt`, `bezug`, `entscheidung`, `qualifikation`,
`grenzen`, `kaltmodus`, `lauf`) kennen weder Netz noch Datenbank noch
Playwright — der Aufrufer reicht die Angaben herein. So ist jede Regel ohne
Browser prüfbar.

### Grundsätze, die man mehreren Dateien nicht ansieht

- **`config/settings.yaml` ist die fachliche Wahrheit.** Gewichte, Grenzen,
  Schwellen und Pfade stehen nirgends im Code. Eine **Abschaltung** gehört
  dorthin (`daily: 0` heißt „gar nicht"), nicht in gelöschten Code. Schalter
  haben im Code die **vorsichtige** Vorgabe; gesagt wird es in der
  Konfiguration.
- **Zwei Vergleichsstrategien in `textnorm.py`.** Lateinisch mit Wortgrenze
  (Fortsetzung erlaubt: „arab" trifft „araber"), arabisch als Teilstring — im
  Arabischen hängen Artikel und Präpositionen am Wort. Wer das vereinheitlicht,
  zerstört die arabische Erkennung. Normalisierung: أ/إ/آ→ا, ة→ه, ى→ي, ؤ→و, ئ→ي.
  Folge: Mehrzahlformen müssen eigens stehen („رحلة" steckt nicht in „رحلات").
- **Nichts wird erfunden.** Fehlende Metadaten bleiben `None`/leer; ein
  unbekannter Wert in einer Liste wird gemeldet, nicht geraten.
- **Zwei Wahrheiten vermeiden.** Eine Regel, eine Rangfolge, eine Zählweise
  steht an **einer** Stelle; wer eine zweite Fassung baut, bekommt zwei
  Ergebnisse. Gerechnete Urteile (Qualifikation, Lauffortschritt) werden
  **nicht gespeichert**, sondern bei jedem Lesen aus ihren Grundlagen gerechnet.
- **Migrationen sind ausschließlich additiv** (`user_version`, Schritte in
  `storage/sqlite_store._MIGRATIONS`, aktuell Version 27). Kein `DROP`, kein
  `RENAME` — deshalb heißt die Spalte `member_count_hint`, das Feld
  `member_count`. `_migrate` übergeht genau `duplicate column name`.
  `MarketingStore` holt fehlende Schritte selbst nach.
- **`upsert_groups` schützt Handarbeit und Erhobenes mit `COALESCE`**:
  Mitgliederzahl, Aktivität, Note, Aktivitätsstufe; `review_status` und
  `notes` werden nie überschrieben; `ValidationStatus.UNREACHABLE` (ein
  Menschenurteil) nie zurückgenommen.
- **Technik ist kein Urteil über eine Gruppe.** Ein geschlossenes
  Browserfenster, ein fehlendes Kommentarfeld, eine tote Beitragsadresse
  sagen nichts über die Gruppe. Was die Gruppe selbst sagt (abgelehnt, Spam,
  „Link in Kommentar"), zählt.
- **Tests laufen nicht gegen die `.env` des Rechners** — die autouse-Fixture
  `_ohne_env_datei` in `tests/conftest.py` setzt die Schlüssel je Test auf leer.

## Bestand und Score

### Die Mitgliederliste (`mitglieder.py`, `import-mitglieder`)

Jede Zeile heißt „wir sind Mitglied" (`MarketingStatus.MEMBER`, ein weiter
fortgeschrittener Stand wird nie zurückgedreht; `--ohne-status` lässt ihn
unberührt). Die Spalten sind eine fremde Tabelle:

| Spalte | Inhalt | Ergebnis |
|---|---|---|
| `category` | Anzeigename („Reise & Transport") | Kennung (`reise`) über `KATEGORIEN`; Unbekanntes gemeldet |
| `city` | **Reiseziel** („Damaskus") | **verworfen** — `Group.city` ist der deutsche Sitz und trägt Score-Punkte; Hinweis in `notes` |
| `country` | Raum | `Group.country` |
| `activity` | Seitenkopf **oder** Stufe | Sichtbarkeit, Mitgliederzahl, Beiträge/Tag — sonst `aktivitaetsstufe` |
| `rating` | Note („A++") | `listenprioritaet` |

- „25 ungelesene Beiträge" ist unser Postfachstand, **keine** Rate —
  `_BEITRAEGE_RE` verlangt „pro Tag".
- `الإشعارات` („Benachrichtigungen") ist kein Gruppenname (`KEIN_NAME`).
- Aus „sehr aktiv" wird keine Beitragszahl und umgekehrt. „Sehr Aktiv" enthält
  „Aktiv" — die Reihenfolge in `AKTIVITAETSSTUFEN_TEXT` ist der halbe Inhalt.
- `parse_member_count` steht in `textnorm.py` (ein Parser für CSV und Browser).
- Auf den Server: `bash ./ausrollen.sh --mitglieder` (erst Trockenlauf, dann
  Rückfrage; eingelesen nach dem Einsetzen; zugeordnet wird dabei nichts).

### Note und Aktivitätsstufe

Urteile eines Menschen (`listenprioritaet` A++…B, `aktivitaetsstufe`). Sie sind
**Auskunft und Kampagnenfilter** (`target_prioritaeten`, `target_aktivitaet`)
— sie entscheiden **keine** Schwelle und **keine** Reihenfolge. Eine genannte
Note im Filter schließt Unbeurteilte aus; `nicht eingestuft` ist eine eigene
Wahl. Geprüft wird gegen die Aufzählung, nicht gegen den Bestand.

### Der Score (`scoring.py`)

100 Punkte aus fünf Bestandteilen; `config-check` prüft Summe 100 **und**
`members + activity = 50`.

| Bestandteil | Punkte | Grundlage |
|---|---:|---|
| `members` | 25 | Mitgliederzahl, logarithmisch (`member_count_buckets`) |
| `activity` | 25 | `facebook` (erhobene Zahl, Konfidenz 1,0), sonst `resonanz` (Klicks/Registrierungen, 0,8) |
| `category` | 20 | Haupt- + Nebenkategorien (Bonus 0,08 je Thema, max 0,24) |
| `location` | 15 | Stadt → Bundesland (0,45) → Land (0,20) → unbekannt |
| `target_audience` | 15 | Zielgruppen-Tags |

- **Kein Score ohne Grundlage.** `score` ist `None` mit Grund in
  `score_reason`. Ein Bestandteil liefert `Befund` oder `None`, nie eine 0 —
  `None` senkt `score_max`. **Nicht hochrechnen** (sonst standen 27 Gruppen auf
  exakt 100). Gewicht `0` schaltet einen Bestandteil ganz ab (`name_quality`).
- **`scoring.BESTANDTEILE` ist die einzige Quelle**; neuer Bestandteil =
  Funktion mit `@bestandteil`, Feld in `ScoreBreakdown`, Gewicht in
  `settings.yaml`. Ein Gewicht für einen unbekannten Namen ist ein Tippfehler
  (`config-check`).
- **„Nicht gemessen" ≠ „wirkungslos"**: ohne veröffentlichten Beitrag oder in
  der Schonfrist (`schonfrist_tage: 3`) ist die Resonanz `None`; ein Beitrag mit
  null Klicks ist ein Ergebnis. Zielquote 15 %, `mindest_klicks: 20`,
  Reichweite je Beitrag. `scoring.Resonanz` beschreibt, `marketing/resonanz.py`
  beschafft — `scoring.py` importiert nichts aus `marketing`.
- `data_confidence` steht **neben** dem Score, nie darin. `data_quality`
  zählt nur erhobene Felder.
- Sortiert wird über `scoring.sort_by_rank` (Punkte, bei Gleichstand der Anteil
  an `score_max`). Neu bewertet wird im Lauf (`rescoring.bewerte_neu`), das
  **nicht klassifiziert** — Kategorie, Zielgruppe, Stadt werden gepflegt.
- Statusachsen: `validation_status` (URL, rein strukturell — `test_data` ist
  ein Verdacht, markiert statt gelöscht), `data_quality`, `status`.

## Bezüge statt Zielklassen (23.09.2026)

Eine Gruppe wird nach den **Bezügen** beurteilt, die in ihren Beiträgen
tatsächlich vorkommen — nicht nach einer Klasse aus ihrem Namen. Die früheren
Zielklassen A–D, die Region (DE/EU/außerhalb) und die Note als Schwelle sind
entfernt (`marketing/zielgruppe.py` gibt es nicht mehr).

```
GRUPPE -> BEITRAEGE LESEN -> BEZUEGE JE BEITRAG -> BEZUEGE DER GRUPPE
            []       -> spaeter definierte Sonderbehandlung (NOCH OFFEN)
            nicht [] -> spaeter normale Behandlung
```

- **Die Liste** (`bezug.Bezug`, 21 Werte): `GEPAECK_GEWICHT`, `UEBERGEPAECK`,
  `GEPAECK`, `GEPAECK_KAPAZITAET`, `GEGENSTAND`, `PERSOENLICHE_GEGENSTAENDE`,
  `PAKET_SENDUNG`, `VERSENDEN_ZUSTELLUNG_ABHOLUNG`, `AMANAH`, `DOKUMENTE`,
  `REISENDER`, `MEDIKAMENTE`, `GESCHENKE`, `ELEKTRONIK`, `TRANSPORT`,
  `FLUGHAFEN`, `MITNAHME`, `UEBERGABE_ABHOLUNG`, `TERMIN_DATUM`, abgeleitet
  `RICHTUNG` und `SENDUNG_MIT_REISENDEM`. Die endgültige Liste prüft der Nutzer
  noch.
- **Ein Bezug ist ein Begriff, kein Wort.** `REISENDER` verlangt Bewegung
  **und** Ziel/Herkunft/Reisewort („رايح ع الشغل" ist keiner); Gewicht verlangt
  Gepäckzusammenhang und fällt beim Körper weg („وزني 80 وبدي انحف");
  `TERMIN_DATUM` nur neben Reise/Mitnahme/Übergabe; „شحن رصيد" ist kein
  Transport, „دوام" kein Medikament, „الجاي" (nächste) keine Rückreise.
  Einzelwörter wie „شي", „مكان", „محل", „نقل", „وصل" stehen bewusst nicht darin
  („keine zu breiten Keywords"). Jeder Befund trägt seine Treffer.
- **Gesammelt wird, wo gelesen wird.** `automatik.entscheide_und_kommentiere`
  hängt die Bezüge **jedes gelesenen** Beitrags an den Ausgang
  (`Schrittergebnis.bezuege`) — auch wenn nichts geschrieben wurde. Gespeichert
  örtlich in `_fuehre_schritt_aus`, im Fernbetrieb vom Server
  (`AutomatikErgebnis.bezuege`), in `beitrag_bezuege` (eine Zeile je Beitrag;
  auch ohne Bezug, denn „gelesen, nichts gefunden" ≠ „nie gelesen").
  `store.gruppenbezuege` → `bezug.Gruppenbezuege` (Anzahl je Bezug).
- **Die Bezüge entscheiden noch nichts.** Bis die Behandlung von `[]`
  festgelegt ist, werden alle zugeordneten Gruppen gleich behandelt; gezählt
  wird `gruppen_ohne_bezug` in der Laufmeldung. Übersicht: Spalte, Filter und
  Kacheln „Bezüge" (mit / ohne / ungelesen).
- **Eine Schwelle für alle Gruppen**: `marketing.mindestrelevanz: mittel`
  (`automatik.anspruch_aus_config`; „niedrig" heißt mittel).
- **Beitrittsanfragen stehen auf 0** (`limits.join_requests.daily`,
  `beitritt.anfragen_pro_tag`), bis festgelegt ist, welche Bezüge eine Anfrage
  rechtfertigen. Der Weg im Code gilt für jede Gruppe — Einschalten ist eine Zahl.
- Festgehalten in `tests/test_bezuege.py`.

## Marketing-Erweiterung (`marketing/`)

Aufsatz auf den Bestand, ohne ihn zu verändern. Gleiche Datenbank, gleiches
Migrationsverfahren.

### Kampagnen, Zuordnung, Tracking-Codes

- **Beschreibung und Auswahlregel sind zwei Dinge.** `campaigns.audiences`/
  `cities` sagen, wen die Kampagne bewirbt; die `target_*`-Spalten, welche
  Gruppen einen Code bekommen. Leer heißt **keine Einschränkung**; auf der
  Kommandozeile hebt `alle` eine Einschränkung auf.
- **Anlegen vergibt keine Codes.** Codes entstehen über `campaign sync`
  (wiederholbar; `POST /kampagnen/{id}/sync` antwortet mit `dry_run: true` als
  Vorgabe) oder `add-groups` (Schnappschuss) oder die angehakten Zeilen der
  Übersicht (`POST /kampagnen/{id}/gruppen`, bestätigt mit der **Zahl**).
  Vorschau und Ernstfall lesen denselben `selection.baue_plan`.
- **Ein vergebener Tracking-Code ändert sich nie und wird nie zurückgenommen**
  — er steht in veröffentlichten Beiträgen. Zugeordnet wird nur hinzugefügt;
  was nicht mehr passt, erscheint als `nicht_mehr_passend`. Der Code ist über
  alle Kampagnen eindeutig.
- **Codevergabe folgt `first_seen_at`**, nicht dem Score
  (`CodeAllocator`, merkt sich je Kürzelpaar die höchste Nummer; frei
  gewordene Nummern werden nicht wieder ausgegeben). Kürzel aus den ersten drei
  Buchstaben von `audience_tags[0]` und `city`, sonst `GEN`/`DE`.
- `auto_assign` greift nur bei `status: active`; `campaign sync` von Hand fragt
  nicht nach dem Status.
- **„Bearbeiten wir sie?" ist eine eigene Achse** (`GroupMarketing.bearbeiten`
  neben `marketing_status`). Ausschließen setzt nur diese Achse; der
  Tracking-Code bleibt gültig. Kein Lauf schließt eine Gruppe selbst aus — das
  ist ein Haken in der Übersicht.
- Arbeitsstand in `group_marketing`, nicht in `groups` (Schreibläufe über
  `upsert_groups` schreiben den ganzen Datensatz). Beitritt als eigene Schritte
  `beitritt_angefragt` → `mitglied` → `contacted` (`join_requested_at`); ein
  erreichter Stand wird nie zurückgedreht, eine Ablehnung bleibt stehen.
- `APP_BASE_URL` (Umgebung) schlägt `marketing.app_base_url`
  (`https://go.b-tarikak.de`); Landingpage `marketing.browser_url`
  (`https://b-tarikak.de/home`). `campaign refresh-urls` stellt den Vorspann
  aller vier Adressspalten um, nie den Code.

### Der öffentliche Kurzcode (`kurzcode.py`)

Nach außen steht ein Deckname (`go.b-tarikak.de/r/8wa6dja`), nach innen der
Code (`FB-SYR-DUE-004`). **Gespeichert und ausgewertet wird nur der innere
Code** (`aufloesen` ist die eine Stelle). Abgeleitet aus Code + Geheimnis
(`marketing_meta`), aber gespeichert. Alphabet ohne `0/o`, `1/l/i`, `u/v`.
Ein vergebener Kurzcode ändert sich nie; der alte lange Link bleibt gültig.
Fehlt der Kurzcode, geht die lange Adresse hinaus — und der Lauf sagt es
(`campaign kurzlinks` trägt nach).

### Beitragsstand je Paar

- Der Beitragsstand gehört zum **Paar aus Kampagne und Gruppe**
  (`campaign_groups`), nicht zur Gruppe. `posted_at` nur beim ersten Erfolg;
  `post_attempts` zählt jeden Ausgang; ein Erfolg löscht den alten Fehlergrund.
- `uebersprungen` ist kein Fehlschlag; `campaign retry` holt nur
  Fehlgeschlagene zurück. `campaign retry --alle --kommentare` nimmt auch
  Fassungs-Fehlschläge und Erschöpfung zurück, **veröffentlichte Fassungen
  bleiben** — anders als `campaign reset`.
- Ein Beitrag ändert den `marketing_status` nicht (`post_status` ist eine
  eigene Frage).

## Texte (`vorlagen.py`, `beitrag.py`, `config/textvorlagen.yaml`)

```
vorlagen: <sprache>: <post|kommentar>: <mit_stadt|ohne_stadt>: [Fassungen mit Kennung]
anlaesse: <sprache>: <anlass>: [Fassungen]
```

- **Zwei Stufen.** `vorlagen.fuelle` ersetzt die Angaben über die Gruppe und
  **speichert**; `beitrag.mit_link` löst `{link}` und `{datum}` erst **beim
  Lesen** auf. Der gespeicherte Text trägt nie den Code.
- **Die Wahl folgt `blake2b("<group_id>|<zweck>")`**, nicht `hash()` (je
  Prozess gesalzen). `vorlage_key` wird gespeichert (`ar/post/mit_stadt/alltag`
  — eine **Kennung**, keine Position; drei-teilige Altschlüssel werden gelesen).
- `generated_text` steht neben `post_text` (Ausgangspunkt vs. was hinausgeht);
  je Zweck eigene Spalten (`post_*`, `kommentar_*`).
- **Beitrag und Kommentar teilen keine Vorlage.** Der Beitrag trägt Freigabe
  und Warteschlange; jede Kampagne führt Kommentare (`campaign text` ohne
  `--typ` erzeugt beide).
- **Platzhalter:** `{zielgruppe}` (immer `anrede_allgemein`, „الأصدقاء"),
  `{stadt}`, `{ziel}` (immer `ziel_allgemein`, „سوريا"), `{gegenstand}`,
  `{gruppe}` (Füllen) sowie `{link}`, `{tracking_code}`, `{landing_page}`,
  `{datum}` (Lesen; `{datum}` = laufender Monat, levantinische Namen, zwölf
  in `textvorlagen.yaml`). Jeder andere wirft `UnbekannterPlatzhalter`.
- **`vorlagen.pruefe_platzhalter`** gilt für **jeden** Text (auch von Hand):
  genau ein `{link}`, keine ausgeschriebene Adresse, kein codeähnliches Muster.
- **Arabische Grammatik:** Vor `{zielgruppe}` steht ein eigenes Wort (مِن، إلى،
  أهلنا) — nie „لـ" oder „يا" (beides verträgt den Artikel nicht).
- Die arabischen `mit_stadt`-Vorlagen sind die des Nutzers; jede Fassung sagt:
  die App **vermittelt nur**, kennt **keinen Preis**, wickelt **keine Zahlung**
  ab, verspricht weder Zustellung noch Versicherung. `ohne_stadt` behält den
  früheren Ton.
- `campaign.message_template` gilt für **alle** Gruppen der Kampagne (dann
  klingen alle gleich) — Sonderfall, nur über `campaign set --vorlage`.

## Arbeitsseite (`arbeit.py`, `arbeitsseite.py`, `/arbeit/{kampagne}`)

- Der Bestand lebt auf dem Server, Zwischenablage und Browser beim Menschen:
  Der Server bereitet vor und zählt, der Browser kopiert und öffnet.
- **Die Einheit ist die Gruppe**, nicht der Beitrag: `hole_gruppenarbeit`
  liefert eine Gruppe mit allen Fassungen (fünf Beiträge, fünf Kommentare);
  `melde_vorschlag` trägt den Ausgang **einer** Fassung ein. Nichts blättert
  von selbst weiter — wohin es geht, entscheidet der Mensch
  (`?gruppe=N` blättert, `?group_id=…` springt; die Kennung geht vor).
- **Ansehen beginnt nichts.** Ansehen, Blättern und Schreiben zählen nirgends;
  erst „veröffentlicht" oder „fehlgeschlagen" schreibt eine Zeile in
  `post_versuche`. Pausiert/gestoppt halten nur das Veröffentlichen an.
- Kein Takt und kein Tageslimit auf der Arbeitsseite — dort sitzt ein Mensch
  (`test_es_gibt_keine_gezaehlte_tagesgrenze_mehr`).
- Ist die Liste leer, bereitet die Seite selbst vor (`_kette_automatisch`:
  Texte, Freigabe, Einreihen — nichts davon veröffentlicht oder überschreibt).
- Wege: `POST /arbeit/{k}/vorschlag/text` (Text von Hand, durch
  `pruefe_platzhalter`), `…/zuruecksetzen`, `…/ergebnis`, `…/auto`. Das
  Ergebnis trägt keinen Text; gezeigt und kopiert wird der Text mit
  eingesetztem Link, gespeichert der mit `{link}`.
- Reihenfolge: `arbeit.arbeitsreihenfolge` (`scoring.sort_by_rank`);
  ausgeschlossene Gruppen fehlen, Gruppen ohne Datensatz stehen am Ende.
- Kein `python-multipart` — `parse_qsl` genügt. Alle schreibenden Wege hinter
  `_nur_lokal`.

## Die Kommentarautomatik (`lauf.py`, `automatik.py`)

### Die Reihenfolge steht an einer Stelle: `lauf.naechster_schritt`

```
Kampagne (sequentiell) → Gruppenregeln lesen → Beitrittsanfragen (derzeit 0)
   → Neubewertung (einmal je Kampagne und Lauf) → Arbeitsliste
   → je Gruppe: Kommentare und Beitrag → nächste Gruppe → nächste Kampagne
```

Kommandozeile, Dienst und Fernbetrieb fragen alle dort; keiner kennt die
Reihenfolge selbst.

- **Arbeitsliste**: stabil nach `vorrang` (Beitrag **und** Kommentare möglich
  vor einem von beiden vor nichts), darin Score-Reihenfolge.
- `automatik.kommentare_zuerst: true` (Vorgabe im Code: erst Beitrag). Zwei
  **Kandidaten** (`_beitragsschritt`, `_kommentarschritt`): gibt der erste
  nichts her, wird der zweite gefragt.
- **Zehn Kommentare je Gruppe aus fünf Vorlagen** (`ZIEL_JE_GRUPPE`,
  `VORLAGEN_JE_TOPF`); das Ziel wird beim Start eingefroren. Fortschritt wird aus
  `campaign_group_texte.status` **gelesen**, nicht geführt.
- **Eine Kampagne ist erreicht** bei `marketing.kampagne.ziel_kommentare` (100)
  erfolgreichen Kommentaren oder wenn jede Gruppe voll ist. `abgeschlossen`
  (erreicht → `completed`) ≠ `fertig` (Lauf versucht nichts mehr); eine leere
  Kampagne beendet den Lauf, wird aber nicht `completed`.
- **Die Kampagnenliste wird beim Start eingefroren**; `--neu` friert eine
  frische ein (nur beim ersten Aufruf im Fernbetrieb). Eine leere Liste wird
  nie eingefroren. Eine Kampagne, deren Gruppen alle ruhen, behält ihren Platz.
- `automatik.mitgliedschaft_pflicht: false` — Gruppen ohne vermerkte
  Mitgliedschaft werden versucht (der Vermerk ist unser Arbeitsstand).
- **Gruppenregeln vor Anfrage und Arbeit** (`Schrittart.REGELN`,
  `beitritt.regeln_zuerst`): `regeln_offen` nennt höchstens die nächste
  Beitritts-, Arbeits- **und** Kommentargruppe. Ein ungelesener Befund schreibt
  nichts. Ungelesene Regeln kosten den **Link**, nicht den Kommentar.

### Ausgänge und was sie kosten

| Ausgang | Folge |
|---|---|
| Erfolg | zählt (Tagesmenge, Gruppe, Takt) |
| kein Anlass (`kein_anlass`) | nichts gebucht, Gruppe **ruht** (`automatik.ruhe_minuten`, 2) |
| tote Beitragsadresse (`beitrag_weg`) | nächster Beitrag im selben Schritt, dann Ruhe |
| technischer Fehlschlag | bis zu drei Beiträge im Schritt (`MAX_BEITRAEGE_JE_SCHRITT`), dann Ruhe — **kein Ausschluss** |
| Ablehnung durch die Gruppe | Gruppe beiseite für diesen Lauf; zählt in `qualifikation.Beobachtung` |
| `GRUPPENLIMIT` (Freigabe-Warteschlange voll) | nächste Gruppe, verbraucht keine Fassung |
| `RATE_LIMIT` (Facebook bremst) | nur diese Aktion pausiert; Backoff verdoppelt sich (60 Min … 24 h), überlebt den Neustart |
| Sitzungsfehler / Anmeldewand | Lauf hält **sofort** an — der einzige Grund dafür |

- **Nur SUCCESS zählt** (`versuche_heute*`, `letzter_versuch` fragen
  `erfolg = 1`). Ein Fehlschlag verbraucht keine Tagesmenge und keinen Takt.
- **Ein technischer Fehlschlag zählt nicht gegen eine Fassung**
  (`gescheiterte_kommentarfassungen` fragt `qualifikation.klassifiziere`, im
  Zweifel `TECHNISCH`). `MAX_VERSUCHE_JE_FASSUNG` = 3, danach `erschoepft`.
- **Ruhezeit statt Schlussstrich** (`automatik_lauf_uebersprungen.wiederholen_ab`).
  Ruht eine Gruppe, wartet der Lauf (`naechste_rueckkehr`), statt sich fertig zu
  melden; Schlaf gedeckelt (`wartesekunden`, `ruhesekunden`), der
  Schleifenwächter vergisst nach einem Schlaf.
- **Fehlerisolierung**: Ein Fehler kostet eine Gruppe diesen Lauf, eine
  Kampagne darf für sich scheitern (`_lies_kampagne`). Netzfehler im
  Fernbetrieb: `MAX_NETZFEHLER` (5); ein nicht buchbarer Ausgang beendet den
  Lauf.
- **Anmeldung vor dem Lauf** (`actions.ist_angemeldet`, `_sitzung_pruefen`):
  abgemeldet → nichts tun, Exit 2. Der Text beginnt mit `NICHT_ANGEMELDET` —
  daran erkennt `automatik._SITZUNG` den Sitzungsfehler.
- **Der Takt hält eine Aktion an, nicht den Lauf.** `wartet_auf_takt` fragt
  jede Bremse einzeln, die kürzeste gewinnt; `Lage.nur_takt` trennt eigenen
  Takt (abwarten) von fremder Bremse (Lauf endet).

### Grenzen je Aktion (`grenzen.py`)

Beitritt, Beitrag und Kommentar haben je eigene Tagesmenge, eigenen Takt und
eigene Sperre; eine Antwort zählt als Kommentar. `limits`/`delays` in
`settings.yaml` sind **unsere Planungswerte**, keine Facebook-Grenzen.

| | Tagesmenge | je Gruppe | Takt |
|---|---:|---:|---|
| Kommentar | 100 | 10 | 5–8 Min |
| Beitrag | 10 | – | 15–30 Min |
| Beitritt | **0** | – | 3–8 Min |

`je_gruppe_taeglich` gilt dem Kommentar, nicht der Gruppe (der Beitrag bleibt
frei); `0` heißt dort „ohne Schranke", bei `daily` „gar nicht".

**Duplikate:** nie zweimal unter denselben Beitrag (`bisherige_post_urls`,
über alle Kampagnen, nur Erfolge sperren, `canonical_post_url` schneidet
`__cft__`/`__tn__` ab), nie zweimal derselbe Satz in derselben Gruppe
(`store.verwendete_vorlagen`). Eine Kontrolle je Person gibt es nicht — sie
bräuchte Autorennamen.

### Beitrag lesen, beurteilen, antworten (`inhalt.py`, `entscheidung.py`)

```
Beitrag -> Thema + Absicht + Ziel/Herkunft/Strecke -> Anlass -> Relevanz
        -> Antwortart (NO_REPLY | PRIVATE_CONTACT_SUGGESTION |
           CONTEXTUAL_APP_MENTION | DIRECT_APP_RECOMMENDATION) -> Linkmodus
```

- **Elf Themen** (Reihenfolge entscheidet: Versand, Reise, … Behörde **vor**
  Job, weil „job" auch „jobcenter" trifft) und eine **Absicht**
  (sucht/bietet/fragt).
- **Anlass** (`erkenne_anlass`, erster Treffer gewinnt): Medikamente → Platz im
  Koffer → sucht Reisenden → Geschenk → Versandweg → bietet Mitnahme →
  Gegenstand. Ein Anlass nur bei Versand-/Reisethema (Ausnahme: ausdrückliche
  Suche nach einem Reisenden). Reiseankündigung = **Bewegung und Ziel**
  (`_reise_mit_ziel`), syrischer Wortschatz (نازل، طالع، رايح، عودة …).
  Ein Gepäckhalbsatz bringt sein Thema mit (nur aus `SONSTIGES`); ein erkannter
  Anlass hebt `KEINE` auf `MITTEL`.
- **Relevanz** hoch/mittel/keine; die Schwelle kommt aus `Anspruch`.
  `marketing.anlass_pflicht: false` — der vorbereitete Text darf auch ohne
  Anlass hinausgehen, sobald die Relevanz reicht.
- **Linkmodus ist die Kehrseite der Antwortart.** `NO_LINK` hat bewusst
  **keinen** Textvorrat (er wäre erfunden) — dort wird nicht kommentiert.
  Link nur mit gelesener Regel ohne Linkverbot und ohne `OHNE_LINKS`-Beobachtung.
  Die Werbungslogik (`Erlaubnis.werbung`) ist entfernt.
- **Anlasstexte** (`anlaesse:` in `textvorlagen.yaml`): kein Vorrat zu einem
  Anlass heißt kein Kommentar. `{link}` wird unmittelbar vor dem Absenden
  aufgelöst (`beitrag.setze_adresse`; die Adresse reist getrennt als
  `link_url`); ein offener Platzhalter verhindert den Kommentar. Der wirklich
  abgesetzte Text wird vor `melde_vorschlag` zurückgeschrieben.
- **Stark vor schwach**: `Gelegenheit.rang` = Nähe der Antwortart, dann
  Reaktionen + Kommentare. Ohne einen lesbaren Text gilt der Rückfall
  (belebtester Beitrag).
- Beiträge werden **während des Scrollens** eingesammelt (virtualisierter
  Strom), zuerst je Artikel, sonst seitenweit mit Kennzahlen 0. Bildtexte
  (`alt`) gehen mit (`actions.mit_bildtexten`).
- `campaign pruefe-inhalt "<text>"` zeigt Befund und Entscheidung ohne Netz.

### Qualifikation (`qualifikation.py`)

Gerechnet aus Mitgliedschaft, gelesenen Gruppenregeln
(`group_marketing.regeln_gelesen_am` + Flags) und Versuchsprotokoll. **Die
Regeln der Gruppe binden** (`darf_nach_regeln`, ohne Schalter), die
Beobachtung schränkt nur ein. `OHNE_LINKS`/`OHNE_KOMMENTARE`/`OHNE_BEITRAEGE`
lassen jeweils das andere zu; `UNGEEIGNET` nur aus Beobachtung. „Nicht
gelesen" ist nicht „nichts verboten". `qualifikation.pflicht: false` betrifft
nur die Beitrittsstufen. `Ablehnungsgrund` (in `post_versuche.grund`,
gerechnet in `beende_versuch`) beantwortet die Frage des Menschen,
`Ausgangsart` die des Laufs.

### Was aus einem abgeschickten Beitrag/Kommentar wird (`automation/actions.py`)

- `comment_on_post` liefert einen `Kommentarausgang` (sichtbar / wartet auf
  Freigabe / nicht angenommen / `beitrag_weg` / Gruppenlimit), kein `bool`.
  `BEITRAG_WEG` und `ANMELDEWAND` werden **vor** der Feldsuche geprüft.
- `post_to_group` wartet auf die Vorschaukarte, nimmt dann die nackte Adresse
  aus dem Text (`trenne_adresse`: nur genau eine, nur am Zeilenende) und
  meldet über `Beitragsausgang`, wenn die Karte das nicht überlebt.

### Der Wächter (`watchdog.py`)

Hält **einen** `campaign automatik` am Leben — ohne Kampagnenlogik
(`test_der_waechter_kennt_keine_kampagnenlogik`). `baue_befehl` ohne `--neu`,
`--kampagne`, `--limit`. Die Sperre (`data/automatik.lock`) gehört dem Lauf;
eine Sperre mit toter Kennung gilt nicht (Windows: `GetExitCodeProcess`).
Macht den SSH-Tunnel selbst auf (`watchdog.tunnel`, `-N`,
`ServerAliveCountMax=3`); gefragt wird der Port. **Kein Kennwort in einer
Datei** (`ssh-add` oder Terminal).

## Dienst (`web.py`, `dashboard.py`)

- **`GET /r/{code}`** zählt und leitet mit **302** weiter (nie 301);
  unbekannter Code → 404. Vorschau-Abrufe (Facebook, WhatsApp, Telegram)
  bekommen eine eigene Karte (`marketing.vorschau`, `og:url` = eigene Adresse,
  Weiterleitung per JavaScript statt Meta-Refresh) und zählen nicht. **Nie zum
  Testen aufrufen** — `/healthz` nehmen.
- **Keine IP-Adressen im Bestand**: HMAC aus IP, User-Agent und Tagesdatum.
  Hinter nginx: uvicorn mit `--proxy-headers --forwarded-allow-ips 127.0.0.1`,
  nginx setzt `X-Forwarded-For $remote_addr` (überschreiben).
- **Zwei Zugänge**: vom selben Rechner (SSH-Tunnel) bedienbar; von außen hinter
  Basic Auth **nur lesend** (`UEBERSICHT_TOKEN`). Die Absicherung liegt im
  Dienst (`_nur_lokal`), ausgeblendete Knöpfe sind nur Aufrichtigkeit.
- **`POST /events`** nimmt Meldungen der App an (`EVENTS_TOKEN`). Trichter
  `click` → `landing_visit` → `registration` → `download` → `activation` →
  `qualified` → `conversion`, jede Stufe für sich gezählt. Spätere Ereignisse
  erben die **erste** Zuordnung; `user_identities` verknüpft `anon-…` mit der
  Benutzerkennung (beim Lesen, nie durch Umschreiben). Ohne erkennbaren
  Menschen keine Zuordnung; unbekannte Codes werden verworfen. `download` zählt
  je Mensch einmal. Die Web-App meldet über ihre eigene API — das Geheimnis
  gehört nicht in den Browser.
- Referral: jede Entscheidung mit Begründung, Verdacht → `review`, ein Status
  fällt nie von selbst zurück; Prämien in `config/rewards.yaml` (kein
  Geldbetrag). `conversion_rate` ist `None` bei null Klicks.
- **Übersicht**: zeigt den ganzen Bestand (kein Filter vorausgewählt), blättert
  ab 25 Zeilen, filtert/sortiert über den ganzen Bestand, merkt sich den Stand
  in `sessionStorage` (jeder Zugriff in `try`/`catch`). Der Zähler nennt, was
  ausgeblendet ist, und bietet „Filter zurücksetzen". Beschriftungen des
  Stands aus `status_label` (eine Quelle). `POST /bearbeiten` nimmt eine
  **Liste**.
- FastAPI ist optional (`[web]`) und wird auf Modulebene importiert (sonst 422
  wegen `from __future__ import annotations`).

## Windows-Fallstricke

- Dateien **immer** mit `encoding="utf-8"` öffnen (Vorgabe cp1252 zerstört
  Arabisch). Von Hand erstellte CSV/TXT mit `utf-8-sig` lesen (BOM), CSV mit
  `utf-8-sig` und `;` schreiben.
- PowerShell 5.1 kennt kein `&&`/`||` — mit `;` und `if ($?) { }` ketten.
- `ausrollen.sh` in **Git Bash**, nicht PowerShell (`tar | ssh`).
