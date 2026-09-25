#!/usr/bin/env bash
#
# Den Bestand vom Server holen - ab dann laeuft fbgroups ganz auf diesem
# Rechner (Entscheidung vom 25.09.2026).
#
#   bash ./umzug-lokal.sh --plan    nur nachsehen: Dienst, Bestand, nginx - nichts anfassen
#   bash ./umzug-lokal.sh           Dienst anhalten, Bestand holen, pruefen, einsetzen
#   bash ./umzug-lokal.sh --weiterleitung
#                                   nur nginx: alte Links auf die Startseite (auch danach)
#
# GIT BASH, NICHT POWERSHELL: Durch ssh fliessen binaere Stroeme, und
# PowerShell 5.1 reicht zwischen zwei Programmen Text statt Bytes weiter.
# Die Passphrase des Schluessels wird einmal gefragt, nicht bei jedem Schritt.
#
# Ohne --plan, in dieser Reihenfolge:
#   1. Hier: Laeuft ein campaign automatik oder ein Waechter? Dann Abbruch.
#   2. Server: Dienst fbgroups und Sicherungs-Timer anhalten. Ab jetzt
#      schreibt dort niemand mehr - auch /r/ und /events antworten nicht mehr.
#   3. Server: Sicherung des Bestands mit der SQLite-Schnittstelle, gepruefte
#      Kopie in /opt/fbgroups/backups/umzug-<zeit>.sqlite.
#   4. Herunterladen nach data/umzug/, Pruefsumme vergleichen.
#   5. Die bisherige data/groups.sqlite nach data/backup/ legen, die
#      heruntergeladene einsetzen und mit dem Code dieses Rechners oeffnen
#      (fehlende Migrationsschritte werden dabei nachgeholt).
#   6. Server: Dienst und Timer abschalten. Geloescht wird dort NICHTS - die
#      Datei bleibt als Rueckfallebene liegen.
#   7. Hier: erste Sicherung, Vermerk data/umgezogen.txt. Ab dann verweigert
#      dieses Skript einen zweiten Umzug.
#
# DIE ALTEN TRACKING-LINKS (go.b-tarikak.de/r/..., b-tarikak.de/t/...) stehen
# in veroeffentlichten Beitraegen. Ab Schritt 2 antwortet nginx dort mit 502,
# bis `--weiterleitung` gelaufen ist (ein eigener Schritt, jederzeit
# wiederholbar). Er ersetzt in den beiden Seiten genau vier location-Bloecke:
#   go.b-tarikak.de  /r/       -> 302 https://b-tarikak.de/home (ohne Zaehlung)
#                    /events   -> 410 (die App soll es merken, nicht 502 raten)
#                    /healthz  -> 410
#   b-tarikak.de     /t/       -> 302 /home
# Vorher werden beide Dateien nach /opt/fbgroups/backups/nginx-<zeit>/
# gesichert; scheitert das Umstellen oder `nginx -t`, kommen sie zurueck, und
# nginx wird gar nicht erst neu geladen. Alles andere in den Dateien -
# b-tarikak.de selbst, Zertifikate, api. - bleibt, wie es ist.
#
# ZURUECK, solange hier noch nichts gebucht wurde:
#   ssh -i ~/.ssh/b-tarikak_vps_new root@159.195.216.246 \
#       'systemctl enable --now fbgroups fbgroups-backup.timer'
#   und data/umgezogen.txt loeschen. Sobald der Lauf hier gebucht hat, ist
#   der Server-Bestand veraltet - dann gibt es kein Zurueck mehr ohne
#   Verlust, nur ein erneutes Hinaufladen.

set -euo pipefail

SCHLUESSEL="$HOME/.ssh/b-tarikak_vps_new"
# Als root: Dienst anhalten und abschalten darf karim nicht.
ZIEL="${FBG_ZIEL:-root@159.195.216.246}"
BESTAND="/opt/fbgroups/app/data/groups.sqlite"
FERN_PY="/opt/fbgroups/venv/bin/python"
SICHERUNGEN="/opt/fbgroups/backups"
DIENST="fbgroups"
TIMER="fbgroups-backup.timer"
NGINX_SEITEN="/etc/nginx/sites-available"
PY="${FBG_PY:-./.venv/Scripts/python.exe}"
STEMPEL="$(date +%Y-%m-%dT%H%M%S)"
export PYTHONIOENCODING=utf-8

plan=0
nur_weiterleitung=0
for arg in "$@"; do
    case "$arg" in
        --plan) plan=1 ;;
        --weiterleitung) nur_weiterleitung=1 ;;
        -h|--help) sed -n '3,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unbekannte Option: $arg" >&2; exit 2 ;;
    esac
done

cd "$(dirname "$0")"

schritt() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
fern() { ssh -i "$SCHLUESSEL" "$ZIEL" "$@"; }

# Ein Bericht ueber eine Bestandsdatei - derselbe auf dem Server und hier,
# damit sich die beiden Ausgaben Zeile fuer Zeile vergleichen lassen. Nur
# lesend (mode=ro), also auch neben dem laufenden Dienst unbedenklich.
BERICHT=$(cat <<'PY'
import sqlite3
import sys
from pathlib import Path

pfad = Path(sys.argv[1])
if not pfad.is_file():
    print(f"{pfad}: gibt es nicht")
    sys.exit(0)
conn = sqlite3.connect(f"{pfad.resolve().as_uri()}?mode=ro", uri=True)


def eins(sql):
    try:
        zeile = conn.execute(sql).fetchone()
    except sqlite3.Error as exc:
        return f"- ({exc})"
    return zeile[0] if zeile else None


print(f"{'Datei':28} {pfad}  ({pfad.stat().st_size // 1024} KB)")
for titel, sql in [
    ("Schema", "PRAGMA user_version"),
    ("Gruppen", "select count(*) from groups"),
    ("Kampagnen", "select count(*) from campaigns"),
    ("  davon aktiv", "select count(*) from campaigns where status = 'active'"),
    ("Zuordnungen", "select count(*) from campaign_groups"),
    ("Kommentare veroeffentlicht",
     "select count(*) from campaign_group_texte"
     " where texttyp = 'kommentar' and status = 'veroeffentlicht'"),
    ("Versuche", "select count(*) from post_versuche"),
    ("  davon erfolgreich", "select count(*) from post_versuche where erfolg = 1"),
    ("  der letzte", "select max(begonnen_am) from post_versuche"),
    ("Laeufe offen", "select count(*) from automatik_lauf where beendet_am is null"),
    ("Tracking-Ereignisse", "select count(*) from tracking_events"),
    ("  letzte 30 Tage",
     "select count(*) from tracking_events where occurred_at >= date('now', '-30 day')"),
    ("  das letzte", "select max(occurred_at) from tracking_events"),
]:
    print(f"{titel:28} {eins(sql)}")
PY
)

# Eine gepruefte Kopie mit der Sicherungsschnittstelle - auf dem Server
# ausgefuehrt, bei angehaltenem Dienst.
KOPIEREN=$(cat <<'PY'
import sqlite3
import sys

quelle, ziel = sys.argv[1], sys.argv[2]
von = sqlite3.connect(quelle, timeout=30)
nach = sqlite3.connect(ziel)
von.backup(nach)
nach.close()
von.close()
pruef = sqlite3.connect(ziel)
befund = pruef.execute("PRAGMA integrity_check").fetchall()
pruef.close()
print("integrity_check:", befund[0][0] if len(befund) == 1 else befund[:3])
sys.exit(0 if befund == [("ok",)] else 1)
PY
)

# Laeuft hier ein Lauf? Die Sperre gehoert dem Lauf (data/automatik.lock).
LAUF_PRUEFEN=$(cat <<'PY'
import sys

from fbgroups.config import load_config
from fbgroups.marketing import watchdog

sperre = watchdog.sperre_fuer(load_config())
if sperre.laeuft():
    gehalten = sperre.lies() or {}
    print(f"Es laeuft ein campaign automatik (PID {gehalten.get('pid', '?')}) - erst beenden.")
    sys.exit(3)
print("Kein campaign automatik.")
PY
)

# Ein Waechter haelt keine Sperre, startet aber alle paar Minuten einen Lauf
# - mit den Einstellungen, mit denen er gestartet wurde, also noch gegen den
# Server. Er muss vorher weg und danach neu gestartet werden.
waechter_pids() {
    powershell.exe -NoProfile -NonInteractive -Command \
        "Get-CimInstance Win32_Process | Where-Object { \$_.Name -like 'python*' -and \$_.CommandLine -like '*campaign watchdog*' } | ForEach-Object { \$_.ProcessId }" \
        2>/dev/null | tr -d '\r' | tr '\n' ' ' | sed 's/ *$//'
}

hier_pruefen() {
    "$PY" - <<< "$LAUF_PRUEFEN"
    local pids
    pids="$(waechter_pids || true)"
    if [ -n "$pids" ]; then
        echo "Es laeuft ein Waechter (PID $pids) - erst in seinem Fenster mit Strg+C beenden."
        return 3
    fi
    echo "Kein Waechter."
    # Haelt ein Prozess die Datei offen (die Uebersicht, fbgroups serve),
    # scheitert das Einsetzen unter Windows erst in Schritt 5 - dann stuende
    # der Dienst auf dem Server schon still. Eine geoeffnete Datei laesst
    # sich nicht umbenennen; das ist die Probe.
    if [ -f data/groups.sqlite ]; then
        if mv data/groups.sqlite data/groups.sqlite.umzugsprobe 2>/dev/null; then
            mv data/groups.sqlite.umzugsprobe data/groups.sqlite
        else
            echo "data/groups.sqlite ist geoeffnet (Uebersicht?) - erst schliessen."
            return 3
        fi
    fi
    echo "data/groups.sqlite ist frei."
}

# Den Schluessel einmal in einen Agenten laden, statt bei jedem ssh/scp nach
# der Passphrase zu fragen. Ein eigener Agent nur, wenn keiner erreichbar ist
# - und der wird am Ende wieder beendet.
agent_bereit() {
    local erreichbar=0
    ssh-add -l >/dev/null 2>&1 || erreichbar=$?
    if [ "$erreichbar" = 2 ]; then
        eval "$(ssh-agent -s)" >/dev/null
        trap 'ssh-agent -k >/dev/null 2>&1 || true' EXIT
    fi
    local abdruck
    abdruck="$(ssh-keygen -lf "$SCHLUESSEL.pub" | awk '{print $2}')"
    if ! ssh-add -l 2>/dev/null | grep -F "$abdruck" >/dev/null; then
        echo "Die Passphrase des Schluessels wird einmal gefragt:"
        ssh-add "$SCHLUESSEL"
    fi
}

# --- Die alten Links: nginx statt fbgroups ---------------------------------
#
# Ersetzt in den beiden Seiten je einen location-Block ganz - von der Zeile
# `location /r/ {` bis zu ihrer schliessenden Klammer -, statt einzelne
# Zeilen zu flicken: Was in dem Block sonst stand (proxy_set_header ...),
# gehoert zum Weiterreichen an fbgroups und faellt mit ihm weg. Erst werden
# beide Dateien fertig gerechnet, dann beide geschrieben - oder keine.
NGINX_UMSTELLEN=$(cat <<'PY'
import os
import re
import shutil
import sys
from pathlib import Path

seiten = Path(sys.argv[1])
HINWEIS = "# Seit dem Umzug (25.09.2026) ohne fbgroups-Dienst - hier wird nichts gezaehlt."
UMSTELLUNG = {
    "go.b-tarikak.de": {
        "/r/": "return 302 https://b-tarikak.de/home;",
        "/events": "return 410;",
        "/healthz": "return 410;",
    },
    "b-tarikak.de": {
        "/t/": "return 302 /home;",
    },
}
KOPF = re.compile(r"^(?P<einzug>[ \t]*)location\s+(?P<pfad>\S+)\s*\{")
# Fehlt einem Block die schliessende Klammer, liefe er bis zum Ende des
# server-Blocks weiter - und das Ersetzen naehme Zertifikat und listen mit.
FREMD = re.compile(r"^\s*(location|server|server_name|listen|ssl_\w+|include|root)\b")


def klammern(zeile):
    code = zeile.split("#", 1)[0]
    return code.count("{") - code.count("}")


def umstellen(text, bloecke):
    zeilen = text.splitlines(keepends=True)
    neu, meldungen, gefunden = [], [], set()
    i = 0
    while i < len(zeilen):
        kopf = KOPF.match(zeilen[i])
        if not kopf or kopf["pfad"] not in bloecke:
            neu.append(zeilen[i])
            i += 1
            continue
        tiefe, j = 0, i
        while j < len(zeilen):
            tiefe += klammern(zeilen[j])
            if tiefe <= 0:
                break
            j += 1
        if tiefe != 0:
            raise ValueError(f"location {kopf['pfad']} hat keine schliessende Klammer")
        if any(FREMD.match(zeile) for zeile in zeilen[i + 1:j]):
            raise ValueError(f"location {kopf['pfad']} reicht in fremde Zeilen hinein")
        pfad, ziel = kopf["pfad"], bloecke[kopf["pfad"]]
        gefunden.add(pfad)
        alt = "".join(zeilen[i:j + 1])
        if "proxy_pass" not in alt and ziel in alt:
            meldungen.append(f"{pfad}: war schon umgestellt")
            neu.extend(zeilen[i:j + 1])
        else:
            e = kopf["einzug"]
            neu += [f"{e}location {pfad} {{\n", f"{e}    {HINWEIS}\n",
                    f"{e}    {ziel}\n", f"{e}}}\n"]
            meldungen.append(f"{pfad}: -> {ziel}")
        i = j + 1
    for pfad in bloecke:
        if pfad not in gefunden:
            meldungen.append(f"{pfad}: kein solcher Block - nichts zu tun")
    ergebnis = "".join(neu)
    if sum(map(klammern, ergebnis.splitlines())) != sum(map(klammern, zeilen)):
        raise ValueError("die Klammern gehen nach dem Umstellen nicht mehr auf")
    return ergebnis, meldungen


# newline="" beim Lesen und Schreiben: Die Zeilenenden bleiben, wie sie waren.
fertig = {}
for name, bloecke in UMSTELLUNG.items():
    datei = seiten / name
    try:
        with open(datei, encoding="utf-8", newline="") as ein:
            fertig[datei] = umstellen(ein.read(), bloecke)
    except (OSError, ValueError) as exc:
        print(f"{name}: {exc} - es wird NICHTS geschrieben")
        sys.exit(1)

for datei, (text, meldungen) in fertig.items():
    for meldung in meldungen:
        print(f"{datei.name:18} {meldung}")
    zwischen = datei.with_name(f".{datei.name}.umstellen")
    with open(zwischen, "w", encoding="utf-8", newline="") as aus:
        aus.write(text)
    shutil.copymode(datei, zwischen)
    os.replace(zwischen, datei)
PY
)

# Vom Server selbst abgefragt (--resolve auf 127.0.0.1), damit das Ergebnis
# nicht am DNS dieses Rechners haengt. /r/ zaehlt hier nichts mehr: nginx
# antwortet, bevor irgendetwas bei fbgroups ankaeme.
LINKS_PRUEFEN=$(cat <<'SH'
for adresse in https://go.b-tarikak.de/r/weiterleitung-test \
               https://b-tarikak.de/t/weiterleitung-test \
               https://go.b-tarikak.de/events \
               https://go.b-tarikak.de/healthz; do
    host=${adresse#https://}; host=${host%%/*}
    antwort=$(curl -s -o /dev/null --max-time 10 -w '%{http_code} %{redirect_url}' \
              --resolve "$host:443:127.0.0.1" "$adresse" || true)
    printf '  %-44s %s\n' "$adresse" "${antwort:-keine Antwort}"
done
SH
)

weiterleitung() {
    local ablage="$SICHERUNGEN/nginx-$STEMPEL"
    schritt "nginx: alte Links auf die Startseite"
    fern "mkdir -p '$ablage' && cp -a '$NGINX_SEITEN/go.b-tarikak.de' '$NGINX_SEITEN/b-tarikak.de' '$ablage/' && echo 'Gesichert nach $ablage'"
    if fern "$FERN_PY - '$NGINX_SEITEN'" <<< "$NGINX_UMSTELLEN" && fern "nginx -t"; then
        fern "systemctl reload nginx && echo 'nginx neu geladen.'"
        echo "So antwortet es jetzt:"
        fern "bash -s" <<< "$LINKS_PRUEFEN"
        return 0
    fi
    echo "Umstellen oder 'nginx -t' gescheitert - die gesicherten Dateien kommen zurueck."
    echo "nginx wurde nicht neu geladen und laeuft mit der alten Fassung weiter."
    fern "cp -a '$ablage/go.b-tarikak.de' '$ablage/b-tarikak.de' '$NGINX_SEITEN/' && nginx -t" || true
    return 1
}

# Unabhaengig vom Umzug: vorher, nachher, beliebig oft.
if [ "$nur_weiterleitung" = 1 ]; then
    agent_bereit
    weiterleitung
    exit 0
fi

if [ -f data/umgezogen.txt ]; then
    echo "Der Umzug ist schon gelaufen:"
    sed 's/^/  /' data/umgezogen.txt
    echo "Ein zweiter Umzug holte den veralteten Server-Bestand zurueck. Abbruch."
    exit 2
fi

# --- Plan: nur nachsehen ---------------------------------------------------
if [ "$plan" = 1 ]; then
    schritt "Hier"
    git status --short || true
    echo "Commit: $(git rev-parse --short HEAD 2>/dev/null || echo 'kein git')"
    hier_pruefen || true
    "$PY" - data/groups.sqlite <<< "$BERICHT"

    agent_bereit

    schritt "Server: Dienst und Timer"
    fern "for u in $DIENST $TIMER; do printf '%-24s %-10s %s\n' \$u \$(systemctl is-active \$u 2>/dev/null) \$(systemctl is-enabled \$u 2>/dev/null); done" || true

    schritt "Server: Bestand (nur gelesen)"
    fern "$FERN_PY - '$BESTAND'" <<< "$BERICHT"

    schritt "Server: nginx - was auf fbgroups (127.0.0.1:8090) zeigt"
    # Nur die tragenden Zeilen. proxy_set_header bleibt draussen: Dort steht
    # das Geheimnis der schreibgeschuetzten Uebersicht. -R statt -r, weil
    # sites-enabled aus Verweisen auf sites-available besteht.
    fern "grep -RlE '8090|go\\.b-tarikak' /etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null | while read -r f; do echo \"--- \$f -> \$(readlink -f \"\$f\")\"; grep -nE 'server_name|listen|location|proxy_pass|return|auth_basic|include|ssl_certificate ' \"\$f\"; done" || true

    cat <<'ENDE'

Nichts angefasst. Die nginx-Zeilen oben braucht es fuer die Weiterleitung
der alten Links auf die Startseite - bitte an Claude weitergeben.
Der eigentliche Umzug:  bash ./umzug-lokal.sh
ENDE
    exit 0
fi

# --- 1. Hier -------------------------------------------------------------
schritt "1/7  Laeuft hier noch etwas?"
hier_pruefen
if [ -n "$(git status --short -- src config 2>/dev/null)" ]; then
    echo "Hinweis: src/ oder config/ haben unversionierte Aenderungen - der Bestand"
    echo "wird mit dem Code geoeffnet, der hier liegt."
fi

agent_bereit
FERN_KOPIE="$SICHERUNGEN/umzug-$STEMPEL.sqlite"
LOKAL_KOPIE="data/umzug/groups-vom-server-$STEMPEL.sqlite"

printf '\nDer Dienst auf dem Server wird angehalten. Ab dann zaehlt /r/ nichts mehr,\n'
printf 'und /events nimmt nichts mehr an. Weiter? [j/N] '
read -r antwort
case "$antwort" in
    j|J|ja|Ja) ;;
    *) echo "Nichts angefasst."; exit 1 ;;
esac

# --- 2. Server anhalten ------------------------------------------------------
schritt "2/7  Server: Dienst und Sicherungs-Timer anhalten"
# Scheitert das Anhalten des Dienstes, bricht das Skript hier ab: Eine Kopie
# neben einem Dienst, der weiter bucht, waere schon beim Herunterladen alt.
fern "systemctl stop $DIENST"
fern "systemctl stop $TIMER 2>/dev/null || echo '(kein $TIMER)'; printf '%s: %s\n' $DIENST \$(systemctl is-active $DIENST 2>/dev/null)" || true

# --- 3. Sichern ----------------------------------------------------------
schritt "3/7  Server: gepruefte Kopie des Bestands"
fern "mkdir -p '$SICHERUNGEN' && $FERN_PY - '$BESTAND' '$FERN_KOPIE'" <<< "$KOPIEREN"
fern_summe="$(fern "sha256sum '$FERN_KOPIE'" | awk '{print $1}')"
fern "$FERN_PY - '$FERN_KOPIE'" <<< "$BERICHT"

# --- 4. Holen ------------------------------------------------------------
schritt "4/7  Herunterladen"
mkdir -p data/umzug
scp -i "$SCHLUESSEL" "$ZIEL:$FERN_KOPIE" "$LOKAL_KOPIE"
lokal_summe="$(sha256sum "$LOKAL_KOPIE" | awk '{print $1}')"
if [ "$lokal_summe" != "$fern_summe" ]; then
    echo "Pruefsummen weichen ab (Server $fern_summe, hier $lokal_summe). Abbruch - hier ist nichts ersetzt."
    echo "Der Dienst auf dem Server steht still; wieder an:  ssh $ZIEL 'systemctl start $DIENST $TIMER'"
    exit 4
fi
echo "Pruefsumme stimmt: $lokal_summe"

# --- 5. Einsetzen --------------------------------------------------------
schritt "5/7  Hier einsetzen"
mkdir -p data/backup
for datei in data/groups.sqlite data/groups.sqlite-journal data/groups.sqlite-wal data/groups.sqlite-shm; do
    if [ -f "$datei" ]; then
        # mv scheitert, solange ein Prozess die Datei offen haelt (die
        # Uebersicht) - dann bricht das Skript hier ab, bevor ersetzt wird.
        mv "$datei" "data/backup/$(basename "$datei").vor-umzug-$STEMPEL"
        echo "beiseitegelegt: $datei -> data/backup/"
    fi
done
cp "$LOKAL_KOPIE" data/groups.sqlite

"$PY" - <<'PY'
from fbgroups.config import load_config
from fbgroups.marketing.store import MarketingStore
from fbgroups.storage import SqliteStore

pfad = load_config().path("sqlite_path")
# Beide Speicher einmal oeffnen: Fehlt ein Migrationsschritt, holt der Code
# dieses Rechners ihn jetzt nach - und nicht erst mitten im ersten Lauf.
SqliteStore(pfad).close()
MarketingStore(pfad).close()
print(f"Geoeffnet mit dem Code dieses Rechners: {pfad}")
PY
"$PY" - data/groups.sqlite <<< "$BERICHT"

# --- 6. Server abschalten --------------------------------------------------
schritt "6/7  Server: Dienst und Timer abschalten (geloescht wird nichts)"
fern "systemctl disable $DIENST 2>&1 | tail -1; systemctl disable $TIMER 2>/dev/null || true; printf '%s: %s / %s\n' $DIENST \$(systemctl is-active $DIENST 2>/dev/null) \$(systemctl is-enabled $DIENST 2>/dev/null)" || true

# --- 7. Hier sichern und vermerken -----------------------------------------
schritt "7/7  Erste Sicherung hier"
"$PY" -m fbgroups.cli sicherung
{
    echo "Umgezogen am $(date '+%d.%m.%Y %H:%M')"
    echo "Quelle:     $ZIEL:$BESTAND"
    echo "Kopie dort: $FERN_KOPIE"
    echo "Kopie hier: $LOKAL_KOPIE"
    echo "SHA-256:    $lokal_summe"
} > data/umgezogen.txt

cat <<ENDE

Umgezogen. Der Bestand liegt jetzt in data/groups.sqlite.

Starten (PowerShell):
  \$py = ".\\.venv\\Scripts\\python.exe"; \$env:PYTHONIOENCODING = "utf-8"
  & \$py -m fbgroups.cli campaign watchdog           # Lauf + taegliche Sicherung
  & \$py -m fbgroups.cli serve --port 8090            # Uebersicht: http://127.0.0.1:8090/

Protokoll: data/logs/   Sicherungen: data/backups/ und ~/fbgroups-sicherung

Noch offen:
  * Die alten Links (go.b-tarikak.de/r/..., b-tarikak.de/t/...) antworten mit
    502, bis nginx sie auf die Startseite weiterleitet:
      bash ./umzug-lokal.sh --weiterleitung
  * Die App (api.b-tarikak.de) meldet vielleicht noch an /events - dort
    abschalten.
ENDE
