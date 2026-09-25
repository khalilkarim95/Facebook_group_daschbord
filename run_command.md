# Befehle: oertlicher Betrieb

Nachschlagewerk, kein Skript zum Durchlaufen.

**Seit dem 25.09.2026 laeuft fbgroups ganz auf diesem Rechner.** Bestand,
Browser, Protokoll und Sicherung liegen hier; es gibt keinen Dienst auf dem
Server, keinen Tunnel und kein Ausrollen mehr. Der Server traegt nur noch die
App (b-tarikak.de, api.) - fuer fbgroups ist er Rueckfallebene (Abschnitt 7).

```powershell
# PowerShell - einmal je Fenster
$py = ".\.venv\Scripts\python.exe"; $env:PYTHONIOENCODING = "utf-8"
```

```bash
# Git Bash - einmal je Fenster
py="./.venv/Scripts/python.exe"; export PYTHONIOENCODING=utf-8
```

## 0. Einmalig: der Umzug (Git Bash) - gelaufen am 25.09.2026, 14:05

```bash
bash ./umzug-lokal.sh --plan           # nur nachsehen: Dienst, Bestand, nginx
bash ./umzug-lokal.sh                  # Dienst anhalten, Bestand holen, pruefen, einsetzen
bash ./umzug-lokal.sh --weiterleitung  # nginx: alte Links auf die Startseite (wiederholbar)
```

Vorher Waechter und Lauf beenden (das Skript prueft es) und die Uebersicht
schliessen. Die Passphrase des Schluessels wird einmal gefragt. Danach steht
`data/umgezogen.txt`; ab dann verweigern `umzug-lokal.sh` und `ausrollen.sh`
den Dienst - beide haetten den veralteten Server-Bestand wieder ins Spiel
gebracht. `--weiterleitung` laeuft trotzdem: Es fasst nur nginx an.

## 1. Bestand und Kampagnen

```powershell
& $py -m fbgroups.cli import-mitglieder data\from_lokal\liste.csv --dry-run
& $py -m fbgroups.cli import-mitglieder data\from_lokal\liste.csv

& $py -m fbgroups.cli campaign new "Batreeq Syrian Germany" --sprache ar --prioritaet A++
& $py -m fbgroups.cli campaign target batreeq-syrian-germany                  # Regel ansehen
& $py -m fbgroups.cli campaign target batreeq-syrian-germany --aktivitaet sehr_aktiv
& $py -m fbgroups.cli campaign sync batreeq-syrian-germany --dry-run          # wie viele Gruppen?
& $py -m fbgroups.cli campaign sync batreeq-syrian-germany                    # zuordnen
& $py -m fbgroups.cli campaign status batreeq-syrian-germany active           # ohne 'active' kein Lauf
& $py -m fbgroups.cli campaign text batreeq-syrian-germany --aus-vorlage --ja # Beitrag + Kommentar
& $py -m fbgroups.cli campaign fortschritt batreeq-syrian-germany
```

Eine Mitgliederliste wird **hier** eingelesen, nicht mehr beim Ausrollen.
`sync` vergibt noch Tracking-Codes, bis das Tracking entfernt ist - erst
`--dry-run` lesen.

## 2. Die Automatik

```powershell
& $py -m fbgroups.cli auth login                      # einmal: sichtbares Fenster, von Hand anmelden

& $py -m fbgroups.cli campaign automatik --status     # Stand
& $py -m fbgroups.cli campaign automatik --limit 5    # zum Ausprobieren
& $py -m fbgroups.cli campaign automatik
& $py -m fbgroups.cli campaign automatik --neu        # frische Kampagnenliste
```

* `--neu` nur, wenn der offene Lauf ganz neu beginnen soll; neue Kampagnen
  haengt er ohnehin hinten an. Der Fortschritt geht nicht verloren.
* Vor dem ersten Schritt prueft der Lauf die Anmeldung; ohne sie tut er
  nichts (Exit 2) und nennt `auth login`.

Der Waechter - einmal starten, dann haelt er genau einen Lauf am Leben und
sichert den Bestand einmal am Tag:

```powershell
& $py -m fbgroups.cli campaign watchdog
& $py -m fbgroups.cli campaign watchdog --einmal     # einmal nachsehen (startet, wenn keiner laeuft)
```

## 3. Uebersicht und Arbeitsseite

```powershell
& $py -m fbgroups.cli serve --port 8090     # dann http://127.0.0.1:8090/
```

Nur von diesem Rechner erreichbar (127.0.0.1) und damit voll bedienbar. Der
Lauf braucht sie nicht - sie ist Ansicht und Arbeitsseite, kein Teil der
Automatik. `/r/{code}` **nie zum Testen** aufrufen: Es zaehlt jeden Aufruf
als Klick; zum Pruefen `/healthz`.

## 4. Protokoll

Was `campaign automatik` und `campaign watchdog` im Fenster zeigen, steht
zusaetzlich in `data/logs/`, eine Datei je Befehl und Tag, 60 Tage lang:

```powershell
Get-Content data\logs\automatik-2026-09-25.log -Tail 40 -Wait -Encoding utf8
Select-String -Path data\logs\automatik-*.log -Pattern "fehlgeschlagen" -Encoding utf8
```

Jeder Versuch und sein Ausgang steht ausserdem im Bestand (`post_versuche`)
- das Protokoll ist die Textspur daneben.

## 5. Sicherung

```powershell
& $py -m fbgroups.cli sicherung             # jetzt sichern
& $py -m fbgroups.cli sicherung --liste     # was es gibt
& $py -m fbgroups.cli sicherung --zurueck data\backups\sicherung-2026-09-25T132305.sqlite.gz
```

* Gesichert wird nach `data/backups/` (K:, Festplatte) **und**
  `~/fbgroups-sicherung` (C:, SSD), je 30 Stueck (`sicherung` in
  `settings.yaml`). Der Waechter sichert, sobald die letzte aelter als 24 h ist.
* `--zurueck` nur bei beendetem Lauf und geschlossener Uebersicht. Der Stand
  davor wird vorher selbst gesichert.
* Gegen Diebstahl, Brand oder Verschluesselung hilft nur ein Ort ausser Haus -
  ein Cloud-Ordner als weiterer Eintrag in `sicherung.weitere_ordner`.

## 6. Wenn etwas nicht laeuft

| Bild | Ursache | Was zu tun ist |
|---|---|---|
| "Keine aktive Kampagne" | Status fehlt | `campaign status <k> active` |
| "Diese Kampagne hat keine Gruppen zugeordnet" | nicht zugeordnet | `campaign sync <k>` |
| Lauf tut nichts, offene Arbeit da | Tagesmenge oder Takt | `campaign automatik --status` |
| `von der Gegenseite gebremst` | Facebook bremst | nichts tun, der Backoff laeuft ab |
| "Es laeuft bereits ein Lauf" | zweites Fenster | das andere Fenster suchen; eine Sperre mit toter Kennung gilt nicht |
| `database is locked` | ein Prozess schrieb laenger als 30 s | Uebersicht und Lauf neu starten; ein haengender Python-Prozess im Task-Manager |
| "nicht angemeldet" | Sitzung abgelaufen | `auth login`, neu starten |
| Viele Fehlschlaege nach geschlossenem Fenster | Browser weg | `campaign retry --alle --kommentare`, abgeschlossene Kampagnen wieder `active` |
| Facebook zeigt eine Warnung | zu schnell | sofort `Strg+C`, einen Tag Pause, `limits` halbieren |
| `sicherung_fehlgeschlagen` im Waechter | Ordner fehlt oder voll | Meldung lesen; der naechste Blick versucht es neu |

`campaign retry --alle --kommentare` nimmt Fehlschlaege zurueck und laesst
**veroeffentlichte** Fassungen unberuehrt - anders als `campaign reset`, das
auch Hinausgegangenes auf Anfang stellt.

Die Grenzen je Aktion (`limits`, `delays`) stehen in `config/settings.yaml`
und wirken beim naechsten Start des Laufs. **Beitrittsanfragen stehen seit dem
23.09.2026 auf 0** (`limits.join_requests.daily`), bis festgelegt ist, welche
Bezuege eine Anfrage rechtfertigen.

## 7. Der Server (nur noch Rueckfallebene)

```bash
ssh -i ~/.ssh/b-tarikak_vps_new root@159.195.216.246
```

* Dienst `fbgroups` und `fbgroups-backup.timer` sind seit dem Umzug
  abgeschaltet (`disable`), nicht geloescht. Der letzte Server-Bestand liegt
  unter `/opt/fbgroups/app/data/groups.sqlite`, die Umzugskopie unter
  `/opt/fbgroups/backups/umzug-<zeit>.sqlite`.
* **Nicht wieder starten**, sobald hier gebucht wurde: Der Server-Bestand ist
  dann veraltet, und zwei Bestaende sind genau der Zustand vom 26.08. und
  31.08.2026.
* Die alten Links (`go.b-tarikak.de/r/...`, `b-tarikak.de/t/...`) stehen in
  veroeffentlichten Beitraegen. nginx leitet sie auf `https://b-tarikak.de/home`
  weiter (302, ohne Zaehlung); `/events` und `/healthz` antworten mit 410.
  Eingerichtet von `bash ./umzug-lokal.sh --weiterleitung`; die Fassungen
  davor liegen unter `/opt/fbgroups/backups/nginx-<zeit>/`. Zurueck:

  ```bash
  cp -a /opt/fbgroups/backups/nginx-<zeit>/*.de /etc/nginx/sites-available/ && nginx -t && systemctl reload nginx
  ```
