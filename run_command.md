# Befehle: Server, Ausrollen, Automatik

Nachschlagewerk, kein Skript zum Durchlaufen. Die Bloecke laufen an
verschiedenen Orten - jeweils vermerkt:

* **Server** - nach `ssh` als `karim`. Alles, was den Bestand aendert, laeuft
  nur hier.
* **Oertlich** - auf dem Arbeitsrechner (Git Bash oder PowerShell). Hier steht
  der Browser; der Bestand liegt auf dem Server (`--server`).

```bash
# oertlich, Git Bash - einmal je Fenster
py="./.venv/Scripts/python.exe"; export PYTHONIOENCODING=utf-8
```

```powershell
# oertlich, PowerShell - einmal je Fenster
$py = ".\.venv\Scripts\python.exe"; $env:PYTHONIOENCODING = "utf-8"
```

## 1. Verbindung (oertlich)

```bash
ssh -i ~/.ssh/b-tarikak_vps_new karim@159.195.216.246

# Tunnel fuer Uebersicht und Automatik auf 127.0.0.1:8090
ssh -i ~/.ssh/b-tarikak_vps_new -L 8090:127.0.0.1:8090 -o ServerAliveInterval=30 karim@159.195.216.246
```

Danach `http://127.0.0.1:8090/` im Browser. Der Waechter kann den Tunnel
auch selbst aufmachen (`watchdog.tunnel` in `settings.yaml`).

## 2. Ausrollen (oertlich, Git Bash)

```bash
bash ./ausrollen.sh --plan     # nur zeigen, was liefe
bash ./ausrollen.sh            # uebertragen, einsetzen, Dienst neu starten
bash ./ausrollen.sh --pip      # zusaetzlich Abhaengigkeiten erneuern
bash ./ausrollen.sh --test     # vorher die Tests
bash ./ausrollen.sh --mitglieder            # alle data/from_lokal/*.csv einlesen
bash ./ausrollen.sh --mitglieder liste.csv  # eine bestimmte
```

Uebertragen werden nur `src`, `config`, `pyproject.toml` - nie `.env` oder
`data/`. Ausgerollt wird **aus dem Hauptcheckout auf `main`**.

Rueckrollen, wenn ein Ausrollen schiefging:

```bash
ssh -t -i ~/.ssh/b-tarikak_vps_new karim@159.195.216.246 \
  'sudo rm -rf /opt/fbgroups/app/src /opt/fbgroups/app/config \
   && sudo cp -a /opt/fbgroups/vorher/. /opt/fbgroups/app/ \
   && sudo chown -R fbgroups:fbgroups /opt/fbgroups/app \
   && sudo systemctl restart fbgroups'
```

## 3. Dienst (Server)

```bash
sudo systemctl restart fbgroups
sudo systemctl is-active fbgroups
sudo journalctl -u fbgroups -n 15 --no-pager
curl -s -o /dev/null -w 'healthz: %{http_code}\n' http://127.0.0.1:8090/healthz
fbgroups config-check | tail -20
```

Pruefen immer mit `/healthz`, **nie** mit einem Tracking-Link: `/r/{code}`
zaehlt jeden Aufruf als echten Klick.

Sicherung:

```bash
systemctl list-timers fbgroups-backup.timer
sudo -u fbgroups /usr/local/bin/fbgroups-backup                          # von Hand
scp karim@159.195.216.246:/opt/fbgroups/backups/groups-*.sqlite.gz data/backups/   # oertlich holen
```

## 4. Bestand und Kampagnen (Server)

```bash
fbgroups import-mitglieder /opt/fbgroups/app/data/liste.csv --dry-run
fbgroups import-mitglieder /opt/fbgroups/app/data/liste.csv

fbgroups campaign new "Batreeq Syrian Germany" --sprache ar --prioritaet A++
fbgroups campaign target batreeq-syrian-germany                  # Regel ansehen
fbgroups campaign target batreeq-syrian-germany --aktivitaet sehr_aktiv
fbgroups campaign sync batreeq-syrian-germany --dry-run          # wie viele Codes?
fbgroups campaign sync batreeq-syrian-germany                    # Codes vergeben
fbgroups campaign status batreeq-syrian-germany active           # ohne 'active' kein Lauf
fbgroups campaign text batreeq-syrian-germany --aus-vorlage --ja # Beitrag + Kommentar
fbgroups campaign kurzlinks                                      # Kurzcodes nachtragen
```

`sync` vergibt Tracking-Codes, und **ein vergebener Code wird nie
zurueckgenommen** - erst `--dry-run` lesen.

Nachsehen:

```bash
fbgroups campaign fortschritt batreeq-syrian-germany
fbgroups marketing analytics --top 10
fbgroups marketing overview
```

## 5. Die Automatik (oertlich, Browser; gebucht auf dem Server)

```bash
"$py" -m fbgroups.cli auth login          # einmal: sichtbares Fenster, von Hand anmelden

curl -s http://127.0.0.1:8090/automatik | "$py" -m json.tool   # Stand des Servers

"$py" -m fbgroups.cli campaign automatik --server http://127.0.0.1:8090 --limit 5
"$py" -m fbgroups.cli campaign automatik --server http://127.0.0.1:8090
"$py" -m fbgroups.cli campaign automatik --server http://127.0.0.1:8090 --neu   # neue Kampagnenliste
```

* `--neu` nur, wenn eine Kampagne dazugekommen ist: Ein offener Lauf behaelt
  seine eingefrorene Liste. Der Fortschritt geht dabei nicht verloren.
* `--status` und `--dry-run` lesen die Datei **dieses** Rechners, nicht den
  Server - fuer den Server das `curl` oben.
* Vor dem ersten Schritt prueft der Lauf die Anmeldung; ohne sie tut er
  nichts (Exit 2) und nennt `auth login`.

Der Waechter - einmal starten, dann haelt er genau einen Lauf am Leben:

```bash
"$py" -m fbgroups.cli campaign watchdog --server http://127.0.0.1:8090
"$py" -m fbgroups.cli campaign watchdog --einmal     # nur nachsehen
```

Einen Beitragstext probeweise beurteilen - ohne Netz, ohne Konto:

```bash
"$py" -m fbgroups.cli campaign pruefe-inhalt "مين نازل ع الشام؟"
```

## 6. Wenn etwas nicht laeuft

| Bild | Ursache | Was zu tun ist |
|---|---|---|
| "Keine aktive Kampagne" | Status fehlt | `campaign status <k> active` (Server) |
| "Diese Kampagne hat keine Gruppen zugeordnet" | nicht zugeordnet | `campaign sync <k>` (Server) |
| Lauf tut nichts, offene Arbeit da | Tagesmenge oder Takt | `curl .../automatik`, Feld `aktionen` |
| `von der Gegenseite gebremst` | Facebook bremst | nichts tun, der Backoff laeuft ab |
| 404 auf `/automatik` | Tunnel zu | Abschnitt 1 |
| "nicht angemeldet" | Sitzung abgelaufen | `auth login`, neu starten |
| Viele Fehlschlaege nach geschlossenem Fenster | Browser weg | `fbgroups campaign retry --alle --kommentare` (Server), abgeschlossene Kampagnen wieder `active` |
| Facebook zeigt eine Warnung | zu schnell | sofort `Strg+C`, einen Tag Pause, `limits` halbieren |

`campaign retry --alle --kommentare` nimmt Fehlschlaege zurueck und laesst
**veroeffentlichte** Fassungen unberuehrt - anders als `campaign reset`, das
auch Hinausgegangenes auf Anfang stellt.

Die Grenzen je Aktion (`limits`, `delays`) stehen in `config/settings.yaml`
und wirken erst nach `bash ./ausrollen.sh`. **Beitrittsanfragen stehen seit dem
23.09.2026 auf 0** (`limits.join_requests.daily`), bis festgelegt ist, welche
Bezuege eine Anfrage rechtfertigen.
