#!/usr/bin/env bash
#
# Aenderungen auf den Server bringen und den Dienst neu starten.
#
#   ./ausrollen.sh              uebertragen, einsetzen, neu starten
#   ./ausrollen.sh --plan       nur zeigen, was liefe - nichts anfassen
#   ./ausrollen.sh --pip        zusaetzlich die Abhaengigkeiten erneuern
#   ./ausrollen.sh --test       vorher die Testreihe laufen lassen
#
# GIT BASH, NICHT POWERSHELL. Der Kern ist `tar czf - | ssh` - ein binaerer
# Strom durch eine Rohrleitung. PowerShell 5.1 reicht zwischen zwei nativen
# Programmen keine Bytes weiter, sondern Text: Es wandelt die Ausgabe in
# Zeichen um und wieder zurueck. Das Archiv kommt beschaedigt an, und zwar
# ohne Fehlermeldung - `tar xzf` meldet dann irgendetwas ueber ein
# unerwartetes Dateiende. Aus PowerShell heraus deshalb:
#
#   bash ./ausrollen.sh
#
# UEBERTRAGEN WIRD NUR CODE. `.env` und `data/` sind nicht Teil des Archivs
# und werden auf dem Server nie angefasst: Dort liegen der Schluessel, der
# Bestand und die gezaehlten Klicks. Der Bestand auf dem Server ist die
# einzige gueltige Fassung (siehe docs/plan-go-subdomain.md, Abschnitt 2) -
# ein Ausrollen, das ihn ueberschreibt, kostet jeden Klick seit der letzten
# Sicherung.
#
# DIE UEBERSICHT ERREICHT MAN UEBER EINEN SSH-TUNNEL. Der Dienst horcht auf
# 127.0.0.1:8090 und ist von aussen nur lesend zu haben:
#
#   ssh -i ~/.ssh/b-tarikak_vps_new -L 8090:127.0.0.1:8090 \
#       -o ServerAliveInterval=30 karim@159.195.216.246
#
# Der Tunnel lebt, solange das Fenster offen ist, und braucht keinen Neustart
# des Dienstes. Der frueher noetige Rueckwaertstunnel auf 11434 (Ollama) ist
# entfallen - es gibt keine KI mehr im Projekt.

set -euo pipefail

SCHLUESSEL="$HOME/.ssh/b-tarikak_vps_new"
# Als root, nicht als karim: Jeder Schritt unten braucht Rechte, die karim
# nicht hat ("user karim is not allowed to execute /usr/bin/chown ... as
# root"). Der Benutzer hier ueberschreibt bewusst das `User karim` aus
# ~/.ssh/config; die uebrigen Angaben von dort (IdentityFile, IdentitiesOnly)
# gelten weiter, denn sie haengen am Host und nicht am Benutzer.
#
# Sauberer waere ein eigener Benutzer mit genau diesen Rechten in sudoers,
# statt einer root-Anmeldung ueber SSH. Dann genuegt:
#   FBG_ZIEL=deploy@159.195.216.246 bash ./ausrollen.sh
ZIEL="${FBG_ZIEL:-root@159.195.216.246}"
APP="/opt/fbgroups/app"
NEU="/tmp/fbgroups-neu"
VORHER="/opt/fbgroups/vorher"
DIENST="fbgroups"

plan=0; mit_pip=0; mit_test=0
for arg in "$@"; do
    case "$arg" in
        --plan)  plan=1 ;;
        --pip)   mit_pip=1 ;;
        --test)  mit_test=1 ;;
        -h|--help) sed -n '3,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unbekannte Option: $arg" >&2; exit 2 ;;
    esac
done

cd "$(dirname "$0")"

schritt() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# --- 0. Was geht hinaus? ---------------------------------------------------
schritt "Stand"
git status --short || true
echo "Commit: $(git rev-parse --short HEAD 2>/dev/null || echo 'kein git')"

if [ "$mit_test" = 1 ]; then
    schritt "Testreihe"
    PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m pytest -q
fi

if [ "$plan" = 1 ]; then
    schritt "Plan - es wird nichts uebertragen"
    tar czf - --exclude='__pycache__' --exclude='*.pyc' --exclude='*.egg-info' \
        src config pyproject.toml | wc -c | awk '{print "Archivgroesse: " $1 " Bytes"}'
    echo "Ziel:    $ZIEL:$APP"
    echo "Danach:  systemctl restart $DIENST"
    [ "$mit_pip" = 1 ] && echo "Ausserdem: pip install -e '$APP[web]'"
    exit 0
fi

# --- 1. Code uebertragen ---------------------------------------------------
# Nach /tmp, nicht direkt nach /opt: Bricht die Uebertragung auf halber
# Strecke ab, steht der Dienst sonst auf einem halben Code - und der naechste
# Neustart faellt auf die Nase. Erst wenn das Archiv vollstaendig ausgepackt
# ist, wird eingesetzt.
schritt "1/4  Code uebertragen"
tar czf - --exclude='__pycache__' --exclude='*.pyc' --exclude='*.egg-info' \
    src config pyproject.toml \
| ssh -i "$SCHLUESSEL" "$ZIEL" \
    "rm -rf $NEU && mkdir -p $NEU && tar xzf - -C $NEU && echo CODE-OK"

# --- 2. Einsetzen, pruefen, neu starten ------------------------------------
# `sudo` braucht ein Terminal fuer die Passwortfrage, also `ssh -t`. Das
# schliesst aber aus, das Skript ueber die Standardeingabe zu schicken
# (`bash -s <<REMOTE`): `-t` verlangt, dass stdin selbst ein Terminal ist, und
# meldet sonst "Pseudo-terminal will not be allocated" - danach steht sudo
# ohne Terminal da. Beides zusammen geht nicht.
#
# Deshalb zwei Verbindungen: Die erste legt das Skript als Datei ab, die
# zweite fuehrt es mit echtem Terminal aus. `-tt` waere die kuerzere Antwort
# und die schlechtere - der Text liefe dann durch die Pseudo-Terminalschicht,
# die jede Zeile zurueckwirft und CR anhaengt, bis bash ueber `$'\r'`
# stolpert.
schritt "2/4  Einsetzen und Dienst neu starten"
FERN="/tmp/fbgroups-einsetzen.sh"
EINSETZEN=$(cat <<REMOTE
set -euo pipefail
APP=$APP; NEU=$NEU; VORHER=$VORHER; DIENST=$DIENST

sudo chown -R fbgroups:fbgroups "\$NEU"

# Genau einen Stand zurueck aufheben. Mehr braucht es nicht - alles
# Aeltere steht im Repository; das hier ist fuer den Fall, dass der Dienst
# in der naechsten Minute nicht hochkommt.
sudo rm -rf "\$VORHER"
sudo -u fbgroups mkdir -p "\$VORHER"
sudo cp -a "\$APP/src" "\$APP/config" "\$APP/pyproject.toml" "\$VORHER/"

# Ersetzen statt daruebergekopieren: Eine geloeschte Datei bliebe sonst
# liegen und wuerde weiter importiert.
sudo rm -rf "\$APP/src" "\$APP/config"
sudo cp -a "\$NEU/src" "\$NEU/config" "\$NEU/pyproject.toml" "\$APP/"
sudo chown -R fbgroups:fbgroups "\$APP/src" "\$APP/config" "\$APP/pyproject.toml"
rm -rf "\$NEU"

if [ "\$MIT_PIP" = 1 ]; then
    sudo -u fbgroups /opt/fbgroups/venv/bin/pip install -q -e "\$APP[web]"
fi

# Vor dem Neustart, nicht danach: Eine kaputte Konfiguration soll auffallen,
# solange die alte Fassung noch laeuft.
sudo -u fbgroups /opt/fbgroups/venv/bin/python -m fbgroups.cli config-check

sudo systemctl restart "\$DIENST"
sleep 2
sudo systemctl is-active "\$DIENST"
REMOTE
)

printf '%s\n' "$EINSETZEN" | ssh -i "$SCHLUESSEL" "$ZIEL" "cat > $FERN"
# Der Rueckgabewert muss durch, sonst liefe der Rest weiter, obwohl der Dienst
# gar nicht hochkam - `rm` allein setzte ihn auf 0.
ssh -t -i "$SCHLUESSEL" "$ZIEL" \
    "MIT_PIP=$mit_pip bash $FERN; ergebnis=\$?; rm -f $FERN; exit \$ergebnis"

# --- 3. Laeuft es wirklich? ------------------------------------------------
# `is-active` sagt nur, dass der Prozess lebt. Ob er antwortet, sagt allein
# eine Anfrage.
schritt "3/4  Antwortet der Dienst?"
ssh -i "$SCHLUESSEL" "$ZIEL" \
    "curl -s -o /dev/null -w 'healthz: %{http_code}\n' http://127.0.0.1:8090/healthz"

schritt "4/4  Letzte Zeilen aus dem Protokoll"
ssh -i "$SCHLUESSEL" "$ZIEL" "sudo journalctl -u $DIENST -n 15 --no-pager"

cat <<'ENDE'

Fertig.

Zurueck geht es mit dem beiseitegelegten Stand:
  ssh -t -i ~/.ssh/b-tarikak_vps_new karim@159.195.216.246 \
    'sudo rm -rf /opt/fbgroups/app/src /opt/fbgroups/app/config \
     && sudo cp -a /opt/fbgroups/vorher/. /opt/fbgroups/app/ \
     && sudo chown -R fbgroups:fbgroups /opt/fbgroups/app \
     && sudo systemctl restart fbgroups'

Nach Aenderungen an Gewichten oder Klassifikation ausserdem:
  sudo -u fbgroups /opt/fbgroups/venv/bin/python -m fbgroups.cli rescore
ENDE
