"""Der oeffentliche Kurzcode: was ein Leser sieht und was gezaehlt wird.

Der Anlass steht in einem Satz. Bis zum 14.09.2026 stand in jedem Beitrag die
Buchhaltung der Kampagne: ``go.b-tarikak.de/r/FB-SYR-DUE-004-B`` nennt Kanal,
Zielgruppe, Stadt und laufende Nummer. Seitdem traegt der Beitrag einen
Decknamen, und die Weiterleitung loest ihn auf, bevor sie zaehlt.

Die Datei prueft deshalb vor allem **die Naht** zwischen beidem - genau die
Stelle, an der ein Umbau wie dieser still schiefgeht: Ein Deckname, der in
einer Auswertung landet, zerlegte jede Zahl in zwei Haelften (eine fuer
Beitraege von vorher, eine fuer die von nachher), und niemand saehe es der
Tabelle an.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.marketing.beitrag import beitragstext, mit_link
from fbgroups.marketing.kurzcode import ALPHABET, LAENGE, ist_kurzcode, kurzcode
from fbgroups.marketing.models import Campaign, CampaignGroup, EventType, Texttyp
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

fastapi = pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
from fastapi.testclient import TestClient  # noqa: E402

KAMPAGNE = "batreeq"
GID = "482910573829104"
CODE = "FB-SYR-DUE-004"
BASIS = "https://go.b-tarikak.de"


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
                    city="Düsseldorf",
                    audience_tags=["syrians"],
                )
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(
                campaign_id=KAMPAGNE,
                name="Batreeq",
                language="ar",
                landing_page="https://b-tarikak.de/",
                ziel="landing",
            )
        )
        store.add_link(
            CampaignGroup(
                campaign_id=KAMPAGNE,
                group_id=GID,
                tracking_code=CODE,
                tracking_url=f"{BASIS}/r/{CODE}",
            )
        )
    return pfad


@pytest.fixture()
def store(bestand: Path):
    with MarketingStore(bestand) as s:
        yield s


@pytest.fixture()
def client(bestand: Path, config) -> TestClient:
    from fbgroups.marketing.web import create_app

    return TestClient(create_app(config=config, db_path=bestand), follow_redirects=False)


# --- Der Code selbst ------------------------------------------------------

def test_derselbe_code_ergibt_dieselbe_adresse() -> None:
    """Ein gewuerfelter Code existiert nur in seiner Spalte.

    Geht sie verloren, zeigen alle veroeffentlichten Beitraege ins Leere. Ein
    abgeleiteter laesst sich wieder herstellen - derselbe Gedanke wie bei der
    Vorlagenwahl, die ``blake2b`` nimmt und nicht das eingebaute ``hash``.
    """
    assert kurzcode(CODE, "salz") == kurzcode(CODE, "salz")


def test_ein_anderes_geheimnis_ergibt_eine_andere_adresse() -> None:
    """Ohne Geheimnis koennte jeder Leser die Nachbarcodes ausrechnen.

    Und damit den Aufbau der Kampagne zurueckgewinnen - also genau das, was
    der Deckname verbergen soll.
    """
    assert kurzcode(CODE, "salz") != kurzcode(CODE, "pfeffer")


def test_verschiedene_codes_ergeben_verschiedene_adressen() -> None:
    codes = {kurzcode(f"FB-SYR-BER-{i:03d}", "salz") for i in range(300)}
    assert len(codes) == 300


def test_die_adresse_traegt_keine_verwechselbaren_zeichen() -> None:
    """Wer sie abtippt, verwechselt 0/o und 1/l/i.

    Eine verwechselte Stelle ist kein Tippfehler mit Fehlermeldung, sondern
    ein Klick, der einer anderen Gruppe gutgeschrieben wuerde oder mit 404
    endet.
    """
    for verboten in "01loiuv":
        assert verboten not in ALPHABET

    kurz = kurzcode(CODE, "salz")
    assert len(kurz) == LAENGE
    assert ist_kurzcode(kurz)
    assert not ist_kurzcode(CODE)


# --- Was im Beitrag steht -------------------------------------------------

def test_im_beitrag_steht_kein_kampagnencode(store, config) -> None:
    """Das Ziel des ganzen Umbaus, in einer Zusicherung."""
    campaign = store.load_campaign(KAMPAGNE)
    link = store.link_for(KAMPAGNE, GID)
    text = mit_link(campaign, link, "حمّله من هنا: {link}", config=config)

    assert CODE not in text
    assert "FB-" not in text
    assert f"{BASIS}/r/{link.public_code}" in text


def test_auch_der_platzhalter_tracking_code_nennt_den_decknamen(store, config) -> None:
    """``{tracking_code}`` steht in einem Text, der veroeffentlicht wird.

    Er muss deshalb den oeffentlichen Code liefern und nicht den inneren -
    sonst bliebe ein Weg offen, ueber den die Kampagnenbuchhaltung doch in
    eine Gruppe kommt, und niemand wuerde ihn beim Schreiben einer Vorlage
    vermuten.
    """
    campaign = store.load_campaign(KAMPAGNE)
    link = store.link_for(KAMPAGNE, GID)

    text = mit_link(campaign, link, "{tracking_code} - {link}", config=config)
    assert CODE not in text
    assert link.public_code in text


def test_der_gespeicherte_text_traegt_weiterhin_den_platzhalter(store, config) -> None:
    """Der Deckname wird beim **Lesen** eingesetzt, nicht beim Erzeugen.

    Dieselbe Trennung wie bisher: Was gespeichert ist, traegt ``{link}``; was
    hinausgeht, traegt die Adresse. Sie ist die Grundlage dafuer, dass ein
    gespeicherter Text ueberhaupt weitergereicht werden darf.
    """
    campaign = store.load_campaign(KAMPAGNE)
    link = store.link_for(KAMPAGNE, GID)
    link.post_text = "مرحبا {link}"

    assert "{link}" in link.post_text
    assert "{link}" not in beitragstext(campaign, link, Texttyp.POST, config=config)


# --- Die Naht: gezaehlt wird unter dem inneren Code ------------------------

def test_der_klick_auf_die_kurze_adresse_wird_gezaehlt(client, bestand: Path) -> None:
    """Der Deckname darf nichts kosten - sonst waere er ein zweites System."""
    with MarketingStore(bestand) as store:
        link = store.link_for(KAMPAGNE, GID)
        kurz = link.public_code

    antwort = client.get(f"/r/{kurz}")

    assert antwort.status_code == 302
    with MarketingStore(bestand) as store:
        assert store.event_counts().get(EventType.CLICK.value) == 1


def test_gespeichert_wird_der_innere_code(client, bestand: Path) -> None:
    """**Die eigentliche Zusicherung dieser Datei.**

    Landete der Deckname in der Ereignistabelle, zerfiele jede Auswertung in
    zwei Haelften - eine fuer Beitraege von vor dem 14.09.2026 und eine
    danach -, und der Tabelle saehe man es nicht an.
    """
    with MarketingStore(bestand) as store:
        kurz = store.link_for(KAMPAGNE, GID).public_code

    client.get(f"/r/{kurz}")

    with MarketingStore(bestand) as store:
        ereignis = store.conn.execute("SELECT * FROM tracking_events").fetchone()

    assert ereignis["tracking_code"] == CODE
    assert ereignis["campaign_id"] == KAMPAGNE
    assert ereignis["group_id"] == GID


def test_der_alte_lange_link_funktioniert_weiter(client, bestand: Path) -> None:
    """Er steht in Beitraegen, die seit Wochen in Gruppen stehen.

    Zurueckholen laesst sich keiner davon; ein Code, der ins Leere liefe,
    waere der teuerste Fehler dieses Umbaus.
    """
    antwort = client.get(f"/r/{CODE}")

    assert antwort.status_code == 302
    with MarketingStore(bestand) as store:
        assert store.event_counts().get(EventType.CLICK.value) == 1


def test_beide_adressen_zaehlen_auf_dasselbe_konto(client, bestand: Path) -> None:
    """Sonst stuende dieselbe Gruppe zweimal in der Rangliste."""
    with MarketingStore(bestand) as store:
        kurz = store.link_for(KAMPAGNE, GID).public_code

    # Zwei verschiedene Besucher, damit die Entdopplung nicht dazwischenkommt.
    client.get(f"/r/{kurz}", headers={"user-agent": "Mozilla/5.0 (Android) A"})
    client.get(f"/r/{CODE}", headers={"user-agent": "Mozilla/5.0 (iPhone) B"})

    with MarketingStore(bestand) as store:
        codes = [
            zeile["tracking_code"]
            for zeile in store.conn.execute("SELECT tracking_code FROM tracking_events")
        ]

    assert codes == [CODE, CODE]


def test_die_app_meldet_den_decknamen_und_wir_speichern_den_inneren(
    client, bestand: Path
) -> None:
    """Die Web-App liest den Code aus ``?ref=`` - dort steht der Deckname.

    Ohne die Aufloesung in ``POST /events`` stuenden Registrierungen unter
    einem Code, den keine Auswertung kennt.
    """
    with MarketingStore(bestand) as store:
        kurz = store.link_for(KAMPAGNE, GID).public_code

    antwort = client.post(
        "/events",
        json={"tracking_code": kurz, "event_type": "registration", "user_ref": "user-1"},
    )

    assert antwort.status_code == 200
    assert antwort.json()["tracking_code"] == CODE
    with MarketingStore(bestand) as store:
        zeile = store.conn.execute(
            "SELECT * FROM tracking_events WHERE event_type = 'registration'"
        ).fetchone()
    assert zeile["tracking_code"] == CODE
    assert zeile["group_id"] == GID


# --- Vergabe und Bestand ---------------------------------------------------

def test_ein_vergebener_kurzcode_wird_nie_ersetzt(store) -> None:
    """Er steht moeglicherweise schon in einem Beitrag.

    Dieselbe Regel wie beim Tracking-Code selbst und beim Browser-Code: Was
    einmal veroeffentlicht sein kann, wird nicht neu berechnet.
    """
    vorher = store.link_for(KAMPAGNE, GID).public_code
    store.vergib_kurzcodes(KAMPAGNE, GID)
    store.kurzcodes_nachtragen(KAMPAGNE)

    assert store.link_for(KAMPAGNE, GID).public_code == vorher


def test_der_browsercode_bekommt_einen_eigenen_decknamen(store) -> None:
    """Zwei Ziele, zwei Codes, zwei Adressen - sonst waeren sie im Trichter
    nicht mehr zu unterscheiden, und genau dafuer gibt es sie."""
    store.vergib_browsercode(KAMPAGNE, GID, BASIS)
    link = store.link_for(KAMPAGNE, GID)

    assert link.public_code_browser
    assert link.public_code_browser != link.public_code
    assert link.url_fuer("browser") == f"{BASIS}/r/{link.public_code_browser}"
    assert link.url_fuer("store") == f"{BASIS}/r/{link.public_code}"


def test_ohne_kurzcode_geht_die_lange_adresse_hinaus(store, config) -> None:
    """Der Rueckfall ist Absicht und keine Luecke.

    Ein Datensatz aus der Zeit davor soll einen Beitrag bekommen, der
    funktioniert, und nicht einen ohne Link. Haesslich ist besser als kaputt.
    """
    store.conn.execute(
        "UPDATE campaign_groups SET public_code = NULL, public_url = '' "
        "WHERE campaign_id = ? AND group_id = ?",
        (KAMPAGNE, GID),
    )
    store.conn.commit()

    campaign = store.load_campaign(KAMPAGNE)
    link = store.link_for(KAMPAGNE, GID)

    assert link.url_fuer("store") == link.tracking_url
    assert CODE in mit_link(campaign, link, "{link}", config=config)


def test_nachtragen_ist_wiederholbar(store) -> None:
    """Wer ihn zweimal laufen laesst, bekommt beim zweiten Mal eine Null."""
    store.conn.execute(
        "UPDATE campaign_groups SET public_code = NULL, public_url = ''"
    )
    store.conn.commit()

    assert store.kurzcodes_nachtragen(KAMPAGNE) == 1
    assert store.kurzcodes_nachtragen(KAMPAGNE) == 0


def test_ein_umzug_nimmt_beide_adressen_mit(store) -> None:
    """Nur die Haelfte umzustellen hiesse: zwei Dienste fuer eine Gruppe."""
    store.vergib_browsercode(KAMPAGNE, GID, BASIS)
    store.refresh_tracking_urls(KAMPAGNE, lambda code: f"https://neu.example/r/{code}")

    link = store.link_for(KAMPAGNE, GID)
    for adresse in (
        link.tracking_url,
        link.tracking_url_browser,
        link.public_url,
        link.public_url_browser,
    ):
        assert adresse.startswith("https://neu.example/r/")
    # Die Codes selbst bleiben - sie stehen in veroeffentlichten Beitraegen.
    assert link.tracking_code == CODE


def test_der_bericht_findet_auch_unter_dem_decknamen(store) -> None:
    """Wer einen Kurzcode aus einem Beitrag abschreibt, meint denselben Vorgang.

    ``marketing code 8wa6dja`` muss dasselbe zeigen wie ``marketing code
    FB-SYR-DUE-004``. Faende es "keine Ereignisse", waere das die falsche
    Auskunft - sie stehen sehr wohl da, nur unter dem inneren Code.
    """
    from fbgroups.marketing.analytics import code_bericht
    from fbgroups.marketing.models import TrackingEvent

    kurz = store.link_for(KAMPAGNE, GID).public_code
    store.record_event(
        TrackingEvent(
            tracking_code=CODE,
            campaign_id=KAMPAGNE,
            group_id=GID,
            event_type=EventType.CLICK,
        )
    )

    unter_kurz = code_bericht(store, kurz)
    unter_lang = code_bericht(store, CODE)

    assert unter_kurz.tracking_code == CODE
    assert unter_kurz.group_id == GID
    assert unter_kurz.zahlen == unter_lang.zahlen == {EventType.CLICK.value: 1}
