"""Der Waechter: sorgt dafuer, dass **ein** Lauf laeuft - und sonst nichts.

## Was dieses Modul ist

Die Antwort auf eine Frage, die mit der Kampagne nichts zu tun hat: *Laeuft
sie ueberhaupt noch?* Bis zum 15.09.2026 musste ein Mensch das beantworten,
indem er alle paar Stunden nachsah und den Befehl erneut eintippte - und ein
Lauf, der um drei Uhr nachts an einem Sitzungsfehler endete, stand bis zum
Morgen still.

## Was es ausdruecklich **nicht** ist

Kein zweiter Kampagnenablauf. Hier steht keine Warteschlange, keine
Rangfolge, kein Takt und keine Entscheidung ueber eine Gruppe - all das
bleibt in ``lauf.py`` und ``automatik.py``. Der Waechter startet den
vorhandenen Befehl und sieht ihm beim Leben zu; was dieser Befehl tut, geht
ihn nichts an.

Das ist keine Sparsamkeit, sondern die Bedingung dafuer, dass er
ungefaehrlich ist: Ein Waechter, der selbst entscheiden koennte, was gepostet
wird, waere ein zweiter Runner mit eigener Zaehlweise - und zwei Zaehlweisen
fuer dieselben Kommentare sind genau das, was dieses Projekt an mehreren
Stellen teuer bezahlt hat.

Daraus folgt unmittelbar, was er **nie** tut (Punkte 8-10 der Anforderung):

* Er legt **keine Kampagne an** - er startet einen Befehl, der keine anlegt.
* Er setzt **nichts auf ``completed``** - dafuer gibt es genau eine Stelle
  (``automatik._stand_fortschreiben``), und sie verlangt ein erreichtes Ziel.
* Er beginnt **nichts von vorn** - ``campaign automatik`` ohne ``--neu``
  nimmt ueber ``offener_lauf`` den bestehenden Lauf wieder auf, mit seiner
  eingefrorenen Kampagnenliste und seinem Fortschritt.

Nachpruefbar ist das an einer Stelle: ``baue_befehl`` - was dort nicht
dransteht, kann nicht geschehen.

## Die Sperre gehoert dem Lauf, nicht dem Waechter

``campaign automatik`` nimmt sie selbst (``Sperre.nimm``). Das ist der
Unterschied zwischen "der Waechter startet keinen zweiten" und "es **gibt**
keinen zweiten": Auch ein von Hand gestarteter Lauf haelt sie, und der
Waechter sieht ihn. Andersherum - der Waechter merkt sich seine eigenen
Kinder - waere ein Lauf im zweiten Fenster unsichtbar geblieben, und zwei
Browser haetten in derselben Gruppe gearbeitet.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

#: Wie lange ein Lauf schweigen darf, bevor die Sperre als verwaist gilt.
#: Grosszuegig, weil ein Lauf zwischen zwei Schritten bis zu einer
#: Viertelstunde schlaeft (``automatik.wartesekunden``) und der Beitragstakt
#: noch laenger sein kann. Die Sperre haengt ohnehin in erster Linie an der
#: Prozesskennung; das hier faengt nur den Fall ab, dass die Kennung nach
#: einem Neustart des Rechners an ein fremdes Programm weitergegeben wurde.
VERWAIST_NACH_STUNDEN = 12

#: Vorgabe, wenn ``settings.yaml`` nichts sagt. Fuenf Minuten sind der
#: Kompromiss aus "merkt einen Absturz schnell" und "steht nicht staendig im
#: Weg": Ein Lauf, der gerade seinen Takt abwartet, haelt die Sperre, also
#: kostet ein Blick alle fuenf Minuten nichts.
VORGABE_ABSTAND = 300


@dataclass(frozen=True)
class Tunnel:
    """Der SSH-Tunnel, durch den der Fernbetrieb spricht.

    **Warum der Waechter ihn kennt.** Bis zum 20.09.2026 stand in seinem
    Protokoll nur *"Dienst antwortet nicht - Laeuft der SSH-Tunnel?"*, und
    die Antwort darauf war jedes Mal ein Mensch, der ein zweites Fenster
    oeffnete und denselben Befehl eintippte. Ein Waechter, der einen
    abgestuerzten Lauf neu startet, aber vor einem geschlossenen Tunnel die
    Haende hebt, loest genau die Haelfte des Problems - nachts die falsche.

    Das ist **keine** Kampagnenlogik: Hier steht ein Port und ein Ziel, kein
    Beitrag, keine Gruppe und keine Zaehlung. Der Waechter prueft den Port
    ohnehin schon (``dienst_erreichbar``); neu ist allein, dass er ihn auch
    aufmachen darf.

    **Das Kennwort des Schluessels steht hier nicht** - weder hier noch in
    ``settings.yaml``, noch in einer ``.env``. ``ssh`` nimmt es ohnehin nicht
    auf der Kommandozeile entgegen, und ein Kennwort in einer Datei neben dem
    Bestand waere ein Schluessel ohne Schloss. Zwei Wege bleiben, und beide
    sind Sache des Rechners, nicht dieses Programms:

    * ``ssh-add <schluessel>`` einmal ausfuehren (der Agent von Windows
      behaelt ihn ueber den Neustart) - dann fragt niemand mehr.
    * Sonst fragt ``ssh`` im Terminal, in dem der Waechter laeuft. Deshalb
      bleiben seine Stroeme sichtbar (``starte_prozess``).
    """

    aktiv: bool = False
    ziel: str = ""
    schluessel: str = ""
    port: int = 0
    fernport: int = 0
    fernziel: str = "127.0.0.1"
    lebenszeichen: int = 30

    @property
    def nutzbar(self) -> bool:
        """Ohne Ziel und ohne Port gibt es nichts aufzumachen."""
        return bool(self.aktiv and self.ziel and self.port)


@dataclass(frozen=True)
class Einstellungen:
    """Was der Waechter aus ``settings.yaml`` liest - und sonst nichts.

    Getrennt von der Schleife, damit diese ohne Konfiguration pruefbar
    bleibt: Ein Test baut sich die drei Werte in einer Zeile zusammen.
    Dieselbe Aufteilung wie bei ``grenzen.Grenzen`` / ``grenzen.einstellungen``.
    """

    aktiv: bool = True
    abstand_sekunden: int = VORGABE_ABSTAND
    server: str = ""
    tunnel: Tunnel = Tunnel()

    @property
    def abstand(self) -> float:
        """Nie unter dreissig Sekunden.

        Ein Waechter, der im Sekundentakt nachsieht, ist kein Waechter,
        sondern eine Last - und bei einem abgestuerzten Lauf startete er ihn
        sechzigmal in der Minute. Die Untergrenze steht hier und nicht in der
        Konfiguration: Sie schuetzt vor einem Tippfehler, nicht vor einer
        Entscheidung.
        """
        return float(max(self.abstand_sekunden, 30))


def einstellungen(config) -> Einstellungen:  # noqa: ANN001 - AppConfig
    """Die einzige Stelle dieses Moduls, die die Konfiguration kennt."""
    block = config.get("watchdog", default={}) or {}
    roh = block.get("tunnel", {}) or {}
    port = int(roh.get("port", 0) or 0)
    return Einstellungen(
        aktiv=bool(block.get("enabled", True)),
        abstand_sekunden=int(block.get("check_interval_seconds", VORGABE_ABSTAND) or 0),
        server=str(block.get("server", "") or "").strip(),
        tunnel=Tunnel(
            aktiv=bool(roh.get("enabled", False)),
            ziel=str(roh.get("ziel", "") or "").strip(),
            schluessel=str(roh.get("schluessel", "") or "").strip(),
            port=port,
            fernport=int(roh.get("fernport", 0) or 0) or port,
            fernziel=str(roh.get("fernziel", "") or "127.0.0.1").strip(),
            lebenszeichen=int(roh.get("lebenszeichen", 30) or 30),
        ),
    )


# --- Die Sperre -----------------------------------------------------------

def _lebt(pid: int) -> bool:
    """Gibt es diesen Prozess noch? Ohne zusaetzliche Abhaengigkeit.

    Auf Windows ueber ``OpenProcess``, sonst ueber das Signal 0 - beides
    fragt nur nach, ohne etwas zu tun. ``psutil`` waere die bequemere
    Antwort und ein Paket mehr; dieselbe Ueberlegung wie bei ``webbrowser``
    und der Zwischenablage in ``beitrag.py``.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # PROCESS_QUERY_LIMITED_INFORMATION - genug, um den Ausgangswert zu
        # lesen, und ohne Rechte am fremden Prozess.
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            # **``OpenProcess`` allein genuegt nicht.** Solange irgendwo noch
            # ein Handle auf einen beendeten Prozess offen ist, bleibt seine
            # Kennung gueltig, und das Oeffnen gelingt - der Prozess ist dann
            # laengst tot. Ein Waechter, der das glaubt, startet nach einem
            # Absturz nie wieder. Die Wahrheit steht im Ausgangswert:
            # ``STILL_ACTIVE`` (259) heisst "laeuft noch", alles andere ist
            # der Ausgangswert eines beendeten.
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Es gibt ihn, er gehoert nur jemand anderem.
        return True
    return True


@dataclass(frozen=True)
class Sperre:
    """Genau ein Lauf zur selben Zeit - ueber Prozessgrenzen hinweg.

    Eine Datei mit Prozesskennung und Zeitpunkt. Sie liegt neben dem
    Bestand, nicht im Code: Wer zwei Bestaende faehrt, faehrt zwei Laeufe,
    und das ist richtig so.

    **Warum nicht einfach die Prozessliste durchsuchen?** Weil dort steht,
    was ein Prozess *heisst*, nicht was er *tut*: ``python -m fbgroups.cli``
    trifft auch ``campaign text`` und jeden anderen Befehl. Eine Sperre, die
    der Lauf selbst nimmt, sagt genau das, wonach gefragt ist.
    """

    pfad: Path

    def lies(self) -> dict | None:
        """Was in der Sperre steht - oder ``None``, wenn keine gilt.

        Eine Datei, deren Prozess nicht mehr lebt oder die seit Stunden
        niemand angefasst hat, gilt als verwaist: Ein Absturz soll den
        naechsten Lauf nicht fuer immer aussperren.
        """
        try:
            roh = json.loads(self.pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

        pid = int(roh.get("pid", 0) or 0)
        if not _lebt(pid):
            return None
        try:
            seit = datetime.fromisoformat(str(roh.get("seit", "")))
        except ValueError:
            return None
        alter = (datetime.now(UTC) - seit).total_seconds() / 3600
        if alter > VERWAIST_NACH_STUNDEN:
            return None
        return roh

    def laeuft(self) -> bool:
        """Haelt gerade jemand die Sperre?"""
        return self.lies() is not None

    def nimm(self) -> bool:
        """Sperre nehmen. Returns: ob sie frei war.

        ``False`` heisst: Es laeuft bereits einer. Der Aufrufer bricht dann
        ab, statt einen zweiten Browser in dieselbe Gruppe zu schicken.
        """
        if self.laeuft():
            return False
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        self.pfad.write_text(
            json.dumps(
                {"pid": os.getpid(), "seit": datetime.now(UTC).isoformat()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return True

    def gib_frei(self) -> None:
        """Nur die **eigene** Sperre - eine fremde bleibt liegen.

        Sonst raeumte ein Lauf, der schon an der Sperre gescheitert ist, dem
        laufenden die Datei unter den Fuessen weg.
        """
        roh = self.lies()
        if roh is not None and int(roh.get("pid", 0) or 0) != os.getpid():
            return
        self.pfad.unlink(missing_ok=True)


def sperre_fuer(config) -> Sperre:  # noqa: ANN001 - AppConfig
    """Die Sperre neben dem Bestand dieses Projekts."""
    pfad = Path(config.path("sqlite_path"))
    return Sperre(pfad.with_name("automatik.lock"))


# --- Der Dienst -----------------------------------------------------------

def dienst_erreichbar(server: str, *, frist: float = 3.0) -> bool:
    """Antwortet der Dienst auf seinem Port?

    Eine blosse TCP-Verbindung, keine Anfrage: Ob der Weg ``/healthz``
    antwortet, haengt an nginx und an Schluesseln; ob der **Tunnel** steht,
    haengt nur am Port. Gefragt ist hier das Zweite.

    Ohne ``server`` ist nichts zu pruefen - der oertliche Lauf braucht
    keinen Dienst.
    """
    if not server:
        return True
    ziel = urlparse(server if "//" in server else f"//{server}", scheme="http")
    port = ziel.port or (443 if ziel.scheme == "https" else 80)
    try:
        with socket.create_connection((ziel.hostname or "127.0.0.1", port), timeout=frist):
            return True
    except OSError:
        return False


# --- Der Befehl -----------------------------------------------------------

def baue_befehl(server: str = "") -> list[str]:
    """Der Aufruf, den der Waechter startet - und **nur** dieser.

    Die Stelle, an der die Punkte 8 bis 10 der Anforderung nachpruefbar
    werden: Was hier nicht dransteht, kann nicht geschehen.

    * Kein ``--neu`` - der bestehende Lauf wird fortgesetzt, nicht neu
      eingefroren. Ohne diese Zusicherung finge der Waechter die Kampagne
      alle fuenf Minuten von vorn an, und die uebersprungenen Gruppen
      kaemen jedes Mal zurueck.
    * Kein ``--kampagne`` - der Lauf nimmt **alle** aktiven, wie von Hand.
    * Kein ``--limit`` und kein ``--dry-run``.

    ``sys.executable`` statt eines Namens: Der Waechter laeuft in derselben
    Umgebung wie der Lauf, den er startet - sonst faende ein zweiter Python
    das Paket nicht.
    """
    befehl = [sys.executable, "-m", "fbgroups.cli", "campaign", "automatik"]
    if server:
        befehl += ["--server", server]
    return befehl


def baue_tunnelbefehl(tunnel: Tunnel) -> list[str]:
    """Der Aufruf, der den Tunnel aufmacht - und **nur** dieser.

    Dieselbe Nachpruefbarkeit wie bei ``baue_befehl``: Was hier nicht
    dransteht, kann nicht geschehen. Es ist der Befehl von Hand, mit drei
    Zusaetzen:

    * ``-N`` - **keine Sitzung auf dem Server.** Von Hand oeffnet man dabei
      nebenbei eine Kommandozeile; ein Waechter braucht sie nicht, und eine
      Kommandozeile, die tagelang offensteht und der niemand zusieht, ist der
      Zugang, den man am ehesten vergisst.
    * ``ExitOnForwardFailure`` - steht der Port schon, soll ``ssh``
      **aufhoeren**, statt sich ohne Weiterleitung zu verbinden. Sonst haette
      der Waechter einen Prozess, den er fuer den Tunnel haelt, waehrend
      nichts weitergeleitet wird.
    * ``ServerAliveCountMax`` - nach drei unbeantworteten Lebenszeichen ist
      die Leitung tot und der Prozess endet. Erst dadurch merkt der Waechter
      ueberhaupt, dass er neu aufmachen muss; ohne das haenge ``ssh`` ewig an
      einer Verbindung, die es nicht mehr gibt.

    **Kein Kennwort und keine Kennwortdatei** - siehe ``Tunnel``.
    """
    befehl = ["ssh"]
    if tunnel.schluessel:
        befehl += ["-i", os.path.expanduser(tunnel.schluessel)]
    befehl += [
        "-N",
        "-L",
        f"{tunnel.port}:{tunnel.fernziel}:{tunnel.fernport or tunnel.port}",
        "-o",
        f"ServerAliveInterval={tunnel.lebenszeichen}",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "ExitOnForwardFailure=yes",
        tunnel.ziel,
    ]
    return befehl


class Tunnelwart:
    """Haelt den eigenen ``ssh``-Prozess - und nur den eigenen.

    Dieselbe Trennung wie bei der Sperre: Gefragt wird zuerst der **Port**
    (``dienst_erreichbar``), nicht dieses Objekt. Steht der Tunnel aus einem
    anderen Fenster, ist alles gut, und hier wird nichts gestartet - ein
    zweites ``ssh`` auf denselben Port scheiterte ohnehin
    (``ExitOnForwardFailure``), haette aber einen Fehler ins Protokoll
    geschrieben, an dem nichts liegt.
    """

    def __init__(
        self,
        tunnel: Tunnel,
        *,
        starte: Callable[[list[str]], object] | None = None,
    ) -> None:
        self.tunnel = tunnel
        self._starte = starte or starte_prozess
        self.prozess: object | None = None

    @property
    def nutzbar(self) -> bool:
        """Ist ein Tunnel eingetragen, den dieser Waechter aufmachen darf?"""
        return self.tunnel.nutzbar

    def laeuft(self) -> bool:
        """Laeuft **unser** ``ssh`` noch?"""
        if self.prozess is None:
            return False
        poll = getattr(self.prozess, "poll", None)
        return poll is not None and poll() is None

    def oeffne(self) -> list[str]:
        """Den Tunnel aufmachen. Returns: der gestartete Befehl."""
        befehl = baue_tunnelbefehl(self.tunnel)
        self.prozess = self._starte(befehl)
        return befehl


#: Wie lange nach dem Oeffnen des Tunnels auf den Port gewartet wird.
TUNNEL_FRIST = 10.0


def warte_auf_dienst(
    server: str,
    *,
    frist: float,
    schlafe: Callable[[float], None] = time.sleep,
    takt: float = 1.0,
) -> bool:
    """Kurz warten, bis der Port antwortet. Returns: ob er es tut.

    Ein frisch gestartetes ``ssh`` braucht einen Moment, und ohne dieses
    Warten verstriche bis zum Start des Laufs ein ganzer Blickabstand - bei
    fuenf Minuten Vorgabe also fuenf Minuten fuer nichts. Gewartet wird
    **kurz**: Fragt ``ssh`` nach dem Kennwort des Schluessels, antwortet der
    Port erst, wenn ein Mensch es eingegeben hat, und dann findet ihn der
    naechste Blick offen.
    """
    verbraucht = 0.0
    while True:
        if dienst_erreichbar(server, frist=takt):
            return True
        if verbraucht >= frist:
            return False
        schlafe(takt)
        verbraucht += takt


# --- Die Schleife ---------------------------------------------------------

@dataclass(frozen=True)
class Blick:
    """Was ein einzelner Blick ergeben hat - fuer Protokoll und Test.

    Ein Rueckgabewert statt einer Ausgabe: Die Schleife laesst sich damit
    ohne Bildschirm pruefen, und die Meldungen entstehen an einer Stelle.
    """

    #: ``laeuft`` | ``gestartet`` | ``dienst_weg`` | ``abgeschaltet``
    art: str
    meldung: str


def blicke(
    sperre: Sperre,
    einst: Einstellungen,
    *,
    starte: Callable[[list[str]], object],
    tunnel: Tunnelwart | None = None,
    schlafe: Callable[[float], None] = time.sleep,
) -> Blick:
    """Ein einziger Blick: nachsehen, und wenn noetig starten.

    Die ganze Entscheidung des Waechters in einer reinen Funktion - ohne
    Schlaf, ohne Endlosschleife, ohne Bildschirm. ``wache`` ruft sie in
    Abstaenden auf; ein Test ruft sie einmal.

    Die Reihenfolge der Pruefungen ist nicht beliebig:

    1. **Laeuft schon einer?** Dann ist alles gut, und es wird **nichts**
       gestartet - auch dann nicht, wenn der Dienst gerade nicht antwortet.
    2. **Steht der Dienst?** Wenn nicht, wird der Tunnel aufgemacht - sofern
       einer eingetragen ist (``watchdog.tunnel``, seit 20.09.2026). Kommt
       der Port binnen ``TUNNEL_FRIST`` nicht, wird gewartet statt
       gestartet: Ein Lauf ohne Tunnel scheitert an der ersten Anfrage,
       meldet nichts und bucht nichts - das ist kein Fehlschlag der
       Kampagne, sondern ein geschlossener Tunnel (Punkt 5).
    3. Erst dann starten.
    """
    if not einst.aktiv:
        return Blick("abgeschaltet", "Waechter ist abgeschaltet (watchdog.enabled: false)")

    if sperre.laeuft():
        roh = sperre.lies() or {}
        return Blick("laeuft", f"campaign automatik laeuft (PID {roh.get('pid', '?')})")

    if not dienst_erreichbar(einst.server):
        # **Erst aufmachen, dann klagen** (20.09.2026). Der Port ist zu; ist
        # ein Tunnel eingetragen und laeuft unser eigenes ``ssh`` nicht mehr,
        # wird er gestartet. Steht er aus einem anderen Fenster, antwortet
        # der Port - dann sind wir hier gar nicht.
        if tunnel is not None and tunnel.nutzbar and not tunnel.laeuft():
            befehl = tunnel.oeffne()
            if warte_auf_dienst(einst.server, frist=TUNNEL_FRIST, schlafe=schlafe):
                startbefehl = baue_befehl(einst.server)
                starte(startbefehl)
                return Blick(
                    "gestartet",
                    f"SSH-Tunnel aufgemacht ({' '.join(befehl)}), dann "
                    f"campaign automatik gestartet",
                )
            return Blick(
                "tunnel_gestartet",
                f"SSH-Tunnel gestartet ({' '.join(befehl)}) - der Port antwortet "
                "noch nicht. Fragt ssh nach dem Kennwort des Schluessels? Dann "
                "hier eingeben; der naechste Blick startet den Lauf.",
            )
        return Blick(
            "dienst_weg",
            f"Dienst {einst.server} antwortet nicht - es wird nichts gestartet. "
            + (
                "Unser SSH-Tunnel laeuft, leitet aber nicht weiter. "
                if tunnel is not None and tunnel.laeuft()
                else "Laeuft der SSH-Tunnel? "
            )
            + "Der naechste Blick versucht es erneut.",
        )

    befehl = baue_befehl(einst.server)
    starte(befehl)
    return Blick("gestartet", f"campaign automatik gestartet: {' '.join(befehl[2:])}")


def starte_prozess(befehl: list[str]) -> subprocess.Popen:
    """Den Lauf als eigenen Prozess starten - sichtbar, nicht im Hintergrund.

    Ausgabe und Fehler bleiben bei den Stroemen des Waechters: Der Lauf
    steuert einen **sichtbaren** Browser, und wer ihm zusieht, soll auch
    seine Meldungen lesen koennen. Ein Lauf, dessen Ausgabe niemand sieht,
    ist genau der Zustand, aus dem die Fehler der letzten Tage kamen.
    """
    return subprocess.Popen(befehl)  # noqa: S603 - eigener Befehl, keine Eingabe


def wache(
    sperre: Sperre,
    einst: Einstellungen,
    *,
    starte: Callable[[list[str]], object] = starte_prozess,
    melde: Callable[[Blick], None] | None = None,
    schlafe: Callable[[float], None] = time.sleep,
    durchgaenge: int = 0,
    tunnel: Tunnelwart | None = None,
) -> list[Blick]:
    """Die Schleife: alle ``abstand`` Sekunden ein Blick.

    ``durchgaenge`` begrenzt sie - ``0`` heisst "ohne Ende", und das ist der
    Betriebsfall: einmal starten, dann laeuft er. Ein Test gibt eine Zahl an
    und bekommt die Bloecke zurueck.

    **Zwischen zwei Blicken wird geschlafen, nicht gewartet.** Der Waechter
    haelt keinen Browser und keine Datenbank offen; er kostet zwischen zwei
    Blicken nichts.
    """
    verlauf: list[Blick] = []
    runde = 0
    while True:
        blick = blicke(sperre, einst, starte=starte, tunnel=tunnel, schlafe=schlafe)
        verlauf.append(blick)
        if melde is not None:
            melde(blick)

        runde += 1
        if durchgaenge and runde >= durchgaenge:
            return verlauf
        if blick.art == "abgeschaltet":
            # Abgeschaltet heisst abgeschaltet - nicht "alle fuenf Minuten
            # nachsehen, ob es wieder an ist". Wer ihn einschaltet, startet
            # ihn neu.
            return verlauf
        schlafe(einst.abstand)


__all__ = [
    "TUNNEL_FRIST",
    "VERWAIST_NACH_STUNDEN",
    "VORGABE_ABSTAND",
    "Blick",
    "Einstellungen",
    "Sperre",
    "Tunnel",
    "Tunnelwart",
    "baue_befehl",
    "baue_tunnelbefehl",
    "blicke",
    "dienst_erreichbar",
    "einstellungen",
    "sperre_fuer",
    "starte_prozess",
    "wache",
    "warte_auf_dienst",
]
