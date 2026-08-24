"""Der Weg Facebook -> Play Store -> Installation -> Activation.

Die Zuordnung ueberlebt hier eine Stelle, an der sie vorher zerriss: die
Installation aus dem Play Store. Der Kommentar in ``tracking_storage.dart``
der App benennt den Bruch selbst - *"a Play Store install never sees it"*: Der
Code stand allein im ``?ref=`` der Landingadresse, und diese Adresse sieht ein
Play-Store-Install nie.

Getragen wird er jetzt im ``referrer`` der Play-Adresse. Google reicht das Feld
nach dem Einrichten an die App weiter (Play Install Referrer); die App liest es
beim ersten Start und meldet ``activation`` mit dem Code.

**Die Stufen bleiben unabhaengig.** Kein Ereignis setzt ein anderes voraus:
Eine Registrierung ohne Installation ist gueltig, eine Installation ohne
Registrierung ebenso. Genau das wird hier geprueft, in allen drei Kombinationen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.marketing.analytics import funnel, top_groups
from fbgroups.marketing.models import Campaign, CampaignGroup, EventType
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
from fastapi.testclient import TestClient  # noqa: E402

from fbgroups.marketing.web import create_app, play_store_url  # noqa: E402

# Genau der Fall aus der Aufgabenstellung.
CODE = "FB-SYR-KLN-002"
GID = "482910573829104"
BENUTZER = "test-user-123"
PAKET = "de.btarikak.app"


@pytest.fixture()
def bestand(tmp_path: Path) -> Path:
    """Eine Gruppe, eine Kampagne, ein Code - mit dem Play Store als Ziel."""
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=GID,
                    url_canonical=f"https://www.facebook.com/groups/{GID}",
                    name="Syrer in Koeln",
                )
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(
                campaign_id="batreeq",
                name="Batreeq Syrian Germany",
                landing_page="https://b-tarikak.de/",
                ziel="store",
            )
        )
        store.add_link(
            CampaignGroup(campaign_id="batreeq", group_id=GID, tracking_code=CODE)
        )
    return pfad


@pytest.fixture()
def client(bestand: Path, config) -> TestClient:
    return TestClient(create_app(config=config, db_path=bestand), follow_redirects=False)


# --- Die Play-Adresse -----------------------------------------------------

def test_die_echte_package_id_steht_in_der_konfiguration(config) -> None:
    """Keine Beispielkennung.

    Eine falsche fuehrte auf eine fremde oder gar keine Store-Seite, und der
    Fehler faellt erst auf, wenn niemand die App findet.
    """
    assert config.get("marketing", "store", "android_package") == PAKET


def test_die_play_adresse_traegt_paket_und_referrer(config) -> None:
    adresse = play_store_url(CODE, config)

    assert adresse.startswith("https://play.google.com/store/apps/details?")
    assert f"id={PAKET}" in adresse
    assert f"referrer={CODE}" in adresse


def test_der_code_wird_prozentkodiert(config) -> None:
    """Die Kuerzel kommen aus der Konfiguration und koennen sich aendern.

    Ein ungeschuetztes ``&`` im Code zerlegte die Adresse - der Play Store saehe
    dann einen abgeschnittenen Referrer, und die Zuordnung waere still falsch.
    """
    adresse = play_store_url("FB&SYR KLN", config)

    assert "referrer=FB%26SYR%20KLN" in adresse
    assert "&SYR" not in adresse.split("referrer=")[1]


# --- Der Klick ------------------------------------------------------------

def test_klick_fuehrt_direkt_zum_play_store(client: TestClient) -> None:
    """Niemand muss mehr ueber b-tarikak.de gehen."""
    antwort = client.get(f"/r/{CODE}")

    assert antwort.status_code == 302
    ziel = antwort.headers["location"]
    assert ziel.startswith("https://play.google.com/store/apps/details?")
    assert f"referrer={CODE}" in ziel


def test_der_klick_wird_trotzdem_gezaehlt(client: TestClient, bestand: Path) -> None:
    """Die Weiterleitung darf den Zaehler nicht ueberspringen."""
    client.get(f"/r/{CODE}")

    with MarketingStore(bestand) as store:
        zahlen = store.event_counts()

    assert zahlen.get(EventType.CLICK.value) == 1
    assert zahlen.get(EventType.STORE_VISIT.value) == 1


def test_store_visit_ist_keine_installation(client: TestClient, bestand: Path) -> None:
    """Der Name ist der Punkt.

    Gemessen ist, dass wir jemanden zum Play Store geschickt haben - nicht,
    dass er die App installiert hat. Das meldet uns niemand; der Beweis kommt
    erst als ``activation`` aus der App.
    """
    client.get(f"/r/{CODE}")

    with MarketingStore(bestand) as store:
        zahlen = store.event_counts()

    assert zahlen.get(EventType.DOWNLOAD.value, 0) == 0
    assert zahlen.get(EventType.ACTIVATION.value, 0) == 0


def test_kein_zweites_ref_an_der_play_adresse(client: TestClient) -> None:
    """Google reichte es nicht weiter - es waere Zierrat mit Verwechslungsgefahr."""
    ziel = client.get(f"/r/{CODE}").headers["location"]

    from urllib.parse import parse_qs, urlparse

    felder = parse_qs(urlparse(ziel).query)
    assert felder["referrer"] == [CODE]        # das Feld, das die Installation ueberlebt
    assert "ref" not in felder                 # und kein zweites daneben


# --- Die Activation aus der App ------------------------------------------

def test_activation_mit_referrer_findet_die_gruppe(client: TestClient, bestand: Path) -> None:
    """Der Kern des Umbaus: Die Zuordnung ueberlebt die Installation.

    Die App liest den Install Referrer beim ersten Start und meldet ihn. Der
    Benutzer ist dem Dienst dabei voellig unbekannt - er kommt aus dem Store
    und war nie auf der Webseite.
    """
    client.get(f"/r/{CODE}")

    antwort = client.post(
        "/events",
        json={
            "event_type": "activation",
            "user_ref": BENUTZER,
            "tracking_code": CODE,
        },
    )

    assert antwort.status_code == 200
    assert antwort.json()["tracking_code"] == CODE

    with MarketingStore(bestand) as store:
        zeilen = top_groups(store, {GID: "Syrer in Koeln"})

    assert zeilen[0].schluessel == GID
    assert zeilen[0].clicks == 1
    assert zeilen[0].activations == 1


def test_der_geforderte_endzustand(client: TestClient, bestand: Path) -> None:
    """Wortwoertlich die Abnahme aus der Aufgabenstellung.

        FB-SYR-KLN-002
        Clicks: 1
        Activation: 1
    """
    client.get(f"/r/{CODE}")
    client.post(
        "/events",
        json={"event_type": "activation", "user_ref": BENUTZER, "tracking_code": CODE},
    )

    with MarketingStore(bestand) as store:
        je_code = {
            row["tracking_code"]: row
            for row in store.conn.execute(
                "SELECT tracking_code, "
                "  SUM(event_type = 'click')      AS clicks, "
                "  SUM(event_type = 'activation') AS activations "
                "FROM tracking_events GROUP BY tracking_code"
            )
        }

    assert je_code[CODE]["clicks"] == 1
    assert je_code[CODE]["activations"] == 1


def test_spaetere_stufen_erben_die_gruppe(client: TestClient, bestand: Path) -> None:
    """Nach der Activation kennt die App nur noch ihren Benutzer.

    ``qualified`` traegt keinen Tracking-Code mehr - die Zuordnung muss ueber
    die Kennung vererbt werden, sonst stuenden genau die interessanten Stufen
    ohne Gruppe da.
    """
    client.get(f"/r/{CODE}")
    client.post(
        "/events",
        json={"event_type": "activation", "user_ref": BENUTZER, "tracking_code": CODE},
    )

    antwort = client.post(
        "/events", json={"event_type": "qualified", "user_ref": BENUTZER}
    )

    assert antwort.json()["tracking_code"] == CODE


# --- Die Unabhaengigkeit der Stufen --------------------------------------

def test_registrierung_ohne_installation(client: TestClient, bestand: Path) -> None:
    client.get(f"/r/{CODE}")
    client.post(
        "/events",
        json={"event_type": "registration", "user_ref": "nur-web", "tracking_code": CODE},
    )

    with MarketingStore(bestand) as store:
        zahlen = store.event_counts()

    assert zahlen.get("registration") == 1
    assert zahlen.get("download", 0) == 0
    assert zahlen.get("activation", 0) == 0


def test_installation_ohne_registrierung(client: TestClient, bestand: Path) -> None:
    """Wer die App aus dem Store holt und nie ein Konto anlegt, ist gueltig."""
    client.get(f"/r/{CODE}")
    client.post(
        "/events",
        json={"event_type": "activation", "user_ref": "nur-app", "tracking_code": CODE},
    )

    with MarketingStore(bestand) as store:
        zahlen = store.event_counts()

    assert zahlen.get("registration", 0) == 0
    assert zahlen.get("activation") == 1


def test_beides_zusammen(client: TestClient, bestand: Path) -> None:
    client.get(f"/r/{CODE}")
    for art in ("registration", "download", "activation"):
        client.post(
            "/events",
            json={"event_type": art, "user_ref": BENUTZER, "tracking_code": CODE},
        )

    with MarketingStore(bestand) as store:
        zahlen = store.event_counts()

    assert zahlen.get("registration") == 1
    assert zahlen.get("download") == 1
    assert zahlen.get("activation") == 1


def test_activation_setzt_keine_registrierung_voraus(client: TestClient, bestand: Path) -> None:
    """Kein Ereignis erzeugt ein anderes mit - auch nicht rueckwirkend."""
    client.post(
        "/events",
        json={"event_type": "activation", "user_ref": "frisch", "tracking_code": CODE},
    )

    with MarketingStore(bestand) as store:
        stufen = dict((t.value, anzahl) for t, anzahl, _ in funnel(store))

    assert stufen["activation"] == 1
    assert stufen["registration"] == 0
    assert stufen["click"] == 0            # dieser Mensch kam ohne Klick an


# --- Der Landingpage-Weg bleibt --------------------------------------

def test_landing_bleibt_moeglich(bestand: Path, config) -> None:
    """Nicht jede Kampagne bewirbt die App.

    Wer auf die eigene Seite fuehrt, bekommt weiterhin ``?ref=`` - und keinen
    Store-Besuch gezaehlt.
    """
    with MarketingStore(bestand) as store:
        campaign = store.load_campaign("batreeq")
        assert campaign is not None
        campaign.ziel = "landing"
        store.save_campaign(campaign)

    client = TestClient(create_app(config=config, db_path=bestand), follow_redirects=False)
    antwort = client.get(f"/r/{CODE}")

    assert antwort.headers["location"].startswith("https://b-tarikak.de/")
    assert f"ref={CODE}" in antwort.headers["location"]
    with MarketingStore(bestand) as store:
        assert store.event_counts().get(EventType.STORE_VISIT.value, 0) == 0


def test_ohne_package_id_wird_nicht_ins_leere_geleitet(bestand: Path, config) -> None:
    """Ein Einrichtungsfehler darf nicht als Store-Besuch durchgehen.

    Ohne Kennung gibt es keine Store-Seite. Auf die Landingpage auszuweichen
    ist die brauchbare Antwort - sie als Store-Besuch zu zaehlen machte den
    Fehler unsichtbar.
    """
    from dataclasses import replace

    ohne = replace(config)
    ohne.settings["marketing"] = {
        **ohne.settings.get("marketing", {}),
        "store": {"android_package": ""},
    }

    client = TestClient(create_app(config=ohne, db_path=bestand), follow_redirects=False)
    antwort = client.get(f"/r/{CODE}")

    assert antwort.headers["location"].startswith("https://b-tarikak.de/")
    with MarketingStore(bestand) as store:
        assert store.event_counts().get(EventType.STORE_VISIT.value, 0) == 0
