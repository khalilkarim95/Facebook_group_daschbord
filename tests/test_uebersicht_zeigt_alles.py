"""Die Uebersicht zeigt den Bestand - und sagt, was sie ausblendet.

Der Anlass vom 14.09.2026 war eine Frage des Nutzers: *"Wenn die Gruppen
innerhalb einer Kampagne sind und bearbeitet werden (Posts oder Kommentare),
dann finde ich sie nicht mehr in der Gruppenuebersicht."* Daneben stand eine
Zahl: **471 von 621**.

Nachgestellt ergab das zwei Befunde, und nur einer davon war der vermutete:

1. **Es geht keine Gruppe verloren.** Eine bearbeitete Gruppe steht
   unveraendert in der Nutzlast - was sich aendert, ist ihr Stand.
2. **Die Uebersicht blendete beim ersten Aufruf selbst aus.** ``nur
   bewertete`` und ``nur bearbeitete`` standen im HTML auf ``checked``; damit
   fehlten jede Gruppe ohne Score und jede ausgeschlossene, und der Zaehler
   nannte nur die Zahl. 621 - 150 = 471.
3. **Spalte und Filter sprachen zwei Sprachen.** In der Spalte stand "Anfrage
   gesendet", im Filter hiess derselbe Wert "Beitritt angefragt" - wer die
   Gruppe suchte, fand das Wort nicht und griff zu "nichts getan". Danach war
   sie weg.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.marketing.dashboard import sammle_daten, status_label
from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    MarketingStatus,
    PostStatus,
)
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

KAMPAGNE = "k"
BEARBEITET = "482910573829104"
OFFEN = "739201847362915"
OHNE_SCORE = "111222333444555"
AUSGESCHLOSSEN = "999888777666555"


@pytest.fixture()
def bestand(tmp_path: Path) -> Path:
    """Vier Gruppen - je eine fuer jeden Fall, um den es geht."""
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=gid,
                    url_canonical=f"https://www.facebook.com/groups/{gid}",
                    name=f"شحن من المانيا الى سوريا {gid[-3:]}",
                    category="versand",
                    score=score,
                    score_max=100.0 if score is not None else None,
                )
                for gid, score in (
                    (BEARBEITET, 90.0),
                    (OFFEN, 80.0),
                    (OHNE_SCORE, None),
                    (AUSGESCHLOSSEN, 70.0),
                )
            ]
        )

    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(campaign_id=KAMPAGNE, name="test_neu", landing_page="https://b.de/")
        )
        for i, gid in enumerate((BEARBEITET, OFFEN, OHNE_SCORE, AUSGESCHLOSSEN)):
            store.add_link(
                CampaignGroup(
                    campaign_id=KAMPAGNE,
                    group_id=gid,
                    tracking_code=f"FB-SYR-BER-{i:03d}",
                    tracking_url=f"https://go.b.de/r/FB-SYR-BER-{i:03d}",
                )
            )

        # Eine Gruppe wie nach einem Lauf: Beitrag veroeffentlicht, Aufnahme
        # beantragt.
        store.set_post_status(KAMPAGNE, BEARBEITET, PostStatus.VEROEFFENTLICHT)
        eintrag = store.load_marketing(BEARBEITET)
        eintrag.marketing_status = MarketingStatus.JOIN_REQUESTED
        store.save_marketing(eintrag)

        # Und eine, die von Hand ausgeschlossen wurde.
        aus = store.load_marketing(AUSGESCHLOSSEN)
        aus.bearbeiten = False
        store.save_marketing(aus)

    return pfad


@pytest.fixture()
def zeilen(bestand: Path, config) -> list[dict]:
    return sammle_daten(config, bestand)["gruppen"]


@pytest.fixture()
def seite(bestand: Path, config) -> str:
    from fbgroups.marketing.dashboard import render

    return render(sammle_daten(config, bestand))


# --- 1. Es geht keine Gruppe verloren --------------------------------------

def test_eine_bearbeitete_gruppe_bleibt_in_der_uebersicht(zeilen) -> None:
    """Der vermutete Fehler - und er war keiner.

    Ein veroeffentlichter Beitrag nimmt die Gruppe nicht aus der Nutzlast.
    Was sich aendert, ist ihr Stand; gefunden wird sie weiterhin.
    """
    nach_id = {z["id"]: z for z in zeilen}

    assert set(nach_id) == {BEARBEITET, OFFEN, OHNE_SCORE, AUSGESCHLOSSEN}
    assert nach_id[BEARBEITET]["beitrag_status"] == "veroeffentlicht"
    assert nach_id[OFFEN]["beitrag_status"] == "offen"


def test_auch_die_ausgeschlossene_steht_in_der_nutzlast(zeilen) -> None:
    """``bearbeiten = 0`` ist ein Merkmal der Zeile, kein Loeschen.

    Die Gruppe behaelt ihren Tracking-Code und ihre Zahlen; ob sie angezeigt
    wird, entscheidet ein Haken - und der steht jetzt auf aus.
    """
    nach_id = {z["id"]: z for z in zeilen}

    assert nach_id[AUSGESCHLOSSEN]["bearbeiten"] is False
    assert nach_id[OFFEN]["bearbeiten"] is True


# --- 2. Die Vorgabe blendet nichts mehr aus --------------------------------

def test_die_uebersicht_faengt_mit_dem_ganzen_bestand_an(seite: str) -> None:
    """**Der eigentliche Fehler.**

    Beide Haken standen auf ``checked``. Damit fehlten beim ersten Aufruf
    jede Gruppe ohne Score und jede ausgeschlossene - 471 von 621 -, und
    nirgends stand, warum. Eine Uebersicht, die "Gruppen" heisst, zeigt den
    Bestand.
    """
    for kennung in ("f-bewertet", "f-bearbeitet"):
        marke = f'id="{kennung}"'
        assert marke in seite
        stueck = seite.split(marke, 1)[1].split(">", 1)[0]
        assert "checked" not in stueck, f"{kennung} blendet beim ersten Aufruf wieder aus"


def test_der_zaehler_nennt_den_grund_und_den_weg_zurueck(seite: str) -> None:
    """"4 von 621" sagt, DASS etwas fehlt, und verschweigt, warum.

    Genau daran ist die Suche gescheitert: Der Nutzer hatte einen Stand
    gewaehlt und hielt die bearbeiteten Gruppen fuer verschwunden.
    """
    assert 'id="treffer-grund"' in seite
    assert "ausgeblendet durch" in seite
    assert "Filter zurücksetzen" in seite
    assert "function aktiveFilter()" in seite


# --- 3. Ein Wortschatz fuer den Stand --------------------------------------

def test_spalte_und_filter_nennen_denselben_stand_gleich(seite: str, zeilen) -> None:
    """Zwei Namen fuer ein Feld, und die Gruppe war nicht mehr zu finden.

    Die Spalte zeigte "Anfrage gesendet", der Filter bot "Beitritt
    angefragt". Beide kommen jetzt aus ``status_label`` - derselben Quelle,
    aus der auch das Filterfeld gefuellt wird.
    """
    assert "nicht gesendet" not in seite
    assert "Anfrage gesendet" not in seite
    # Die Zelle liest die Beschriftung aus den mitgelieferten Staenden.
    assert "DATEN.staende.find" in seite

    nach_id = {z["id"]: z for z in zeilen}
    assert nach_id[BEARBEITET]["marketing_label"] == status_label("beitritt_angefragt")
    assert nach_id[OFFEN]["marketing_label"] == status_label("not_contacted")


def test_jeder_vorkommende_stand_ist_auch_filterbar(zeilen, seite: str) -> None:
    """Das Filterfeld wird aus den Zeilen gefuellt - also fehlt keiner.

    Die Zusicherung dahinter: Es gibt keinen Stand, in den eine Gruppe
    geraten kann, ohne dass man sie danach wieder herausfiltern koennte.
    """
    assert 'fuelleAuswahl("f-marketing", zeilen.map((z) => z.marketing_label))' in seite

    vorkommend = {z["marketing_label"] for z in zeilen}
    assert vorkommend == {
        status_label("beitritt_angefragt"),
        status_label("not_contacted"),
    }


# --- 4. Ein Beitrag aendert den Stand nicht --------------------------------

def test_ein_veroeffentlichter_beitrag_setzt_den_stand_nicht(zeilen) -> None:
    """Die beiden Achsen bleiben getrennt.

    "Wo stehen wir im Kooperationsweg?" (``marketing_status``) und "ist der
    Beitrag hinaus?" (``post_status``) sind zwei Fragen. Wer sie
    zusammenlegte, koennte die eine nicht mehr lesen, ohne die andere zu
    veraendern - und genau daran haengt, dass man eine bearbeitete Gruppe
    ueber ihren Stand wiederfindet.
    """
    nach_id = {z["id"]: z for z in zeilen}
    ohne_lauf = nach_id[OHNE_SCORE]

    assert ohne_lauf["beitrag_status"] == "offen"
    assert ohne_lauf["marketing_label"] == status_label("not_contacted")
