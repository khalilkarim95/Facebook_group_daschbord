"""Der Waechter: sorgt dafuer, dass **ein** Lauf laeuft - und sonst nichts.

Die Anforderung vom 15.09.2026 in Tests. Ihr Kern ist eine Abgrenzung:
*"Lege die Kampagnenlogik nicht in den Waechter. Er ist nur dafuer
verantwortlich, dass der Prozess laeuft."*

Geprueft wird deshalb vor allem, was er **nicht** tut - und das laesst sich
an einer Stelle festmachen: ``baue_befehl``. Was dort nicht dransteht, kann
nicht geschehen.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fbgroups.marketing import watchdog
from fbgroups.marketing.watchdog import Einstellungen, Sperre


@pytest.fixture()
def sperre(tmp_path: Path) -> Sperre:
    return Sperre(tmp_path / "automatik.lock")


class _Starter:
    """Statt eines Prozesses: haelt fest, was gestartet worden waere."""

    def __init__(self) -> None:
        self.befehle: list[list[str]] = []

    def __call__(self, befehl: list[str]) -> object:
        self.befehle.append(befehl)
        return object()


def _einst(**kw) -> Einstellungen:
    # Ohne Server: ``dienst_erreichbar`` hat dann nichts zu pruefen, und die
    # Tests haengen nicht am Netz dieses Rechners.
    return Einstellungen(**{"aktiv": True, "abstand_sekunden": 300, "server": "", **kw})


# --- 1./7. Genau ein Lauf --------------------------------------------------

def test_eine_freie_sperre_laesst_sich_nehmen(sperre: Sperre) -> None:
    assert sperre.laeuft() is False
    assert sperre.nimm() is True
    assert sperre.laeuft() is True


def test_eine_gehaltene_sperre_laesst_sich_nicht_zweimal_nehmen(sperre: Sperre) -> None:
    """**Punkt 7.** Zwei gleichzeitige Laeufe setzten denselben Kommentar
    zweimal ab - und zwei Browser arbeiteten in derselben Gruppe."""
    sperre.nimm()

    assert sperre.nimm() is False


def test_eine_sperre_mit_totem_prozess_gilt_nicht(sperre: Sperre) -> None:
    """**Punkt 6.** Ein Absturz darf den naechsten Lauf nicht aussperren.

    Die Prozesskennung ist die Wahrheit, nicht die Datei: Wo sie nicht mehr
    lebt, ist die Sperre verwaist.
    """
    sperre.pfad.write_text(
        json.dumps({"pid": 999_999_999, "seit": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )

    assert sperre.laeuft() is False
    assert sperre.nimm() is True


def test_eine_uralte_sperre_gilt_nicht(sperre: Sperre) -> None:
    """Der Rueckfall fuer den Fall, dass die Kennung neu vergeben wurde.

    Nach einem Neustart des Rechners kann dieselbe Zahl an ein fremdes
    Programm gegangen sein - dann lebt sie, ohne unser Lauf zu sein.
    """
    alt = datetime.now(UTC) - timedelta(hours=watchdog.VERWAIST_NACH_STUNDEN + 1)
    sperre.pfad.write_text(
        json.dumps({"pid": os.getpid(), "seit": alt.isoformat()}), encoding="utf-8"
    )

    assert sperre.laeuft() is False


def test_eine_fremde_sperre_wird_nicht_freigegeben(sperre: Sperre, monkeypatch) -> None:
    """Sonst raeumte ein abgewiesener Lauf dem laufenden die Datei weg.

    Nachgestellt wird eine **lebende** fremde Kennung: Eine tote gilt als
    verwaist, und die aufzuraeumen ist richtig - genau das prueft
    ``test_eine_sperre_mit_totem_prozess_gilt_nicht``.
    """
    monkeypatch.setattr(watchdog, "_lebt", lambda _pid: True)
    sperre.pfad.write_text(
        json.dumps({"pid": os.getpid() + 1, "seit": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )

    sperre.gib_frei()

    assert sperre.pfad.exists(), "die fremde Sperre bleibt liegen"


def test_eine_verwaiste_sperre_wird_aufgeraeumt(sperre: Sperre) -> None:
    """Die Gegenprobe: Was niemandem mehr gehoert, darf weg."""
    sperre.pfad.write_text(
        json.dumps({"pid": 999_999_999, "seit": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )

    sperre.gib_frei()

    assert sperre.pfad.exists() is False


def test_eine_kaputte_sperrdatei_haelt_nichts_auf(sperre: Sperre) -> None:
    """Ein halb geschriebenes JSON ist kein laufender Lauf."""
    sperre.pfad.write_text("{kaputt", encoding="utf-8")

    assert sperre.laeuft() is False


# --- 2./3./4. Der Blick ----------------------------------------------------

def test_bei_laufendem_prozess_wird_nichts_gestartet(sperre: Sperre) -> None:
    """**Punkt 3.** Der haeufigste Fall - und der, in dem nichts geschieht."""
    sperre.nimm()
    starter = _Starter()

    blick = watchdog.blicke(sperre, _einst(), starte=starter)

    assert blick.art == "laeuft"
    assert starter.befehle == []


def test_ohne_laufenden_prozess_wird_gestartet(sperre: Sperre) -> None:
    """**Punkt 4.** Und zwar genau der Befehl von der Kommandozeile."""
    starter = _Starter()

    blick = watchdog.blicke(sperre, _einst(server="http://127.0.0.1:8090"), starte=starter)

    assert blick.art == "gestartet"
    assert len(starter.befehle) == 1
    assert starter.befehle[0][1:] == [
        "-m", "fbgroups.cli", "campaign", "automatik", "--server", "http://127.0.0.1:8090",
    ]


def test_ein_abgeschalteter_waechter_startet_nichts(sperre: Sperre) -> None:
    """**Punkt 12.** ``enabled: false`` heisst aus, nicht "leiser"."""
    starter = _Starter()

    blick = watchdog.blicke(sperre, _einst(aktiv=False), starte=starter)

    assert blick.art == "abgeschaltet"
    assert starter.befehle == []


# --- 5. Ein geschlossener Tunnel ist kein Fehlschlag der Kampagne ---------

def test_ohne_dienst_wird_gewartet_statt_gestartet(sperre: Sperre, monkeypatch) -> None:
    """**Punkt 5.**

    Ein Lauf ohne Tunnel scheiterte an der ersten Anfrage, meldete nichts
    und buchte nichts - er waere ein Fehlschlag, der keiner ist. Gewartet
    wird, und der naechste Blick versucht es erneut.
    """
    monkeypatch.setattr(watchdog, "dienst_erreichbar", lambda *_a, **_k: False)
    starter = _Starter()

    blick = watchdog.blicke(sperre, _einst(server="http://127.0.0.1:8090"), starte=starter)

    assert blick.art == "dienst_weg"
    assert starter.befehle == []
    assert "Tunnel" in blick.meldung


def test_ein_laufender_prozess_geht_dem_dienst_vor(sperre: Sperre, monkeypatch) -> None:
    """Die Reihenfolge der Pruefungen ist nicht beliebig.

    Laeuft schon einer, ist alles gut - auch wenn der Tunnel gerade
    wackelt. Ihn deswegen als "Dienst weg" zu melden waere eine Auskunft
    ueber den falschen Gegenstand.
    """
    sperre.nimm()
    monkeypatch.setattr(watchdog, "dienst_erreichbar", lambda *_a, **_k: False)

    assert watchdog.blicke(sperre, _einst(server="x"), starte=_Starter()).art == "laeuft"


def test_ohne_server_wird_kein_dienst_verlangt() -> None:
    """Der oertliche Lauf braucht keinen - sonst startete er nie."""
    assert watchdog.dienst_erreichbar("") is True


# --- 6. Nach einem Absturz ------------------------------------------------

def test_nach_einem_absturz_wird_im_naechsten_blick_gestartet(sperre: Sperre) -> None:
    """**Punkt 6.** Der Absturz hinterlaesst eine Sperre mit toter Kennung."""
    sperre.pfad.write_text(
        json.dumps({"pid": 999_999_999, "seit": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )
    starter = _Starter()

    blick = watchdog.blicke(sperre, _einst(), starte=starter)

    assert blick.art == "gestartet"
    assert len(starter.befehle) == 1


# --- 8./9./10. Was der Waechter NICHT tut ---------------------------------

def test_der_befehl_beginnt_keine_kampagne_von_vorn() -> None:
    """**Punkt 10.** Kein ``--neu``.

    Mit ``--neu`` schloesse jeder Blick den offenen Lauf und froere eine
    frische Liste ein - alle fuenf Minuten. Die uebersprungenen Gruppen
    kaemen jedes Mal zurueck, und fertig wuerde die Kampagne nie.
    """
    befehl = watchdog.baue_befehl("http://127.0.0.1:8090")

    assert "--neu" not in befehl


def test_der_befehl_legt_keine_kampagne_an_und_schliesst_keine() -> None:
    """**Punkte 8 und 9**, an der Stelle, an der sie nachpruefbar sind.

    Der Waechter ruft genau einen Befehl auf; was dort nicht dransteht, kann
    nicht geschehen. ``campaign new`` und ``campaign status`` stehen nicht
    dran - und ``completed`` wird ohnehin nur an einer Stelle gesetzt, die
    ein erreichtes Ziel verlangt.
    """
    befehl = watchdog.baue_befehl("http://127.0.0.1:8090")

    assert befehl[1:5] == ["-m", "fbgroups.cli", "campaign", "automatik"]
    for verboten in ("new", "status", "reset", "--neu", "--limit", "--dry-run", "--kampagne"):
        assert verboten not in befehl[5:], f"{verboten} gehoert nicht in den Waechter"


def test_der_waechter_kennt_keine_kampagnenlogik() -> None:
    """Die Abgrenzung, um die es in der Anforderung geht.

    Kein Import von ``lauf``, ``automatik``, ``store`` oder ``vorlagen``:
    Ein Waechter, der selbst entscheiden koennte, was gepostet wird, waere
    ein zweiter Runner mit eigener Zaehlweise.
    """
    quelltext = Path("src/fbgroups/marketing/watchdog.py").read_text(encoding="utf-8")

    for modul in ("marketing.lauf", "marketing.automatik", "marketing.store",
                  "marketing.vorlagen", "marketing.entscheidung"):
        assert f"import {modul}" not in quelltext
        assert f"from fbgroups.{modul}" not in quelltext


# --- 11./12. Schleife, Protokoll, Einstellungen ---------------------------

def test_die_schleife_sieht_wiederholt_nach(sperre: Sperre) -> None:
    """Und schlaeft dazwischen - ohne Browser und ohne Datenbank."""
    starter = _Starter()
    geschlafen: list[float] = []

    verlauf = watchdog.wache(
        sperre,
        _einst(abstand_sekunden=42),
        starte=starter,
        schlafe=geschlafen.append,
        durchgaenge=3,
    )

    assert [b.art for b in verlauf] == ["gestartet", "gestartet", "gestartet"]
    assert geschlafen == [42.0, 42.0], "zwischen drei Blicken wird zweimal geschlafen"


def test_jeder_blick_wird_gemeldet(sperre: Sperre) -> None:
    """**Punkt 11.** Vier unterscheidbare Arten, jede mit Grund im Klartext."""
    gemeldet: list[watchdog.Blick] = []

    watchdog.wache(
        sperre, _einst(), starte=_Starter(), melde=gemeldet.append,
        schlafe=lambda _s: None, durchgaenge=1,
    )

    assert len(gemeldet) == 1
    assert gemeldet[0].art in {"laeuft", "gestartet", "dienst_weg", "abgeschaltet"}
    assert gemeldet[0].meldung


def test_ein_abgeschalteter_waechter_schlaeft_nicht_endlos(sperre: Sperre) -> None:
    """Abgeschaltet heisst abgeschaltet - nicht "alle fuenf Minuten nachsehen,
    ob es wieder an ist". Wer ihn einschaltet, startet ihn neu."""
    geschlafen: list[float] = []

    verlauf = watchdog.wache(
        sperre, _einst(aktiv=False), starte=_Starter(),
        schlafe=geschlafen.append, durchgaenge=0,
    )

    assert [b.art for b in verlauf] == ["abgeschaltet"]
    assert geschlafen == []


def test_der_abstand_hat_eine_untergrenze() -> None:
    """Ein Waechter im Sekundentakt ist eine Last, kein Waechter."""
    assert Einstellungen(abstand_sekunden=1).abstand == 30.0
    assert Einstellungen(abstand_sekunden=0).abstand == 30.0
    assert Einstellungen(abstand_sekunden=600).abstand == 600.0


def test_die_einstellungen_kommen_aus_der_konfiguration(config) -> None:
    """Und der Block steht wirklich in ``settings.yaml``."""
    gelesen = watchdog.einstellungen(config)

    assert gelesen.aktiv is True
    assert gelesen.abstand >= 30
    assert gelesen.server.startswith("http")


def test_ohne_block_gilt_die_vorgabe() -> None:
    """Eine bestehende ``settings.yaml`` ohne den Block verhaelt sich
    vernuenftig - und nicht "abgeschaltet"."""

    class _Leer:
        def get(self, *_pfad, default=None):  # noqa: ANN001, ANN202
            return default

    gelesen = watchdog.einstellungen(_Leer())

    assert gelesen.aktiv is True
    assert gelesen.abstand == float(watchdog.VORGABE_ABSTAND)
    assert gelesen.server == ""
