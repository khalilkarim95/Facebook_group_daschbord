# Server-Befehle: Verbindung, Ausrollen, Automatik

Nachschlagewerk, keine Skriptdatei zum Durchlaufen: Die Bloecke laufen in
verschiedenen Kontexten (lokal PowerShell, lokal Git Bash, auf dem Server nach
SSH-Login) - jeweils vermerkt. Quelle: CLAUDE.md, docs/plan-go-subdomain.md,
ausrollen.sh. Siehe auch [[ausfuehrung-nur-auf-dem-server]]: Alles, was den
Bestand aendert, laeuft nur auf dem Server, nie auf dem Arbeitsrechner.

## 1. Verbindung zum Server (SSH)

```bash
# Normale Anmeldung als karim - fuer CLI-Befehle (kein root, kein Passwort)
ssh -i ~/.ssh/b-tarikak_vps_new karim@159.195.216.246

# SSH-Tunnel fuer die Uebersicht im Browser auf 127.0.0.1:8090
ssh -i ~/.ssh/b-tarikak_vps_new -L 8090:127.0.0.1:8090 -o ServerAliveInterval=30 karim@159.195.216.246
```
Nach dem Tunnelaufbau: `http://127.0.0.1:8090/` im lokalen Browser oeffnen.
Der Tunnel bleibt aktiv, solange das Fenster offen ist.

## 2. Code ausrollen (Deploy)

Lokal, **Git Bash, nicht PowerShell** (`tar | ssh` braucht einen Binaerstrom):

```bash
bash ./ausrollen.sh --plan     # nur anzeigen, was liefe - nichts uebertragen
bash ./ausrollen.sh            # uebertragen, einsetzen, Dienst neu starten
bash ./ausrollen.sh --pip      # zusaetzlich die Abhaengigkeiten erneuern
bash ./ausrollen.sh --test     # vorher die Testreihe laufen lassen
```
Uebertragen werden nur `src`, `config`, `pyproject.toml` - nie `.env` oder `data/`.

## 3. Dienst auf dem Server steuern (systemd)

Auf dem Server, nach SSH-Login:

```bash
sudo systemctl restart fbgroups
sudo systemctl is-active fbgroups
sudo journalctl -u fbgroups -n 15 --no-pager
curl -s -o /dev/null -w 'healthz: %{http_code}\n' http://127.0.0.1:8090/healthz
```

## 4. Befehle, die den Bestand aendern - nur auf dem Server

Als `karim` (eng gefasste sudoers-Regel, nur Zielbenutzer `fbgroups`, nur dieses Programm):

```bash
fbgroups search --dry-run --provider serper --show-all
fbgroups search --provider serper --limit 5
fbgroups rescore --dry-run
fbgroups rescore
fbgroups report --top 20
fbgroups campaign links batreeq-syrian-germany
fbgroups marketing overview
```
Oder ausdruecklich als Dienstbenutzer:
```bash
sudo -u fbgroups /opt/fbgroups/venv/bin/python -m fbgroups.cli <befehl>
sudo -u fbgroups /opt/fbgroups/venv/bin/python -m fbgroups.cli config-check
```

## 5. Qualifikation: die Frage vor dem Text

`Entdecken -> Regeln lesen -> Beitreten -> Warten -> Bewerten -> Arbeiten` -
und ausdruecklich nicht "entdecken, dann ueberall posten".

**Regeln lesen: oertlich, angemeldeter Browser** (ueber `httpx` kommt eine
Anmeldewand, und dort steht keine Gruppenregel). Startet nie beilaeufig -
ohne `--limit` oder `--alle` Exit-Code 2, wie `fbgroups enrich`
(`$py` wie in Abschnitt 6 setzen):

```bash
"$py" -m fbgroups.cli marketing regeln --limit 10   # hoechstens 10 Gruppenseiten
"$py" -m fbgroups.cli marketing regeln --alle       # alle noch ungelesenen
"$py" -m fbgroups.cli marketing regeln --limit 10 --erneut   # auch schon gelesene
```

`marketing regeln` kennt **kein `--server`** und schreibt in die Datei des
Rechners, auf dem es laeuft - anders als `campaign automatik --server`, das
den Befund auf dem Server bucht. Solange es den Weg nicht gibt, bleibt der
Regelbefund oertlich; auf dem Server steht kein Browser zur Verfuegung.

**Einstufung ansehen: auf dem Server** (liest nur den Bestand, ruft nichts ab):

```bash
fbgroups campaign qualifikation batreeq-syrian-germany
fbgroups campaign qualifikation batreeq-syrian-germany --stufe ohne_links
```

Stufen fuer `--stufe`: `unbekannt`, `beitritt_noetig`, `beitritt_angefragt`,
`bewertung`, `geeignet`, `ohne_links`, `ohne_kommentare`, `ohne_beitraege`,
`ungeeignet`. Gerechnet wird bei jedem Aufruf neu aus Mitgliedschaft,
gelesenen Regeln und Versuchsprotokoll - keine Spalte haelt das Urteil.

Die drei mittleren Urteile sind **Einschraenkungen, kein Ausschluss**:
`ohne_links` nimmt denselben Kommentar ohne Link, `ohne_kommentare` den
Beitrag, `ohne_beitraege` die Kommentare.

`qualifikation.pflicht` in `config/settings.yaml` steht auf `false` -
beobachtet und angezeigt wird, gesperrt wird auf Ansage. Der Schalter wirkt
dort, wo der Bestand liegt: erst nach `bash ./ausrollen.sh` auf dem Server.

## 6. Die ganze Kette: von der Anmeldung bis zum Kommentar

Der Weg am Stueck. Zwei Orte, und die Trennung ist der Kern: Der **Bestand**
lebt auf dem Server, der **Browser** auf dem Arbeitsrechner. Jeder Befehl
steht deshalb unter seiner Ueberschrift; `--server` bedeutet immer "der
Server haelt den Stand, dieser Rechner nur den Browser".

Voraussetzung: SSH-Tunnel aus Abschnitt 1 laeuft, Code ist ausgerollt
(Abschnitt 2), Dienst antwortet (Abschnitt 3).

### 0. Einmal je Rechner - die Facebook-Sitzung

**Oertlich, Git Bash oder PowerShell.** Sichtbares Fenster, Anmeldung von
Hand (auch 2FA), dann Fenster schliessen. Die Sitzung landet in
`data/browser_state` und haelt, bis Facebook sie verwirft.

```bash
py="./.venv/Scripts/python.exe"
export PYTHONIOENCODING=utf-8

"$py" -m fbgroups.cli auth login
```

Kein stiller Login, kein Headless - das ist eine harte Projektgrenze und
keine Einstellung.

### 1. Gruppen in den Bestand - auf dem Server

Ueberspringen, wenn der Bestand schon steht (`fbgroups report --top 5`).

```bash
fbgroups import-seeds data/seeds/pruefliste.csv   # von Hand gesammelte URLs
fbgroups search --dry-run --provider serper --show-all   # Plan + Verbrauch
fbgroups search --provider serper --limit 20             # hoechstens 20 NEUE Anfragen
fbgroups rescore                                          # Bestand bewerten
```

### 2. Kampagne anlegen und mit Gruppen fuellen - auf dem Server

```bash
fbgroups campaign new "Batreeq Syrian Germany" --zielgruppe syrians --sprache ar
fbgroups campaign target batreeq-syrian-germany            # geltende Regel ansehen
fbgroups campaign target batreeq-syrian-germany --stadt alle --min-score 40
fbgroups campaign sync batreeq-syrian-germany --dry-run    # wie viele Codes entstuenden?
fbgroups campaign sync batreeq-syrian-germany              # Codes vergeben
fbgroups campaign status batreeq-syrian-germany active     # ohne 'active' laeuft nichts
```

`sync` vergibt Tracking-Codes, und **ein vergebener Code wird nie
zurueckgenommen** - er steht spaeter in veroeffentlichten Beitraegen. Deshalb
erst `--dry-run` lesen.

### 3. Texte erzeugen - auf dem Server

```bash
fbgroups campaign text batreeq-syrian-germany --aus-vorlage --ja   # Beitrag UND Kommentar
fbgroups campaign message batreeq-syrian-germany <group_id>        # einen ansehen
```

Ohne Text kommt eine Zuordnung nicht durch die Freigabe. Der Lauf zieht
fehlende Texte selbst nach; dieser Befehl macht es fuer alle auf einmal und
zeigt dabei, wo eine Vorlage fehlt.

### 4. Gruppenregeln lesen - oertlich, angemeldeter Browser

Das ist Abschnitt 5. Es entscheidet, was der Lauf spaeter **darf**: keine
Links, wo Links verboten sind. Ohne diesen Schritt laeuft alles weiter, aber
jede Gruppe steht auf "in Bewertung" statt auf "geeignet".

```bash
"$py" -m fbgroups.cli marketing regeln --limit 20
```

Achtung: schreibt in die Datei **dieses** Rechners (kein `--server`) - siehe
Abschnitt 5.

### 4b. Bestehende Kampagnen: die Liste des Laufs auffrischen

Ein offener Lauf **behaelt seine eingefrorene Kampagnenliste**. Das schuetzt
einen laufenden Vorgang - und wird zum Hindernis, sobald Kampagnen dazukommen:
Sie kaemen nie dran, und von aussen sieht das aus, als taete die Automatik
nichts.

```bash
"$py" -m fbgroups.cli campaign automatik --status   # welche Kampagnen sind drin?
```

Nennt die Ausgabe aktive Kampagnen, die **nicht** in der Liste stehen, dann
einmalig:

```bash
"$py" -m fbgroups.cli campaign automatik --server http://127.0.0.1:8090 --neu --limit 5
```

### Der Waechter - einmal starten, dann laeuft er

Statt alle paar Stunden nachzusehen und den Befehl erneut einzutippen:

```bash
"$py" -m fbgroups.cli campaign watchdog --server http://127.0.0.1:8090
```

Alle fuenf Minuten ein Blick. Laeuft ein Lauf, geschieht **nichts**. Ist
keiner da - abgestuerzt, beendet, nie gestartet -, startet er genau den
Befehl darueber. Antwortet der Tunnel nicht, wird gewartet statt gestartet.

Einmal nachsehen, ohne zu ueberwachen:

```bash
"$py" -m fbgroups.cli campaign watchdog --einmal
```

Er enthaelt keine Kampagnenlogik: kein `--neu`, also wird der offene Lauf
fortgesetzt und keine Kampagne von vorn begonnen. Beenden mit Strg+C - ein
laufender `campaign automatik` laeuft dabei weiter.

Einstellungen in `config/settings.yaml` unter `watchdog:`.


`--neu` schliesst den offenen Lauf ab und friert eine frische Liste aus
**allen aktiven** Kampagnen ein (Reihenfolge: `created_at`). Verloren geht
dabei nur die Liste - der Fortschritt steht in den Fassungen und wird
gelesen: Veroeffentlichte Kommentare bleiben veroeffentlicht, nichts faengt
von vorn an. Zurueckgesetzt wird allein, was in **diesem** Lauf uebersprungen
wurde, und das ist gewollt.

Nur eine Kampagne zuerst sehen? `--neu --kampagne essen-1109`.

### 4c. Nach einem Abbruch sauber anfangen (geschlossener Browser)

Das Bild: Kampagnen stehen auf **FERTIG mit null Kommentaren**, und ueber den
Texten steht `BrowserContext.new_page: Target page, context or browser has
been closed`. Passiert am 11.09.2026 (45 Gruppen) und am 12.09.2026
(78 Gruppen in vier Kampagnen).

Es ist **kein** Urteil ueber die Gruppen - es war ein Fenster, das zuging.
Zurueckgeholt wird es mit **einem** Befehl:

```bash
# 1. Fehlschlaege zuruecknehmen - ALLE Kampagnen, Beitraege UND Kommentare
fbgroups campaign retry --alle --kommentare
```

**Ohne Kennung gilt es fuer alle Kampagnen** - der Fall, fuer den man es
braucht, ist nie einer: Ein geschlossenes Fenster laesst nicht eine Kampagne
scheitern, sondern die, an der gerade gearbeitet wurde, und jede danach. Eine
einzelne geht weiterhin:

```bash
fbgroups campaign retry essen-1109 --alle --kommentare
```

`--alle` uebergeht die Grenze von drei Versuchen, `--kommentare` geht eine
Ebene tiefer auf die einzelnen Fassungen und nimmt die **Erschoepfung** aus
diesen Versuchen zurueck. **Veroeffentlichte Fassungen bleiben unberuehrt** -
genau darin liegt der Unterschied zu `campaign reset`, das die ganze Kampagne
auf Anfang stellt, auch das bereits Hinausgegangene. Bei koln und Essen stehen
Kommentare draussen; `reset` wuerde sie ein zweites Mal posten.

```bash
# 2. Die abgeschlossenen Kampagnen wieder auf aktiv
#    (ein fertiger Lauf setzt sie auf 'completed' - sonst kommen sie nicht mit)
fbgroups campaign list --status completed     # welche sind es?
fbgroups campaign status hamburg-1109 active
fbgroups campaign status stuttgart-1109 active
fbgroups campaign status bremen-1109 active
fbgroups campaign status berlin-1109 active

# 3. Nachsehen, was jetzt dasteht
fbgroups campaign fortschritt hamburg-1109
```

Der Status ist die eine Stelle, die **je Kampagne** entschieden werden muss:
"wieder aufnehmen" ist ein Urteil ueber diese Kampagne, kein Aufraeumen nach
einem Fehler. Die Kennungen nennt `campaign list --status completed`.

```bash
# 4. Oertlich: Browser neu anmelden, dann mit frischer Liste starten
"$py" -m fbgroups.cli auth login
"$py" -m fbgroups.cli campaign automatik --status
"$py" -m fbgroups.cli campaign automatik --server http://127.0.0.1:8090 --neu --limit 5
```

**Seit dem 12.09.2026 sollte dieser Abschnitt nicht mehr gebraucht werden:**

* Ein technischer Fehlschlag zaehlt nicht mehr gegen eine Fassung
  (`gescheiterte_kommentarfassungen` fragt `qualifikation.klassifiziere`) -
  ein geschlossener Browser erschoepft damit keine Gruppe mehr.
* Fuenf technische Fehlschlaege **hintereinander** beenden den Lauf mit
  Ansage, statt sich durch 121 Gruppen zu arbeiten und ueberall dasselbe zu
  vermerken.

Was die **Gruppe** sagt ("abgelehnt", "Spam", "Link in Kommentar"), zaehlt
unveraendert weiter - sonst waere die Grenze abgeschafft statt praezisiert.

### 5. Der Lauf - oertlich, Browser; gebucht auf dem Server

**Ein Befehl fuer Schritt 2 bis 5 des Ablaufs**: Beitrittsanfragen →
Neubewertung → beste Gruppen zuerst → Beitrag, dann zehn Kommentare → naechste
Kampagne.

```bash
curl -s http://127.0.0.1:8090/automatik | "$py" -m json.tool   # Stand vorher ansehen

"$py" -m fbgroups.cli campaign automatik --server http://127.0.0.1:8090 --limit 5
"$py" -m fbgroups.cli campaign automatik --server http://127.0.0.1:8090
```

Beim ersten Mal mit `--limit 5` anfangen und zusehen. `--kampagne <kennung>`
schraenkt einen **neuen** Lauf ein; ein offener Lauf behaelt seine
eingefrorene Liste.

### 6. Nachsehen - auf dem Server

```bash
fbgroups campaign qualifikation batreeq-syrian-germany   # wer darf was?
fbgroups campaign fortschritt batreeq-syrian-germany     # wie weit?
fbgroups marketing analytics --top 10                    # was hat es gebracht?
```

### Die Kette am Stueck

Zum Nachschlagen, in der Reihenfolge - **nicht** zum Einfuegen in eine Zeile:
Die Bloecke laufen an verschiedenen Orten, und zwischen Schritt 2 und 3 will
man lesen, was `--dry-run` sagt.

**Auf dem Server** (nach `ssh -i ~/.ssh/b-tarikak_vps_new karim@159.195.216.246`):

```bash
fbgroups rescore
fbgroups campaign new "Batreeq Syrian Germany" --zielgruppe syrians --sprache ar
fbgroups campaign target batreeq-syrian-germany --stadt alle --min-score 40
fbgroups campaign sync batreeq-syrian-germany --dry-run
fbgroups campaign sync batreeq-syrian-germany
fbgroups campaign status batreeq-syrian-germany active
fbgroups campaign text batreeq-syrian-germany --aus-vorlage --ja
```

**Oertlich** (Git Bash, Tunnel offen):

```bash
py="./.venv/Scripts/python.exe"; export PYTHONIOENCODING=utf-8
"$py" -m fbgroups.cli auth login
"$py" -m fbgroups.cli marketing regeln --limit 20
"$py" -m fbgroups.cli campaign automatik --server http://127.0.0.1:8090 --limit 5
"$py" -m fbgroups.cli campaign automatik --server http://127.0.0.1:8090
```

### Was der eine Lauf-Befehl selbst erledigt

| Schritt | Frueher eigener Befehl | Heute |
|---------|------------------------|-------|
| Beitrittsanfragen | `campaign beitritt --server ...` | im Lauf, je Kampagne zuerst |
| Neubewertung | `fbgroups rescore` | im Lauf, einmal je Kampagne |
| Texte nachziehen | `campaign text ... --ja` | im Lauf, wo einer fehlt |
| Reihenfolge | Score | erst was moeglich ist, dann Score |
| Beitrag + Kommentare | `campaign auto <k> <gruppe>` | im Lauf, Gruppe fuer Gruppe |

Die Einzelbefehle bleiben - fuer den Fall, dass man **nur** das eine will
(etwa fuenfzig Beitrittsanfragen ohne einen einzigen Beitrag).

### Wenn es haengt

* **Der Lauf wartet Minuten** - das ist der Takt der Beitrittsanfragen
  (`beitritt.mindestabstand_minuten`, 3 Min). Er zieht keine Beitraege vor.
  Bei 50 Anfragen am Tag sind das gut zweieinhalb Stunden vor dem ersten
  Beitrag einer frischen Kampagne. Wer das nicht will:
  `beitritt.anfragen_pro_tag` niedriger setzen, `0` nimmt die Anfragen ganz
  aus dem Lauf (`config/settings.yaml`, dann `bash ./ausrollen.sh`).
* **"Keine aktive Kampagne"** - `campaign status <kennung> active` fehlt.
* **"Diese Kampagne hat keine Gruppen zugeordnet"** - `campaign sync` fehlt.
* **Eine Gruppe wurde uebersprungen** - der Grund steht in der
  Abschlussmeldung. Ein Fehler kostet die Gruppe **diesen** Lauf, nicht mehr;
  der naechste Lauf nimmt sie wieder mit.
* **Der Dienst haelt den Aufruf fuer nicht-oertlich (404)** - der SSH-Tunnel
  aus Abschnitt 1 ist zu.
* **"Fehlgeschlagen: BrowserContext.new_page ... has been closed"** - das
  Browserfenster ist zu. Der Lauf hoert nach fuenf solchen Fehlschlaegen von
  selbst auf; anmelden mit `auth login`, dann erneut starten. Steht schon
  eine Kampagne faelschlich auf "fertig mit null Kommentaren": Abschnitt 4c.

## 7. Pruefkette nach der Ueberarbeitung (12.09.2026)

Der Runner entscheidet jetzt **je Beitrag**, ob eine Antwort etwas beitraegt -
und haelt Grenzen je Aktion ein. Beides laesst sich pruefen, **bevor** ein
Kommentar irgendwo steht. Die Kette geht von "nichts beruehrt" zu "eine
Kampagne, fuenf Schritte".

Jeder Abschnitt hat eine Frage. Wer eine davon nicht beantworten kann, geht
nicht zum naechsten.

### Stufe 0 - Ohne Netz, ohne Konto, ohne Server

**Frage: Ist der Bau in Ordnung?**

```bash
py="./.venv/Scripts/python.exe"; export PYTHONIOENCODING=utf-8

"$py" -m pytest -q              # erwartet: 1045 passed
"$py" -m ruff check src tests   # erwartet: All checks passed
"$py" -m fbgroups.cli config-check
```

`config-check` zeigt seit dem 12.09.2026 die **Grenzen je Aktion**. Genau
hinsehen - das ist die groesste Verhaltensaenderung:

```
 Aktion     je Tag  Abstand
 beitritt        4  30-90 Min
 post            3  120-240 Min
 kommentar       6  30-90 Min
Hinweis: beitritt.anfragen_pro_tag (50) wird von limits.join_requests.daily (4) ueberstimmt.
```

Aus 50 Beitrittsanfragen am Tag sind **4** geworden. Das ist so gewollt
(konservative Vorgaben), aber es verlangsamt sieben Kampagnen erheblich. Wer
mehr will, aendert `limits` in `config/settings.yaml` - nicht den alten
`beitritt`-Block, der wird ueberstimmt.

### Stufe 1 - Die Erkennung an den eigenen Texten

**Frage: Sieht der Runner in unseren Gruppen das Richtige?**

Kein Abruf, keine Buchung, kein Browser - der Text kommt von der
Kommandozeile:

```bash
"$py" -m fbgroups.cli campaign pruefe-inhalt \
  "كيف فيني ابعت غرض صغير من ألمانيا لسوريا؟" \
  "مسافر من برلين لدمشق وعندي مكان بالشنطة" \
  "Suche dringend 2-Zimmer-Wohnung in Stuttgart" \
  "شو أحسن مطعم عربي بشتوتغارت؟"
```

Erwartet: die ersten beiden mit Bezug `hoch`, die letzten beiden `no_reply`.
**Wenn ein Beitrag aus Ihren Gruppen falsch eingeordnet wird, ist das der
Moment, es zu merken** - nicht wenn der Kommentar schon dort steht.

Echte Texte aus einer Datei pruefen (ein Beitrag je Zeile):

```bash
"$py" -m fbgroups.cli campaign pruefe-inhalt --datei beitraege.txt
```

Und wie sich die Gruppenregeln auswirken - derselbe Text, drei Gruppen:

```bash
T="كيف فيني ابعت غرض صغير من ألمانيا لسوريا؟"
"$py" -m fbgroups.cli campaign pruefe-inhalt "$T"                                  # Regeln ungelesen
"$py" -m fbgroups.cli campaign pruefe-inhalt "$T" --regeln "Willkommen, seid nett"  # nichts verboten
"$py" -m fbgroups.cli campaign pruefe-inhalt "$T" --regeln "Keine Links erlaubt"    # ohne Link
"$py" -m fbgroups.cli campaign pruefe-inhalt "$T" --kein-mitglied                   # kein Zutritt
```

Erwartet der Reihe nach: `private_contact_suggestion` (ungelesen =
vorsichtig), `direct_app_recommendation` **mit** Link, `contextual_app_mention`
**ohne** Link.

Der letzte Aufruf (`--kein-mitglied`) zeigt weiterhin eine Entscheidung und
darunter einen Hinweis - **das ist richtig so**: Die Mitgliedschaft ist eine
Angabe ueber unseren Arbeitsstand, nicht ueber den Beitrag. Ob sie den Lauf
sperrt, sagt `automatik.mitgliedschaft_pflicht` (steht auf `false`, also wird
es versucht) bzw. `qualifikation.pflicht`. Der Hinweis nennt den geltenden
Stand.

### Stufe 2 - Ausrollen und Dienst

**Frage: Laeuft der neue Stand auf dem Server?**

```bash
bash ./ausrollen.sh --plan     # erst ansehen
bash ./ausrollen.sh            # dann uebertragen
```

Auf dem Server:

```bash
sudo systemctl is-active fbgroups
curl -s -o /dev/null -w 'healthz: %{http_code}\n' http://127.0.0.1:8090/healthz
fbgroups config-check | tail -12    # dieselben Grenzen wie oertlich?
```

### Stufe 3 - Der Bestand, ohne etwas zu tun

**Frage: Was wuerde der Lauf vorfinden?**

Auf dem Server:

```bash
fbgroups campaign list --status active
fbgroups campaign qualifikation essen-1109        # wer darf ueberhaupt was?
fbgroups campaign qualifikation essen-1109 --stufe beitritt_noetig
```

Oertlich (liest den Server durch den Tunnel):

```bash
curl -s http://127.0.0.1:8090/automatik | "$py" -m json.tool
```

Neu in der Antwort: `abschnitt`, `beitritt_offen`, `beitritt_kontingent`,
`gruppen_gesperrt`, `gruppen_uebersprungen`.

### Stufe 4 - Ein Lauf, eine Kampagne, fuenf Schritte

**Frage: Tut er, was er sagt?**

```bash
"$py" -m fbgroups.cli auth login      # Fenster offen lassen, bis der Lauf durch ist
"$py" -m fbgroups.cli campaign automatik --server http://127.0.0.1:8090 \
      --neu --kampagne essen-1109 --limit 5
```

Worauf zu achten ist, waehrend es laeuft:

| Zeile auf dem Bildschirm | Bedeutung |
|--------------------------|-----------|
| `Beitrittsanfrage` | Schritt 2 des Ablaufs - kommt **vor** jedem Kommentar |
| `Neubewertung` | Schritt 3 - laeuft einmal je Kampagne, kostet kein `--limit` |
| `direct_app_recommendation: versand/fragt ...` | der gewaehlte Beitrag und **warum** er gewaehlt wurde |
| `kein Anlass: kein passender Beitrag (...)` | NO_REPLY - ein Ergebnis, kein Fehlschlag |
| `Die Gegenseite bremst: kommentar pausiert 60 Min` | nur Kommentare ruhen, Beitraege laufen weiter |
| `Beitrittstakt: noch 42 Min` | der Lauf wartet, statt Beitraege vorzuziehen |

### Stufe 5 - Nachsehen, was geschehen ist

**Frage: Stimmt das Gebuchte mit dem Gesehenen ueberein?**

```bash
"$py" -m fbgroups.cli campaign automatik --status
```

Die Ausgabe hat drei neue Teile:

* **Eingefrorene Kampagnenliste** - welche Kampagnen im Lauf sind, mit
  Abschnitt je Kampagne.
* **Aktionen** - was jede Aktion heute noch darf. Hier steht die Antwort auf
  "warum passiert nichts?".
* **In diesem Lauf uebersprungen** - mit **Grund** je Gruppe. `kein Anlass:`
  ist das neue Verhalten (kein passender Beitrag), alles andere ein Hinweis
  auf Technik oder Regeln.

Auf dem Server:

```bash
fbgroups campaign fortschritt essen-1109
fbgroups marketing analytics --top 10        # Klicks, Registrierungen
fbgroups campaign qualifikation essen-1109   # hat sich die Einstufung bewegt?
```

### Was ein gelungener Probelauf zeigt

* Keine Gruppe steht auf **erschoepft**, ohne dass es dafuer einen Grund aus
  der Gruppe gibt (technische Fehlschlaege zaehlen nicht mehr mit).
* Keine Kampagne steht auf **completed**, solange sie ihr Ziel nicht erreicht
  hat - stattdessen `erschoepft_vorerst` oder `wartet_auf_beitritt`.
* Unter Wohnungs-, Job- und Restaurantbeitraegen steht **nichts** von uns.
* Die Tagesmengen aus `config-check` sind nicht ueberschritten.

### Zurueck auf Anfang

Der Probelauf hat Spuren hinterlassen (Versuche, Uebersprungsliste). Fuer
einen zweiten sauberen Durchgang:

```bash
fbgroups campaign retry --alle --kommentare   # auf dem Server
"$py" -m fbgroups.cli campaign automatik --server http://127.0.0.1:8090 --neu --limit 5
```

Die Uebersprungsliste haengt an der `lauf_id` und ist mit `--neu` ohnehin weg.
Veroeffentlichte Kommentare bleiben veroeffentlicht - das ist der Unterschied
zu `campaign reset`, und er ist der Grund, warum hier kein `reset` steht.

## 8. Scharf schalten: der erste echte Lauf

Abschnitt 6 ist der Weg, Abschnitt 7 die Probe. Hier steht, was **heute**
einzutippen ist, um mit den sieben aktiven Kampagnen anzufangen - in der
Reihenfolge, in der es getan wird.

### 8.0 Zuerst: ausrollen

Ohne diesen Schritt laeuft auf dem Server der alte Stand. Die neuen Befehle
(`campaign pruefe-inhalt`, die Grenzen im `config-check`) gibt es dort noch
nicht, und der Ablauf waere der von gestern.

```bash
bash ./ausrollen.sh --plan
bash ./ausrollen.sh
```

Auf dem Server pruefen, dass der neue Stand steht:

```bash
sudo systemctl is-active fbgroups
fbgroups config-check | tail -12
```

Die Tabelle "Grenzen je Aktion" muss erscheinen. Tut sie es nicht, ist das
Ausrollen nicht durchgelaufen - nicht weitermachen.

### 8.1 Die Zahl, ueber die vor dem Start zu entscheiden ist

Der Bestand: **121 zugeordnete Gruppen** in sieben Kampagnen, davon fast keine
mit vermerkter Mitgliedschaft. Mit der Vorgabe von **4 Beitrittsanfragen am
Tag** dauert die Runde:

```
121 Gruppen / 4 Anfragen am Tag  =  rund 30 Tage
```

Das ist die vorsichtige Zahl aus der Anforderung (3-5). Sie ist nicht falsch -
sie ist langsam, und das mit Absicht: An diesem einen Konto haengen alle
bestehenden Mitgliedschaften. Wer schneller vorankommen will, aendert **eine**
Stelle:

```yaml
# config/settings.yaml
limits:
  join_requests:
    daily: 4      # 8 verkuerzt auf ~15 Tage, 12 auf ~10
```

Danach `bash ./ausrollen.sh`. Der alte Schluessel `beitritt.anfragen_pro_tag:
50` wird **ueberstimmt** und aendert nichts mehr.

Empfehlung fuer den ersten Tag: **nichts aendern**. Erst sehen, wie sich vier
Anfragen und sechs Kommentare anfuehlen, dann entscheiden.

### 8.2 Browser anmelden

Oertlich, sichtbares Fenster. Es bleibt offen, solange der Lauf laeuft - wird
es geschlossen, hoert der Lauf nach fuenf technischen Fehlschlaegen von selbst
auf (und vermerkt nichts gegen die Gruppen).

```bash
py="./.venv/Scripts/python.exe"; export PYTHONIOENCODING=utf-8
"$py" -m fbgroups.cli auth login
```

### 8.3 Der erste echte Lauf - eine Kampagne, drei Schritte

**Nicht mit allen sieben anfangen.** Der erste Lauf ist die Stelle, an der
sich zeigt, ob die Erkennung in echten Gruppen greift.

```bash
"$py" -m fbgroups.cli campaign automatik --server http://127.0.0.1:8090 \
      --neu --kampagne essen-1109 --limit 3
```

`essen-1109` deshalb: 16 Gruppen, und dort stehen bereits Kommentare mit
gezaehlten Klicks - die Kampagne ist erprobt, die Gruppen sind erreichbar.

Danebensitzen und mitlesen. Erwartet wird ungefaehr:

```
Neuer Lauf 12
Essen - Neubewertung
  16 Gruppen bewertet, 3 mit geaendertem Score
<Gruppe> - Beitrittsanfrage
  angefragt
Beitrittstakt: noch 41 Min
```

Der Lauf **wartet** dann. Das ist richtig: Beitrittsanfragen gehen vor, und
zwischen zweien liegen 30-90 Minuten. Wer nicht warten will, bricht mit
`Strg+C` ab - der Stand bleibt, nichts geht verloren.

### 8.4 Nachsehen, bevor es weitergeht

```bash
"$py" -m fbgroups.cli campaign automatik --status
```

Drei Dinge lesen:

* **Aktionen** - was heute noch frei ist. `beitritt 3`, `kommentar 6`,
  `post 3` heisst: eine Anfrage ist raus, sonst nichts.
* **In diesem Lauf uebersprungen** - mit Grund. `kein Anlass: kein passender
  Beitrag (...)` ist das neue Verhalten und **kein Fehler**: In der Gruppe
  stand nichts, worauf eine Antwort von uns gepasst haette.
* **Abschnitt** je Kampagne - `Beitrittsanfragen`, `Neubewertung` oder
  `Beitraege und Kommentare`.

Auf dem Server:

```bash
fbgroups campaign qualifikation essen-1109
```

### 8.5 Alle sieben Kampagnen

Wenn 8.3 und 8.4 stimmen:

```bash
"$py" -m fbgroups.cli campaign automatik --server http://127.0.0.1:8090 --neu
```

`--neu` friert eine frische Liste aus **allen aktiven** Kampagnen ein, in der
Reihenfolge ihrer Anlage: hamburg, stuttgart, bremen, berlin, Essen,
Doutmound, köln. `hallo-123` steht auf `completed` und kommt nicht mit.

Der Lauf endet von selbst, wenn die Tagesmengen erschoepft sind. Das ist kein
Fehler und keine Erschoepfung der Gruppen - die Meldung sagt es.

### 8.6 Der taegliche Rhythmus

Die Grenzen gelten **je Tag** (ab UTC-Mitternacht). Danach genuegt einmal am
Tag:

```bash
"$py" -m fbgroups.cli campaign automatik --server http://127.0.0.1:8090
```

**Ohne `--neu`** - der offene Lauf wird fortgesetzt und behaelt seine Liste.
`--neu` nur, wenn eine Kampagne dazugekommen ist oder die uebersprungenen
Gruppen wieder mitsollen.

Alle paar Tage:

```bash
fbgroups marketing analytics --top 10          # bringt es etwas?
fbgroups campaign qualifikation <kampagne>     # bewegt sich der Trichter?
"$py" -m fbgroups.cli marketing regeln --limit 10   # Regeln nachlesen (oertlich)
```

Der letzte Befehl ist der lohnendste: Solange die Regeln einer Gruppe
ungelesen sind, bleibt der Runner dort **vorsichtig** - kein Link, keine
Nennung der App, nur das Angebot eines Gespraechs. Gelesene Regeln schalten
die volle Empfehlung frei, wo sie erlaubt ist.

### Der Lauf endet sofort und hat nichts getan

Das Bild (12.09.2026):

```
koeln: 11 Gruppen bewertet, 0 mit geaendertem Score
Automatik NICHT vollstaendig abgeschlossen
Kampagnen:  0 / 7   Gruppen: 0 / 121   Kommentare: 13 / 1210
Offen bei: koeln / Arab in Koeln (5 / 10 Kommentare)
```

Es ist offene Arbeit da, und trotzdem geschieht nichts. **Das ist fast immer
die Tagesmenge** - an dem Tag war schon gearbeitet worden, und sechs
Kommentare sind sechs Kommentare.

Nachsehen, und zwar auf dem **Server** (``--status`` liest die Datei des
Arbeitsrechners, nicht die des Servers):

```bash
curl -s http://127.0.0.1:8090/automatik | "$py" -m json.tool | head -40
```

Im Feld `aktionen` steht je Aktion, was heute noch frei ist:

```json
"aktionen": {
  "beitritt":  {"moeglich": false, "grund": "beitritt: Tagesmenge erreicht (4/4)", "rest_heute": 0},
  "post":      {"moeglich": true,  "rest_heute": 2},
  "kommentar": {"moeglich": false, "grund": "kommentar: Abstandsregel", "wartezeit": "noch 24 Min"}
}
```

Drei Faelle, drei Antworten:

| `grund` | Bedeutung | Was zu tun ist |
|---------|-----------|----------------|
| `Tagesmenge erreicht (6/6)` | heute nicht mehr | morgen weiter, oder `limits` erhoehen |
| `Abstandsregel` + `wartezeit` | noch nicht | warten, der Lauf holt es selbst |
| `von der Gegenseite gebremst` | Facebook bremst | nichts tun, der Backoff laeuft ab |
| `abgeschaltet (pro_tag 0)` | wir selbst | `limits` in `settings.yaml` |

Seit dem 12.09.2026 steht der Grund auch in der **Abschlussmeldung** des
Laufs - dann eruebrigt sich das Nachsehen.

### Wann aufhoeren

| Beobachtung | Was zu tun ist |
|-------------|----------------|
| `Die Gegenseite bremst: ... pausiert 60 Min` | nichts. Nur diese Aktion ruht, der Rest laeuft |
| Zweite Bremsung am selben Tag | Lauf beenden, morgen weiter. Der Backoff verdoppelt sich von selbst |
| Facebook zeigt eine Warnung im Browser | sofort `Strg+C`, einen Tag Pause, `limits` halbieren |
| Viele `kein Anlass` | richtig - die Gruppen passen nicht zum Angebot. `campaign qualifikation` ansehen |
| Viele `fehlgeschlagen` mit Moderationsgrund | `campaign qualifikation` - die Gruppen lehnen ab, nicht die Technik |

## 9. Kampagnen automatisieren (Browser oertlich, Buchung auf dem Server)

Lokal - sichtbarer Browser (`headless=False`, harte Projektgrenze), Zugang zum
Server ueber den SSH-Tunnel aus Abschnitt 1.

**Git Bash:**

```bash
py="./.venv/Scripts/python.exe"
export PYTHONIOENCODING=utf-8

"$py" -m fbgroups.cli auth login                                          # Facebook-Sitzung zuerst

# Stand des Servers: --status und --dry-run gelten NUR fuer den oertlichen
# Bestand und werden mit --server abgewiesen (Exit-Code 2).
curl -s http://127.0.0.1:8090/automatik | "$py" -m json.tool

"$py" -m fbgroups.cli campaign automatik --server http://127.0.0.1:8090
"$py" -m fbgroups.cli campaign automatik --server http://127.0.0.1:8090 --limit 5
"$py" -m fbgroups.cli campaign automatik --server http://127.0.0.1:8090 --kampagne batreeq-syrian-germany

"$py" -m fbgroups.cli campaign beitritt --server http://127.0.0.1:8090 --dry-run   # Beitrittsanfragen
"$py" -m fbgroups.cli campaign beitritt --server http://127.0.0.1:8090 --limit 10

"$py" -m fbgroups.cli campaign abgleich --server http://127.0.0.1:8090 --ja        # oertlich Gepostetes nachtragen

"$py" -m fbgroups.cli enrich --server http://127.0.0.1:8090 --limit 5              # Gruppenseiten-Befunde buchen
```

**PowerShell** - dieselben Befehle. `$py` gilt nur in dem Fenster, in dem es
gesetzt wurde; in einer neuen Sitzung erst wieder setzen, sonst meldet
PowerShell "Der Ausdruck nach & ... hat ein ungueltiges Objekt erzeugt":

```powershell
$py = ".\.venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"

& $py -m fbgroups.cli campaign automatik --server http://127.0.0.1:8090 --limit 5
```

Nur oertlich (liest die Datei dieses Rechners, nicht den Server) - deshalb im
Regelfall nicht die Frage, die man stellen will:

```bash
"$py" -m fbgroups.cli campaign automatik --status
"$py" -m fbgroups.cli campaign automatik --dry-run
```

**Die Reihenfolge des Laufs** (seit 12.09.2026): Je Kampagne zuerst die
offenen Beitrittsanfragen ihrer Gruppen, dann die Neubewertung, dann die
besten qualifizierten Gruppen - darin erst der Beitrag, dann die zehn
Kommentare. Erst wenn die Kampagne durch ist, kommt die naechste. Der Lauf
stellt die Anfragen also selbst; `campaign beitritt` bleibt fuer den Fall,
dass man nur Anfragen stellen will.

Zwei Dinge, die dabei auffallen und beide Absicht sind:

* Zwischen zwei Beitrittsanfragen **wartet** der Lauf
  (`beitritt.mindestabstand_minuten`, 3 Min) - er zieht keine Beitraege vor.
  Bei 50 Anfragen am Tag sind das gut zweieinhalb Stunden. Wer das nicht
  will: `anfragen_pro_tag` niedriger setzen, `0` nimmt die Anfragen ganz aus
  dem Lauf.
* Ein Fehler bei einer Gruppe beendet den Lauf **nicht**. Die Gruppe wird mit
  Grund uebersprungen und ist im naechsten Lauf wieder dabei; die
  Abschlussmeldung nennt die Zahl.

```bash
curl -s http://127.0.0.1:8090/automatik | "$py" -m json.tool | grep abschnitt
```
zeigt, in welchem Abschnitt die laufende Kampagne gerade steht.

`campaign kaltmodus <kampagne>` (Tagesportion anzeigen) und
`campaign auto <kampagne> [group_id]` (Einzelschritt) kennen kein `--server`
und arbeiten direkt auf dem Bestand - deshalb gehoeren sie in Abschnitt 4, auf
den Server.

## 10. Sicherung und Wiederherstellung

Auf dem Server:
```bash
systemctl list-timers fbgroups-backup.timer
journalctl -u fbgroups-backup.service -n 5
sudo -u fbgroups /usr/local/bin/fbgroups-backup      # von Hand
```
Lokal, Kopie holen (Git Bash):
```bash
scp karim@159.195.216.246:/opt/fbgroups/backups/groups-*.sqlite.gz data/backups/
```

## 11. Regelmaessige Neubewertung (Rescore-Timer, alle 6 Stunden)

Auf dem Server:
```bash
systemctl daemon-reload
systemctl enable --now fbgroups-rescore.timer
systemctl list-timers fbgroups-rescore.timer
journalctl -u fbgroups-rescore.service -n 20
```

## 12. Rueckrollen (Rollback) bei fehlgeschlagenem Ausrollen

Lokal:
```bash
ssh -t -i ~/.ssh/b-tarikak_vps_new karim@159.195.216.246 \
  'sudo rm -rf /opt/fbgroups/app/src /opt/fbgroups/app/config \
   && sudo cp -a /opt/fbgroups/vorher/. /opt/fbgroups/app/ \
   && sudo chown -R fbgroups:fbgroups /opt/fbgroups/app \
   && sudo systemctl restart fbgroups'
```
