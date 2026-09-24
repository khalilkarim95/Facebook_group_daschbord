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
mitglieder.py ──► scoring ──► storage/sqlite_store (groups)
                                │
                  marketing/store (Kampagnen, Codes, Texte, Versuche, Ereignisse)
                                │
   Text:     vorlagen, beitrag, kurzcode
   Urteil:   inhalt (Thema/Anlass/Relevanz), bezug (Bezüge), entscheidung,
             ausgang (Antwort von Facebook), grenzen (Tagesmengen/Takt)
   Ablauf:   lauf (Reihenfolge), automatik (Treiber), arbeit (Arbeitsseite),
             watchdog
   Dienst:   web (FastAPI), dashboard, arbeitsseite, tracking, referral, rewards
                                │
                  automation/ (Playwright, sichtbar: actions, browser)
```

Die reinen Module (`inhalt`, `bezug`, `entscheidung`, `ausgang`,
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
  Ergebnisse. Gerechnete Urteile (Lauffortschritt) werden
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
- **Kein Test schläft länger als zehn Sekunden** (`_kein_langer_schlaf`): Ein
  Treiber ohne `warte=`, der in eine Ruhezeit läuft, scheitert statt zu
  hängen — am 23.09.2026 blieb die ganze Testfolge so ohne Meldung stehen.

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
- Auf den Server: **jedes** `bash ./ausrollen.sh` nimmt die CSV-Dateien aus
  `data/from_lokal/` mit (seit 23.09.2026; erst Trockenlauf, dann Rückfrage,
  `--ja` ohne Rückfrage, `--ohne-mitglieder` nur Code; eingelesen nach dem
  Einsetzen; zugeordnet wird dabei nichts).

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
  an `score_max`). Gerechnet wird der Score beim Einlesen
  (`import-mitglieder`); **der Lauf bewertet nicht neu** (seit 23.09.2026).
  Kategorie, Zielgruppe, Stadt werden gepflegt, nie abgeleitet.
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

**Lesbare Namen seit dem 23.09.2026:** Ist `marketing.link_basis` gesetzt
(`https://b-tarikak.de/t`), bekommt jede neue Zuordnung statt `wr4s9xw` einen
Namen aus zwei Wörtern und einer Zahl (`kurzcode.lesbarer_code`,
`safar-sham-12`) unter `b-tarikak.de/t/…`. Der Dienst beantwortet `/t/{name}`
wie `/r/{code}`; nginx auf b-tarikak.de reicht `/t/` an 127.0.0.1:8090 weiter.
Die Basis steht zusätzlich im Speicher (`marketing_meta.link_basis`, beim
Dienststart geschrieben), weil `vergib_kurzcodes` keine Konfiguration kennt.
`campaign kurzlinks --lesbar` stellt Zuordnungen **ohne** veröffentlichten
Text um; veröffentlichte behalten ihre Adresse.
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
Kampagne (sequentiell) → Beitrittsanfragen (derzeit 0)
   → Runde 1: Gruppe 1 → 2 → … → N   (jede einmal, gleich mit welchem Ausgang)
   → Runde 2: Gruppe 1 → 2 → … → N   → … bis die Kampagne erreicht ist
   → nächste Kampagne
```

Kommandozeile, Dienst und Fernbetrieb fragen alle dort; keiner kennt die
Reihenfolge selbst.

- **Arbeitsliste**: die Score-Reihenfolge (`sort_by_rank`). Sie ordnet die
  Runde, sie wählt nicht mehr — siehe „Die Runde".
- **Entfernt am 23.09.2026** (Anweisung des Nutzers): das Lesen der
  Gruppenregeln (`Schrittart.REGELN`, `marketing regeln`), die Neubewertung im
  Lauf (`Schrittart.BEWERTEN`, `rescoring.py`) und die ganze Qualifikation
  (`qualifikation.py`, `campaign qualifikation`, Spalte „Darf", Sperre nach
  wiederholter Ablehnung). Jede Gruppe bekommt dieselbe `Erlaubnis()`; ein
  Kommentar trägt trotzdem **keinen Link** (siehe „Nur Kommentare, ohne
  Link"). Die Spalten `regel_*`, `regeln_gelesen_am`, `bewertet_am`
  bleiben (Migrationen additiv), werden aber nicht mehr geschrieben.
- `automatik.kommentare_zuerst: true` (Vorgabe im Code: erst Beitrag). Zwei
  **Kandidaten** (`_beitragsschritt`, `_kommentarschritt`): gibt der erste
  nichts her, wird der zweite gefragt. Seit dem 23.09.2026 postet der Lauf
  gar nicht (`automatik.beitraege: false`) — siehe „Nur Kommentare".
- **Zwanzig Kommentare je Gruppe aus fünf Vorlagen** (`ZIEL_JE_GRUPPE` = 20
  seit 23.09.2026, `VORLAGEN_JE_TOPF`); das Ziel wird beim Start eingefroren —
  ein offener Lauf behält seine Zahl bis `campaign automatik --neu`.
  Fortschritt wird aus `campaign_group_texte.status` **gelesen**, nicht geführt.
- **Eine Kampagne ist erreicht** bei `marketing.kampagne.ziel_kommentare` (240)
  erfolgreichen Kommentaren oder wenn jede Gruppe voll ist. `abgeschlossen`
  (erreicht → `completed`) ≠ `fertig` (Lauf versucht nichts mehr); eine leere
  Kampagne beendet den Lauf, wird aber nicht `completed`.
- **Die Kampagnenliste wird beim Start eingefroren**; `--neu` friert eine
  frische ein (nur beim ersten Aufruf im Fernbetrieb). Eine leere Liste wird
  nie eingefroren. Eine Kampagne, deren Gruppen alle ruhen, behält ihren Platz.
- `automatik.mitgliedschaft_pflicht: false` — Gruppen ohne vermerkte
  Mitgliedschaft werden versucht (der Vermerk ist unser Arbeitsstand).

### Die Runde: jede Gruppe einmal, der Reihe nach (23.09.2026)

`Kampagnenfortschritt.rundenwahl` gibt die nächste Gruppe der laufenden Runde
heraus, die noch nicht dran war; sind alle durch, beginnt die nächste Runde
bei Gruppe 1. Gespeichert ist allein, wer in welcher Runde dran war
(`automatik_lauf_besuche`, Schritt 27, `store.merke_besuch`); die Runde
selbst wird gerechnet.

- **Vermerkt wird der Besuch, wenn der Schritt hinausgeht** — örtlich vor dem
  Browser, im Fernbetrieb in `/automatik/naechster`. Der Ausgang spielt keine
  Rolle: Erfolg, kein Anlass, technischer Fehlschlag, ein Absturz — die Gruppe
  war dran, die nächste ist an der Reihe.
- **Nur die Ruhezeit übergeht eine Gruppe**, und nur für ihre Dauer; danach
  holt sie ihren Platz **in derselben Runde** nach. Ruhen alle, wartet der
  Lauf (`naechste_rueckkehr`). Die Ruhe ist so zugleich die Ruhephase
  zwischen zwei Runden.
- **Nicht in der Runde** (`Gruppenfortschritt.rundenfaehig`): voll,
  erschöpft, an der Tagesmenge je Gruppe, ohne offene Fassung, für den Lauf
  beiseite. Eine erschöpfte Gruppe mit offenem Beitrag bekommt deshalb keinen
  Kommentarschritt mehr — vorher blieb sie über `bearbeitbar` darin.
- **Der Beitrag gehört nicht zur Runde**: `_beitragsschritt` nimmt die erste
  Gruppe mit offenem Beitrag (vorher nur die erste Gruppe überhaupt).
- **Der Anlass** war ein Lauf über elf Gruppen, in dem nur die ersten vier dran
  kamen und eine davon gut fünfzigmal hintereinander („alle sichtbaren
  Beiträge sind bereits kommentiert"). Gewählt wurde die erste Gruppe, die
  gerade durfte, und nach zwei Minuten Ruhe durfte sie wieder. Festgehalten
  in `tests/test_runden.py` (11 Gruppen → alle 11 → Runde 2 → wieder alle 11,
  örtlich und im Fernbetrieb).

### Ausgänge und was sie kosten

| Ausgang | Folge |
|---|---|
| Erfolg | zählt (Tagesmenge, Gruppe, Takt) |
| kein Anlass (`kein_anlass`), auch „alle sichtbaren Beiträge sind bereits kommentiert" und „keine Beiträge gefunden" | nichts gebucht, Gruppe **ruht** (`automatik.ruhe_minuten`, 2) — bis 23.09.2026 hieß das `erschoepft` und war ein Urteil über die Gruppe; ein älterer Arbeitsrechner sendet es noch, es gilt wie `kein_anlass` |
| tote Beitragsadresse (`beitrag_weg`) | nächster Beitrag im selben Schritt, dann Ruhe |
| technischer Fehlschlag | bis zu drei Beiträge im Schritt (`MAX_BEITRAEGE_JE_SCHRITT`), dann Ruhe — **kein Ausschluss** |
| Ablehnung durch die Gruppe | Gruppe beiseite für diesen Lauf (keine dauerhafte Sperre) |
| `GRUPPENLIMIT` (Freigabe-Warteschlange voll) | nächste Gruppe, verbraucht keine Fassung |
| `RATE_LIMIT` (Facebook bremst) | nur diese Aktion pausiert; Backoff verdoppelt sich (60 Min … 24 h), überlebt den Neustart |
| Sitzungsfehler / Anmeldewand | Lauf hält **sofort** an — der einzige Grund dafür |

- **Nur SUCCESS zählt** (`versuche_heute*`, `letzter_versuch` fragen
  `erfolg = 1`). Ein Fehlschlag verbraucht keine Tagesmenge und keinen Takt.
- **Ein technischer Fehlschlag zählt nicht gegen eine Fassung**
  (`gescheiterte_kommentarfassungen` fragt `ausgang.klassifiziere`, im
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

### Nur Kommentare, ohne Link (23.09.2026)

Anweisung des Nutzers: kein automatisches Posten, kein Tracking-Link in einem
Kommentar, 20 Kommentare je Gruppe, das Kampagnenziel aus 12 Stunden und
3 Minuten Abstand gerechnet.

```
12 h = 720 Min / 3 Min Abstand = 240 Kommentare   (ziel_kommentare, comments.daily)
240 / 20 je Gruppe             =  12 Gruppen      (bei 11 Gruppen: 220, "alle voll")
```

- **Kein automatischer Beitrag** (`automatik.beitraege: false`, Vorgabe im
  Code ebenfalls aus). `lies_fortschritt(beitraege=False)` gibt keiner Gruppe
  einen offenen Beitrag — keine Gruppe bleibt seinetwegen offen, keine
  Kampagne wartet auf ihn, `naechster_schritt` liefert nur Kommentare.
  `texte_sicherstellen` legt für einen Kommentarschritt keine Beitragstexte
  an (`stelle_texte_bereit(nur=...)`). Der Weg bleibt erhalten (`true`
  schaltet ihn ein); von Hand (Arbeitsseite) ist der Beitrag unberührt.
- **Kein Link in einem Kommentar — an drei Stellen gehalten:**
  1. Wo der Text entsteht: `beitrag.mit_link(..., texttyp=KOMMENTAR)` nimmt
     `{link}`, `{landing_page}` und `{tracking_code}` **samt Hinführung**
     heraus (`ohne_link`: „… بنفس الاتجاه. حمّل … من هنا: {link}" → „…
     بنفس الاتجاه."). Steht der App-Name nur im Satz des Links, bleibt der
     Satz und nur „من هنا:" fällt. Server und örtlicher Lauf schicken für
     Kommentare keine `link_url` mehr.
  2. Wo der Lauf den Text wählt: `entscheide_und_kommentiere` wendet
     `ohne_link` auf jeden Anlasstext an und benutzt `link_url` nicht mehr —
     auch nicht, wenn ein älterer Server sie schickt.
  3. Vor dem Absenden: ein `/r/`- oder `/t/`-Pfad, ein Tracking-Parameter
     oder ein innerer Code (`FB-…`) verhindert den Kommentar — im Lauf und in
     `actions.comment_on_post`.
     Jeder Kommentar kommt hier durch, auch `campaign auto` und die
     Arbeitsseite.
- **Die freie Adresse am Ende** (`marketing.kommentar_adresse`,
  `https://b-tarikak.de/home`, Wunsch des Nutzers am selben Tag): ohne Code,
  sie zählt nichts. `beitrag.mit_kommentaradresse` hängt sie hinter den
  letzten Satz — „… من سوريا. https://b-tarikak.de/home" — und nie zweimal;
  Server und örtlicher Lauf schicken sie beim Kommentar als `link_url`. Eine
  Tracking-Adresse (`/r/`, `/t/`, `?ref=`) wird dort nie angenommen
  (`kommentar_adresse` → `""`). Die Prüfung vor dem Absenden unterscheidet
  deshalb: `comment_on_post` sperrt jede **Tracking**-Adresse
  (`urls.tracking_adresse_im_text`), der Lauf jede Adresse außer genau der
  freien (`urls.adresse_im_text(..., erlaubt=...)`).
- **Das Tracking bleibt**: Codes, Kurzcodes, `/r/`, `/t/`, der Beitrag mit
  Link und die Auswertung sind unverändert. Die Vorlagen in
  `textvorlagen.yaml` tragen ihr `{link}` weiter — entfernt wird beim
  Ausfertigen, nicht in der Vorlage.
- **Die Runde bleibt** (eine Gruppe je Runde einmal), `je_gruppe_taeglich:
  20` ist die Tagesgrenze je Gruppe.
- Festgehalten in `tests/test_nur_kommentare.py`.

### Bis zu 15 Scroll-Runden je Gruppe (23.09.2026)

```
Gruppe öffnen → Runde 1 scrollen + beurteilen → Runde 2 → … → Runde 15
   → geeigneter Beitrag gefunden?  ja → kommentieren (bestehende Kette)
                                   nein → Gruppe ruht, nächste Gruppe der Runde
```

- **Eine Scrollfunktion: `actions.fetch_top_posts`.** Das Analyseskript des
  Nutzers (`GroupPostAnalyzer.scan_and_analyze_current_group`,
  `GROUP_SCROLL_ROUNDS`) stand nie im Projekt; sein Kern (scrollen,
  `div[role='article']`, Text + Bildtexte) steckte schon hier. Ausgebaut,
  keine zweite Schleife: `runden` (`automatik.scroll_runden`, 15; vorher fest
  5), `bekannt` (schon kommentierte Beiträge zählen nicht mit — vorher füllten
  sie die Menge, und die Suche hörte bei „alle sichtbaren schon kommentiert"
  auf) und `geeignet` (nach **jeder** Runde).
- **Das Urteil ist dasselbe wie beim Kommentieren** (`automatik.geeignet_fuer`):
  `beurteile_beitraege` (Relevanz mit der Schwelle aus `Anspruch`, nicht
  pauschal `hoch`) und `text_zur_gelegenheit`. Ein einzelnes Wort wie „سفر"
  oder „نقل" genügt nicht — das entscheidet `inhalt.lies`.
- **Ein ungeeigneter Beitrag beendet nichts.** Weder die Suche (weiter bis
  Runde 15) noch den Kommentarschritt: Hat der beste Beitrag keinen Text,
  kommt der nächste dran (vorher endete der Schritt mit „kein Anlass").
- **Ein Fehler in einer Gruppe hält die anderen nicht auf**: Die Runde hat
  die Gruppe schon als besucht vermerkt, die nächste ist dran. Im
  Fernbetrieb meldet der Arbeitsrechner den Fehler als Ausgang; die Gruppe
  kommt in der nächsten Runde wieder.
- Die Wege von Hand (`campaign auto`, Arbeitsseite) rufen ohne `geeignet`
  und behalten ihr `limit`. Festgehalten in `tests/test_scroll_runden.py`.

### Neue Kampagnen kommen in den offenen Lauf (24.09.2026)

Der Wächter startet `campaign automatik` bewusst **ohne** `--neu`, und ein
offener Lauf behielt seine eingefrorene Kampagnenliste — eine neu angelegte
Kampagne kam damit nie dran. Jetzt hängt `hole_oder_starte_lauf` bei jedem
Fortsetzen die aktiven Kampagnen, die noch fehlen, **hinten** an
(`store.ergaenze_lauf_kampagnen`). Was schon drinsteht, behält Platz, Stand
und Bewertung; der laufende Vorgang wird nicht umgestellt. `--neu` bleibt
für eine ganz frische Liste. Test:
`test_ein_offener_lauf_nimmt_eine_neue_kampagne_hinten_auf`.

### Bild und Schlusssatz statt Adresse im Kommentar (24.09.2026)

```
vorher  … من سوريا. https://b-tarikak.de/home
jetzt   … من سوريا. الرابط المباشر للتحميل موجود في البايو (أعلى الصفحة) 👇   + Bild
```

Wunsch des Nutzers: Die ausgeschriebene Adresse machte bei Facebook
Probleme. Sie steht jetzt **im Bild** (`config/bilder/kommentar.jpg`, der
Pinguin mit Karte, Name, Adresse und Store-Knöpfen).

- **`marketing.kommentar_schluss` geht `kommentar_adresse` vor**
  (`beitrag.kommentar_adresse`). Der Satz reist auf demselben Weg wie vorher
  die Adresse (`link_url`) und wird genauso angehängt — einmal, mit einem
  Leerzeichen, nicht mit einem Zeilenumbruch (ein `\n` über `insert_text`
  ist im Kommentarfeld unberechenbar). `kommentar_adresse` steht auf `""`.
- **`marketing.kommentar_bild`** (`beitrag.kommentar_bild`): relativ zum
  Projekt, liegt unter `config/` und kommt damit mit jedem Ausrollen auf
  Server **und** Arbeitsrechner — hochgeladen wird dort, wo der Browser ist.
  JPEG, weil Facebooks Kommentar-Upload damit am sichersten ist (das
  Original war WebP).
- **`comment_on_post(..., bild=)`** hängt es nach dem Text an
  (`_bild_anhaengen`): das Dateifeld im **selben Formular** wie das
  Kommentarfeld, sonst das letzte der Seite; abgesendet wird erst, wenn die
  Vorschau im Formular steht (bis 15 s). Kommt das Bild nicht an, geht der
  Text trotzdem hinaus — er nennt App und Weg.
- Die Auswahlkette ruft weiter `kommentieren(context, url, text)`; das Bild
  kommt über `automatik._mit_bild` (ein `partial`) dazu, örtlich wie fern.
  Die Arbeitsseite von Hand zeigt nur den Text — das Bild fügt dort der
  Mensch ein.
- Festgehalten in `tests/test_kommentarbild.py`.

### Zehn Beiträge ansehen, dann erst aufgeben (24.09.2026)

```
Gruppe → bis 15 Runden, mindestens 10 Beiträge → bis zu 5 Versuche
   → nicht beschreibbar / gelöscht → Beitrag 24 h gesperrt, nächster Beitrag
   → volle Suche, nichts Kommentierbares → Gruppe ausgeschlossen
```

Anweisung des Nutzers nach einem Lauf, in dem Runde für Runde **derselbe**
Beitrag scheiterte („Kommentarfeld nicht beschreibbar", „Found 1 post(s)",
„1 Beitraege versucht"): *bis 15 Mal herunterscrollen, mindestens 10
Beiträge ansehen, und wenn dann wirklich nichts zu machen ist, die Gruppe
ausschließen.*

- **Die Suche hört nicht mehr beim ersten geeigneten Beitrag auf**
  (`fetch_top_posts(mindestens=…)`, im Lauf `MINDEST_BEITRAEGE` = 10).
  Vorher gab es genau einen Kandidaten, und scheiterte der, gab es keinen
  zweiten. Ohne geeigneten Beitrag laufen weiterhin alle Runden.
- **Mehr Adressen je Seite.** Die Zeitangabe eines Beitrags zeigt im Strom
  oft nur `#`; die echte Adresse setzt Facebook erst beim Überfahren ein
  (`_adresse_nach_hover`). Dazu trägt der Bildverweis die Kennung
  (`set=pcb.<id>`, `set=gm.<id>` in `urls.beitragslinks`).
- **Fünf Versuche je Schritt statt drei** (`MAX_BEITRAEGE_JE_SCHRITT`).
- **Ein gescheiterter Beitrag kommt nicht wieder** (Tabelle
  `gescheiterte_beitraege`, Migrationsschritt 28; `store.gesperrte_post_urls`
  = kommentiert ∪ gescheitert der letzten `SPERRE_GESCHEITERT_STUNDEN`).
  `post_versuche` trägt die Adresse nur bei Erfolg — deshalb stand derselbe
  gescheiterte Beitrag in jeder Runde wieder oben. Gesperrt wird nur, was am
  **Beitrag** liegt (kein Feld, nicht beschreibbar, gelöscht); ein
  Sitzungsfehler sperrt nichts und beendet den Schritt sofort. Der Server
  schickt die gesperrten Adressen als `bisherige_post_urls` mit.
- **Nicht beschreibbar heißt jetzt: zweimal versucht.** `_feld_anklicken`
  klickt, und wenn etwas darüber liegt, drückt es Escape, scrollt das Feld
  in den Blick und klickt noch einmal.
- **Der Ausschluss** (`nichts_zu_machen` → `Schrittergebnis.ausschliessen`
  → `store.schliesse_gruppe_aus`, örtlich wie fern) verlangt alles zugleich:
  alle Runden gelaufen, etwas gesehen, etwas gelesen, kein Beitrag
  geeignet, und der Schritt endete mit „kein Anlass". Ein technischer
  Fehlschlag oder eine Anmeldewand schließt nie aus — sie sperren
  höchstens Beiträge. Ausgeschlossen heißt `bearbeiten = 0` mit Grund,
  Tracking-Code bleibt gültig, ein Haken in der Übersicht nimmt es zurück.
  Das hebt die Regel vom 21.09.2026 („keine Gruppe fällt dauerhaft heraus")
  für genau diesen einen Fall auf.
- Festgehalten in `tests/test_zehn_beitraege.py`.

### Grenzen je Aktion (`grenzen.py`)

Beitritt, Beitrag und Kommentar haben je eigene Tagesmenge, eigenen Takt und
eigene Sperre; eine Antwort zählt als Kommentar. `limits`/`delays` in
`settings.yaml` sind **unsere Planungswerte**, keine Facebook-Grenzen.

| | Tagesmenge | je Gruppe | Takt |
|---|---:|---:|---|
| Kommentar | 240 | 20 | 3 Min |
| Beitrag | 10 (im Lauf aus) | – | 15–30 Min |
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
  Der Deckel `marketing.linkmodus_max` steht seit dem 23.09.2026 auf
  `app_name_only`: Die App wird genannt, nie verlinkt. Die Werbungslogik
  (`Erlaubnis.werbung`) ist entfernt.
- **Anlasstexte** (`anlaesse:` in `textvorlagen.yaml`): kein Vorrat zu einem
  Anlass heißt kein Kommentar. Ein Kommentar trägt **keine Adresse**
  (`beitrag.ohne_link`, siehe „Nur Kommentare, ohne Link"); ein offener
  Platzhalter verhindert den Kommentar. Der wirklich abgesetzte Text wird vor
  `melde_vorschlag` zurückgeschrieben.
- **Stark vor schwach**: `Gelegenheit.rang` = Nähe der Antwortart, dann
  Reaktionen + Kommentare. Ohne einen lesbaren Text gilt der Rückfall
  (belebtester Beitrag).
- Beiträge werden **während des Scrollens** eingesammelt (virtualisierter
  Strom), zuerst je Artikel, sonst seitenweit mit Kennzahlen 0. Bildtexte
  (`alt`) gehen mit (`actions.mit_bildtexten`).
- `campaign pruefe-inhalt "<text>"` zeigt Befund und Entscheidung ohne Netz.

### Die Antwort von Facebook (`ausgang.py`)

`Ausgangsart` beantwortet die Frage des Laufs (`RATE_LIMIT` pausiert die
Aktion, `GRUPPENLIMIT` die Gruppe, `TECHNISCH` zählt nirgends — im Zweifel
technisch), `Ablehnungsgrund` die des Menschen in der Übersicht
(`post_versuche.grund`, gerechnet in `beende_versuch`).

### Was aus einem abgeschickten Beitrag/Kommentar wird (`automation/actions.py`)

- `comment_on_post` liefert einen `Kommentarausgang` (sichtbar / wartet auf
  Freigabe / nicht angenommen / `beitrag_weg` / Gruppenlimit), kein `bool`.
  `BEITRAG_WEG` und `ANMELDEWAND` werden **vor** der Feldsuche geprüft.
- `post_to_group` wartet auf die Vorschaukarte, nimmt dann die nackte Adresse
  aus dem Text (`trenne_adresse`: nur genau eine, nur am Zeilenende) und
  meldet über `Beitragsausgang`, wenn die Karte das nicht überlebt.
- `_artikel_auswerten` liest Links und Bildtexte **in einem Zug**
  (`Locator.evaluate_all`) und gibt dem Artikeltext eine kurze Frist
  (`ARTIKEL_FRIST_MS`, 3 s). Der Beitragsstrom hängt Artikel beim Scrollen
  wieder aus; `get_attribute` je Link wartete dann 30 s auf ein Element, das
  nicht mehr kam. Ein `Locator` hat **kein** `eval_on_selector_all` — damit
  wurden die Bildtexte vom 23.09.2026 morgens bis abends nie gelesen, und
  das `except` verschwieg es (Test: `test_die_bildtexte_kommen_wirklich_im_artikel_an`).

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
