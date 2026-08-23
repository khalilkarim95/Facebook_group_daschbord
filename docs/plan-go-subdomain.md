# `go.b-tarikak.de` — Tracking-Dienst

In Betrieb seit 18.08.2026. Dieses Dokument beschreibt den **Ist-Zustand** und
die Bedienung. Der frühere Planungsstand ist überholt und ersetzt.

---

## 1. Was läuft

```
159.195.216.246 (netcup VPS, Debian 13)
   nginx (systemd, nativ)
     ├─ b-tarikak.de + www    → /var/www/b-tarikak         (Flutter, statisch)
     ├─ admin.b-tarikak.de    → /var/www/admin.b-tarikak   (statisch, Passwort)
     ├─ api.b-tarikak.de      → 127.0.0.1:3000  Docker: btarikak-api (Node)
     └─ go.b-tarikak.de       → 127.0.0.1:8090  systemd: fbgroups     ← NEU
   Docker: btarikak-api, btarikak-pg (PostgreSQL 16), btarikak-verify
```

Ein Klick läuft so:

```
go.b-tarikak.de/r/FB-SYR-BER-001
   → nginx → 127.0.0.1:8090 → Klick zählen → 302
   → https://b-tarikak.de/?ref=FB-SYR-BER-001
```

Öffentlich erreichbar ist **nur `/r/`**. Alles andere — insbesondere die
Übersicht — beantwortet nginx mit 404.

## 2. Der Bestand lebt auf dem Server

`/opt/fbgroups/app/data/groups.sqlite` ist die **einzige** gültige Fassung.
Die Kopie auf dem Arbeitsrechner dient nur noch Entwicklung und Tests.

Das ist eine bewusste Entscheidung gegen zwei Kopien: Klicks entstehen auf dem
Server, neue Gruppen entstehen beim Suchlauf. Lägen beide auseinander, würde
ein Abgleich in der einen Richtung die Klicks überschreiben und in der anderen
die neuen Gruppen verlieren — und neu vergebene Codes liefen ins Leere, ohne
dass es jemandem auffällt: Der Link im Beitrag sieht ja richtig aus.

**Wer lokal einen Suchlauf startet, erzeugt genau diesen Zustand.** Suchläufe
gehören auf den Server.

## 3. Bedienung

### Übersicht im Browser

Zwei Wege mit **verschiedenen Rechten**. Der Unterschied ist Absicht: Die
schreibenden Wege vergeben Tracking-Codes, und ein vergebener Code wird nie
zurückgenommen — er steht später in veröffentlichten Beiträgen. Ein
abhandengekommenes Passwort soll Zahlen zeigen können, aber nicht mit einem
Klick 400 Codes vergeben.

**Nur lesen — von überall, ohne Tunnel:**

```
https://go.b-tarikak.de/uebersicht      Benutzername + Passwort
```

Dieselben Zahlen, dieselben Filter, dieselbe Sortierung. Ohne die
Bedienelemente, die schreiben: Der Stand steht als Text statt als Auswahlfeld,
„Zuordnen", „Ausschließen" und die Kampagnenformulare erscheinen nicht.
Eingerichtet wird der Weg in Abschnitt 3.1.

**Ändern — über den SSH-Tunnel:**

```powershell
ssh -i $HOME\.ssh\b-tarikak_vps_new -L 8090:127.0.0.1:8090 karim@159.195.216.246
# Fenster offen lassen, dann im Browser:  http://127.0.0.1:8090/
```

Der Dienst sieht den Aufruf dann als lokal und zeigt die volle Arbeitsliste.
Reicht mit dem Benutzer `karim`, kein root nötig.

### 3.1 Den lesenden Zugang einrichten

Der Dienst zeigt die Übersicht von außen nur, wenn nginx eine bestandene
Passwortprüfung mit der Kopfzeile `X-Uebersicht-Token` bezeugt. Der Wert ist
geheim, obwohl `proxy_set_header` eine mitgeschickte Kopfzeile überschreibt:
Sonst hinge der Schutz daran, dass **jeder** künftige `location`-Block das
Überschreiben nicht vergisst — auch der, den in zwei Jahren jemand anders
schreibt. Ein Weg, der Auskunft gibt, bringt seinen Schutz besser selbst mit,
als ihn von einer Datei nebenan zu borgen.

```bash
# 1. Geheimnis erzeugen
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# 2. In die .env des Dienstes
printf 'UEBERSICHT_TOKEN=%s\n' "<wert>" | sudo tee -a /opt/fbgroups/app/.env

# 3. Passwortdatei anlegen (einmalig)
sudo apt install apache2-utils          # falls htpasswd fehlt
sudo htpasswd -c /etc/nginx/.htpasswd-fbgroups karim

# 4. nginx-Block ergaenzen (siehe unten), pruefen, neu laden
sudo nginx -t && sudo systemctl reload nginx

# 5. Dienst neu starten - .env wird beim Start gelesen
sudo systemctl restart fbgroups
```

In `/etc/nginx/sites-available/go.b-tarikak.de` kommt hinzu:

```nginx
# Uebersicht: dieselben Zahlen wie im Tunnel, nur lesend.
location = /uebersicht {
    auth_basic           "fbgroups";
    auth_basic_user_file /etc/nginx/.htpasswd-fbgroups;

    # Hier endet jeder schreibende Aufruf, noch vor dem Dienst. Der Dienst
    # weist ihn ohnehin ab - zwei Riegel, weil einer davon irgendwann in
    # einer Datei steht, die jemand anders bearbeitet.
    limit_except GET HEAD { deny all; }

    proxy_pass http://127.0.0.1:8090/;
    proxy_set_header Host               $host;
    proxy_set_header X-Forwarded-For    $remote_addr;
    proxy_set_header X-Forwarded-Proto  $scheme;
    proxy_set_header X-Uebersicht-Token "<wert aus Schritt 1>";
}
```

Und in **jedem** anderen Block, der auf `127.0.0.1:8090` zeigt (`/r/`,
`/events`), eine Zeile dazu — sonst könnte ein Besucher die Kopfzeile selbst
mitschicken:

```nginx
proxy_set_header X-Uebersicht-Token "";
```

Prüfen, dass beides stimmt:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://go.b-tarikak.de/uebersicht
#   401 = Passwort wird verlangt   (richtig)
curl -su karim:PASSWORT -o /dev/null -w "%{http_code}\n" https://go.b-tarikak.de/uebersicht
#   200 = Seite kommt              (richtig)
curl -su karim:PASSWORT -o /dev/null -w "%{http_code}\n" -X POST https://go.b-tarikak.de/uebersicht
#   405 = schreiben geht nicht     (richtig)
```

### Suchlauf und Marketing-Befehle

Als `karim`, ohne root und ohne Passwort:

```bash
ssh -i ~/.ssh/b-tarikak_vps_new karim@159.195.216.246

fbgroups search --dry-run --provider serper --show-all   # Plan, kostet nichts
fbgroups search --provider serper --limit 5              # hoechstens 5 neue Anfragen
fbgroups report --top 20
fbgroups campaign links batreeq-syrian-germany
fbgroups marketing overview
```

`/usr/local/bin/fbgroups` fuehrt die CLI als Dienstbenutzer aus. Grundlage ist
eine eng gefasste Regel in `/etc/sudoers.d/fbgroups`:

```
karim ALL=(fbgroups) NOPASSWD: /usr/local/bin/fbgroups
```

`(fbgroups)` heisst: nur zu diesem Zielbenutzer, niemals zu root, und nur
dieses eine Programm. Geprueft: `sudo id` und `sudo -u fbgroups bash` werden
weiterhin abgewiesen.

Der Umweg ueber den Dienstbenutzer ist kein Zierrat. Schriebe `karim` direkt
in die Datenbank, legte SQLite Journaldateien (`-wal`, `-shm`) mit fremdem
Eigentuemer an -- und der laufende Dienst koennte sie nicht mehr beschreiben.
Der Fehler faellt erst Tage spaeter auf, wenn kein Klick mehr gezaehlt wird.

`--provider serper` ist nötig: aktiv laut Konfiguration ist `fixture`. Das ist
Absicht — ein bezahlter Suchlauf soll nicht beiläufig starten.

Neue Gruppen bekommen ihren Tracking-Code beim Suchlauf automatisch
(`auto_assign` steht auf der Kampagne). Da der Lauf auf dem Server stattfindet,
ist der Code sofort gültig.

### Nach jedem Suchlauf

Nichts. Der Code entsteht dort, wo er auch beantwortet wird.

## 4. Was auf dem Server angelegt wurde

| Ort | Inhalt |
|---|---|
| Benutzer `fbgroups` | uid 101, `nologin`, besitzt nur `/opt/fbgroups` |
| `/opt/fbgroups/app/` | Code (`src`, `config`), `.env` (Rechte 600) |
| `/opt/fbgroups/app/data/` | `groups.sqlite`, `query_cache.sqlite` (142 bezahlte Antworten) |
| `/opt/fbgroups/venv/` | eigene Python-Umgebung, 29 Pakete |
| `/etc/systemd/system/fbgroups.service` | Dienst auf `127.0.0.1:8090` |
| `/etc/nginx/sites-available/go.b-tarikak.de` | eigener Block, keine andere Datei berührt |
| `/etc/letsencrypt/live/go.b-tarikak.de/` | eigenes Zertifikat, automatische Erneuerung |
| `/var/log/nginx/go.b-tarikak.de.*.log` | eigene Protokolle |

Systemweit kamen vier apt-Pakete dazu (`python3-venv`, `python3.13-venv`,
`python3-pip-whl`, `python3-setuptools-whl`) — ohne sie kann auf Debian kein
Python-Programm eine eigene Umgebung mit pip bauen.

**Port 8090, nicht 3000:** 3000 gehört `btarikak-api`. Die Voreinstellung im
Code ist 3000; die Unit setzt `--port 8090` dagegen.

## 5. Warum der Dienst die bestehende Anwendung nicht stört

- **nginx:** eigene Datei. `nginx -t` vor jedem Reload, `reload` statt
  `restart` — laufende Verbindungen brechen nicht ab.
- **Datenbank:** SQLite-Datei gegen PostgreSQL im Container. fbgroups kennt
  kein anderes Datenbanksystem; es *kann* die Produktionsdaten nicht berühren.
- **Prozess:** eigener Benutzer, `ProtectSystem=strict`, Schreibrecht nur auf
  `/opt/fbgroups/app/data`.
- **Deployment:** eigene systemd-Unit, nicht Teil der Compose-Datei der API.
  Bewusst kein Container: die Compose-Datei der API hält selbst fest, dass
  Docker `ufw` umgeht — als systemd-Dienst gilt die Bindung an `127.0.0.1`
  verlässlich.
- **Zertifikat:** `--cert-name go.b-tarikak.de`. Die drei bestehenden
  Zertifikate behielten ihr Ablaufdatum (27.10.).

Nachweis aus dem Abschlusstest: `btarikak-api` und `btarikak-pg` liefen zu dem
Zeitpunkt seit 40 Stunden bzw. 4 Tagen durch — kein Neustart, kein Ausfall.

## 6. Absicherung der öffentlichen Wege

| Weg | Zugang | Grund |
|---|---|---|
| `/r/{code}` | offen | muss es sein |
| `/events` | offen, `X-Events-Token` | die Zielanwendung läuft im Container und erreicht `127.0.0.1` des Wirts nicht |
| `/uebersicht` | Basic Auth, nur GET | dieselben Zahlen, ohne die Knöpfe, die Codes vergeben |
| `/healthz` | nur 127.0.0.1 | |
| `/`, `/stand`, `/referral/` | 404 | die schreibenden Wege und der Empfehlungsstand gehören nicht ins Netz |

`proxy_set_header X-Forwarded-For $remote_addr` **überschreibt** und hängt
nicht an — sonst wäre der Wert vom Besucher beeinflussbar. Daran hängen zwei
Dinge zugleich: die Entdopplung der Klicks und die Zugangsprüfung der
Übersicht. uvicorn 0.52.3 vertraut `127.0.0.1` von sich aus; zusätzliche
Startschalter sind nicht nötig.

## 7. Rollback

```bash
systemctl disable --now fbgroups
rm /etc/systemd/system/fbgroups.service && systemctl daemon-reload
rm /etc/nginx/sites-enabled/go.b-tarikak.de
nginx -t && systemctl reload nginx
certbot delete --cert-name go.b-tarikak.de
rm -rf /opt/fbgroups && deluser fbgroups
```

Die bestehende Anwendung ist davon nicht betroffen — sie wurde nie angefasst.

## 8. Sicherung

Täglich um 00:15 (± 30 min Streuung) über `fbgroups-backup.timer`. Zustand:

```bash
systemctl list-timers fbgroups-backup.timer
journalctl -u fbgroups-backup.service -n 5
sudo -u fbgroups /usr/local/bin/fbgroups-backup    # von Hand
```

Ablage: `/opt/fbgroups/backups/groups-JJJJ-MM-TT.sqlite.gz`, 30 Tage;
Stände vom Monatsersten bleiben unbefristet.

Drei Punkte, an denen so etwas sonst scheitert:

- **Keine Dateikopie.** Der Dienst schreibt bei jedem Klick. `sqlite3.
  Connection.backup` sperrt sauber und liefert einen stimmigen Stand, ohne den
  Dienst anzuhalten — eine Kopie mitten im Schreibvorgang wäre unbrauchbar, und
  es fiele erst beim Zurückspielen auf.
- **Jede Sicherung wird geprüft.** Das Skript entpackt sie sofort wieder,
  läuft `PRAGMA integrity_check` und zählt die Gruppen. Eine ungeprüfte
  Sicherung ist eine Vermutung.
- **Erst schreiben, dann umbenennen.** Ein Abbruch mittendrin hinterlässt sonst
  eine halbe Datei, die aussieht wie eine ganze. Die Zwischendatei liegt im
  Sicherungsverzeichnis, nicht in `/tmp`: Unter `ProtectSystem=strict` ist
  `/tmp` schreibgeschützt, und ein Umbenennen ist nur innerhalb desselben
  Dateisystems atomar.

### Kopie vom Server holen

Die Sicherung liegt auf **derselben Platte** wie der Bestand. Gegen einen
Plattenfehler hilft nur eine Kopie anderswo:

```powershell
scp karim@159.195.216.246:/opt/fbgroups/backups/groups-*.sqlite.gz dataackups```

`data/backups/` ist aus dem Repository ausgeschlossen.

## 8.1 Regelmässige Neubewertung (gemessene Resonanz)

Der Score enthält seit 21.08.2026 einen gemessenen Anteil: Klicks und
Registrierungen je Gruppe. Diese Zahlen ändern sich laufend, der gespeicherte
Score nicht — `fbgroups rescore` schreibt ihn.

**Warum kein Neuberechnen im Klickpfad:** `GET /r/{code}` und `POST /events`
öffnen bewusst **nur** den `MarketingStore` (siehe CLAUDE.md). Ein
`SqliteStore.update_scores` dort hinein zu ziehen, hieße den Weg, an dem das
Geld hängt, von einer zweiten Migrationskette abhängig zu machen — und der
Ausfall fiele erst auf, wenn kein Klick mehr ankommt. Ein Timer ist die
langweiligere und darum bessere Lösung.

`/etc/systemd/system/fbgroups-rescore.service`:

```ini
[Unit]
Description=fbgroups: Bestand neu bewerten (gemessene Resonanz)
After=network.target

[Service]
Type=oneshot
User=fbgroups
WorkingDirectory=/opt/fbgroups/app
ExecStart=/opt/fbgroups/venv/bin/python -m fbgroups.cli rescore
```

`/etc/systemd/system/fbgroups-rescore.timer`:

```ini
[Unit]
Description=fbgroups: alle 6 Stunden neu bewerten

[Timer]
OnCalendar=*-*-* 00,06,12,18:20:00
RandomizedDelaySec=600
Persistent=true

[Install]
WantedBy=timers.target
```

`RandomizedDelaySec` streut den Start: Läuft der Rescore auf die Minute genau
mit der Sicherung um 00:15 zusammen, sperren sich zwei Schreiber auf derselben
SQLite-Datei. `Persistent=true` holt einen verpassten Lauf nach, wenn der
Server aus war.

```bash
systemctl daemon-reload
systemctl enable --now fbgroups-rescore.timer
systemctl list-timers fbgroups-rescore.timer
journalctl -u fbgroups-rescore.service -n 20
```

Die Frequenz ist damit eine Zeile in der Timer-Datei. Sechs Stunden sind ein
Vorschlag, kein Zwang — der Score ändert sich nur, wenn Klicks entstehen.

Von Hand jederzeit:

```bash
fbgroups rescore --dry-run   # zeigt, wie viele Gruppen einen anderen Score bekämen
fbgroups rescore
```

## 9. Offene Punkte

- **`/events` ist angebunden** (21.08.2026). Der Weg steht öffentlich hinter
  `X-Events-Token` — nicht auf `127.0.0.1`, weil die Zielanwendung in einem
  Container läuft und `127.0.0.1` dort der Container selbst ist. Die Alternative
  wäre gewesen, den Dienst zusätzlich an das Docker-Gateway zu binden; dessen
  Adresse wechselt aber, sobald das Compose-Netz neu entsteht, und offen wäre er
  dann für jeden Container. Nachgewiesen mit einem Testereignis
  (`user_ref: test-kette-2026-08-21`): Antwort 200, Empfehlungscode vergeben.
- **Der Download-Trichter ist angebunden** (23.08.2026). `download` ist eine
  eigene Stufe, und die Zuordnung überlebt den Wechsel von der anonymen
  Browserkennung zur Benutzerkennung (`user_identities`, Schema-Version 10).
  Beim Ausrollen ist **nichts von Hand zu tun**: `MarketingStore` holt den
  Migrationsschritt beim ersten `/r/` oder `/events` selbst nach, additiv, ohne
  eine bestehende Zeile anzufassen. Bereits gezählte Ereignisse behalten ihre
  Zahlen — der Schritt legt nur eine leere Tabelle an.

  Nach dem Ausrollen prüfbar mit einem Testereignis, das die neue Naht trifft:

  ```bash
  T=$(grep '^EVENTS_TOKEN=' /opt/fbgroups/app/.env | cut -d= -f2)
  C=FB-SYR-BER-001                      # ein wirklich vergebener Code
  for J in     '{"event_type":"landing_visit","anon_ref":"probe-1","tracking_code":"'$C'"}'     '{"event_type":"registration","anon_ref":"probe-1","user_ref":"probe-user"}'     '{"event_type":"download","user_ref":"probe-user"}'
  do curl -s -X POST https://go.b-tarikak.de/events        -H 'Content-Type: application/json' -H "X-Events-Token: $T" -d "$J"; echo; done
  # Die letzte Antwort muss "tracking_code":"FB-SYR-BER-001" nennen.
  # Steht dort "", ist die Verknüpfung nicht angekommen.
  ```

  Danach `fbgroups marketing code FB-SYR-BER-001 --benutzer`; die Probezeile
  steht dort unter `probe-user`.

  **Die Probe danach wieder entfernen.** Sie schreibt drei echte Ereignisse in
  den Echtbestand und hebt die Zahlen einer wirklichen Gruppe um je eins an —
  genau die Zahlen, nach denen entschieden wird, wo die nächsten Beiträge
  hingehen. Eine Prüfung, die ihre eigenen Spuren stehen lässt, verfälscht das,
  was sie prüfen sollte:

  ```bash
  sudo -u fbgroups /opt/fbgroups/venv/bin/python - <<'EOF'
  import sqlite3
  conn = sqlite3.connect("/opt/fbgroups/app/data/groups.sqlite")
  proben = ("probe-1", "probe-user")
  for tabelle in ("tracking_events", "user_identities", "referral_codes"):
      cur = conn.execute(
          f"DELETE FROM {tabelle} WHERE user_ref IN (?,?)", proben)
      print(tabelle, cur.rowcount)
  conn.commit()
  EOF
  ```

  Erwartet: `tracking_events 3`, `user_identities 2`, `referral_codes 1` — die
  Registrierung vergibt einen Empfehlungscode, auch an eine Probe.

  **Ein Rückschritt auf die alte Fassung braucht einen Handgriff mehr.** Die
  Datei trägt danach `user_version = 10`; die alte Fassung erwartet 9, kennt
  Schritt 9 nicht und lehnt die Datei mit `SchemaVersionError` ab — der Dienst
  startet dann gar nicht. Weil der Schritt rein additiv ist (eine leere
  Tabelle, keine geänderte Zeile), genügt es, die Nummer zurückzusetzen; die
  Tabelle darf stehen bleiben und stört die alte Fassung nicht:

  ```bash
  sudo -u fbgroups /opt/fbgroups/venv/bin/python -c     "import sqlite3; c=sqlite3.connect('/opt/fbgroups/app/data/groups.sqlite');      c.execute('PRAGMA user_version = 9'); c.commit()"
  ```

  Beim erneuten Vorrollen holt der Migrationsschritt sich die Nummer selbst
  wieder; `CREATE TABLE IF NOT EXISTS` findet die Tabelle vor und lässt sie in
  Ruhe.
- **`BRAVE_API_KEY` fehlt auf dem Server.** Ohne Belang, solange `brave` in
  `providers.yaml` deaktiviert ist.
- **Der lokale Bestand ist nur noch eine Kopie.** Ein Suchlauf auf dem
  Arbeitsrechner erzeugt Codes, die der Server nicht kennt.
