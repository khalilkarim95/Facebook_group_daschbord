"""Die oertliche Sicherung des Bestands (25.09.2026).

Seit dem Umzug liegt ``groups.sqlite`` auf diesem Rechner; der systemd-Timer
des Servers sichert nichts mehr. Festgehalten wird, was eine Sicherung
ausmacht: ein stimmiger Stand, geprueft, an zwei Orten, und aufgeraeumt wird
nur, was diesem Modul gehoert.
"""

from __future__ import annotations

import gzip
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fbgroups import sicherung
from fbgroups.sicherung import Einstellungen, SicherungFehlgeschlagen

PROJEKT = Path(__file__).resolve().parents[1]
JETZT = datetime(2026, 9, 25, 13, 23, 5)


def _bestand(pfad: Path, *zeilen: str) -> Path:
    """Eine kleine Datenbank mit einer Tabelle und den genannten Zeilen."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(pfad)
    conn.execute("CREATE TABLE IF NOT EXISTS gruppen (name TEXT)")
    conn.executemany("INSERT INTO gruppen VALUES (?)", [(zeile,) for zeile in zeilen])
    conn.execute("PRAGMA user_version = 28")
    conn.commit()
    conn.close()
    return pfad


def _inhalt(gepackt: Path, ablage: Path) -> list[str]:
    """Was in einer gepackten Sicherung steht."""
    roh = ablage / f"{gepackt.name}.roh"
    with gzip.open(gepackt, "rb") as ein, roh.open("wb") as aus:
        shutil.copyfileobj(ein, aus)
    conn = sqlite3.connect(roh)
    try:
        return [zeile[0] for zeile in conn.execute("SELECT name FROM gruppen ORDER BY rowid")]
    finally:
        conn.close()


@pytest.fixture()
def einst(tmp_path: Path) -> Einstellungen:
    return Einstellungen(
        ordner=tmp_path / "backups",
        weitere_ordner=(tmp_path / "ssd",),
        behalten=3,
        abstand_stunden=24,
    )


# --- Anlegen ----------------------------------------------------------------

def test_eine_sicherung_ist_geprueft_gepackt_und_an_beiden_orten(
    tmp_path: Path, einst: Einstellungen
) -> None:
    quelle = _bestand(tmp_path / "groups.sqlite", "Damaskus", "حلب")

    ergebnis = sicherung.sichere(quelle, einst, jetzt=JETZT)

    assert ergebnis.pfad == einst.ordner / "sicherung-2026-09-25T132305.sqlite.gz"
    assert ergebnis.schema_version == 28
    assert ergebnis.kopien == (tmp_path / "ssd" / ergebnis.pfad.name,)
    assert ergebnis.fehler == ()
    assert _inhalt(ergebnis.pfad, tmp_path) == ["Damaskus", "حلب"]
    assert _inhalt(ergebnis.kopien[0], tmp_path) == ["Damaskus", "حلب"]


def test_gesichert_wird_der_gebuchte_stand_nicht_die_halbe_buchung(
    tmp_path: Path, einst: Einstellungen
) -> None:
    """Der Grund fuer die Sicherungsschnittstelle statt ``copy``: Ein anderer
    Prozess schreibt gerade. In die Sicherung gehoert, was gebucht ist."""
    quelle = _bestand(tmp_path / "groups.sqlite", "gebucht")
    schreiber = sqlite3.connect(quelle, isolation_level=None)
    schreiber.execute("BEGIN")
    schreiber.execute("INSERT INTO gruppen VALUES ('mitten in der Buchung')")
    try:
        ergebnis = sicherung.sichere(quelle, einst, jetzt=JETZT)
    finally:
        schreiber.execute("ROLLBACK")
        schreiber.close()

    assert _inhalt(ergebnis.pfad, tmp_path) == ["gebucht"]


def test_ohne_bestand_gibt_es_keine_sicherung(tmp_path: Path, einst: Einstellungen) -> None:
    with pytest.raises(SicherungFehlgeschlagen, match="gibt es nicht"):
        sicherung.sichere(tmp_path / "fehlt.sqlite", einst, jetzt=JETZT)

    assert sicherung.vorhandene(einst.ordner) == []


def test_eine_kaputte_datei_hinterlaesst_keine_halbe_sicherung(
    tmp_path: Path, einst: Einstellungen
) -> None:
    """Sonst gaelte der Rest spaeter als die neueste Sicherung."""
    kaputt = tmp_path / "groups.sqlite"
    kaputt.write_bytes(b"das ist keine Datenbank" * 100)

    with pytest.raises(SicherungFehlgeschlagen):
        sicherung.sichere(kaputt, einst, jetzt=JETZT)

    assert list(einst.ordner.iterdir()) == []


def test_ein_fehlender_zweiter_ort_haelt_die_sicherung_nicht_auf(tmp_path: Path) -> None:
    """Ein abgezogener USB-Stick ist kein Grund, gar nicht zu sichern."""
    quelle = _bestand(tmp_path / "groups.sqlite", "Damaskus")
    versperrt = tmp_path / "keine-ordner"
    versperrt.write_text("eine Datei, wo ein Ordner sein sollte", encoding="utf-8")
    einst = Einstellungen(ordner=tmp_path / "backups", weitere_ordner=(versperrt,))

    ergebnis = sicherung.sichere(quelle, einst, jetzt=JETZT)

    assert ergebnis.pfad.is_file()
    assert ergebnis.kopien == ()
    assert len(ergebnis.fehler) == 1
    assert "NICHT gesichert nach" in sicherung.beschreibe(ergebnis)


# --- Aufraeumen -------------------------------------------------------------

def test_es_bleiben_die_neuesten_und_fremde_dateien_bleiben_liegen(
    tmp_path: Path, einst: Einstellungen
) -> None:
    """Geloescht wird nur, was ``sicherung-*`` heisst - die alte Sicherung vom
    Server im selben Ordner ist nicht Sache dieses Moduls."""
    quelle = _bestand(tmp_path / "groups.sqlite", "Damaskus")
    einst.ordner.mkdir(parents=True)
    vom_server = einst.ordner / "groups-2026-08-18.sqlite.gz"
    vom_server.write_bytes(b"alt")

    for stunde in range(5):
        sicherung.sichere(quelle, einst, jetzt=JETZT + timedelta(hours=stunde))

    namen = [datei.name for datei in sicherung.vorhandene(einst.ordner)]
    assert namen == [
        "sicherung-2026-09-25T152305.sqlite.gz",
        "sicherung-2026-09-25T162305.sqlite.gz",
        "sicherung-2026-09-25T172305.sqlite.gz",
    ]
    assert len(sicherung.vorhandene(tmp_path / "ssd")) == 3
    assert vom_server.is_file()


def test_behalten_null_heisst_alle(tmp_path: Path) -> None:
    quelle = _bestand(tmp_path / "groups.sqlite", "Damaskus")
    einst = Einstellungen(ordner=tmp_path / "backups", behalten=0)

    for tag in range(4):
        sicherung.sichere(quelle, einst, jetzt=JETZT + timedelta(days=tag))

    assert len(sicherung.vorhandene(einst.ordner)) == 4


# --- Wann ------------------------------------------------------------------

def test_faellig_ohne_sicherung_und_nach_dem_abstand(tmp_path: Path, einst: Einstellungen) -> None:
    quelle = _bestand(tmp_path / "groups.sqlite", "Damaskus")
    assert sicherung.faellig(einst, JETZT)

    sicherung.sichere(quelle, einst, jetzt=JETZT)

    assert not sicherung.faellig(einst, JETZT + timedelta(hours=23))
    assert sicherung.faellig(einst, JETZT + timedelta(hours=24))
    assert sicherung.letzte(einst) == JETZT


def test_bei_bedarf_sichert_nur_wenn_faellig(tmp_path: Path, einst: Einstellungen) -> None:
    quelle = _bestand(tmp_path / "groups.sqlite", "Damaskus")

    assert sicherung.bei_bedarf(quelle, einst, jetzt=JETZT) is not None
    assert sicherung.bei_bedarf(quelle, einst, jetzt=JETZT + timedelta(hours=1)) is None
    assert len(sicherung.vorhandene(einst.ordner)) == 1


def test_abstand_null_heisst_der_waechter_sichert_nicht(tmp_path: Path) -> None:
    einst = Einstellungen(ordner=tmp_path / "backups", abstand_stunden=0)

    assert not sicherung.faellig(einst, JETZT)


# --- Zurueckspielen ------------------------------------------------------

def test_zurueckspielen_sichert_erst_den_stand_davor(tmp_path: Path, einst: Einstellungen) -> None:
    """Damit auch das Zurueckspielen zurueckgeht."""
    quelle = _bestand(tmp_path / "groups.sqlite", "alt")
    alte = sicherung.sichere(quelle, einst, jetzt=JETZT)
    _bestand(quelle, "neu")

    version, vorher = sicherung.zurueckspielen(
        alte.pfad, quelle, einst, lauf_aktiv=False, jetzt=JETZT + timedelta(hours=1)
    )

    assert version == 28
    assert vorher is not None
    assert _inhalt(vorher.pfad, tmp_path) == ["alt", "neu"]
    conn = sqlite3.connect(quelle)
    try:
        assert [zeile[0] for zeile in conn.execute("SELECT name FROM gruppen")] == ["alt"]
    finally:
        conn.close()


def test_zurueckspielen_verweigert_neben_einem_laufenden_lauf(
    tmp_path: Path, einst: Einstellungen
) -> None:
    quelle = _bestand(tmp_path / "groups.sqlite", "alt")
    alte = sicherung.sichere(quelle, einst, jetzt=JETZT)

    with pytest.raises(SicherungFehlgeschlagen, match="campaign automatik"):
        sicherung.zurueckspielen(alte.pfad, quelle, einst, lauf_aktiv=True)


def test_zurueckspielen_verweigert_neben_einem_journal(
    tmp_path: Path, einst: Einstellungen
) -> None:
    """Ein liegengebliebenes Journal spielte SQLite in die neue Datei ein."""
    quelle = _bestand(tmp_path / "groups.sqlite", "alt")
    alte = sicherung.sichere(quelle, einst, jetzt=JETZT)
    (tmp_path / "groups.sqlite-journal").write_bytes(b"\x00")

    with pytest.raises(SicherungFehlgeschlagen, match="journal"):
        sicherung.zurueckspielen(alte.pfad, quelle, einst, lauf_aktiv=False)


def test_eine_kaputte_sicherung_wird_nicht_eingespielt(
    tmp_path: Path, einst: Einstellungen
) -> None:
    quelle = _bestand(tmp_path / "groups.sqlite", "bleibt")
    kaputt = tmp_path / "sicherung-2026-01-01T000000.sqlite.gz"
    with gzip.open(kaputt, "wb") as aus:
        aus.write(b"keine Datenbank" * 100)

    with pytest.raises(SicherungFehlgeschlagen):
        sicherung.zurueckspielen(kaputt, quelle, einst, lauf_aktiv=False)

    conn = sqlite3.connect(quelle)
    try:
        assert [zeile[0] for zeile in conn.execute("SELECT name FROM gruppen")] == ["bleibt"]
    finally:
        conn.close()
    assert not list(tmp_path.glob(".*.zurueck"))


# --- Konfiguration und Befehl ---------------------------------------------

def test_die_einstellungen_kommen_aus_der_konfiguration(config) -> None:  # noqa: ANN001
    einst = sicherung.einstellungen(config)

    assert einst.ordner == config.root / "data" / "backups"
    assert einst.abstand_stunden == 24
    assert einst.behalten == 30
    # Der zweite Ort liegt nicht unter data/ - sonst naehme ein Plattenschaden
    # beide mit.
    assert einst.weitere_ordner
    assert all(config.root / "data" not in ordner.parents for ordner in einst.weitere_ordner)
    assert all(ordner.is_absolute() for ordner in einst.weitere_ordner)


def test_der_befehl_sichert_in_die_konfigurierten_ordner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fbgroups import cli
    from fbgroups.config import load_config

    shutil.copytree(PROJEKT / "config", tmp_path / "config")
    settings = tmp_path / "config" / "settings.yaml"
    zweiter = tmp_path / "zweiter-ort"
    settings.write_text(
        settings.read_text(encoding="utf-8").replace(
            "- ~/fbgroups-sicherung", f"- '{zweiter.as_posix()}'"
        ),
        encoding="utf-8",
    )
    _bestand(tmp_path / "data" / "groups.sqlite", "Damaskus")
    monkeypatch.setattr(cli, "load_config", lambda: load_config(tmp_path))

    ergebnis = CliRunner().invoke(cli.app, ["sicherung"])

    assert ergebnis.exit_code == 0, ergebnis.output
    assert "Gesichert" in ergebnis.output
    assert len(sicherung.vorhandene(tmp_path / "data" / "backups")) == 1
    assert len(sicherung.vorhandene(zweiter)) == 1
