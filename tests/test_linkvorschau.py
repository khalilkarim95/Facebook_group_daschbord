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


def test_auch_der_kurzcode_bekommt_die_karte(bestand: Path, client: TestClient) -> None:
    """**Die Luecke, die dieser Test schliesst.**

    Geprueft war bisher allein der innere Code (``FB-SYR-DUE-004``). In einen
    Beitrag geht aber der **Kurzcode** - ``url_fuer`` setzt ihn ein, sobald es
    einen gibt. Baute der Dienst die Karte nur fuer die lange Form, saehe die
    Testreihe gruen aus, waehrend in der Gruppe eine nackte Adresse stuende:
    Ohne Karte ueberspringt ``post_to_group`` das Verbergen
    (``if karte and link_verbergen``), und die Adresse bleibt im Text.
    """
    with MarketingStore(bestand) as store:
        link = store.link_for(KAMPAGNE, GID)

    assert link.public_code, "add_link vergibt keinen Kurzcode mehr"
    assert link.public_code != link.tracking_code

    antwort = client.get(f"/r/{link.public_code}", headers=ABRUFER)

    assert antwort.status_code == 200, "der Kurzcode bekommt keine Vorschauseite"
    for angabe in ("og:title", "og:description", "og:image", "og:site_name"):
        assert f"property='{angabe}'" in antwort.text


def test_die_karte_traegt_das_app_logo(client: TestClient, config) -> None:
    """Ohne Bild zeigt Facebook gar keine Karte - und dann bleibt die Adresse.

    Das ist die stillste Stelle der ganzen Kette: Steht in
    ``marketing.vorschau.bild`` nichts oder etwas Unerreichbares, entsteht
    keine Karte, ``_link_verbergen`` laeuft nie, und im Beitrag steht die
    lange Adresse - ohne dass irgendwo ein Fehler gemeldet wuerde.
    """
    bild = str(config.get("marketing", "vorschau", "bild", default="") or "")

    assert bild.startswith("http"), "ohne og:image baut Facebook keine Karte"
    assert f"og:image' content='{bild}'" in client.get(f"/r/{CODE}", headers=ABRUFER).text


def test_der_kurzcode_wird_in_den_text_gesetzt_nicht_das_aktenzeichen() -> None:
    """Was im Beitrag steht, ist der Deckname - nicht die Buchhaltung.

    ``FB-SYR-BER-010-B`` nennt jedem Leser Kanal, Zielgruppe, Stadt und
    laufende Nummer. Der Rueckfall auf diese Form ist Absicht (ein Beitrag
    ohne Link waere schlimmer), aber er ist der Grund, aus dem eine lange
    rohe Adresse in einer Gruppe landen kann - der Lauf warnt seit dem
    20.09.2026 davor.
    """
    lang = "FB-SYR-BER-010-B"
    ohne = CampaignGroup(
        campaign_id="k",
        group_id="1",
        tracking_code=lang,
        tracking_url=f"https://go.b-tarikak.de/r/{lang}",
    )
    mit = CampaignGroup(
        campaign_id="k",
        group_id="1",
        tracking_code=lang,
        tracking_url=f"https://go.b-tarikak.de/r/{lang}",
        public_code="bp3g3fq",
        public_url="https://go.b-tarikak.de/r/bp3g3fq",
    )

    assert ohne.url_fuer("store").endswith(lang)
    assert mit.url_fuer("store").endswith("bp3g3fq")
    # Die Auswertung laeuft in beiden Faellen unter dem inneren Code.
    assert mit.code_fuer("store") == lang


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
