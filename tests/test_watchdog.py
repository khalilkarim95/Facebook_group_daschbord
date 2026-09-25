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


def test_ohne_laufenden_prozess_wird_gestartet(
    sperre: Sperre, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Punkt 4.** Und zwar genau der Befehl von der Kommandozeile.

    Der Dienst gilt als erreichbar, ohne dass einer laeuft: Bis zum
    25.09.2026 bestand der Test nur, solange auf diesem Rechner der
    SSH-Tunnel auf 8090 offen war - er haengt nicht am Netz dieses Rechners.
    """
    monkeypatch.setattr(watchdog, "dienst_erreichbar", lambda *_a, **_k: True)
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
    """Und der Block steht wirklich in ``settings.yaml`` - seit dem Umzug
    (25.09.2026) fuer den oertlichen Betrieb: kein Dienst, kein Tunnel."""
    gelesen = watchdog.einstellungen(config)

    assert gelesen.aktiv is True
    assert gelesen.abstand >= 30
    assert gelesen.server == ""
    assert not gelesen.tunnel.nutzbar
    assert watchdog.baue_befehl(gelesen.server)[-2:] == ["campaign", "automatik"]


def test_nebenbei_meldet_sich_nur_wenn_es_etwas_getan_hat(sperre: Sperre) -> None:
    """Die taegliche Sicherung (25.09.2026) haengt als ``nebenbei`` an der
    Schleife. Die Schleife weiss nicht, was es tut - sie reicht nur weiter."""
    antworten = iter([watchdog.Blick("gesichert", "sicherung-...gz"), None, None])
    gemeldet: list[str] = []

    verlauf = watchdog.wache(
        sperre,
        _einst(),
        starte=_Starter(),
        schlafe=lambda _s: None,
        durchgaenge=3,
        nebenbei=lambda: next(antworten),
        melde=lambda blick: gemeldet.append(blick.art),
    )

    assert [b.art for b in verlauf] == ["gesichert", "gestartet", "gestartet", "gestartet"]
    assert gemeldet == ["gesichert", "gestartet", "gestartet", "gestartet"]


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


# --- Der Tunnel (20.09.2026) ----------------------------------------------
#
# Der Waechter startete einen abgestuerzten Lauf neu, hob aber vor einem
# geschlossenen SSH-Tunnel die Haende: "Dienst antwortet nicht - laeuft der
# SSH-Tunnel?". Die Antwort darauf war jedes Mal ein Mensch mit einem
# zweiten Fenster - nachts also niemand.


class _Fakeprozess:
    """Ein gestarteter Befehl, der laeuft, bis man ihn beendet."""

    def __init__(self, *, lebt: bool = True) -> None:
        self._lebt = lebt

    def poll(self):  # noqa: ANN201 - wie subprocess.Popen
        return None if self._lebt else 0


def _tunnel(**kwargs):
    from fbgroups.marketing import watchdog

    werte = {
        "aktiv": True,
        "ziel": "karim@159.195.216.246",
        "schluessel": "~/.ssh/b-tarikak_vps_new",
        "port": 8090,
        "fernport": 8090,
    }
    werte.update(kwargs)
    return watchdog.Tunnel(**werte)


def test_der_tunnelbefehl_ist_der_befehl_von_hand() -> None:
    """Dieselbe Weiterleitung, derselbe Schluessel, dasselbe Ziel."""
    from fbgroups.marketing import watchdog

    befehl = watchdog.baue_tunnelbefehl(_tunnel())

    assert befehl[0] == "ssh"
    assert "-L" in befehl
    assert "8090:127.0.0.1:8090" in befehl
    assert "ServerAliveInterval=30" in befehl
    assert befehl[-1] == "karim@159.195.216.246"
    assert "b-tarikak_vps_new" in " ".join(befehl)


def test_der_tunnel_oeffnet_keine_kommandozeile() -> None:
    """``-N``: Eine Sitzung, die tagelang offensteht, will niemand.

    Und ``ExitOnForwardFailure``, damit ein ``ssh`` ohne Weiterleitung nicht
    als stehender Tunnel gilt - der Waechter haelt ihn sonst fuer erledigt,
    waehrend nichts durchgeht.
    """
    from fbgroups.marketing import watchdog

    befehl = watchdog.baue_tunnelbefehl(_tunnel())

    assert "-N" in befehl
    assert "ExitOnForwardFailure=yes" in befehl
    assert "ServerAliveCountMax=3" in befehl


def test_im_tunnelbefehl_steht_kein_kennwort() -> None:
    """**Die Zusicherung.** ``ssh`` nimmt es nicht entgegen, und wir auch nicht.

    Ein Kennwort in ``settings.yaml`` oder in einer ``.env`` neben dem
    Bestand waere ein Schluessel ohne Schloss. Es gehoert in den Agenten
    (``ssh-add``) oder in die Hand dessen, der im Terminal sitzt.
    """
    from fbgroups.marketing import watchdog

    befehl = " ".join(watchdog.baue_tunnelbefehl(_tunnel()))

    for verdacht in ("pass", "kennwort", "passphrase", "-p "):
        assert verdacht not in befehl.lower()
    quelle = Path("src/fbgroups/marketing/watchdog.py").read_text(encoding="utf-8")
    assert "sshpass" not in quelle
    einstellungen = Path("config/settings.yaml").read_text(encoding="utf-8")
    assert "passphrase:" not in einstellungen
    assert "kennwort:" not in einstellungen


def test_ein_geschlossener_port_macht_den_tunnel_auf(tmp_path) -> None:
    """Erst aufmachen, dann klagen - und dann den Lauf starten."""
    from fbgroups.marketing import watchdog

    einst = watchdog.Einstellungen(server="http://127.0.0.1:59999", tunnel=_tunnel())
    sperre = watchdog.Sperre(tmp_path / "automatik.lock")
    gestartet: list[list[str]] = []
    wart = watchdog.Tunnelwart(
        einst.tunnel, starte=lambda befehl: gestartet.append(befehl) or _Fakeprozess()
    )

    blick = watchdog.blicke(
        sperre, einst, starte=gestartet.append, tunnel=wart, schlafe=lambda _s: None
    )

    assert gestartet and gestartet[0][0] == "ssh", "der Tunnel zuerst"
    # Der Port antwortet im Test nie - dann wird gewartet, nicht gestartet.
    assert blick.art == "tunnel_gestartet"
    assert not any(befehl[0] != "ssh" for befehl in gestartet), "kein Lauf ohne Port"


def test_ein_laufender_tunnel_wird_nicht_zweimal_aufgemacht(tmp_path) -> None:
    """Ein zweites ``ssh`` auf denselben Port scheiterte ohnehin.

    Es schriebe aber einen Fehler ins Protokoll, an dem nichts liegt - und
    verdeckte damit den Grund, der wirklich zaehlt: Der Tunnel steht und
    leitet trotzdem nicht weiter.
    """
    from fbgroups.marketing import watchdog

    einst = watchdog.Einstellungen(server="http://127.0.0.1:59999", tunnel=_tunnel())
    sperre = watchdog.Sperre(tmp_path / "automatik.lock")
    versuche: list[list[str]] = []
    wart = watchdog.Tunnelwart(
        einst.tunnel, starte=lambda befehl: versuche.append(befehl) or _Fakeprozess()
    )
    wart.oeffne()

    blick = watchdog.blicke(
        sperre, einst, starte=versuche.append, tunnel=wart, schlafe=lambda _s: None
    )

    assert len(versuche) == 1, "der laufende Tunnel bleibt"
    assert blick.art == "dienst_weg"
    assert "leitet aber nicht weiter" in blick.meldung


def test_ohne_eingetragenen_tunnel_bleibt_alles_wie_bisher(tmp_path) -> None:
    """Der Waechter macht nur auf, was in ``settings.yaml`` steht."""
    from fbgroups.marketing import watchdog

    einst = watchdog.Einstellungen(server="http://127.0.0.1:59999")
    sperre = watchdog.Sperre(tmp_path / "automatik.lock")
    gestartet: list[list[str]] = []

    blick = watchdog.blicke(
        sperre,
        einst,
        starte=gestartet.append,
        tunnel=watchdog.Tunnelwart(einst.tunnel),
        schlafe=lambda _s: None,
    )

    assert blick.art == "dienst_weg"
    assert not gestartet


def test_ein_laufender_lauf_geht_dem_tunnel_vor(tmp_path) -> None:
    """Haelt jemand die Sperre, wird **nichts** angefasst - auch kein ssh.

    Die Reihenfolge der Pruefungen ist nicht beliebig: Ein laufender Lauf
    hat seinen Tunnel, sonst liefe er nicht.
    """
    from fbgroups.marketing import watchdog

    einst = watchdog.Einstellungen(server="http://127.0.0.1:59999", tunnel=_tunnel())
    sperre = watchdog.Sperre(tmp_path / "automatik.lock")
    assert sperre.nimm()
    versuche: list[list[str]] = []
    wart = watchdog.Tunnelwart(
        einst.tunnel, starte=lambda befehl: versuche.append(befehl) or _Fakeprozess()
    )

    blick = watchdog.blicke(sperre, einst, starte=versuche.append, tunnel=wart)

    assert blick.art == "laeuft"
    assert not versuche


def test_die_einstellungen_lesen_den_tunnel() -> None:
    """Aus ``config/settings.yaml``, nicht aus dem Code - und seit dem Umzug
    (25.09.2026) abgeschaltet: Es gibt keinen Dienst mehr, zu dem er fuehrte."""
    from fbgroups.config import load_config
    from fbgroups.marketing import watchdog

    einst = watchdog.einstellungen(load_config())

    assert not einst.tunnel.nutzbar, "der Bestand liegt auf diesem Rechner"
    assert einst.server == ""


def test_der_waechter_kennt_weiterhin_keine_kampagnenlogik_mit_tunnel() -> None:
    """Ein Port ist keine Gruppe.

    Der Tunnel aendert an der Zusicherung nichts: keine Warteschlange, keine
    Rangfolge, kein Takt, keine Entscheidung ueber eine Gruppe.
    """
    from fbgroups.marketing import watchdog

    quelle = Path("src/fbgroups/marketing/watchdog.py").read_text(encoding="utf-8")

    for verboten in ("lauf", "automatik", "store", "vorlagen", "entscheidung"):
        assert f"from fbgroups.marketing import {verboten}" not in quelle
    # Und der gestartete Befehl ist derselbe wie vorher.
    assert "--neu" not in watchdog.baue_befehl("http://127.0.0.1:8090")
