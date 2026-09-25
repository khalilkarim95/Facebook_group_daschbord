"""Das oertliche Protokoll (25.09.2026).

Seit dem Umzug gibt es kein ``journalctl`` mehr. Was ``campaign automatik``
und ``campaign watchdog`` im Fenster zeigen, steht zusaetzlich in einer
Tagesdatei - dieselben Zeilen, ohne Farbcodes, mit Uhrzeit.
"""

from __future__ import annotations

import io
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from rich.console import Console

from fbgroups import protokoll
from fbgroups.protokoll import Einstellungen, Strom, Tagesdatei


class _Uhr:
    def __init__(self, *zeiten: datetime) -> None:
        self._zeiten = list(zeiten)

    def __call__(self) -> datetime:
        return self._zeiten.pop(0) if len(self._zeiten) > 1 else self._zeiten[0]


def _zeilen(pfad: Path) -> list[str]:
    return pfad.read_text(encoding="utf-8").splitlines()


def test_was_im_fenster_steht_steht_auch_in_der_datei(tmp_path: Path) -> None:
    fenster = io.StringIO()
    datei = Tagesdatei(tmp_path, "automatik", uhr=lambda: datetime(2026, 9, 25, 14, 3, 12))
    strom = Strom(fenster, datei)

    strom.write("Gruppe 3 von 11: ")
    strom.write("kommentiert\nzweite Zeile\n")

    assert fenster.getvalue() == "Gruppe 3 von 11: kommentiert\nzweite Zeile\n"
    assert _zeilen(tmp_path / "automatik-2026-09-25.log") == [
        "14:03:12  Gruppe 3 von 11: kommentiert",
        "14:03:12  zweite Zeile",
    ]


def test_farbcodes_bleiben_im_fenster(tmp_path: Path) -> None:
    datei = Tagesdatei(tmp_path, "automatik", uhr=lambda: datetime(2026, 9, 25, 9, 0, 0))
    strom = Strom(io.StringIO(), datei)

    strom.write("\x1b[1;32mErfolg\x1b[0m   \n\x1b]0;Titel\x07weiter\n")

    assert _zeilen(tmp_path / "automatik-2026-09-25.log") == [
        "09:00:00  Erfolg",
        "09:00:00  weiter",
    ]


def test_ein_wagenruecklauf_ueberschreibt_wie_im_fenster() -> None:
    assert protokoll.bereinigt("10 %\r50 %\r100 %") == "100 %"
    assert protokoll.bereinigt("Windows-Zeile\r") == "Windows-Zeile"


def test_um_mitternacht_beginnt_die_naechste_datei(tmp_path: Path) -> None:
    datei = Tagesdatei(
        tmp_path,
        "waechter",
        uhr=_Uhr(datetime(2026, 9, 25, 23, 59, 59), datetime(2026, 9, 26, 0, 0, 1)),
    )

    datei.zeile("vor Mitternacht")
    datei.zeile("nach Mitternacht")
    datei.schliesse()

    assert _zeilen(tmp_path / "waechter-2026-09-25.log") == ["23:59:59  vor Mitternacht"]
    assert _zeilen(tmp_path / "waechter-2026-09-26.log") == ["00:00:01  nach Mitternacht"]


def test_ein_schreibfehler_haelt_den_lauf_nicht_an(tmp_path: Path) -> None:
    """Das Protokoll schaltet sich ab und sagt es einmal - der Lauf merkt nichts."""
    versperrt = tmp_path / "logs"
    versperrt.write_text("eine Datei, wo ein Ordner sein sollte", encoding="utf-8")
    fenster = io.StringIO()
    stoerung = io.StringIO()
    strom = Strom(fenster, Tagesdatei(versperrt, "automatik", stoerung=stoerung))

    strom.write("erste\n")
    strom.write("zweite\n")

    assert fenster.getvalue() == "erste\nzweite\n"
    assert stoerung.getvalue().count("Protokoll abgeschaltet") == 1


def test_aufgeraeumt_wird_nur_was_dem_befehl_gehoert(tmp_path: Path) -> None:
    for name in (
        "automatik-2026-07-01.log",
        "automatik-2026-09-20.log",
        "waechter-2026-07-01.log",
        "automatik-notizen.log",
    ):
        (tmp_path / name).write_text("x", encoding="utf-8")

    weg = protokoll.raeume_auf(tmp_path, "automatik", 60, date(2026, 9, 25))

    assert [pfad.name for pfad in weg] == ["automatik-2026-07-01.log"]
    assert sorted(pfad.name for pfad in tmp_path.iterdir()) == [
        "automatik-2026-09-20.log",
        "automatik-notizen.log",
        "waechter-2026-07-01.log",
    ]
    assert protokoll.raeume_auf(tmp_path, "automatik", 0, date(2030, 1, 1)) == []


def test_jede_console_des_projekts_schreibt_durch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Grund fuer den Weg ueber ``sys.stdout``: Die ``Console()`` der
    Module entstehen beim Import, lange vor dem Einschalten - und schreiben
    trotzdem ins Protokoll, samt Arabisch und ohne Farbcodes."""
    fenster = io.StringIO()
    monkeypatch.setattr(sys, "stdout", fenster)
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    vorher_angelegt = Console(force_terminal=True, legacy_windows=False, width=80)

    pfad = protokoll.einschalten(Einstellungen(ordner=tmp_path), "automatik")
    protokoll.einschalten(Einstellungen(ordner=tmp_path), "automatik")
    vorher_angelegt.print("[green]kommentiert[/green] in مجموعة السوريين")

    assert pfad is not None
    assert "\x1b[" in fenster.getvalue()
    inhalt = pfad.read_text(encoding="utf-8")
    assert "kommentiert in مجموعة السوريين" in inhalt
    assert "\x1b" not in inhalt
    assert inhalt.count("kommentiert") == 1


def test_die_einstellungen_kommen_aus_der_konfiguration(config) -> None:  # noqa: ANN001
    einst = protokoll.einstellungen(config)

    assert einst.ordner == config.root / "data" / "logs"
    assert einst.tage == 60
