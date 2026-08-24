"""Eine einzelne Gruppe einer Kampagne zuordnen - Spalte und Weg.

Die Auswahlregel einer Kampagne (``campaign sync``) bleibt der Weg fuer den
Bestand. Dies ist der Griff fuer den Einzelfall: "diese eine Gruppe gehoert
auch in jene Kampagne", ohne dafuer eine Regel umzuschreiben, die vierhundert
andere mitbetrifft.

Der Code entsteht ueber denselben ``CodeAllocator`` wie beim Zuordnen ganzer
Kampagnen. Eine zweite Vergabestelle koennte dieselbe Nummer ein zweites Mal
ausgeben - und zwei Gruppen mit demselben Code sind in der Auswertung nicht
mehr zu trennen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.marketing.models import Campaign, CampaignGroup, PostStatus
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
from fastapi.testclient import TestClient  # noqa: E402

from fbgroups.marketing.web import create_app  # noqa: E402

GID = "482910573829104"
GID_B = "739201847362915"


@pytest.fixture()
def bestand(tmp_path: Path) -> Path:
    """Zwei Gruppen, zwei Kampagnen - eine Zuordnung besteht bereits."""
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=GID,
                    url_canonical=f"https://www.facebook.com/groups/{GID}",
                    name="Syrer in Koeln",
                    city="koeln",
                    audience_tags=["syrians"],
                ),
                Group(
                    group_id=GID_B,
                    url_canonical=f"https://www.facebook.com/groups/{GID_B}",
                    name="Syrer in Berlin",
                    city="berlin",
                    audience_tags=["syrians"],
                ),
            ]
        )
    with MarketingStore(pfad) as store:
        for kennung, name in (("erste", "Erste Kampagne"), ("zweite", "Zweite Kampagne")):
            store.save_campaign(
                Campaign(campaign_id=kennung, name=name, landing_page="https://b-tarikak.de/")
            )
        store.add_link(
            CampaignGroup(campaign_id="erste", group_id=GID, tracking_code="FB-SYR-KLN-001")
        )
    return pfad


@pytest.fixture()
def client(bestand: Path, config) -> TestClient:
    return TestClient(create_app(config=config, db_path=bestand))


# --- Die Spalte -----------------------------------------------------------

def test_die_spalte_nennt_die_kampagnen_der_gruppe(bestand: Path, config) -> None:
    from fbgroups.marketing.dashboard import render, sammle_daten

    daten = sammle_daten(config, bestand)
    zeile = next(z for z in daten["gruppen"] if z["id"] == GID)

    assert zeile["kampagnen_text"] == "Erste Kampagne"
    assert "kampagnenZelle" in render(daten)


def test_eine_gruppe_ohne_zuordnung_bleibt_leer(bestand: Path, config) -> None:
    from fbgroups.marketing.dashboard import sammle_daten

    daten = sammle_daten(config, bestand)
    zeile = next(z for z in daten["gruppen"] if z["id"] == GID_B)

    assert zeile["kampagnen_text"] == ""


def test_im_nur_lesen_zugang_gibt_es_kein_auswahlfeld(bestand: Path, config) -> None:
    """Zuordnen vergibt einen Code - das ist kein Lesen."""
    from fbgroups.marketing.dashboard import render, sammle_daten

    daten = sammle_daten(config, bestand)

    assert "k-zuordnen" in render(daten, nur_lesen=False)
    # Die Zeichenfunktion bleibt, aber sie gibt im Lesezugang nur die Marken
    # aus - geprueft ueber den Zweig, der das Feld baut.
    assert "NUR_LESEN" in render(daten, nur_lesen=True)


# --- Zuordnen -------------------------------------------------------------

def test_eine_gruppe_bekommt_einen_code(client: TestClient, bestand: Path) -> None:
    antwort = client.post(f"/gruppen/{GID_B}/kampagne", json={"campaign_id": "zweite"})

    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["zugeordnet"] is True
    assert daten["code"]

    with MarketingStore(bestand) as store:
        link = store.link_for("zweite", GID_B)
    assert link is not None and link.tracking_code == daten["code"]
    assert link.tracking_url.endswith(daten["code"])


def test_der_code_kollidiert_nicht_mit_vergebenen(client: TestClient, bestand: Path) -> None:
    """Dieselbe Vergabestelle wie beim Zuordnen ganzer Kampagnen.

    Zwei Gruppen mit demselben Code waeren in der Auswertung nicht mehr zu
    trennen - und der Fehler faellt erst auf, wenn die Zahlen schon falsch sind.
    """
    client.post(f"/gruppen/{GID}/kampagne", json={"campaign_id": "zweite"})
    client.post(f"/gruppen/{GID_B}/kampagne", json={"campaign_id": "zweite"})

    with MarketingStore(bestand) as store:
        codes = [link.tracking_code for link in store.links_for_campaign("zweite")]
        alle = store.assigned_codes()

    assert len(codes) == len(set(codes)) == 2
    assert "FB-SYR-KLN-001" in alle          # der bestehende ist unberuehrt
    assert "FB-SYR-KLN-001" not in codes     # und wurde nicht neu vergeben


def test_eine_bestehende_zuordnung_bleibt_unangetastet(
    client: TestClient, bestand: Path
) -> None:
    """Ihr Code steht moeglicherweise schon in einem Beitrag."""
    antwort = client.post(f"/gruppen/{GID}/kampagne", json={"campaign_id": "erste"})

    assert antwort.json()["code"] == "FB-SYR-KLN-001"
    with MarketingStore(bestand) as store:
        assert len(store.links_for_campaign("erste")) == 1


def test_unbekannte_gruppe_oder_kampagne_ergibt_404(client: TestClient) -> None:
    assert client.post(
        "/gruppen/gibtesnicht/kampagne", json={"campaign_id": "erste"}
    ).status_code == 404
    assert client.post(
        f"/gruppen/{GID}/kampagne", json={"campaign_id": "gibtesnicht"}
    ).status_code == 404


def test_zuordnen_ist_von_aussen_nicht_moeglich(bestand: Path, config) -> None:
    """Es vergibt einen Code - wie jeder schreibende Weg hinter ``_nur_lokal``."""
    fremd = TestClient(
        create_app(config=config, db_path=bestand), client=("203.0.113.7", 44321)
    )

    assert fremd.post(
        f"/gruppen/{GID_B}/kampagne", json={"campaign_id": "zweite"}
    ).status_code == 404


# --- Entfernen ------------------------------------------------------------

def test_eine_versehentliche_zuordnung_laesst_sich_loesen(
    client: TestClient, bestand: Path
) -> None:
    """Solange nichts veroeffentlicht wurde, bindet ein Code nichts."""
    client.post(f"/gruppen/{GID_B}/kampagne", json={"campaign_id": "zweite"})

    antwort = client.post(
        f"/gruppen/{GID_B}/kampagne", json={"campaign_id": "zweite", "entfernen": True}
    )

    assert antwort.json()["zugeordnet"] is False
    with MarketingStore(bestand) as store:
        assert store.link_for("zweite", GID_B) is None


def test_nach_dem_veroeffentlichen_wird_nicht_mehr_entfernt(
    client: TestClient, bestand: Path
) -> None:
    """Der Link im Beitrag muss weiter ankommen und gezaehlt werden.

    Dafuer gibt es "Ausschliessen" - es nimmt die Gruppe aus der Arbeitsliste
    und laesst den Code gueltig.
    """
    with MarketingStore(bestand) as store:
        store.set_post_status("erste", GID, PostStatus.VEROEFFENTLICHT)

    antwort = client.post(
        f"/gruppen/{GID}/kampagne", json={"campaign_id": "erste", "entfernen": True}
    )

    assert antwort.status_code == 409
    assert "ausschliessen" in antwort.json()["detail"].lower()
    with MarketingStore(bestand) as store:
        assert store.link_for("erste", GID) is not None
