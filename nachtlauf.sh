#!/usr/bin/env bash
# Nachtlauf - haelt `campaign automatik` ueber Nacht am Leben.
#
# Am 22.09.2026 auf Wunsch des Nutzers entstanden ("leave it working all
# night, each 60 min check if its not working correct it").
#
# Er macht GENAU DREI Dinge und sonst nichts:
#
#   1. Alle 5 Min: laeuft kein Lauf, wird einer fortgesetzt
#      (`campaign automatik`, ohne --neu - der offene Lauf behaelt
#       seinen Fortschritt).
#   2. Hoechstens **einmal je Stunde**: ist der Lauf durch, weil alle
#      Gruppen beiseitegelegt sind, wird mit `--neu` eine frische Liste
#      eingefroren. Haeufiger waere schaedlich - die eben uebersprungenen
#      Gruppen kaemen sofort zurueck, und genau davor warnt der eingebaute
#      Waechter.
#   3. Eine verwaiste Browsersperre wird geloest - aber nur, wenn
#      nachweislich kein Lauf mehr laeuft. Das ist der Fehler, der heute
#      zweimal aufgetreten ist: Chrome haelt `SingletonLock`, und jeder
#      Start scheitert mit TargetClosedError.
#
# Was er ausdruecklich NICHT tut - dieselbe Zusage wie beim eingebauten
# Waechter (`marketing/watchdog.py`): keine Kampagnenlogik. Er legt keine
# Kampagne an, setzt keine auf `completed`, aendert keine Konfiguration,
# keine Schwelle und keine Tagesmenge. Alle Grenzen (Tagesmengen, Takt,
# Sperren) liegen in `campaign automatik` selbst und gelten unveraendert.
#
# Beenden:  kill $(cat data/nachtlauf.pid)

set -uo pipefail

WURZEL="/home/k/Facebook_group_daschbord"
PY="$WURZEL/.venv/bin/python"
LOG="$WURZEL/data/nachtlauf.log"
PIDDATEI="$WURZEL/data/nachtlauf.pid"

ABSTAND=300           # 5 Min zwischen zwei Blicken
NEU_ABSTAND=3600      # fruehestens jede Stunde eine frische Liste

cd "$WURZEL" || exit 1
export PYTHONIOENCODING=utf-8
export DISPLAY="${DISPLAY:-:0.0}"

echo $$ > "$PIDDATEI"

sage() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# **Die Sperre des Projekts, nicht die Prozessliste.** Der erste Wurf fragte
# `pgrep -f "fbgroups.cli campaign automatik"` - und traf damit jede
# Diagnosezeile, in der derselbe Text vorkam. In der Nacht vom 22. auf den
# 23.09.2026 meldete der Nachtlauf deshalb "laeuft - nichts zu tun",
# waehrend Lauf 5 laengst auf `angehalten` stand: eine halbe Stunde, in der
# nichts geschah und niemand etwas merkte.
#
# `watchdog.Sperre` beantwortet genau die gestellte Frage, und ihr eigener
# Docstring nennt den Grund: In der Prozessliste steht, wie ein Prozess
# *heisst*, nicht was er *tut*. Sie prueft zusaetzlich, ob die eingetragene
# Kennung noch lebt - ein Absturz sperrt den naechsten Lauf also nicht aus.
laeuft() {
  "$PY" - <<'PY' 2>/dev/null
import sys
from fbgroups.config import load_config
from fbgroups.marketing.watchdog import sperre_fuer
sys.exit(0 if sperre_fuer(load_config()).laeuft() else 1)
PY
}

# Nur echte Browserprozesse. Gezaehlt wird ueber den **Prozessnamen**
# (`comm`), nicht ueber die Kommandozeile: Jede Zeile, die das Suchmuster
# nur erwaehnt - eine Diagnose, dieses Skript selbst -, traegt es auch in
# ihrer eigenen Kommandozeile, und `grep`/`pgrep -f` zaehlen sie mit.
# Derselbe Fehler wie bei `laeuft()`, eine Ebene tiefer.
chrome_prozesse() {
  ps -eo pid=,comm=,args= \
    | awk -v d="data/browser_state" '$2=="chrome" && index($0,d)>0 {n++} END{print n+0}'
}

# Wie viele Kommentare stehen bisher? Die **gebuchte Wirkung**, nicht eine
# Schaetzung darueber, ob noch Arbeit dalaege.
#
# Hier stand bis zum 23.09.2026 `offene_gruppen()`: zugeordnete minus
# beiseitegelegte Gruppen. Die Rechnung ging 17-16=1 aus, also "es ist noch
# etwas offen", also fortsetzen statt `--neu` - und der fortgesetzte Lauf
# war nach drei Sekunden wieder zu Ende, weil jene eine Gruppe in Wahrheit
# nichts hergab. Da die Zahl nie 0 wurde, ist der `--neu`-Zweig in einer
# ganzen Nacht **kein einziges Mal** gelaufen: 80 Starts, 80 Sofortenden,
# ein Kommentar.
#
# Die Lehre ist dieselbe, die schon `laeuft()` gekostet hat: nicht ableiten,
# was sich messen laesst. Gezaehlt wird jetzt, was wirklich gebucht wurde.
kommentare_gesamt() {
  "$PY" - <<'PY' 2>/dev/null || echo 0
import sqlite3
c = sqlite3.connect("data/groups.sqlite")
print(c.execute(
    "SELECT COUNT(*) FROM post_versuche WHERE texttyp='kommentar' AND erfolg=1"
).fetchone()[0])
PY
}

sage "=== Nachtlauf gestartet (PID $$) ==="
letztes_neu=0
runde_leer=0

while true; do
  if laeuft; then
    sage "laeuft - nichts zu tun"
  else
    # Verwaiste Sperre? Nur loesen, wenn wirklich kein Lauf mehr da ist.
    n=$(chrome_prozesse)
    if [ "$n" -gt 0 ]; then
      sage "WARNUNG: $n Chrome-Prozesse ohne Lauf - verwaiste Sperre, wird geloest"
      pkill -TERM -f "chrome.*browser_state" 2>/dev/null
      sleep 15
      sage "  danach noch $(chrome_prozesse) Prozesse"
    fi

    jetzt=$(date +%s)

    # **Die Uhr entscheidet, nicht eine Schaetzung ueber offene Arbeit.**
    # Ist die Stunde um, bekommt der Lauf eine frische Liste; dazwischen
    # wird fortgesetzt. Damit kann der Zustand "fortsetzen bringt nichts"
    # hoechstens eine Stunde bestehen, statt eine ganze Nacht.
    if [ $((jetzt - letztes_neu)) -ge "$NEU_ABSTAND" ]; then
      art="--neu"; sage "Stunde um - frische Liste mit --neu"
      letztes_neu=$jetzt
      runde_leer=0
    elif [ "$runde_leer" = "1" ]; then
      # **Die Runde ist durch - Nachsehen kostet einen Browserstart.**
      # Am 23.09.2026 beobachtet: zwischen zwei `--neu` startete der
      # Nachtlauf elfmal je Stunde einen Lauf, der nach drei Sekunden
      # ohne Ergebnis endete. Elf Browserfenster fuer nichts; gegenueber
      # Facebook ist das ausserdem ein Muster, das niemand erzeugen will.
      # Gewartet wird jetzt auf die volle Stunde - dann faellt die Liste
      # ohnehin frisch.
      rest=$(( (NEU_ABSTAND - (jetzt - letztes_neu)) / 60 ))
      sage "Runde durch - warte auf --neu (in ${rest} Min)"
      sleep "$ABSTAND"; continue
    else
      rest=$(( (NEU_ABSTAND - (jetzt - letztes_neu)) / 60 ))
      art="fortsetzen"; sage "kein Lauf - wird fortgesetzt (--neu in ${rest} Min)"
    fi

    vorher=$(kommentare_gesamt)
    start=$(date +%s)
    if [ "$art" = "--neu" ]; then
      "$PY" -m fbgroups.cli campaign automatik --neu >> "$LOG" 2>&1
    else
      "$PY" -m fbgroups.cli campaign automatik >> "$LOG" 2>&1
    fi
    ende=$?
    dauer=$(( $(date +%s) - start ))
    nachher=$(kommentare_gesamt)
    zuwachs=$((nachher - vorher))
    sage "Lauf beendet (Exit $ende, ${dauer}s) - Kommentare: $vorher -> $nachher (+$zuwachs)"

    # Sofort zu Ende und nichts gebucht = die Runde gibt nichts mehr her.
    if [ "$zuwachs" -eq 0 ] && [ "$dauer" -lt 30 ]; then
      runde_leer=1
      sage "  -> Runde erschoepft, bis zum naechsten --neu wird nicht mehr gestartet"
    else
      runde_leer=0
    fi
  fi
  sleep "$ABSTAND"
done
