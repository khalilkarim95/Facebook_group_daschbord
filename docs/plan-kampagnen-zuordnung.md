# Kampagnen-Zuordnung: von 8 auf 310 Gruppen

**Analyse und Umsetzungsplan.** Stand: 17.08.2026.

Ergänzt [`plan-akquise-referral-reward.md`](plan-akquise-referral-reward.md) –
dort geht es um die Kette *nach* dem Klick, hier um die Zuordnung *davor*.

> **Umgesetzt am 17.08.2026 (Schritte 1–5).** Die Kampagne
> `batreeq-syrian-germany` erfasst jetzt alle 310 Gruppen, jede mit einem
> eigenen Tracking-Code; die ursprünglichen acht sind zeichengleich erhalten,
> samt `tracking_url` und `added_at`. 393 Tests grün. Offen ist Schritt 6
> (Dashboard und Kampagnenseite) – siehe [Stand der Umsetzung](#stand-der-umsetzung)
> am Ende.

---

## Kurzfassung

Die acht Zuordnungen sind kein Fehler, sondern das genaue Ergebnis von zwei
Schnitten hintereinander:

```
310 Gruppen heute
 └─ 138 Gruppen zum Zeitpunkt der Zuordnung (16.08. 18:07)
     └─  17 passten den Kampagnenkriterien (Berlin + syrians/arabs)
         └─   8 blieben nach  --top  übrig      ← die acht Codes
```

Dahinter steckt ein Konstruktionsfehler, den man erst bei 310 Gruppen sieht:
**`campaign add-groups` ist ein einmaliger Schnappschuss, keine Regel.** Die
Kampagne speichert zwar Zielgruppen und Städte, aber niemand wendet sie je
erneut an. Und `campaigns.audiences`/`cities` sind gleichzeitig *Beschreibung*
(„wen bewirbt diese Kampagne") und *Filter* („welche Gruppen bekommen einen
Code") – deshalb kollidiert Anforderung 4 (alle 310) mit dem Namen der
Kampagne. Diese beiden Rollen müssen getrennt werden.

Gute Nachricht vorweg: **die Anforderungen 5, 9 und 10 sind bereits strukturell
garantiert** und brauchen keine Zeile Code. Siehe [Abschnitt C](#c-tabelle-campaign_groups).

---

## A) Datenbankstruktur (Ist-Zustand)

`data/groups.sqlite`, `user_version = 6`, 310 Gruppen, 1 Kampagne, 8 Zuordnungen.

Der Ausschnitt, um den es hier geht:

```
groups (310)  ──┐
                ├──  campaign_groups (8)  ──  tracking_events (1)
campaigns (1) ──┘
```

## B) Tabelle `campaigns`

| Spalte | Wert bei `batreeq-syrian-germany` |
| --- | --- |
| `campaign_id` | `batreeq-syrian-germany` |
| `name` | Batreeq Syrian Germany |
| `audiences` | `["syrians", "arabs"]` |
| `cities` | `["berlin"]` |
| `landing_page` | `https://b-tarikak.de/` |
| `status` | `draft` ← obwohl die Links öffentlich sind |

`audiences` und `cities` verweisen auf die Kennungen aus `config/audiences.yaml`
und `config/cities.yaml`. **Sie sind heute zugleich der Zuordnungsfilter.**

## C) Tabelle `campaign_groups`

```sql
PRIMARY KEY (campaign_id, group_id)      -- eine Gruppe je Kampagne genau einmal
tracking_code TEXT NOT NULL UNIQUE       -- über ALLE Kampagnen eindeutig
FOREIGN KEY (group_id)    REFERENCES groups(group_id)       ON DELETE CASCADE
FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE
```

Daraus folgt direkt, ohne dass etwas gebaut werden muss:

| Anforderung | Status |
| --- | --- |
| 5 – genau **ein** Code je Gruppe in der Kampagne | ✅ durch den Primärschlüssel erzwungen |
| 9 – keine doppelten Tracking-Codes | ✅ durch `UNIQUE` erzwungen, kampagnenübergreifend |
| 10 – keine zweite Zuordnung derselben Gruppe | ✅ `add_link` prüft `link_for` und lässt Bestehendes unangetastet |

Die gewünschte Struktur `1 Campaign → viele Gruppen → 1 Code je Gruppe` ist das
Modell, das bereits da ist. Es fehlt nur die **Befüllung**.

Die acht vorhandenen Zeilen, alle mit `added_at = 2026-08-16T18:07:34`:

| # | Tracking-Code | Gruppe | Score |
| ---: | --- | --- | ---: |
| 1 | `FB-ARA-BER-001` | Araber in Berlin Gruppe \| العرب في برلين | 100 |
| 2 | `FB-ARA-BER-002` | Arabische Studierende in Berlin | 100 |
| 3 | `FB-SYR-BER-001` | سوريين في برلين / إستشارات / فرص عمل | 100 |
| 4 | `FB-ARA-BER-003` | سوق العرب في برلين – Arabischer Flohmarkt | 100 |
| 5 | `FB-ARA-BER-004` | ســفارة الجمهوريـة العربيـة السوريـة – برلين | 92 |
| 6 | `FB-SYR-BER-002` | سوريون مقيمين في برلين | 92 |
| 7 | `FB-ARA-BER-005` | عرب برلين Arab Berlin | 92 |
| 8 | `FB-SYR-BER-003` | السوريين في برلين | 84 |

## D) Die Tracking-Code-Logik

`marketing/tracking.py`, Aufbau `FB-SYR-BER-001`:

| Teil | Herkunft |
| --- | --- |
| `FB` | `marketing.tracking.prefix` aus `settings.yaml` |
| `SYR` | `code:` der ersten Zielgruppe aus `audiences.yaml`, sonst erste 3 Buchstaben; ohne Zielgruppe → `GEN` |
| `BER` | `code:` der Stadt aus `cities.yaml`; ohne Stadt → `DE` |
| `001` | laufende Nummer je Kürzelpaar, `number_width: 3` |

`next_tracking_code` zählt von `001` hoch, bis eine freie Nummer gefunden ist.
Der Vorrat `vergeben` ist die Vereinigung aus den Codes dieser Kampagne **und**
allen Codes überhaupt – deshalb kann derselbe Code nie zweimal entstehen.

## E) Wie die acht Gruppen zustande kamen

Die Codes sind lückenlos in genau der Reihenfolge vergeben, in der
`sort_by_rank` die Gruppen liefert – Zeile 1 der Tabelle oben bekam `ARA-BER-001`,
Zeile 3 die erste `SYR-BER`-Nummer, und so weiter. Das ist der Fingerabdruck
eines einzigen Laufs von `campaign add-groups` mit einer Obergrenze von 8.

## F) Warum es genau 8 sind — die vollständige Kette

Vier Ursachen, die sich stapeln:

### 1. `campaign add-groups` ist ein Schnappschuss, keine Regel

Der Befehl liest den Bestand **in dem Moment**, in dem er läuft, schreibt die
Zuordnungen und ist fertig. Es gibt keinen Mechanismus, der ihn erneut auslöst.
Die 172 Gruppen, die am 17.08. um 10:44 durch den nächsten Suchlauf dazukamen,
hat die Kampagne nie gesehen.

### 2. Der Bestand war damals kleiner

Nachweisbar an den Sicherungskopien:

| Datei | Zeit | Gruppen | passend (Berlin + ara/syr) |
| --- | --- | ---: | ---: |
| `bak-vor-beispieldaten` | 16.08. 18:54 | 138 | 17 |
| `bak-vor-marketing` | 16.08. 20:07 | 132 | 17 |
| `groups.sqlite` heute | 17.08. 16:37 | **310** | **18** |

### 3. Die Kampagnenkriterien schneiden auf 18 von 310

```python
staedte     = campaign.cities     # ["berlin"]
zielgruppen = campaign.audiences  # ["syrians", "arabs"]
```

Von den 310 Gruppen sind nur **18** gleichzeitig Berlin *und* arabisch/syrisch.
Selbst ohne jede Obergrenze käme die Kampagne heute also auf 18 Gruppen, nicht
auf 310.

Zum Vergleich, wie sich die 310 verteilen:

| Merkmal | Verteilung |
| --- | --- |
| Stadt | 148 ohne Stadt · Berlin 18 · Stuttgart 15 · Dortmund 15 · Hamburg 14 · Frankfurt 14 · … |
| Zielgruppe | 116 ohne · syrians 97 · arabs 74 · beide 23 |
| Kategorie | 224 ohne · community 19 · jobs 16 · essen 14 · … |
| Score | 195 bewertet · 115 ohne Score |

### 4. `--top` schnitt die 17 auf 8

```python
if top > 0:
    passend = passend[:top]
```

Die acht höchstbewerteten blieben übrig. Das war der Testlauf.

### Und heute gibt es keinen Weg, das zu ändern

`campaign set` kennt Optionen für Name, Beschreibung, Sprache, Landingpage,
Vorlage, Start und Ende – **aber keine für `audiences` oder `cities`**. Und
`campaign add-groups --stadt …` kann die Auswahl nur weiter *verengen*, niemals
erweitern:

```python
staedte = {c.lower() for c in (city or campaign.cities)}
```

Anforderung 4 („alle 310") ist mit dem heutigen Stand also nicht per Befehl
erreichbar, sondern nur durch direktes SQL. Das ist der harte Blocker.

---

## Der eigentliche Konstruktionsfehler

`campaigns.audiences` und `campaigns.cities` tragen zwei Bedeutungen zugleich:

- **Beschreibung:** Wen bewirbt diese Kampagne? → „Syrer und Araber in Berlin"
- **Filter:** Welche Gruppen bekommen einen Tracking-Code? → nur Berlin

Solange beides dasselbe ist, fällt es nicht auf. Anforderung 4 zwingt sie
auseinander: Die Kampagne soll weiter „Batreeq Syrian Germany" heißen und
syrische Zielgruppen bewerben, aber **alle** 310 Gruppen abdecken. Das geht nur,
wenn die Auswahlregel ein eigenes Feld wird.

---

## Umsetzungsplan

### Schritt 1 — Die Auswahlregel wird ein gespeichertes Feld

Neue Spalten auf `campaigns`, rein additiv:

| Spalte | Typ | Bedeutung |
| --- | --- | --- |
| `target_audiences` | TEXT `'[]'` | leer = **keine Einschränkung** |
| `target_cities` | TEXT `'[]'` | leer = keine Einschränkung |
| `target_categories` | TEXT `'[]'` | leer = keine Einschränkung |
| `target_statuses` | TEXT `'[]'` | leer = keine Einschränkung |
| `target_min_score` | REAL NULL | NULL = kein Mindestwert |
| `target_include_unscored` | INTEGER `0` | Gruppen ohne Score mitnehmen? |
| `auto_assign` | INTEGER `0` | neue Gruppen automatisch übernehmen? |

**Leer heißt „alles"** – das ist die Kernentscheidung. Eine Kampagne ohne
Einschränkung erfasst den gesamten Bestand, und Anforderung 4 wird zu einem
Befehl statt zu einem Sonderfall.

Die Migration füllt `target_audiences` und `target_cities` aus den heutigen
`audiences`/`cities`. **Für die bestehende Kampagne ändert sich dadurch nichts** –
sie bleibt bei ihren 18 Treffern, bis jemand die Regel ausdrücklich weitet.
`audiences`/`cities` bleiben bestehen und sind ab dann reine Beschreibung.

Neuer Befehl:

```powershell
fbgroups campaign target batreeq-syrian-germany --alle          # jede Einschränkung löschen
fbgroups campaign target batreeq-syrian-germany --stadt berlin --stadt hamburg
fbgroups campaign target batreeq-syrian-germany --min-score 40 --auch-unbewertete
fbgroups campaign target batreeq-syrian-germany --auto-assign an
```

### Schritt 2 — `campaign sync` wendet die Regel an

```powershell
fbgroups campaign sync batreeq-syrian-germany --dry-run   # zeigt exakt, was passieren würde
fbgroups campaign sync batreeq-syrian-germany
```

Drei Eigenschaften, die nicht verhandelbar sind:

- **Nur hinzufügend.** Eine bestehende Zuordnung wird nie angefasst, ein Code nie
  neu berechnet, eine Zuordnung nie entfernt. Passt eine Gruppe später nicht mehr
  zur Regel (etwa weil ein `rescore` sie unter `min_score` drückt), bleibt ihr
  Link bestehen und wird nur **gemeldet** – der Code steht möglicherweise schon
  in einem veröffentlichten Beitrag.
- **Wiederholbar.** Zweimal hintereinander aufgerufen ändert der zweite Lauf
  nichts. Genau das macht Anforderung 11 möglich.
- **`--dry-run` zeigt dieselbe Rechnung wie der echte Lauf**, aus derselben
  Funktion – kein zweiter Zählweg, wie schon bei `search.build_plan`.

Bericht am Ende: `neu zugeordnet` · `bereits vorhanden` · `passt nicht mehr zur
Regel` · `übersprungen`.

### Schritt 3 — Codevergabe wird deterministisch (Anforderung 8)

Heute ist sie es nicht. `campaign add-groups` verarbeitet die Kandidaten in
`sort_by_rank`-Reihenfolge, also nach Score. Ändert sich ein Score – und
`fbgroups rescore` ändert Scores –, bekämen dieselben Gruppen beim nächsten Lauf
andere Nummern. Vergebene Codes sind zwar eingefroren und damit sicher, aber
„gleiche Eingabe → gleiches Ergebnis" gilt nicht.

**Änderung:** Für die Codevergabe wird nach einem Schlüssel sortiert, der sich
nie ändert – `first_seen_at`, bei Gleichstand `group_id`. Die Anzeige sortiert
weiter nach Score. Damit ist ein `--dry-run` reproduzierbar und die Vergabe von
der Bewertung entkoppelt.

### Schritt 4 — Was dabei herauskommt

Durchgerechnet auf dem echten Bestand, alle 310 Gruppen ohne Einschränkung:

| | |
| --- | ---: |
| Gruppen gesamt | 310 |
| bereits zugeordnet (unverändert) | 8 |
| **neue Tracking-Codes** | **302** |
| verschiedene Kürzel-Präfixe | 33 |
| größter Block: `FB-GEN-DE` | 115 |
| `number_width: 3` ausreichend? | ja, mit Abstand |

Die Berliner Blöcke wachsen genau so, wie es das Beispiel in der Anforderung
zeigt – die acht bestehenden Codes bleiben exakt stehen:

| Präfix | vergeben | kommt hinzu | gesamt |
| --- | --- | --- | ---: |
| `FB-ARA-BER` | 001–005 | **006–010** | 10 |
| `FB-SYR-BER` | 001–003 | **004–008** | 8 |
| `FB-SYR-DE` | – | 001–019 | 19 |
| `FB-ARA-HAM` | – | 001–013 | 13 |
| `FB-GEN-DE` | – | 001–115 | 115 |
| … 28 weitere Präfixe | | | 51 |

### Schritt 5 — Anforderung 11: später importierte Gruppen

Zwei Wege, beide vorgesehen:

- **Von Hand:** `campaign sync` nach jedem Import oder Suchlauf.
- **Automatisch:** Kampagnen mit `auto_assign = 1` werden am Ende von
  `import-seeds` und `search` synchronisiert, mit einer Zeile im Bericht
  („27 neue Gruppen, 27 Tracking-Codes vergeben").

Vorgabe ist **aus**. Das entspricht der Linie des Projekts, dass nichts
beiläufig passiert – aber anders als bei der Suche kostet dieser Schritt kein
Guthaben und ruft nichts von außen ab, deshalb ist das Einschalten unbedenklich.

### Schritt 6 — Dashboard und Kampagnenseite (Anforderungen 12–16)

Das ist dieselbe Seite, die im anderen Plan als Anforderung 10 steht – sie wird
einmal gebaut, nicht zweimal. Die Tabelle nach Anforderung 13 mit Suchfeld und
den Filtern nach Stadt, Zielgruppe, Kategorie, Status und Score; Filterung im
Browser über die eingebetteten Daten, wie bei der bestehenden Übersicht. 310
Zeilen laden in einem Zug.

**Wichtig für die Erwartung:** Die Spalten *Registrations*, *Referrals* und
*Rewards* zeigen so lange `0`, bis die Schritte 1–3 aus
[`plan-akquise-referral-reward.md`](plan-akquise-referral-reward.md) stehen –
ohne Benutzer- und Herkunftsebene gibt es nichts zu zählen. *Clicks* funktioniert
sofort, die Spalte hängt nur an `tracking_events`.

### Migrationsschritte

```
6 → 7   campaigns: target_* Spalten, auto_assign; Übernahme aus audiences/cities
```

Rein additiv. Kein Schreibzugriff auf `groups`, kein `UPDATE` auf bestehende
`campaign_groups`-Zeilen. Vorher eine Sicherungskopie
`groups.sqlite.bak-vor-zuordnung`, wie bei den vier bisherigen Eingriffen.

Kollidiert **nicht** mit den Schritten 6 → 7 → 8 aus dem anderen Plan; welcher
zuerst gebaut wird, entscheidet die Reihenfolge der Versionsnummern. Wird dieser
zuerst umgesetzt, verschieben sich die anderen auf 7 → 8 → 9.

### Betroffene Dateien

| Datei | Änderung |
| --- | --- |
| `marketing/models.py` | `Campaign` um die `target_*`-Felder und `auto_assign` |
| `marketing/store.py` | Schema, Lesen/Schreiben der neuen Felder |
| `marketing/selection.py` **(neu)** | die Auswahlregel als eine Funktion – eine Wahrheit für `sync`, `--dry-run` und die Anzeige |
| `marketing/cli.py` | `campaign target`, `campaign sync`; `add-groups` ruft künftig `selection` auf |
| `storage/sqlite_store.py` | Migrationsschritt, `SCHEMA_VERSION` |
| `cli.py` | `import-seeds`/`search` synchronisieren bei `auto_assign` |
| `marketing/dashboard.py`, `detail_pages.py` | Kampagnenseite mit Tabelle, Suche, Filtern |
| `tests/test_kampagnen_zuordnung.py` **(neu)** | siehe unten |

### Tests, die den Bestand absichern

- Die acht Codes bleiben nach `sync` **zeichengleich** erhalten, samt
  `tracking_url` und `added_at`.
- `sync` zweimal hintereinander erzeugt beim zweiten Mal null neue Zuordnungen.
- Kein Code kommt zweimal vor – kampagnenübergreifend geprüft.
- Jede Gruppe hat je Kampagne höchstens einen Code.
- Gleiche Eingabe → gleiche Codes, auch nach einem `rescore`.
- `groups` hat nach der Migration unverändert 310 Zeilen mit unveränderten Scores.

---

## Befunde, die vor Schritt 4 eine Entscheidung brauchen

### 1. 115 Gruppen sind nur eine URL

| | |
| --- | ---: |
| Gruppen mit `status = insufficient_data` | 115 |
| davon ohne Namen | 114 |
| davon ohne Stadt **und** ohne Zielgruppe | 115 |
| davon ohne Score | 115 |

Diese 115 werden zum Block `FB-GEN-DE-001` bis `-115` – **37 % aller Codes,
und keiner davon trägt eine Information.** Für eine Gruppe, von der nicht
einmal der Name bekannt ist, lässt sich auch kein passender Beitrag schreiben.

Die Regel kann sie über `target_include_unscored` ein- oder ausschließen. Meine
Empfehlung: erst einmal **aus**, dann kommt die Kampagne auf 195 Gruppen mit
aussagekräftigen Codes. Anforderung 4 sagt allerdings ausdrücklich „alle 310" –
das ist deine Entscheidung, und beides ist ein Einzeiler in der Regel.

### 2. `status = duplicate` bedeutet nicht „Dublette"

```python
if group.times_seen > 1:
    return RecordStatus.DUPLICATE
```

Es heißt: **von mehr als einer Suchanfrage gefunden.** Echte Dubletten werden
über `group_id` automatisch zusammengeführt und tauchen gar nicht erst als
eigener Datensatz auf.

Alle 144 dieser Gruppen haben einen Namen und einen Score. Es sind die am besten
auffindbaren Gruppen im Bestand:

| Status | Anzahl | mit Name | mit Score |
| --- | ---: | ---: | ---: |
| `duplicate` | 144 | 144 | 144 |
| `insufficient_data` | 115 | 1 | 0 |
| `validated` | 51 | 51 | 51 |

Für den Statusfilter aus Anforderung 15 ist das eine Falle: Wer „duplicate"
wegfiltert, wirft die besten 144 Gruppen weg und behält 51. Auf der Oberfläche
sollte dort **„mehrfach gefunden"** stehen, nicht „Dublette".

### 3. „Tracking Links: 310" ist eine Aussage über Vorbereitung

310 Codes heißen nicht 310 Beiträge. Jeder Beitrag wird weiterhin von Hand
geschrieben und von Hand gepostet, und vorher muss man in der Gruppe aufgenommen
sein – aktuell steht das für **4 von 310** Gruppen in `group_marketing`. Die
Kachel sollte deshalb ehrlich beschriftet sein, etwa „Tracking-Links vorbereitet:
310 · davon in Gruppen mit Mitgliedschaft: 1". Sonst liest sich das Dashboard
nach Reichweite, die es noch nicht gibt.

### 4. Die Kampagne steht auf `draft`

Ihre acht Links sind öffentlich, ihr Status ist `draft`. Vor dem Bauen der
Kampagnenseite entscheiden: auf `active` setzen oder die Seite ohne Statusfilter
bauen.

### 5. Anforderungen 17–25 sind durch diesen Entwurf gedeckt

Keine Änderung an `groups`, kein Schreibzugriff auf bestehende Codes, `/r/{code}`
und `APP_BASE_URL` unverändert, SQLite bleibt, Excel/CSV bleiben reine Exporte
(`export/csv_export.py`, `excel_export.py` lesen nur), kein Facebook-Zugriff,
kein automatisches Posten, keine Mitglieder- oder Profildaten.

---

## Stand der Umsetzung

Umgesetzt am 17.08.2026. Sicherung vorher:
`data/groups.sqlite.bak-vor-zuordnung`.

| Schritt | Stand |
| --- | --- |
| 1 – Auswahlregel als gespeichertes Feld (`campaign target`) | ✅ |
| 2 – `campaign sync`, wiederholbar und nur hinzufügend | ✅ |
| 3 – deterministische Codevergabe über `first_seen_at` | ✅ |
| 4 – Kampagne auf alle 310 Gruppen angewendet | ✅ |
| 5 – `auto_assign` in `import-seeds` und `search` | ✅ |
| 6 – Dashboard-Kacheln und Kampagnenseite | offen |

### Ergebnis in Zahlen

| | |
| --- | ---: |
| Gruppen | 310 (unverändert) |
| Zuordnungen | 310 |
| verschiedene Tracking-Codes | 310 |
| Gruppen mit zwei Codes | 0 |
| Links ohne URL | 0 |
| `user_version` | 7 |

Die acht ursprünglichen Codes sind zeichengleich erhalten – gleiche
`tracking_url`, gleiches `added_at` (`2026-08-16T18:07:34`). Die Berliner
Blöcke sind lückenlos: `FB-ARA-BER-001…010`, `FB-SYR-BER-001…008`.

Weiterleitung geprüft (auf einer Kopie, damit kein Testklick in den Bestand
gerät): alter Code, zwei neue Codes und ein unbekannter Code verhalten sich
unverändert – 302 mit `?ref=`, bzw. 404.

### Dabei zusätzlich behoben

- **`MarketingStore` migriert jetzt selbst.** Er las die `user_version` nicht
  und holte keinen Schritt nach – `GET /r/{code}` und `POST /events` öffnen aber
  **nur** diesen Speicher. Auf einem Server mit älterer Datei hätte die neue
  Spalte gefehlt und die Weiterleitung wäre gestorben.
- **Eine von `MarketingStore` neu angelegte Datei bekommt ihre
  `user_version`.** Vorher blieb sie auf 0, und der nächste `SqliteStore` hielt
  sie für eine Datei aus grauer Vorzeit und verweigerte den Dienst.
- **Massen-Insert in einer Transaktion.** 302 einzelne Schreibvorgänge wären
  302 Bestätigungen auf die Platte gewesen; ein Abbruch mittendrin hätte eine
  halb zugeordnete Kampagne hinterlassen, deren Codes bereits vergeben sind.
- **Vier vorbestehend rote Tests repariert.** Sie behaupteten
  `http://localhost:3000` als Basis-URL, seit dem Wechsel auf
  `https://b-tarikak.de/` in `settings.yaml` also falsch. Sie prüfen jetzt das
  *Verfahren* (Umgebung schlägt Datei) statt eines Betriebswerts.

### Neue Tests

`tests/test_kampagnen_zuordnung.py`, 27 Stück. Die wichtigsten:

- Die echte Datenbank wird kopiert, synchronisiert und danach geprüft: 310
  Gruppen, unveränderte Scores, die acht Codes zeichengleich.
- 1000 Gruppen bekommen 1000 verschiedene Codes bis `FB-SYR-BER-1000`, der
  zweite Lauf legt nichts an.
- Codes bleiben gleich, wenn man die Score-Reihenfolge auf den Kopf stellt.
- Eine Migration vergrößert eine bestehende Kampagne nicht.
- `--dry-run` und Ernstfall erzeugen dieselben Codes.
