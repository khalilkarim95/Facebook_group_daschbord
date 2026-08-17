"""Tests fuer die Uebersichtsseite unter ``/``.

Der Dienst laeuft gegen eine eigene Datenbank im tmp-Verzeichnis - der echte
Bestand wird dabei nie geoeffnet.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.marketing.dashboard import render, sammle_daten
from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    MarketingStatus,
)
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group, Provenance, SourceType
from fbgroups.storage import SqliteStore

fastapi = pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
from fastapi.testclient import TestClient  # noqa: E402

REAL_ID_A = "482910573829104"
REAL_ID_B = "739201847362915"
REAL_ID_C = "615043928175306"


@pytest.fixture()
def bestand(tmp_path: Path) -> Path:
    """Drei Gruppen: bewertet, arabisch benannt und nicht bewertbar."""
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=REAL_ID_A,
                    url_canonical=f"https://www.facebook.com/groups/{REAL_ID_A}",
                    name="Syrer in Berlin",
                    audience_tags=["syrians"],
                    city="Berlin",
                    category="community",
                    score=92.0,
                    score_max=100.0,
                    score_reason="Zielgruppe 45 + Stadt 27 + Name 12 = 84 von hoechstens 100",
                ),
                Group(
                    group_id=REAL_ID_B,
                    url_canonical=f"https://www.facebook.com/groups/{REAL_ID_B}",
                    name="تجمع السوريين في ميونخ",
                    audience_tags=["syrians"],
                    city="München",
                    score=100.0,
                    score_max=100.0,
                ),
                Group(
                    group_id=REAL_ID_C,
                    url_canonical=f"https://www.facebook.com/groups/{REAL_ID_C}",
                    name="",
                    score=None,
                    score_max=None,
                    score_reason="insufficient_data: kein Gruppenname vorhanden",
                ),
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(Campaign(campaign_id="batreeq", name="Batreeq Syrian Germany"))
        store.add_link(
            CampaignGroup(
                campaign_id="batreeq", group_id=REAL_ID_A, tracking_code="FB-SYR-BER-001"
            )
        )
        eintrag = store.load_marketing(REAL_ID_A)
        eintrag.marketing_status = MarketingStatus.CONTACTED
        store.save_marketing(eintrag)
    return pfad


@pytest.fixture()
def client(bestand: Path, config) -> TestClient:
    from fbgroups.marketing.web import create_app

    return TestClient(create_app(config=config, db_path=bestand), follow_redirects=False)


# --- Inhalt ------------------------------------------------------------

def test_uebersicht_zeigt_gruppen_kampagne_und_tracking_code(client: TestClient) -> None:
    antwort = client.get("/")

    assert antwort.status_code == 200
    assert "text/html" in antwort.headers["content-type"]
    assert "Syrer in Berlin" in antwort.text
    assert "Batreeq Syrian Germany" in antwort.text
    assert "FB-SYR-BER-001" in antwort.text


def test_arabischer_name_bleibt_lesbar(client: TestClient) -> None:
    """Regressionstest gegen cp1252: Die Seite muss UTF-8 ausliefern."""
    antwort = client.get("/")

    assert "utf-8" in antwort.headers["content-type"].lower()
    assert "تجمع السوريين في ميونخ" in antwort.text


def test_status_steht_auf_deutsch_auf_der_seite(bestand: Path, config) -> None:
    daten = sammle_daten(config, bestand)
    zeile = next(z for z in daten["gruppen"] if z["id"] == REAL_ID_A)

    assert zeile["marketing"] == "contacted"
    assert zeile["marketing_label"] == "Leitung angesprochen"


def test_bezeichnungen_kommen_aus_der_konfiguration(bestand: Path, config) -> None:
    """Nicht die Kennung ``community`` anzeigen, sondern ihr ``label_de``."""
    daten = sammle_daten(config, bestand)
    zeile = next(z for z in daten["gruppen"] if z["id"] == REAL_ID_A)

    erwartet = next(c.label_de for c in config.categories if c.id == "community")
    assert zeile["kategorie"] == erwartet
    assert zeile["zielgruppen"] == [config.audiences["syrians"].label_de]


def test_spalte_zaehlt_anfragen_und_nicht_funde(bestand: Path, config) -> None:
    """Zwei Funde derselben Anfrage sind ein Fundweg, nicht zwei.

    ``times_seen`` waechst mit jedem Lauf weiter - auch wenn der Lauf komplett
    aus dem Anfragespeicher kommt. Es misst damit das Alter des Datensatzes,
    nicht die Auffindbarkeit der Gruppe: Eine Gruppe der ersten Ausbaustufe
    stand bei 99 Funden aus zwei Anfragen, eine gleich gute aus einer neuen
    Stadt bei 1 aus einer.
    """
    with SqliteStore(bestand) as store:
        # Derselbe Fund ein zweites Mal - wie ein wiederholter Lauf.
        gruppe = next(g for g in store.load_groups() if g.group_id == REAL_ID_A)
        gruppe.sources = [
            Provenance(source_type=SourceType.SEARCH, source_ref="serper:cp_01__berlin"),
            Provenance(source_type=SourceType.SEARCH, source_ref="serper:cp_01__berlin"),
            Provenance(source_type=SourceType.SEARCH, source_ref="serper:cp_03__berlin"),
        ]
        store.upsert_groups([gruppe])
        erneut = next(g for g in store.load_groups() if g.group_id == REAL_ID_A)
        assert erneut.times_seen > 1

    daten = sammle_daten(config, bestand)
    zeile = next(z for z in daten["gruppen"] if z["id"] == REAL_ID_A)

    assert zeile["anfragen"] == 2


def test_nicht_bewertbare_gruppe_bekommt_keinen_ersatzwert(bestand: Path, config) -> None:
    """``None`` heisst nicht bewertbar - auf der Seite darf daraus keine 0 werden."""
    daten = sammle_daten(config, bestand)
    zeile = next(z for z in daten["gruppen"] if z["id"] == REAL_ID_C)

    assert zeile["score"] is None
    assert zeile["score_max"] is None
    assert daten["kennzahlen"]["gesamt"] == 3
    assert daten["kennzahlen"]["bewertet"] == 2


# --- Grenzen -----------------------------------------------------------

def test_uebersicht_ist_von_aussen_nicht_vorhanden(bestand: Path, config) -> None:
    """Fremde Adresse bekommt 404, nicht 403.

    Der Dienst steht oeffentlich, damit die Tracking-Links funktionieren. Die
    Arbeitsliste darf dabei nicht mit heraus - und ein 403 verriete, dass es
    ueberhaupt eine gibt.
    """
    from fbgroups.marketing.web import create_app

    fremd = TestClient(
        create_app(config=config, db_path=bestand),
        client=("203.0.113.7", 44321),
    )

    assert fremd.get("/").status_code == 404
    assert fremd.post(
        "/stand", json={"group_id": REAL_ID_A, "status": "mitglied"}
    ).status_code == 404
    # Der eigentliche Zweck des Dienstes bleibt von aussen erreichbar.
    assert fremd.get("/healthz").status_code == 200


def test_stand_laesst_sich_setzen(client: TestClient, bestand: Path) -> None:
    """Der eine Wert, den das Programm nicht selbst ermitteln kann.

    Facebook meldet nicht, dass eine Beitrittsanfrage gestellt wurde. Der Stand
    kann deshalb nur von dem Menschen kommen, der sie geschickt hat.
    """
    antwort = client.post(
        "/stand", json={"group_id": REAL_ID_B, "status": "beitritt_angefragt"}
    )

    assert antwort.status_code == 200
    assert antwort.json()["label"] == "Beitritt angefragt"

    with MarketingStore(bestand) as store:
        eintrag = store.load_marketing(REAL_ID_B)
    assert eintrag.marketing_status is MarketingStatus.JOIN_REQUESTED
    assert eintrag.join_requested_at is not None


def test_zeitpunkt_der_anfrage_wird_nicht_ueberschrieben(
    client: TestClient, bestand: Path
) -> None:
    """Ein spaeterer Korrekturklick darf das Datum nicht auf heute ziehen."""
    client.post("/stand", json={"group_id": REAL_ID_B, "status": "beitritt_angefragt"})
    with MarketingStore(bestand) as store:
        zuerst = store.load_marketing(REAL_ID_B).join_requested_at

    client.post("/stand", json={"group_id": REAL_ID_B, "status": "mitglied"})
    client.post("/stand", json={"group_id": REAL_ID_B, "status": "beitritt_angefragt"})

    with MarketingStore(bestand) as store:
        assert store.load_marketing(REAL_ID_B).join_requested_at == zuerst


def test_nur_der_arbeitsstand_ist_aenderbar(client: TestClient) -> None:
    """Bewertung und Klassifikation entstehen aus der Suche, nicht aus Handeingabe."""
    seite = client.get("/").text

    assert "<form" not in seite.lower()
    assert client.post("/").status_code == 405
    # Das Modell kennt kein Feld fuer Score, Name oder Stadt - mitgeschickte
    # Werte koennten gar nicht ankommen.
    antwort = client.post(
        "/stand",
        json={"group_id": REAL_ID_A, "status": "mitglied", "score": 100.0, "name": "geaendert"},
    )
    assert antwort.status_code == 200
    assert "geaendert" not in client.get("/").text


def test_unbekannte_gruppe_wird_nicht_angelegt(client: TestClient) -> None:
    antwort = client.post("/stand", json={"group_id": "gibtsnicht", "status": "mitglied"})
    assert antwort.status_code == 404


def test_unbekannter_stand_wird_abgewiesen(client: TestClient) -> None:
    antwort = client.post("/stand", json={"group_id": REAL_ID_A, "status": "erfunden"})
    assert antwort.status_code == 422


def test_fremde_seite_darf_nicht_schreiben(bestand: Path, config) -> None:
    """Ohne diese Pruefung koennte jede offene Webseite an localhost schreiben.

    Der Browser sitzt auf demselben Rechner - die Absenderadresse allein
    genuegt hier also nicht.
    """
    from fbgroups.marketing.web import create_app

    lokal = TestClient(create_app(config=config, db_path=bestand))
    antwort = lokal.post(
        "/stand",
        json={"group_id": REAL_ID_A, "status": "mitglied"},
        headers={"Origin": "https://boese.example"},
    )

    assert antwort.status_code == 404
    with MarketingStore(bestand) as store:
        assert store.load_marketing(REAL_ID_A).marketing_status is MarketingStatus.CONTACTED


def test_seite_laedt_nichts_von_aussen(client: TestClient) -> None:
    """Kein CDN, keine Schriftart, kein fremdes Skript.

    Die Seite muss ohne Internet funktionieren, und ein Abruf bei einem Dritten
    verriete, woran hier gearbeitet wird. Verlinkte Gruppen sind etwas anderes:
    Sie werden erst durch einen Klick des Nutzers geoeffnet.
    """
    seite = client.get("/").text

    assert "<script src" not in seite
    assert "<link" not in seite
    assert "@import" not in seite
    assert "cdn" not in seite.lower()


def test_gruppenname_kann_das_skript_nicht_beenden(tmp_path: Path, config) -> None:
    """Ein ``</script>`` im Namen wuerde die Seite sonst leer zuruecklassen."""
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=REAL_ID_A,
                    url_canonical=f"https://www.facebook.com/groups/{REAL_ID_A}",
                    name="</script><b>Einschub</b>",
                    score=50.0,
                    score_max=100.0,
                )
            ]
        )

    seite = render(sammle_daten(config, pfad))

    # Genau ein schliessendes Skript-Tag: das der Seite selbst.
    assert seite.count("</script>") == 1
    assert "<\\/script>" in seite
