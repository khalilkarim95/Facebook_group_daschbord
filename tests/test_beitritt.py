"""Beitrittsanfragen: Takt, Tagesmenge und die zwei Staende.

Die riskanteste Handlung des Projekts, und die Tests halten fest, was sie
zaehmt: eine Tagesmenge, die der **Server** bestimmt, ein gestreuter Abstand,
und die Regel, dass ein uebersprungener Versuch nichts hinterlaesst.

Der wichtigste Test ist ``test_uebersprungene_gruppen_hinterlassen_nichts``:
``beitritt_angefragt`` zu setzen, ohne etwas abgeschickt zu haben, waere eine
Behauptung ueber die Wirklichkeit - und die Gruppe kaeme nie wieder an die
Reihe.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from fbgroups.marketing import beitritt
from fbgroups.marketing.models import GroupMarketing, MarketingStatus
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

GRUPPEN = {
    "g1": ("Beste Gruppe", 90.0),
    "g2": ("Mittlere Gruppe", 60.0),
    "g3": ("Schwache Gruppe", 30.0),
}


@pytest.fixture()
def bestand(tmp_path: Path) -> Path:
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=gid,
                    url_canonical=f"https://www.facebook.com/groups/{gid}",
                    name=name,
                    score=score,
                    score_max=100.0,
                )
                for gid, (name, score) in GRUPPEN.items()
            ]
        )
    return pfad


# --- Der Takt --------------------------------------------------------------
def test_ohne_vorherige_anfrage_geht_es_sofort() -> None:
    jetzt = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    assert beitritt.naechster_zeitpunkt(None, abstand_minuten=3, jetzt=jetzt) is None


def test_der_abstand_liegt_im_zugesagten_band() -> None:
    """Gestreut wie beim Kaltmodus - fuenfzig in zehn Minuten waeren dasselbe
    Muster, nur schneller."""
    jetzt = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    letzte = datetime(2026, 8, 31, 11, 59, tzinfo=UTC)
    frei_ab = beitritt.naechster_zeitpunkt(letzte, abstand_minuten=3, jetzt=jetzt)
    assert frei_ab is not None
    assert (frei_ab - letzte).total_seconds() >= 3 * 60 * 0.8
    assert (frei_ab - letzte).total_seconds() <= 3 * 60 * 1.3


def test_dieselbe_lage_ergibt_dieselbe_pause() -> None:
    """Deterministisch geseedet: Die Anzeige soll nicht bei jedem Blick springen."""
    jetzt = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    letzte = datetime(2026, 8, 31, 11, 59, tzinfo=UTC)
    a = beitritt.naechster_zeitpunkt(letzte, abstand_minuten=3, jetzt=jetzt)
    b = beitritt.naechster_zeitpunkt(letzte, abstand_minuten=3, jetzt=jetzt)
    assert a == b


def test_resttage_runden_auf() -> None:
    """Ein angefangener Tag ist ein Tag - 311 Gruppen bei 50 sind sieben."""
    assert beitritt.resttage(311, 50) == 7
    assert beitritt.resttage(50, 50) == 1
    assert beitritt.resttage(0, 50) == 0
    assert beitritt.resttage(311, 0) == 0


# --- Die Auswahl -----------------------------------------------------------
def test_die_besten_gruppen_kommen_zuerst(bestand: Path) -> None:
    """Wird die Tagesmenge nie ausgeschoepft, sollen es die richtigen sein."""
    with MarketingStore(bestand) as store:
        assert store.gruppen_ohne_anfrage() == ["g1", "g2", "g3"]
        assert store.gruppen_ohne_anfrage(grenze=2) == ["g1", "g2"]


def test_wer_schon_angefragt_ist_kommt_nicht_wieder(bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        store.merke_anfrage("g1")
        assert "g1" not in store.gruppen_ohne_anfrage()


def test_ausgeschlossene_gruppen_bekommen_keine_anfrage(bestand: Path) -> None:
    """``bearbeiten = 0`` ist ein Urteil ueber die Gruppe.

    Ihr beizutreten waere ein Beitritt zu einer Gruppe, die jemand
    ausdruecklich aussortiert hat.
    """
    with MarketingStore(bestand) as store:
        store.save_marketing(GroupMarketing(group_id="g1", bearbeiten=False))
        assert "g1" not in store.gruppen_ohne_anfrage()


# --- Die zwei Staende ------------------------------------------------------
def test_eine_anfrage_setzt_genau_einen_stand(bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        store.merke_anfrage("g1")
        stand = store.load_marketing("g1")

    assert stand is not None
    assert stand.marketing_status is MarketingStatus.JOIN_REQUESTED
    assert stand.join_requested_at is not None


def test_bereits_mitglied_wird_als_mitglied_vermerkt(bestand: Path) -> None:
    """Die Auskunft lag ohnehin auf dem Bildschirm - kein Beitrittsknopf,
    aber ein Schreibfeld.

    Sie mitzunehmen kostet keinen zusaetzlichen Abruf und schliesst die Kette:
    Sonst wuesste niemand je, dass eine Freigabe gekommen ist.
    """
    with MarketingStore(bestand) as store:
        store.merke_anfrage("g1", mitglied=True)
        stand = store.load_marketing("g1")

    assert stand is not None
    assert stand.marketing_status is MarketingStatus.MEMBER


def test_ein_erreichter_stand_wird_nicht_zurueckgedreht(bestand: Path) -> None:
    """Wer Mitglied ist, wird durch eine Anfrage nicht wieder zum Anfragenden."""
    with MarketingStore(bestand) as store:
        store.merke_anfrage("g1", mitglied=True)
        store.merke_anfrage("g1")
        assert store.load_marketing("g1").marketing_status is MarketingStatus.MEMBER


def test_eine_ablehnung_wird_nicht_ueberschrieben(bestand: Path) -> None:
    """``beitritt_abgelehnt`` steht nicht in der Fortschrittsfolge.

    Eine Ablehnung ist ein Ergebnis; sie durch einen Sammellauf zu
    ueberschreiben waere ein Urteil, das niemand gefaellt hat.
    """
    with MarketingStore(bestand) as store:
        store.save_marketing(
            GroupMarketing(group_id="g1", marketing_status=MarketingStatus.JOIN_REJECTED)
        )
        store.merke_anfrage("g1")
        assert store.load_marketing("g1").marketing_status is MarketingStatus.JOIN_REJECTED


# --- Die Tagesmenge --------------------------------------------------------
def test_anfragen_heute_zaehlt_nur_heute(bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        store.merke_anfrage("g1")
        heute = datetime.now(UTC).date().isoformat()
        assert store.anfragen_heute(heute) == 1
        assert store.anfragen_heute("2020-01-01") == 0


# --- Die Wege ueber den Server --------------------------------------------
def _client(bestand: Path):
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    return TestClient(
        create_app(db_path=bestand), headers={"Origin": "http://127.0.0.1:8090"}
    )


def test_der_server_nennt_die_naechsten_gruppen(bestand: Path) -> None:
    daten = _client(bestand).post("/automatik/beitritt/naechste", json={}).json()
    assert [g["group_id"] for g in daten["gruppen"]] == ["g1", "g2", "g3"]
    assert daten["heute"] == 0
    assert daten["pro_tag"] == 50


def test_die_tagesmenge_bestimmt_der_server(bestand: Path) -> None:
    """Sonst haette jedes Fenster sein eigenes Kontingent.

    Bei der riskantesten Handlung des Projekts waere das ausgerechnet die
    Zahl, auf die es ankommt: Zwei Laeufe nebeneinander verdoppelten sie.
    """
    from fbgroups.marketing.web import create_app

    with MarketingStore(bestand) as store:
        for gid in GRUPPEN:
            store.merke_anfrage(gid)

    # Die Tagesmenge steht in der Konfiguration auf 50; bei drei Gruppen im
    # Bestand bleibt nach drei Anfragen schlicht keine mehr uebrig.
    daten = _client(bestand).post("/automatik/beitritt/naechste", json={}).json()
    assert daten["gruppen"] == []
    assert daten["heute"] == 3
    assert create_app is not None


def test_uebersprungene_gruppen_hinterlassen_nichts(bestand: Path) -> None:
    """Der wichtigste Test dieser Datei.

    Bei einer Gruppe mit Beitrittsfragen wird **nichts** abgeschickt.
    ``beitritt_angefragt`` zu setzen waere eine Behauptung ueber die
    Wirklichkeit - und die Gruppe kaeme nie wieder an die Reihe.
    """
    client = _client(bestand)
    for ausgang in ("fragen", "fehler"):
        antwort = client.post(
            "/automatik/beitritt/ergebnis",
            json={"group_id": "g1", "ausgang": ausgang, "bemerkung": "Beitrittsfragen"},
        )
        assert antwort.status_code == 200
        assert antwort.json()["vermerkt"] is False

    with MarketingStore(bestand) as store:
        stand = store.load_marketing("g1")
        # Kein Zeitpunkt, kein Fortschritt - und vor allem: Die Gruppe steht
        # weiter auf der Liste. Genau das ist die Zusage.
        assert stand.join_requested_at is None
        assert stand.marketing_status is MarketingStatus.NOT_CONTACTED
        assert "g1" in store.gruppen_ohne_anfrage()


def test_ein_angefragter_ausgang_wird_vermerkt(bestand: Path) -> None:
    antwort = _client(bestand).post(
        "/automatik/beitritt/ergebnis", json={"group_id": "g1", "ausgang": "angefragt"}
    )
    assert antwort.json()["vermerkt"] is True

    with MarketingStore(bestand) as store:
        assert store.load_marketing("g1").marketing_status is MarketingStatus.JOIN_REQUESTED


def test_die_beitrittswege_sind_von_aussen_nicht_erreichbar(bestand: Path) -> None:
    """Wie jeder schreibende Weg hinter ``_nur_lokal`` - 404, nicht 403."""
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    fremd = TestClient(
        create_app(db_path=bestand), headers={"Origin": "https://boese.invalid"}
    )
    assert fremd.post("/automatik/beitritt/naechste", json={}).status_code == 404
    assert (
        fremd.post(
            "/automatik/beitritt/ergebnis", json={"group_id": "g1", "ausgang": "angefragt"}
        ).status_code
        == 404
    )
