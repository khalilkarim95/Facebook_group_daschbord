"""Was Facebook aus einem Tracking-Link macht - und was im Text davon bleibt.

Zwei Dinge, die zusammengehoeren und doch an verschiedenen Stellen wohnen:

* **Die Karte** (``marketing/web.py``). Sie entsteht aus dem, was der Abrufer
  der Plattform unter ``/r/{code}`` findet.
* **Der Text** (``automation/actions.py``). Die nackte Adresse hat ihre
  Aufgabe erfuellt, sobald die Karte steht.

Der Anlass ist ein Fehler, den man der Karte nicht ansah: Bis zum 14.09.2026
trug die Vorschauseite ``<meta http-equiv='refresh'>`` auf ihr Ziel. Facebooks
Abrufer folgt dem und beschreibt, was er am Ende findet - bei einem Store-Code
also ``play.google.com``. Im Beitrag stand dann die Karte von Google Play, und
die sorgfaeltig gesetzten Angaben darueber las niemand.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.automation.actions import trenne_adresse
from fbgroups.marketing.models import Campaign, CampaignGroup
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

fastapi = pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
from fastapi.testclient import TestClient  # noqa: E402

KAMPAGNE = "batreeq"
GID = "482910573829104"
CODE = "FB-SYR-DUE-004"
ABRUFER = {"user-agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"}


@pytest.fixture()
def bestand(tmp_path: Path) -> Path:
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=GID,
                    url_canonical=f"https://www.facebook.com/groups/{GID}",
                    name="Syrer in Duesseldorf",
                )
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(
                campaign_id=KAMPAGNE,
                name="Batreeq",
                landing_page="https://b-tarikak.de/",
                # Ausdruecklich der Store-Weg: Genau er hat die falsche Karte
                # erzeugt, also prueft diese Datei ihn und nicht die Vorgabe.
                ziel="store",
            )
        )
        store.add_link(
            CampaignGroup(
                campaign_id=KAMPAGNE,
                group_id=GID,
                tracking_code=CODE,
                tracking_url=f"https://go.b-tarikak.de/r/{CODE}",
            )
        )
    return pfad


@pytest.fixture()
def client(bestand: Path, config) -> TestClient:
    from fbgroups.marketing.web import create_app

    return TestClient(create_app(config=config, db_path=bestand), follow_redirects=False)


# --- Die Karte -------------------------------------------------------------

def test_die_karte_fuehrt_den_abrufer_nicht_zu_google_play(client: TestClient) -> None:
    """**Der Fehler, um den es geht.**

    Eine Meta-Weiterleitung auf der Vorschauseite ist keine Feinheit: Facebook
    folgt ihr und beschreibt das Ziel. Bei einem Store-Code stand damit die
    Karte von Google Play im Beitrag - mit fremdem Namen und fremdem Bild.
    """
    seite = client.get(f"/r/{CODE}", headers=ABRUFER).text

    assert "http-equiv" not in seite.lower()
    assert "refresh" not in seite.lower()


def test_die_karte_nennt_die_app_und_nicht_den_store(client: TestClient) -> None:
    seite = client.get(f"/r/{CODE}", headers=ABRUFER).text

    assert "بطريقك" in seite
    assert "og:site_name" in seite
    # Der Store steht nur noch im Verweis fuer den seltenen Menschen, der
    # diese Seite doch aufruft - in keiner Angabe, die die Karte beschreibt.
    for angabe in ("og:title", "og:description", "og:image", "og:url"):
        zeile = seite.split(f"property='{angabe}'")[1].split(">")[0]
        assert "play.google.com" not in zeile


def test_og_url_bleibt_die_zaehlende_adresse(client: TestClient) -> None:
    """Zeigte sie auf das Ziel, fuehrte die Karte an der Zaehlung vorbei."""
    seite = client.get(f"/r/{CODE}", headers=ABRUFER).text

    assert "og:url' content='http://testserver/r/" in seite


def test_ein_mensch_wird_weiterhin_weitergeleitet(client: TestClient) -> None:
    """Die Karte ist fuer die Abrufer, nicht fuer den Leser."""
    antwort = client.get(
        f"/r/{CODE}", headers={"user-agent": "Mozilla/5.0 (Linux; Android 14) Chrome/128.0"}
    )

    assert antwort.status_code == 302
    assert "play.google.com" in antwort.headers["location"]


def test_wer_die_seite_doch_sieht_kommt_weiter(client: TestClient) -> None:
    """Ein Browser mit ungewoehnlicher Kennung landet hier.

    Statt der Meta-Weiterleitung steht eine Zeile JavaScript da - genau der
    Unterschied: Ein Browser fuehrt sie aus, ein Abrufer nicht.
    """
    seite = client.get(f"/r/{CODE}", headers=ABRUFER).text

    assert "location.replace(" in seite
    assert "play.google.com" in seite


# --- Der Text --------------------------------------------------------------

def test_die_adresse_faellt_aus_dem_text_wenn_sie_am_ende_steht() -> None:
    """Der Regelfall der Vorlagen: Link auf eigener Zeile oder am Satzende."""
    ohne, adresse = trenne_adresse("مرحبا\nهذا نص\nhttps://go.b-tarikak.de/r/k7m2x9q")

    assert adresse == "https://go.b-tarikak.de/r/k7m2x9q"
    assert ohne == "مرحبا\nهذا نص"
    # Keine Leerzeile am Ende: Die Zeile bestand nur aus der Adresse.
    assert not ohne.endswith("\n")


def test_ein_absatz_bleibt_ein_absatz() -> None:
    """Eine Leerzeile, die vorher dastand, ist Gliederung und kein Rest."""
    ohne, _ = trenne_adresse("A\n\nB\nhttps://x.de/a")

    assert ohne == "A\n\nB"


def test_mitten_im_satz_bleibt_die_adresse_stehen() -> None:
    """Sonst hinterliesse das Entfernen eine Luecke im Satz.

    "Text (siehe {link}), danke" wuerde zu "Text (siehe ), danke" - lieber
    eine sichtbare Adresse als ein zerbrochener Satz.
    """
    text = "Text (siehe https://go.b-tarikak.de/r/abc), danke"

    assert trenne_adresse(text) == (text, "")


def test_zwei_adressen_werden_nicht_angefasst() -> None:
    """Wir wuessten nicht, welche die Karte gebaut hat.

    Die falsche zu entfernen naehme dem Beitrag seinen Link - und seine Gruppe
    bekaeme nie einen Klick gutgeschrieben.
    """
    text = "zwei https://a.de/x und https://b.de/y"

    assert trenne_adresse(text) == (text, "")


def test_ein_text_der_nur_aus_dem_link_besteht_bleibt_stehen() -> None:
    """Ein leerer Beitrag ist kein Beitrag."""
    text = "https://go.b-tarikak.de/r/k7m2x9q"

    assert trenne_adresse(text) == (text, "")


def test_ohne_adresse_gibt_es_nichts_zu_tun() -> None:
    assert trenne_adresse("kein Link hier") == ("kein Link hier", "")
