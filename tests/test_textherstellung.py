"""Vom Vorlagentopf bis zum Text auf der Arbeitsseite.

Die Kette, die der Nutzer beschrieben hat: Kampagne -> Gruppen -> je Gruppe
Vorlage waehlen, mit Stadt und Zielgruppe fuellen, fertigen Text speichern ->
Arbeit. Und daneben der Weg, der erst danach kommt: die KI ueberarbeitet den
fertigen Text, ein Mensch uebernimmt oder verwirft.

Der wichtigste Test der Datei ist ``test_das_modell_sieht_den_tracking_code
_nie``. Alles andere hier ist Bequemlichkeit; das ist die Zusicherung.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    JobStatus,
    TextQuelle,
    Texttyp,
)
from fbgroups.marketing.store import MarketingStore
from fbgroups.marketing.web import create_app
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

KAMPAGNE = "bonn-123"
CODE = "FB-SYR-BON-001"

# Drei Gruppen, die sich in genau den Angaben unterscheiden, die in den Text
# gehen: Stadt vorhanden/andere/keine.
GRUPPEN = (
    ("100000000000001", "Syrer in Bonn", "Bonn", ["syrians"]),
    ("100000000000002", "Araber in Koeln", "Köln", ["arabs"]),
    ("100000000000003", "Syrer Deutschland", None, ["syrians"]),
)


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
                    city=stadt,
                    audience_tags=list(tags),
                )
                for gid, name, stadt, tags in GRUPPEN
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(
                campaign_id=KAMPAGNE,
                name="Syrer in Bonn",
                language="ar",
                audiences=["syrians"],
                landing_page="https://b-tarikak.de/",
            )
        )
        for nummer, (gid, _, _, _) in enumerate(GRUPPEN, 1):
            code = f"FB-SYR-BON-00{nummer}"
            store.add_link(
                CampaignGroup(
                    campaign_id=KAMPAGNE,
                    group_id=gid,
                    tracking_code=code,
                    tracking_url=f"https://go.b-tarikak.de/r/{code}",
                )
            )
    return pfad


@pytest.fixture()
def client(bestand: Path, config) -> TestClient:
    return TestClient(create_app(config=config, db_path=bestand))


def _fuelle(client: TestClient, schritt: str = "text") -> dict:
    antwort = client.post(f"/kampagnen/{KAMPAGNE}/vorbereiten", json={"schritt": schritt})
    assert antwort.status_code == 200, antwort.text
    return antwort.json()


# --- Die deterministische Haelfte -----------------------------------------


def test_jede_gruppe_bekommt_ihren_eigenen_text(client: TestClient, bestand: Path) -> None:
    """Sonst waere der einzige Unterschied zwischen 310 Beitraegen der Link."""
    _fuelle(client)
    with MarketingStore(bestand) as store:
        texte = {
            link.group_id: link.post_text for link in store.links_for_campaign(KAMPAGNE)
        }

    assert "بون" in texte["100000000000001"]
    assert "كولونيا" in texte["100000000000002"]
    # Ohne Stadt darf kein Stadtname und kein offener Platzhalter im Text sein.
    assert "{stadt}" not in texte["100000000000003"]
    assert "بون" not in texte["100000000000003"]


def test_die_zielgruppe_bestimmt_das_reiseziel(client: TestClient, bestand: Path) -> None:
    """Seit dem 28.08.2026 traegt der Beitrag das Ziel, nicht die Anrede.

    Die arabischen Vorlagen sprechen die Zielgruppe nicht mehr an; sie fragen
    nach Reisenden **dorthin**. Abgeleitet wird das weiterhin aus derselben
    Zielgruppe - "syrians" ergibt Syrien, und "arabs" hat kein einzelnes Land
    und faellt auf ``ziel_allgemein`` zurueck, statt eines zu erfinden.
    """
    _fuelle(client)
    with MarketingStore(bestand) as store:
        texte = {
            link.group_id: link.post_text for link in store.links_for_campaign(KAMPAGNE)
        }
    assert "سوريا" in texte["100000000000001"]
    assert "الوطن" in texte["100000000000002"]


def test_der_tracking_platzhalter_bleibt_im_gespeicherten_text(
    client: TestClient, bestand: Path
) -> None:
    """Der Code kommt erst in ``beitrag.beitragstext`` hinein - nie frueher."""
    _fuelle(client)
    with MarketingStore(bestand) as store:
        for link in store.links_for_campaign(KAMPAGNE):
            assert "{link}" in link.post_text
            assert link.tracking_code not in link.post_text


def test_erzeugter_und_laufender_text_stehen_nebeneinander(
    client: TestClient, bestand: Path
) -> None:
    _fuelle(client)
    with MarketingStore(bestand) as store:
        link = store.link_for(KAMPAGNE, "100000000000001")
    assert link.generated_text == link.post_text
    assert link.vorlage_key.startswith("ar/post/mit_stadt/")


def test_ein_vorhandener_text_wird_nicht_ueberschrieben(
    client: TestClient, bestand: Path
) -> None:
    """Handarbeit ueberlebt einen Sammelschritt - wie Notizen einen Reimport."""
    with MarketingStore(bestand) as store:
        store.set_post_text(KAMPAGNE, "100000000000001", "Von Hand {link}", TextQuelle.HAND)

    _fuelle(client)

    with MarketingStore(bestand) as store:
        link = store.link_for(KAMPAGNE, "100000000000001")
    assert link.post_text == "Von Hand {link}"
    # Der erzeugte Text wird trotzdem aufgefrischt - er ist die Vergleichsgroesse.
    assert "بون" in link.generated_text


def test_text_neu_ueberschreibt_auch_vorhandene(client: TestClient, bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        store.set_post_text(KAMPAGNE, "100000000000001", "Von Hand {link}", TextQuelle.HAND)

    _fuelle(client, "text_neu")

    with MarketingStore(bestand) as store:
        link = store.link_for(KAMPAGNE, "100000000000001")
    assert "بون" in link.post_text


def test_veroeffentlichte_beitraege_bleiben_unangetastet(
    client: TestClient, bestand: Path
) -> None:
    """Der Text steht dort schon in der Gruppe - ihn zu aendern hiesse luegen."""
    with MarketingStore(bestand) as store:
        store.set_post_text(KAMPAGNE, "100000000000001", "Steht schon drin {link}", TextQuelle.HAND)
        for stand in (JobStatus.PENDING_REVIEW, JobStatus.APPROVED, JobStatus.QUEUED,
                      JobStatus.PROCESSING, JobStatus.PUBLISHED):
            store.set_job_status(KAMPAGNE, "100000000000001", stand)

    _fuelle(client, "text_neu")

    with MarketingStore(bestand) as store:
        link = store.link_for(KAMPAGNE, "100000000000001")
    assert link.post_text == "Steht schon drin {link}"


def test_die_eigene_vorlage_der_kampagne_geht_vor(client: TestClient, bestand: Path) -> None:
    """Ein Mensch hat sie hingeschrieben - ein Vorrat ueberstimmt das nicht.

    Personalisiert wird sie trotzdem: ``{zielgruppe}`` und ``{stadt}`` wirken
    darin genauso wie in einer Vorlage aus dem Topf.
    """
    with MarketingStore(bestand) as store:
        campaign = store.load_campaign(KAMPAGNE)
        campaign.message_template = "Eigener Text fuer {zielgruppe} in {stadt}: {link}"
        store.save_campaign(campaign)

    _fuelle(client)

    with MarketingStore(bestand) as store:
        link = store.link_for(KAMPAGNE, "100000000000001")
    assert link.post_text == "Eigener Text fuer السوريين in بون: {link}"
    assert link.vorlage_key == "kampagne"


def test_eine_eigene_vorlage_ohne_link_wird_abgewiesen(
    client: TestClient, bestand: Path
) -> None:
    with MarketingStore(bestand) as store:
        campaign = store.load_campaign(KAMPAGNE)
        campaign.message_template = "Ohne Platzhalter"
        store.save_campaign(campaign)

    antwort = _fuelle(client)
    assert antwort["betroffen"] == 0
    assert "{link}" in antwort["hinweis"]


# --- Der Kommentar ---------------------------------------------------------


def test_jede_kampagne_bekommt_kommentartexte(
    client: TestClient, bestand: Path
) -> None:
    """Seit dem 28.08.2026 gibt es die Frage nicht mehr.

    Vorher hing der Kommentar an einem Haken je Kampagne. Der stand in jeder
    Zeile der Uebersicht und musste in keiner beantwortet werden: Wer in einer
    Gruppe postet, kommentiert dort auch. Ein Kommentar zu viel kostet einen
    Blick, ein fehlender einen Handgriff.
    """
    _fuelle(client)
    with MarketingStore(bestand) as store:
        for link in store.links_for_campaign(KAMPAGNE):
            assert link.kommentar_text.strip(), link.group_id


def test_beitrag_und_kommentar_kommen_aus_getrennten_toepfen(
    client: TestClient, bestand: Path
) -> None:
    """Ein gekuerzter Beitrag als Kommentar liest sich wie eingeworfene Werbung."""
    _fuelle(client)

    with MarketingStore(bestand) as store:
        link = store.link_for(KAMPAGNE, "100000000000001")

    assert link.vorlage_key.startswith("ar/post/")
    assert link.kommentar_vorlage_key.startswith("ar/kommentar/")
    assert link.kommentar_text.strip()
    assert link.kommentar_text != link.post_text
    # Beide gefuellt, beide mit Platzhalter - der Code kommt erst beim Lesen
    # hinein. Nicht auf einen Stadtnamen geprueft: Eine Kommentarvorlage darf
    # ohne Stadt auskommen und im Topf "mit_stadt" stehen; nur umgekehrt waere
    # es ein Fehler. Was zaehlt, ist, dass kein Platzhalter offen bleibt.
    assert "{stadt}" not in link.kommentar_text
    assert "{zielgruppe}" not in link.kommentar_text
    assert "{link}" in link.kommentar_text
    assert link.tracking_code not in link.kommentar_text


def test_der_kommentar_bleibt_kurz(client: TestClient, bestand: Path) -> None:
    """Nicht Kosmetik: Ein Kommentar, der wie ein Beitrag aussieht, ist Spam.

    Gemessen an der Form, nicht am Vergleich mit dem Beitrag - die kuerzeste
    Beitragsvorlage ist kuerzer als die laengste Kommentarvorlage, und das ist
    in Ordnung. Was einen Kommentar ausmacht, ist, dass er nicht eroeffnet:
    wenige Zeilen, kein Aufbau mit Anrede, Erklaerung und Aufruf.
    """
    _fuelle(client)
    with MarketingStore(bestand) as store:
        for link in store.links_for_campaign(KAMPAGNE):
            zeilen = [z for z in link.kommentar_text.splitlines() if z.strip()]
            assert len(zeilen) <= 4, link.group_id
            assert len(link.kommentar_text) <= 250, link.group_id


def test_ein_vorhandener_kommentar_wird_nicht_ueberschrieben(
    client: TestClient, bestand: Path
) -> None:
    with MarketingStore(bestand) as store:
        store.set_post_text(
            KAMPAGNE,
            "100000000000001",
            "Von Hand {link}",
            TextQuelle.HAND,
            texttyp=Texttyp.KOMMENTAR,
        )

    _fuelle(client)

    with MarketingStore(bestand) as store:
        link = store.link_for(KAMPAGNE, "100000000000001")
    assert link.kommentar_text == "Von Hand {link}"
    assert link.kommentar_generated.strip()          # die Vergleichsgroesse doch


def test_eine_neue_gruppe_bekommt_eigene_texte(client: TestClient, bestand: Path) -> None:
    """Bestehende Gruppentexte bleiben, die neue bekommt ihre eigenen."""
    _fuelle(client)
    with MarketingStore(bestand) as store:
        vorher = {
            link.group_id: (link.post_text, link.kommentar_text)
            for link in store.links_for_campaign(KAMPAGNE)
        }

    neue = "100000000000009"
    with SqliteStore(bestand) as gruppen_store:
        gruppen_store.upsert_groups(
            [
                Group(
                    group_id=neue,
                    url_canonical=f"https://www.facebook.com/groups/{neue}",
                    name="Syrer in Essen",
                    city="Essen",
                    audience_tags=["syrians"],
                )
            ]
        )
    with MarketingStore(bestand) as store:
        store.add_link(
            CampaignGroup(
                campaign_id=KAMPAGNE,
                group_id=neue,
                tracking_code="FB-SYR-ESS-001",
                tracking_url="https://go.b-tarikak.de/r/FB-SYR-ESS-001",
            )
        )

    _fuelle(client)

    with MarketingStore(bestand) as store:
        nachher = {
            link.group_id: (link.post_text, link.kommentar_text)
            for link in store.links_for_campaign(KAMPAGNE)
        }
    for gid, texte in vorher.items():
        assert nachher[gid] == texte, gid
    assert nachher[neue][0].strip()
    assert nachher[neue][1].strip()


def test_die_arbeitsseite_zeigt_beide_spalten(client: TestClient, bestand: Path) -> None:
    """Dieselbe Gruppe, derselbe Handgriff - eine Seite, zwei Spalten."""
    _fuelle(client)

    seite = client.get(f"/arbeit/{KAMPAGNE}").text
    assert "data-spalte='post'" in seite
    assert "data-spalte='kommentar'" in seite
    assert "Beitrag kopieren" in seite
    assert "Kommentar kopieren" in seite
    # Jede Spalte nennt ihre eigene Vorlage - eine gemeinsame Zeile liesse
    # offen, welche von beiden gemeint ist.
    assert "ar/post/" in seite
    assert "ar/kommentar/" in seite


def test_die_kommentarspalte_steht_vor_dem_ersten_lauf(
    client: TestClient, bestand: Path
) -> None:
    """Sie ist die Stelle, an der ein Kommentar entsteht.

    Ohne gefuellte Fassungen steht sie trotzdem - eine Stelle, die es nur
    gibt, wenn schon etwas dasteht, ist keine. Der frueher hier gepruefte
    zweite Grund ("Diese Kampagne fuehrt keine Kommentare") ist entfallen:
    Jede Kampagne fuehrt welche.
    """
    seite = client.get(f"/arbeit/{KAMPAGNE}?gruppe=1").text
    assert "data-spalte='kommentar'" in seite


# --- Zurueckholen ----------------------------------------------------------


def test_zuruecksetzen_holt_den_erzeugten_text(client: TestClient, bestand: Path) -> None:
    """Und zwar den **dieser** Fassung - nicht den der Gruppe."""
    _fuelle(client)
    with MarketingStore(bestand) as store:
        erzeugt = store.vorschlag(
            KAMPAGNE, "100000000000001", Texttyp.POST, 2
        ).generated_text
        store.setze_vorschlag_text(
            KAMPAGNE, "100000000000001", Texttyp.POST, 2, "Missraten {link}"
        )

    antwort = client.post(
        f"/arbeit/{KAMPAGNE}/vorschlag/zuruecksetzen",
        json={"group_id": "100000000000001", "nummer": 2},
    )
    assert antwort.status_code == 200
    assert antwort.json()["ok"] is True

    with MarketingStore(bestand) as store:
        assert store.vorschlag(
            KAMPAGNE, "100000000000001", Texttyp.POST, 2
        ).text == erzeugt


def test_ohne_erzeugten_text_gibt_es_nichts_zurueckzuholen(
    client: TestClient, bestand: Path
) -> None:
    """Ein leerer Text waere schlimmer als ein unpassender."""
    antwort = client.post(
        f"/arbeit/{KAMPAGNE}/vorschlag/zuruecksetzen",
        json={"group_id": "100000000000001", "nummer": 1},
    )
    assert antwort.status_code == 200
    assert antwort.json()["ok"] is False


# --- Von Hand schreiben ----------------------------------------------------
#
# Hier stand einmal der KI-Weg: ein Modell, das den Text ueberarbeitet, ein
# Entwurf, ein Uebernehmen. Die KI ist aus dem Projekt entfernt - Texte kommen
# aus den Vorlagen oder aus der Hand des Menschen, der vor der Arbeitsseite
# sitzt. Was bleibt, ist die Zusicherung, um die es dabei immer ging: Der
# Tracking-Code kommt erst beim Lesen in den Text, und was zurueckkommt, wird
# geprueft.


def test_der_editor_schreibt_den_text_dieser_gruppe(
    client: TestClient, bestand: Path
) -> None:
    """Der Kern der Umstellung: Der Text entsteht auf der Arbeitsseite.

    Und er gehoert genau **dieser** Gruppe - die naechste bleibt leer, bis
    jemand auch dort etwas schreibt.
    """
    antwort = client.post(
        f"/arbeit/{KAMPAGNE}/vorschlag/text",
        json={
            "group_id": "100000000000001",
            "nummer": 1,
            "text": "Von Hand fuer Bonn {link}",
        },
    )
    assert antwort.status_code == 200
    assert antwort.json()["ok"] is True
    # Die Antwort traegt beides: den gespeicherten Text mit Platzhalter und
    # die angezeigte Fassung mit eingesetztem Link. Der Browser rechnet das
    # eine nicht in das andere um.
    assert antwort.json()["text"] == "Von Hand fuer Bonn {link}"
    assert "go.b-tarikak.de/r/" in antwort.json()["angezeigt"]

    with MarketingStore(bestand) as store:
        vorschlag = store.vorschlag(KAMPAGNE, "100000000000001", Texttyp.POST, 1)
        link = store.link_for(KAMPAGNE, "100000000000001")
        andere = store.link_for(KAMPAGNE, "100000000000002")
    assert vorschlag.text == "Von Hand fuer Bonn {link}"
    assert vorschlag.quelle is TextQuelle.HAND
    # Das Paar zeigt weiterhin, woran zuletzt gearbeitet wurde - sonst laese
    # ``campaign message`` einen Text von vorgestern.
    assert link.post_text == "Von Hand fuer Bonn {link}"
    assert andere.post_text == ""


def test_der_gespeicherte_text_traegt_keinen_ausgeschriebenen_link(
    client: TestClient, bestand: Path
) -> None:
    """Ein Beitrag, der richtig aussieht und dessen Gruppe nie einen Klick bekommt.

    Der Fehler, den niemand bemerkt - deshalb weist der Server ihn ab, statt
    ihn zu speichern.
    """
    antwort = client.post(
        f"/arbeit/{KAMPAGNE}/vorschlag/text",
        json={
            "group_id": "100000000000001",
            "nummer": 1,
            "text": f"Schaut hier: https://go.b-tarikak.de/r/{CODE} - oder hier {{link}}",
        },
    )
    assert antwort.json()["ok"] is False
    assert "Adresse" in antwort.json()["meldung"]

    with MarketingStore(bestand) as store:
        assert store.vorschlag(KAMPAGNE, "100000000000001", Texttyp.POST, 1) is None


def test_ohne_platzhalter_wird_nichts_gespeichert(client: TestClient) -> None:
    antwort = client.post(
        f"/arbeit/{KAMPAGNE}/vorschlag/text",
        json={"group_id": "100000000000001", "nummer": 1, "text": "Ganz ohne Link"},
    )
    assert antwort.json()["ok"] is False
    assert "{link}" in antwort.json()["meldung"]


# --- Die Kampagne und ihre Textarten ---------------------------------------


def test_eine_frisch_angelegte_kampagne_fuehrt_kommentare(
    client: TestClient, bestand: Path
) -> None:
    """Ohne Angabe und ohne Haken - es gibt beides nicht mehr."""
    antwort = client.post(
        "/kampagnen", json={"name": "Schlicht", "campaign_id": "schlicht"}
    )
    assert antwort.status_code == 201, antwort.text

    with MarketingStore(bestand) as store:
        campaign = store.load_campaign("schlicht")
    assert campaign is not None
    assert not hasattr(campaign, "kommentare")


def test_den_umschaltweg_gibt_es_nicht_mehr(client: TestClient) -> None:
    """``POST /kampagnen/{id}/texte`` ist entfallen - es gibt nichts zu schalten."""
    antwort = client.post(f"/kampagnen/{KAMPAGNE}/texte", json={"kommentare": True})
    assert antwort.status_code == 404


# --- Die Rechte ------------------------------------------------------------


@pytest.mark.parametrize(
    ("weg", "rumpf"),
    [
        ("vorschlag/zuruecksetzen", {"group_id": "100000000000001", "nummer": 1}),
        (
            "vorschlag/text",
            {"group_id": "100000000000001", "nummer": 1, "text": "Fremd {link}"},
        ),
        (
            "vorschlag/ergebnis",
            {
                "group_id": "100000000000001",
                "nummer": 1,
                "ausgang": "veroeffentlicht",
            },
        ),
    ],
)
def test_von_aussen_geht_keiner_dieser_wege(
    bestand: Path, config, weg: str, rumpf: dict
) -> None:
    """Beide schreiben - und ein Weg, der von aussen offen stuende, brachte
    einen fremden Text in einen veroeffentlichten Beitrag."""
    fremd = TestClient(
        create_app(config=config, db_path=bestand), client=("203.0.113.7", 44321)
    )
    assert fremd.post(f"/arbeit/{KAMPAGNE}/{weg}", json=rumpf).status_code == 404


# --- Der Rueckweg auf der Seite -------------------------------------------


def _seite(client: TestClient, bestand: Path) -> str:
    """Die Arbeitsseite dieser Kampagne, mit gefuellten Fassungen."""
    _fuelle(client)
    return client.get(f"/arbeit/{KAMPAGNE}").text


def test_jede_spalte_hat_ihren_eigenen_kopierknopf(
    client: TestClient, bestand: Path
) -> None:
    """Kopiert wird der **angezeigte** Text, also der mit eingesetztem Link.

    ``{link}`` steht im Textfeld und geht nie in die Zwischenablage - deshalb
    faehrt beides getrennt zur Seite (``text`` und ``angezeigt``), und der
    Browser rechnet das eine nicht in das andere um.
    """
    seite = _seite(client, bestand)
    assert "id='kopieren-post'" in seite
    assert "id='kopieren-kommentar'" in seite
    # Welche Gruppe oben steht, entscheidet der Score - der Code ist deshalb
    # nicht vorhersagbar, das Praefix schon.
    assert "go.b-tarikak.de/r/FB-SYR-BON-00" in seite


def test_beide_spalten_melden_ihren_eigenen_ausgang(
    client: TestClient, bestand: Path
) -> None:
    """Post und Kommentar gehen getrennt hinaus - also melden sie getrennt.

    Frueher hatte der Kommentar gar keinen Ausgang: Der Ablauf gehoerte dem
    Beitrag, und ein gesetzter Kommentar liess sich nirgends festhalten.
    """
    seite = _seite(client, bestand)
    assert "id='gut-post'" in seite
    assert "id='gut-kommentar'" in seite
    assert "id='schlecht-post'" in seite
    assert "id='schlecht-kommentar'" in seite


def test_statt_des_rueckwegs_steht_die_gruppe_in_jeder_spalte(
    client: TestClient, bestand: Path
) -> None:
    """Der dritte Knopf ist der naechste Schritt, nicht der vorige.

    "Zurueck zur Vorlage" beantwortete eine Frage, die sich hier selten
    stellt, und stand an der Stelle, an der man nach dem Kopieren weitergeht:
    in die Gruppe. Der Weg zurueck bleibt - ``generated_text`` steht
    unveraendert neben ``text`` und ``/vorschlag/zuruecksetzen`` holt ihn.
    """
    seite = _seite(client, bestand)
    assert "id='zurueck-post'" not in seite
    assert "id='zurueck-kommentar'" not in seite
    # Zweimal in den Spalten, einmal in der Gruppen-Navigation darueber.
    assert seite.count("Gruppe bei Facebook oeffnen") == 3


# --- Die Migration ---------------------------------------------------------


def test_eine_datei_ohne_kommentarspalten_holt_sie_nach(bestand: Path) -> None:
    """Der Bestand auf dem Server ist die einzige gueltige Fassung.

    Er wird nicht neu aufgebaut, sondern nachgezogen - additiv und ohne
    Abschrift: Ein Beitrag, als Kommentar unter einen fremden Beitrag gesetzt,
    ist genau das, was hier vermieden werden soll.
    """
    import sqlite3

    with MarketingStore(bestand) as store:
        store.set_post_text(KAMPAGNE, "100000000000001", "Beitrag {link}", TextQuelle.HAND)

    # Die Datei auf den Stand vor den Kommentaren zuruecksetzen.
    conn = sqlite3.connect(bestand)
    conn.executescript(
        """
        ALTER TABLE campaign_groups DROP COLUMN kommentar_text;
        ALTER TABLE campaign_groups DROP COLUMN kommentar_generated;
        ALTER TABLE campaign_groups DROP COLUMN kommentar_vorlage_key;
        ALTER TABLE campaign_groups DROP COLUMN kommentar_quelle;
        ALTER TABLE campaign_groups DROP COLUMN kommentar_generiert_am;
        ALTER TABLE campaigns DROP COLUMN kommentare;
        PRAGMA user_version = 12;
        """
    )
    conn.commit()
    conn.close()

    with SqliteStore(bestand):
        pass

    with MarketingStore(bestand) as store:
        link = store.link_for(KAMPAGNE, "100000000000001")
        campaign = store.load_campaign(KAMPAGNE)
    assert link.post_text == "Beitrag {link}"       # unangetastet
    assert link.kommentar_text == ""                # keine Abschrift
    assert campaign is not None                     # die Kampagne laedt weiterhin


def test_der_marketing_speicher_holt_den_schritt_ebenfalls_nach(bestand: Path) -> None:
    """``GET /r/{code}`` oeffnet nur diesen Speicher - er darf nicht scheitern."""
    import sqlite3

    conn = sqlite3.connect(bestand)
    conn.executescript(
        """
        ALTER TABLE campaign_groups DROP COLUMN kommentar_text;
        PRAGMA user_version = 12;
        """
    )
    conn.commit()
    conn.close()

    with MarketingStore(bestand) as store:
        assert store.link_for(KAMPAGNE, "100000000000001") is not None


def test_die_uebersicht_zeigt_keinen_schalter_mehr(client: TestClient) -> None:
    """Eine Frage, die in jeder Zeile stand und in keiner beantwortet wurde."""
    assert "k-kommentare" not in client.get("/").text
