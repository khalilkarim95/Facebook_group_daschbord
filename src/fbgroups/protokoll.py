"""Das oertliche Protokoll: was im Terminal steht, steht auch in einer Datei.

Bis zum Umzug (25.09.2026) lief der Dienst auf dem Server, und was er sagte,
stand in ``journalctl``. Die Automatik lief schon damals auf diesem Rechner -
ihre Ausgabe stand aber nur im Terminal und war mit dem Fenster weg. Seit
fbgroups ganz hier laeuft, gibt es kein ``journalctl`` mehr; dieses Modul
ersetzt es fuer die beiden Befehle, die tagelang laufen: ``campaign
automatik`` und ``campaign watchdog``.

**Mitgeschrieben wird, was ohnehin auf dem Schirm steht** - keine zweite,
eigens formulierte Fassung. Zwei Fassungen liefen auseinander, und gesucht
wird im Protokoll genau das, was man im Fenster gesehen hat. Deshalb haengt
es sich an ``sys.stdout``/``sys.stderr``: ``rich`` fragt bei jeder Ausgabe
nach ``sys.stdout``, also schreiben alle ``Console()`` des Projekts durch,
ohne dass eine davon angefasst wird.

* Eine Datei je Befehl und Tag (``data/logs/automatik-2026-09-25.log``).
  Getrennt je Befehl, weil der Waechter den Lauf als eigenen Prozess
  startet, und zwei Prozesse, die unter Windows an dieselbe Datei anhaengen,
  koennen einander Zeilen ueberschreiben.
* Jede Zeile mit Uhrzeit und ohne Farbcodes.
* **Ein Fehler beim Schreiben der Datei haelt den Lauf nicht an.** Das
  Protokoll schaltet sich ab, sagt es einmal, und der Lauf arbeitet weiter.
* Dateien, die aelter sind als ``protokoll.tage``, werden beim Start
  geloescht - nur solche, deren Name genau diesem Muster folgt.

Der Bestand selbst - jeder Versuch, jeder Ausgang - steht weiter in SQLite
(``post_versuche``, ``automatik_lauf*``). Das Protokoll ist die Textspur
daneben, nicht die Wahrheit.
"""

from __future__ import annotations

import contextlib
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import IO, Any, TextIO

#: Wie lange eine Tagesdatei bleibt, wenn ``protokoll.tage`` fehlt.
VORGABE_TAGE = 60

#: Steuerfolgen des Terminals: Farben, Cursor, Fenstertitel. Im Terminal
#: machen sie die Farben, in der Datei nur Zeichensalat.
_STEUERFOLGE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")

#: ``<name>-JJJJ-MM-TT.log`` - nur was so heisst, raeumt ``raeume_auf`` weg.
_DATEINAME = re.compile(r"^(?P<name>.+)-(?P<tag>\d{4}-\d{2}-\d{2})\.log$")


@dataclass(frozen=True)
class Einstellungen:
    """Wo das Protokoll liegt und wie lange es bleibt."""

    ordner: Path
    tage: int = VORGABE_TAGE


def einstellungen(config) -> Einstellungen:  # noqa: ANN001 - AppConfig
    """Die einzige Stelle dieses Moduls, die die Konfiguration kennt."""
    block = config.get("protokoll", default={}) or {}
    try:
        ordner = config.path("log_dir")
    except KeyError:
        ordner = config.path("data_dir") / "logs"
    return Einstellungen(ordner=ordner, tage=int(block.get("tage", VORGABE_TAGE) or 0))


def bereinigt(zeile: str) -> str:
    """Eine Bildschirmzeile, wie sie in der Datei stehen soll.

    Ohne Steuerfolgen und ohne die Leerzeichen, mit denen ``rich`` einen
    Rahmen auf Fensterbreite auffuellt. Ein Wagenruecklauf mitten in der
    Zeile ueberschreibt im Terminal, was davor stand - hier auch.
    """
    zeile = _STEUERFOLGE.sub("", zeile).rstrip("\r")
    if "\r" in zeile:
        zeile = zeile.rsplit("\r", 1)[-1]
    return zeile.rstrip()


class Tagesdatei:
    """Die Datei des Tages - um Mitternacht beginnt von selbst die naechste."""

    def __init__(
        self,
        ordner: Path,
        name: str,
        *,
        uhr: Callable[[], datetime] = datetime.now,
        stoerung: TextIO | None = None,
    ) -> None:
        self.ordner = Path(ordner)
        self.name = name
        self._uhr = uhr
        self._stoerung = stoerung
        self._tag: date | None = None
        self._datei: IO[str] | None = None
        self.abgeschaltet = False

    def pfad(self, tag: date) -> Path:
        return self.ordner / f"{self.name}-{tag:%Y-%m-%d}.log"

    def zeile(self, text: str) -> None:
        """Eine Zeile mit Uhrzeit anhaengen - und sofort auf die Platte.

        Sofort, weil das Protokoll gerade dann gebraucht wird, wenn der
        Prozess abgestuerzt ist; was im Puffer stand, waere mit ihm weg.
        """
        if self.abgeschaltet:
            return
        jetzt = self._uhr()
        try:
            if self._datei is None or jetzt.date() != self._tag:
                self._oeffne(jetzt.date())
            assert self._datei is not None
            self._datei.write(f"{jetzt:%H:%M:%S}  {text}\n" if text else "\n")
            self._datei.flush()
        except OSError as exc:
            self._abschalten(exc)

    def schliesse(self) -> None:
        if self._datei is not None:
            with contextlib.suppress(OSError):
                self._datei.close()
        self._datei = None
        self._tag = None

    def _oeffne(self, tag: date) -> None:
        self.schliesse()
        self.ordner.mkdir(parents=True, exist_ok=True)
        self._datei = self.pfad(tag).open("a", encoding="utf-8")
        self._tag = tag

    def _abschalten(self, exc: OSError) -> None:
        self.abgeschaltet = True
        self.schliesse()
        if self._stoerung is None:
            return
        # Ein Hinweis, der nicht ankommt, ist kein Grund, den Lauf zu stoeren.
        with contextlib.suppress(Exception):
            self._stoerung.write(f"[Protokoll abgeschaltet: {exc}]\n")


class Strom:
    """Ein Ausgabestrom, der ins Terminal schreibt und zugleich ins Protokoll.

    Alles, was ``rich`` sonst vom Strom wissen will - ob er ein Terminal
    ist, seine Kodierung, seine Kennung -, beantwortet der eigentliche
    Strom. So bleiben Farben und Fensterbreite im Terminal, wie sie waren.
    """

    def __init__(self, ziel: TextIO, datei: Tagesdatei) -> None:
        self._ziel = ziel
        self._datei = datei
        self._rest = ""

    def write(self, text: str) -> int:
        geschrieben = self._ziel.write(text)
        self._rest += text
        if "\n" in self._rest:
            *zeilen, self._rest = self._rest.split("\n")
            for zeile in zeilen:
                self._datei.zeile(bereinigt(zeile))
        return geschrieben if isinstance(geschrieben, int) else len(text)

    def flush(self) -> None:
        self._ziel.flush()

    def isatty(self) -> bool:
        return self._ziel.isatty()

    def fileno(self) -> int:
        return self._ziel.fileno()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ziel, name)


def raeume_auf(ordner: Path, name: str, tage: int, heute: date) -> list[Path]:
    """Tagesdateien dieses Befehls loeschen, die aelter als ``tage`` sind.

    ``0`` heisst: nichts loeschen. Angefasst wird nur, was genau
    ``<name>-JJJJ-MM-TT.log`` heisst - eine Datei, die jemand daneben
    gelegt hat, bleibt liegen.
    """
    if tage <= 0 or not ordner.is_dir():
        return []
    grenze = heute - timedelta(days=tage)
    weg: list[Path] = []
    for datei in ordner.glob(f"{name}-*.log"):
        treffer = _DATEINAME.match(datei.name)
        if not treffer or treffer["name"] != name:
            continue
        try:
            tag = date.fromisoformat(treffer["tag"])
        except ValueError:
            continue
        if tag < grenze:
            try:
                datei.unlink()
            except OSError:
                continue
            weg.append(datei)
    return weg


def einschalten(einst: Einstellungen, name: str, *, heute: date | None = None) -> Path | None:
    """Ab jetzt schreibt dieser Prozess zusaetzlich ins Protokoll.

    Liefert den Pfad der heutigen Datei - oder ``None``, wenn der Ordner
    nicht anzulegen war. Dann laeuft alles weiter wie bisher, nur ohne
    Datei. Zweimal eingeschaltet wird nicht doppelt geschrieben.
    """
    heute = heute or date.today()
    try:
        einst.ordner.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    raeume_auf(einst.ordner, name, einst.tage, heute)

    datei = Tagesdatei(einst.ordner, name, stoerung=sys.__stderr__)
    if sys.stdout is not None and not isinstance(sys.stdout, Strom):
        sys.stdout = Strom(sys.stdout, datei)  # type: ignore[assignment]
    if sys.stderr is not None and not isinstance(sys.stderr, Strom):
        sys.stderr = Strom(sys.stderr, datei)  # type: ignore[assignment]
    return datei.pfad(heute)


__all__ = [
    "VORGABE_TAGE",
    "Einstellungen",
    "Strom",
    "Tagesdatei",
    "bereinigt",
    "einschalten",
    "einstellungen",
    "raeume_auf",
]
