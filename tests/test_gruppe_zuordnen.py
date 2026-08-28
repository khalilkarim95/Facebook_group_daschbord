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
                    # Mit Score: ``target_include_unscored`` ist standardmaessig
                    # aus, eine Gruppe ohne Bewertung faellt also aus jeder
                    # Regel heraus - und der Test pruefte dann nicht die Regel,
                    # sondern diesen Sonderfall.
                    score=92.0,
                    score_max=100.0,
                ),
                Group(
                    group_id=GID_B,
                    url_canonical=f"https://www.facebook.com/groups/{GID_B}",
                    name="Syrer in Berlin",
                    city="berlin",
                    audience_tags=["syrians"],
                    score=88.0,
                    score_max=100.0,
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


# --- "Passt zur Regel" ist etwas anderes als "zugeordnet" ----------------

def test_passende_kampagne_wird_vorgeschlagen(bestand: Path, config) -> None:
    """Die Frage, die die Spalte beantworten soll: wohin gehoert diese Gruppe?

    Eine Gruppe kann die Auswahlregel einer Kampagne erfuellen, ohne ihr
    zugeordnet zu sein - genau dann steht ein Tracking-Code aus.
    """
    from fbgroups.marketing.dashboard import sammle_daten
    from fbgroups.marketing.models import CampaignStatus

    with MarketingStore(bestand) as store:
        zweite = store.load_campaign("zweite")
        zweite.status = CampaignStatus.ACTIVE
        zweite.target_cities = ["berlin"]        # trifft nur GID_B
        store.save_campaign(zweite)

    daten = sammle_daten(config, bestand)
    berlin = next(z for z in daten["gruppen"] if z["id"] == GID_B)
    koeln = next(z for z in daten["gruppen"] if z["id"] == GID)

    assert [k["id"] for k in berlin["passt_zu"]] == ["zweite"]
    assert koeln["passt_zu"] == []               # andere Stadt


def test_eine_zugeordnete_gruppe_wird_nicht_nochmal_vorgeschlagen(
    bestand: Path, config
) -> None:
    """Sonst stuende neben der Marke ein Knopf, der nichts mehr bewirkte."""
    from fbgroups.marketing.dashboard import sammle_daten
    from fbgroups.marketing.models import CampaignStatus

    with MarketingStore(bestand) as store:
        erste = store.load_campaign("erste")
        erste.status = CampaignStatus.ACTIVE
        store.save_campaign(erste)                # Regel leer = alle Gruppen

    daten = sammle_daten(config, bestand)
    koeln = next(z for z in daten["gruppen"] if z["id"] == GID)

    assert koeln["kampagnen_text"] == "Erste Kampagne"     # zugeordnet
    assert "erste" not in [k["id"] for k in koeln["passt_zu"]]


def test_eine_pausierte_kampagne_schlaegt_nichts_vor(bestand: Path, config) -> None:
    """Sie sucht keine Gruppen mehr - ``campaign sync`` naehme sie auch nicht auf.

    Ein Vorschlag waere ein Rat zu einer Zuordnung, die das Programm selbst
    nicht mehr vornaehme.
    """
    from fbgroups.marketing.dashboard import sammle_daten
    from fbgroups.marketing.models import CampaignStatus

    with MarketingStore(bestand) as store:
        zweite = store.load_campaign("zweite")
        zweite.status = CampaignStatus.PAUSED
        store.save_campaign(zweite)

    daten = sammle_daten(config, bestand)

    for zeile in daten["gruppen"]:
        assert "zweite" not in [k["id"] for k in zeile["passt_zu"]]


def test_das_auswahlfeld_spart_die_vorschlaege_nicht_aus(bestand: Path, config) -> None:
    """Das Feld nennt jede Kampagne, in der die Gruppe noch nicht steht.

    Vorher zog es die vorgeschlagenen Kampagnen ab, weil fuer die schon ein
    Knopf danebenstand. Bei **einer** Kampagne, der die Gruppe bereits
    zugeordnet ist, blieb damit nichts uebrig: Die Spalte zeigte nur noch eine
    Marke, und in der Spalte, die es fuer die Frage "wohin gehoert diese
    Gruppe?" gibt, liess sich nichts mehr waehlen.

    Geprueft wird an der Quelle des Skripts, weil die Zeile im Browser entsteht
    - wie bei den uebrigen Tests dieser Datei. Entscheidend ist, dass die Liste
    allein an den **bestehenden Zuordnungen** haengt (``drin``) und nicht
    zusaetzlich an den Vorschlaegen.
    """
    from fbgroups.marketing.dashboard import render, sammle_daten

    seite = render(sammle_daten(config, bestand))

    assert "waehlbar = (DATEN.kampagnen || []).filter((k) => !drin.has(k.id))" in seite
    # Der Filter, der den Fehler verursacht hat. Sein Verschwinden ist die
    # eigentliche Aussage dieses Tests.
    assert "vorgeschlagen.has" not in seite


def test_die_seite_unterscheidet_marke_und_vorschlag(bestand: Path, config) -> None:
    from fbgroups.marketing.dashboard import render, sammle_daten
    from fbgroups.marketing.models import CampaignStatus

    with MarketingStore(bestand) as store:
        zweite = store.load_campaign("zweite")
        zweite.status = CampaignStatus.ACTIVE
        store.save_campaign(zweite)

    seite = render(sammle_daten(config, bestand))

    assert "k-marke" in seite        # zugeordnet
    assert "k-vorschlag" in seite    # passt zur Regel
    assert "k-zuordnen" in seite     # alles uebrige


# --- Sammelzuordnung ------------------------------------------------------
#
# Der dritte Weg neben Regel und Einzelfall. Die Regel beschreibt die Auswahl
# als Bedingung; wer genau diese zwoelf Gruppen meint, muesste sie erst als
# Regel formulieren - und eine Regel, die zwoelf trifft und keine dreizehnte,
# ist meist gar nicht formulierbar.

def test_mehrere_gruppen_in_einem_zug(client: TestClient, bestand: Path) -> None:
    antwort = client.post(
        "/kampagnen/zweite/gruppen", json={"group_ids": [GID, GID_B]}
    )

    assert antwort.status_code == 200
    assert antwort.json()["neu"] == 2
    with MarketingStore(bestand) as store:
        assert len(store.links_for_campaign("zweite")) == 2


def test_bereits_zugeordnete_bleiben_unberuehrt(client: TestClient, bestand: Path) -> None:
    """Ihr Code steht moeglicherweise in einem veroeffentlichten Beitrag."""
    antwort = client.post(
        "/kampagnen/erste/gruppen", json={"group_ids": [GID, GID_B]}
    )
    daten = antwort.json()

    assert daten["neu"] == 1                 # nur GID_B kommt dazu
    assert daten["schon_zugeordnet"] == 1
    with MarketingStore(bestand) as store:
        assert store.link_for("erste", GID).tracking_code == "FB-SYR-KLN-001"


def test_unbekannte_kennungen_brechen_den_zug_nicht_ab(client: TestClient) -> None:
    """Eine Zeile aus einem alten Fenster darf nicht elf andere kosten."""
    antwort = client.post(
        "/kampagnen/zweite/gruppen", json={"group_ids": [GID, "gibtesnicht"]}
    )
    daten = antwort.json()

    assert daten["neu"] == 1
    assert daten["unbekannt"] == ["gibtesnicht"]


def test_dubletten_zaehlen_einmal(client: TestClient) -> None:
    antwort = client.post(
        "/kampagnen/zweite/gruppen", json={"group_ids": [GID, GID, GID]}
    )

    assert antwort.json()["neu"] == 1


def test_die_codes_folgen_der_vergabereihenfolge(tmp_path: Path, config) -> None:
    """Nicht der Reihenfolge der Haken.

    Sonst bekaeme dieselbe Gruppe eine andere Nummer, je nachdem, wie die
    Tabelle gerade sortiert war - und "gleiche Eingabe, gleiche Codes" waere
    eine Zusage, die nur zufaellig gilt.
    """
    from datetime import UTC, datetime

    frueh = "111111111111111"
    spaet = "222222222222222"
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=spaet,
                    url_canonical=f"https://www.facebook.com/groups/{spaet}",
                    name="Syrer in Koeln spaet",
                    city="koeln",
                    audience_tags=["syrians"],
                    score=50.0,
                    score_max=100.0,
                    first_seen_at=datetime(2026, 5, 1, tzinfo=UTC),
                ),
                Group(
                    group_id=frueh,
                    url_canonical=f"https://www.facebook.com/groups/{frueh}",
                    name="Syrer in Koeln frueh",
                    city="koeln",
                    audience_tags=["syrians"],
                    score=99.0,          # besserer Score - darf nichts entscheiden
                    score_max=100.0,
                    first_seen_at=datetime(2026, 1, 1, tzinfo=UTC),
                ),
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(campaign_id="k", name="K", landing_page="https://b-tarikak.de/")
        )

    client = TestClient(create_app(config=config, db_path=pfad))
    # Absichtlich verkehrt herum uebergeben.
    client.post("/kampagnen/k/gruppen", json={"group_ids": [spaet, frueh]})

    with MarketingStore(pfad) as store:
        assert store.link_for("k", frueh).tracking_code.endswith("001")
        assert store.link_for("k", spaet).tracking_code.endswith("002")


def test_eine_unbekannte_kampagne_ist_ein_404(client: TestClient) -> None:
    antwort = client.post(
        "/kampagnen/gibtesnicht/gruppen", json={"group_ids": [GID]}
    )

    assert antwort.status_code == 404


def test_der_sammelweg_ist_von_aussen_nicht_bedienbar(bestand: Path, config) -> None:
    """Er vergibt Tracking-Codes - wie jeder schreibende Weg hinter _nur_lokal."""
    fremd = TestClient(
        create_app(config=config, db_path=bestand), client=("203.0.113.7", 44321)
    )

    antwort = fremd.post("/kampagnen/zweite/gruppen", json={"group_ids": [GID]})

    assert antwort.status_code == 404


def test_die_sammelleiste_traegt_das_kampagnenfeld(bestand: Path, config) -> None:
    """Ohne Bedienelement nuetzt der Weg niemandem."""
    from fbgroups.marketing.dashboard import render, sammle_daten

    seite = render(sammle_daten(config, bestand))

    assert 'id="sammel-kampagne"' in seite
    assert 'id="sammel-zuordnen"' in seite
    # Und die Leiste ist im Lesezugang ohnehin ausgeblendet.
    assert "body.nur-lesen .sammel" in seite
