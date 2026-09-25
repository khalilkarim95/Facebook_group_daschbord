"""Die oertliche Sicherung des Bestands.

Auf dem Server sicherte ein systemd-Timer (``fbgroups-backup.timer``) die
Datenbank einmal am Tag. Seit dem Umzug (25.09.2026) liegt der Bestand auf
diesem Rechner - und die Sicherung mit ihm.

* **Kopiert wird mit der Sicherungsschnittstelle von SQLite**, nicht mit
  ``copy``: Eine Datei, in die gerade gebucht wird, laesst sich nicht
  einfach kopieren - die Kopie kann mitten in einer Buchung stehen. Die
  Schnittstelle liefert einen Stand, der genau so in der Datenbank stand.
* **Jede Sicherung wird geprueft** (``PRAGMA integrity_check``), bevor sie
  zaehlt. Eine Sicherung, die sich nicht oeffnen laesst, ist keine.
* Gepackt, mit Zeitstempel im Namen:
  ``data/backups/sicherung-2026-09-25T132305.sqlite.gz``.
* **Ein zweiter Ort** (``sicherung.weitere_ordner``): ``data/`` liegt auf K:
  (Festplatte), die Vorgabe ``~/fbgroups-sicherung`` auf C: (SSD). Ein
  Plattenschaden nimmt nicht beide mit.
* Aufgehoben werden je Ordner die neuesten ``sicherung.behalten``.
  Geloescht wird nur, was ``sicherung-*.sqlite.gz`` heisst; die aelteren
  Sicherungen vom Server (``groups-2026-08-18.sqlite.gz``) bleiben liegen.

Gesichert wird vom Waechter, sobald die letzte Sicherung aelter ist als
``sicherung.abstand_stunden``, von Hand mit ``fbgroups sicherung``, einmal
beim Umzug und vor jedem Zurueckspielen.

**Zurueckspielen** (``zurueckspielen``) ersetzt den Bestand durch eine
Sicherung - nur, wenn kein Lauf die Sperre haelt und keine Journaldatei
neben der Datenbank liegt: Ein liegengebliebenes Journal spielte SQLite
beim naechsten Oeffnen in die zurueckgespielte Datei ein. Der Stand davor
wird vorher selbst gesichert, damit auch das Zurueckspielen zurueckgeht.
"""

from __future__ import annotations

import gzip
import os
import re
import shutil
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from fbgroups.datenbank import verbinde

#: Der Name jeder Sicherung, die dieses Modul anlegt - und die einzige Art
#: Datei, die es wieder loescht.
PRAEFIX = "sicherung-"
ENDUNG = ".sqlite.gz"
_NAME = re.compile(r"^sicherung-(?P<zeit>\d{4}-\d{2}-\d{2}T\d{6})\.sqlite\.gz$")
_ZEITFORMAT = "%Y-%m-%dT%H%M%S"

VORGABE_BEHALTEN = 30
VORGABE_ABSTAND_STUNDEN = 24.0


class SicherungFehlgeschlagen(RuntimeError):
    """Die Sicherung liess sich nicht anlegen, nicht pruefen oder nicht einspielen."""


@dataclass(frozen=True)
class Einstellungen:
    """Wohin gesichert wird, wie oft und wie viele bleiben.

    ``behalten: 0`` heisst "alle behalten", ``abstand_stunden: 0`` heisst
    "der Waechter sichert nicht" - von Hand geht es trotzdem.
    """

    ordner: Path
    weitere_ordner: tuple[Path, ...] = ()
    behalten: int = VORGABE_BEHALTEN
    abstand_stunden: float = VORGABE_ABSTAND_STUNDEN

    @property
    def alle_ordner(self) -> tuple[Path, ...]:
        return (self.ordner, *self.weitere_ordner)


@dataclass(frozen=True)
class Sicherung:
    """Was eine Sicherung ergeben hat."""

    pfad: Path
    kopien: tuple[Path, ...]
    groesse: int
    schema_version: int
    geloescht: tuple[Path, ...] = ()
    #: Weitere Orte, an die nicht geschrieben werden konnte. Die Sicherung
    #: im Hauptordner steht trotzdem - ein fehlender USB-Stick ist kein
    #: Grund, gar nicht zu sichern.
    fehler: tuple[str, ...] = ()


def _ordner(root: Path, eintrag: object) -> Path:
    pfad = Path(os.path.expandvars(os.path.expanduser(str(eintrag).strip())))
    return pfad if pfad.is_absolute() else root / pfad


def einstellungen(config) -> Einstellungen:  # noqa: ANN001 - AppConfig
    """Die einzige Stelle dieses Moduls, die die Konfiguration kennt."""
    block = config.get("sicherung", default={}) or {}
    try:
        ordner = config.path("backup_dir")
    except KeyError:
        ordner = config.path("data_dir") / "backups"
    weitere = tuple(
        _ordner(config.root, eintrag)
        for eintrag in (block.get("weitere_ordner") or [])
        if str(eintrag).strip()
    )
    return Einstellungen(
        ordner=ordner,
        weitere_ordner=weitere,
        behalten=int(block.get("behalten", VORGABE_BEHALTEN) or 0),
        abstand_stunden=float(block.get("abstand_stunden", VORGABE_ABSTAND_STUNDEN) or 0),
    )


# --- Kopieren und pruefen -----------------------------------------------

def _kopiere(quelle: Path, ziel: Path) -> None:
    """Ein in sich stimmiger Stand von ``quelle`` nach ``ziel``."""
    ziel.unlink(missing_ok=True)
    von = verbinde(quelle)
    try:
        nach = sqlite3.connect(ziel)
        try:
            von.backup(nach)
        finally:
            nach.close()
    finally:
        von.close()


def pruefe(pfad: Path) -> int:
    """``integrity_check`` - und die Schemaversion, wenn alles stimmt."""
    try:
        conn = sqlite3.connect(f"{pfad.resolve().as_uri()}?mode=ro", uri=True)
        try:
            befund = [tuple(zeile) for zeile in conn.execute("PRAGMA integrity_check")]
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise SicherungFehlgeschlagen(f"{pfad.name} ist keine lesbare Datenbank: {exc}") from exc
    if befund != [("ok",)]:
        raise SicherungFehlgeschlagen(f"{pfad.name} ist beschaedigt: {befund[:3]}")
    return version


def _entpacke(gepackt: Path, ziel: Path) -> None:
    ziel.unlink(missing_ok=True)
    try:
        with gzip.open(gepackt, "rb") as ein, ziel.open("wb") as aus:
            shutil.copyfileobj(ein, aus)
    except (OSError, EOFError) as exc:
        ziel.unlink(missing_ok=True)
        raise SicherungFehlgeschlagen(f"{gepackt.name} laesst sich nicht entpacken: {exc}") from exc


# --- Anlegen -------------------------------------------------------------

def dateiname(zeit: datetime) -> str:
    return f"{PRAEFIX}{zeit:{_ZEITFORMAT}}{ENDUNG}"


def zeitpunkt(pfad: Path) -> datetime | None:
    """Wann eine Sicherung entstand - aus ihrem Namen, nicht aus der Datei.

    Der Name reist mit jeder Kopie; das Aenderungsdatum der Datei haengt
    davon ab, wie kopiert wurde.
    """
    treffer = _NAME.match(pfad.name)
    if not treffer:
        return None
    return datetime.strptime(treffer["zeit"], _ZEITFORMAT)


def vorhandene(ordner: Path) -> list[Path]:
    """Die Sicherungen eines Ordners, die aelteste zuerst."""
    if not ordner.is_dir():
        return []
    return sorted(
        (datei for datei in ordner.glob(f"{PRAEFIX}*{ENDUNG}") if _NAME.match(datei.name)),
        key=lambda datei: datei.name,
    )


def raeume_auf(ordner: Path, behalten: int) -> list[Path]:
    """Nur die neuesten ``behalten`` Sicherungen bleiben; ``0`` behaelt alle."""
    if behalten <= 0:
        return []
    weg: list[Path] = []
    for datei in vorhandene(ordner)[:-behalten]:
        try:
            datei.unlink()
        except OSError:
            continue
        weg.append(datei)
    return weg


def sichere(quelle: Path, einst: Einstellungen, *, jetzt: datetime | None = None) -> Sicherung:
    """Jetzt sichern: kopieren, pruefen, packen, verteilen, aufraeumen."""
    quelle = Path(quelle)
    if not quelle.is_file():
        raise SicherungFehlgeschlagen(f"{quelle} gibt es nicht - nichts zu sichern.")
    jetzt = jetzt or datetime.now()
    name = dateiname(jetzt)
    einst.ordner.mkdir(parents=True, exist_ok=True)

    # Erst unter einem Namen, den ``vorhandene`` nicht kennt: Bricht es auf
    # halber Strecke ab, bleibt keine halbe Sicherung liegen, die spaeter
    # als die neueste gaelte.
    roh = einst.ordner / f".{name}.roh"
    teil = einst.ordner / f".{name}.teil"
    ziel = einst.ordner / name
    try:
        _kopiere(quelle, roh)
        version = pruefe(roh)
        with roh.open("rb") as ein, gzip.open(teil, "wb") as aus:
            shutil.copyfileobj(ein, aus)
        teil.replace(ziel)
    except sqlite3.Error as exc:
        raise SicherungFehlgeschlagen(f"{quelle.name} liess sich nicht kopieren: {exc}") from exc
    finally:
        roh.unlink(missing_ok=True)
        teil.unlink(missing_ok=True)

    kopien: list[Path] = []
    fehler: list[str] = []
    for ordner in einst.weitere_ordner:
        zwischen = ordner / f".{name}.teil"
        try:
            ordner.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ziel, zwischen)
            zwischen.replace(ordner / name)
            kopien.append(ordner / name)
        except OSError as exc:
            zwischen.unlink(missing_ok=True)
            fehler.append(f"{ordner}: {exc}")

    geloescht: list[Path] = []
    for ordner in einst.alle_ordner:
        geloescht += raeume_auf(ordner, einst.behalten)

    return Sicherung(
        pfad=ziel,
        kopien=tuple(kopien),
        groesse=ziel.stat().st_size,
        schema_version=version,
        geloescht=tuple(geloescht),
        fehler=tuple(fehler),
    )


def letzte(einst: Einstellungen) -> datetime | None:
    """Wann zuletzt gesichert wurde - gemessen am Hauptordner."""
    zeiten = [zeitpunkt(datei) for datei in vorhandene(einst.ordner)]
    return max((zeit for zeit in zeiten if zeit is not None), default=None)


def faellig(einst: Einstellungen, jetzt: datetime | None = None) -> bool:
    """Ist die letzte Sicherung aelter als ``abstand_stunden``?"""
    if einst.abstand_stunden <= 0:
        return False
    zuletzt = letzte(einst)
    if zuletzt is None:
        return True
    return (jetzt or datetime.now()) - zuletzt >= timedelta(hours=einst.abstand_stunden)


def bei_bedarf(
    quelle: Path, einst: Einstellungen, *, jetzt: datetime | None = None
) -> Sicherung | None:
    """Sichern, wenn es faellig ist - sonst nichts tun. Fuer den Waechter."""
    if not faellig(einst, jetzt):
        return None
    return sichere(quelle, einst, jetzt=jetzt)


def beschreibe(sicherung: Sicherung) -> str:
    """Eine Zeile fuer Terminal und Protokoll."""
    groesse = f"{sicherung.groesse / 1024:.0f} KB"
    teile = [f"{sicherung.pfad.name} ({groesse}, Schema {sicherung.schema_version})"]
    if sicherung.kopien:
        teile.append("Kopie: " + ", ".join(str(kopie.parent) for kopie in sicherung.kopien))
    if sicherung.geloescht:
        teile.append(f"{len(sicherung.geloescht)} alte entfernt")
    if sicherung.fehler:
        teile.append("NICHT gesichert nach: " + "; ".join(sicherung.fehler))
    return " - ".join(teile)


# --- Zurueckspielen ------------------------------------------------------

def _journale(quelle: Path) -> list[Path]:
    return [
        pfad
        for pfad in (quelle.with_name(quelle.name + endung) for endung in ("-journal", "-wal"))
        if pfad.exists()
    ]


def zurueckspielen(
    gepackt: Path,
    quelle: Path,
    einst: Einstellungen,
    *,
    lauf_aktiv: bool,
    jetzt: datetime | None = None,
) -> tuple[int, Sicherung | None]:
    """Den Bestand durch eine Sicherung ersetzen.

    Liefert die Schemaversion der eingespielten Datei und die Sicherung des
    Stands davor (``None``, wenn es noch keinen Bestand gab).

    Verweigert wird, solange ein Lauf die Sperre haelt oder ein Journal
    neben der Datenbank liegt. Unter Windows scheitert das Ersetzen
    ausserdem, solange ein anderer Prozess die Datei offen haelt (die
    Uebersicht) - auch das wird gemeldet, nicht uebergangen.
    """
    gepackt = Path(gepackt)
    quelle = Path(quelle)
    if lauf_aktiv:
        raise SicherungFehlgeschlagen(
            "Es laeuft ein campaign automatik - erst beenden (und den Waechter anhalten)."
        )
    if liegen := _journale(quelle):
        raise SicherungFehlgeschlagen(
            "Neben der Datenbank liegt "
            + ", ".join(pfad.name for pfad in liegen)
            + " - ein Prozess haelt sie offen oder ist abgestuerzt. Erst alles schliessen."
        )
    if not gepackt.is_file():
        raise SicherungFehlgeschlagen(f"{gepackt} gibt es nicht.")

    roh = quelle.with_name(f".{quelle.name}.zurueck")
    _entpacke(gepackt, roh)
    try:
        version = pruefe(roh)
        vorher = sichere(quelle, einst, jetzt=jetzt) if quelle.exists() else None
        try:
            roh.replace(quelle)
        except OSError as exc:
            raise SicherungFehlgeschlagen(
                f"{quelle.name} ist noch geoeffnet (Uebersicht?) - erst schliessen: {exc}"
            ) from exc
    finally:
        roh.unlink(missing_ok=True)
    return version, vorher


def uebersicht(einst: Einstellungen) -> Iterable[tuple[Path, list[Path]]]:
    """Jeder Sicherungsort mit seinen Sicherungen - fuer ``--liste``."""
    for ordner in einst.alle_ordner:
        yield ordner, vorhandene(ordner)


__all__ = [
    "ENDUNG",
    "PRAEFIX",
    "VORGABE_ABSTAND_STUNDEN",
    "VORGABE_BEHALTEN",
    "Einstellungen",
    "Sicherung",
    "SicherungFehlgeschlagen",
    "bei_bedarf",
    "beschreibe",
    "dateiname",
    "einstellungen",
    "faellig",
    "letzte",
    "pruefe",
    "raeume_auf",
    "sichere",
    "uebersicht",
    "vorhandene",
    "zeitpunkt",
    "zurueckspielen",
]
