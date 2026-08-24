"""Tests fuer die Uebersichtsseite unter ``/``.

Der Dienst laeuft gegen eine eigene Datenbank im tmp-Verzeichnis - der echte
Bestand wird dabei nie geoeffnet.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from fbgroups.marketing.dashboard import render, sammle_daten
from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    CampaignStatus,
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


# --- Trichterzahlen auf der Seite ---------------------------------------

def _ereignisse(pfad: Path, group_id: str, *typen: str) -> None:
    """Schreibt Ereignisse, wie sie sonst der Redirect und die App melden."""
    from fbgroups.marketing.models import EventType, TrackingEvent

    with MarketingStore(pfad) as store:
        for typ in typen:
            store.record_event(
                TrackingEvent(
                    tracking_code="FB-SYR-BER-001",
                    campaign_id="batreeq",
                    group_id=group_id,
                    user_ref="user-1" if typ != "click" else "",
                    event_type=EventType(typ),
                )
            )


def test_zahlen_stehen_in_der_zeile_der_gruppe(bestand: Path, config) -> None:
    """Die Frage "welche Gruppe bringt Leute?" muss dieselben Filter haben.

    Eine zweite Bestenliste daneben waere ein zweiter Satz Zahlen, den man
    getrennt filtern und getrennt sortieren muesste.
    """
    _ereignisse(bestand, REAL_ID_A, "click", "click", "registration", "qualified")

    daten = sammle_daten(config, bestand)
    zeile = next(z for z in daten["gruppen"] if z["id"] == REAL_ID_A)

    assert zeile["click"] == 2
    assert zeile["registration"] == 1
    assert zeile["qualified"] == 1
    assert zeile["conversion"] == 0


def test_gruppe_ohne_ereignisse_zeigt_null_und_nicht_leer(bestand: Path, config) -> None:
    daten = sammle_daten(config, bestand)
    zeile = next(z for z in daten["gruppen"] if z["id"] == REAL_ID_B)

    assert zeile["click"] == 0
    assert zeile["conversion"] == 0


def test_kacheln_zaehlen_die_vergebenen_links(bestand: Path, config) -> None:
    daten = sammle_daten(config, bestand)
    assert daten["kennzahlen"]["tracking_links"] == 1


def test_trichter_steht_auf_der_seite(bestand: Path, config) -> None:
    _ereignisse(bestand, REAL_ID_A, "click", "registration")

    daten = sammle_daten(config, bestand)
    stufen = {s["stufe"]: s["anzahl"] for s in daten["trichter"]}

    assert stufen["click"] == 1
    assert stufen["registration"] == 1
    assert "Trichter" in render(daten)


def test_quote_ohne_klicks_ist_kein_null_prozent(bestand: Path, config) -> None:
    """Eine Quote ohne Grundgesamtheit gibt es nicht - auch nicht auf der Seite."""
    daten = sammle_daten(config, bestand)
    kampagne = next(c for c in daten["kampagnen"] if c["id"] == "batreeq")

    assert kampagne["klicks"] == 0
    assert kampagne["quote"] is None
    assert "–" in render(daten)


def test_hinweis_wenn_links_vergeben_sind_aber_kein_klick_ankommt(
    bestand: Path, config
) -> None:
    """Der haeufigste stille Fehler: Die Domain zeigt auf eine andere Anwendung.

    Der Besucher sieht dann eine Seite, der Klick geht aber verloren. Ohne
    Hinweis faellt das erst auf, wenn jemand die Zahlen vermisst.
    """
    seite = render(sammle_daten(config, bestand))
    assert "Noch kein einziger Klick" in seite

    _ereignisse(bestand, REAL_ID_A, "click")
    assert "Noch kein einziger Klick" not in render(sammle_daten(config, bestand))


# --- Arbeitsentscheidung: bearbeiten wir diese Gruppe? -----------------

def test_ausschliessen_laesst_den_tracking_code_gueltig(
    client: TestClient, bestand: Path
) -> None:
    """Die wichtigste Zusage: Ein Ausschluss widerruft keinen Code.

    Der Code steht moeglicherweise schon in einem veroeffentlichten Beitrag.
    Wer ihn dort anklickt, muss weiter ankommen - und der Klick muss gezaehlt
    werden. Ausschliessen ist eine Entscheidung ueber die eigene Arbeit, nicht
    ueber bereits veroeffentlichte Links.
    """
    antwort = client.post(
        "/bearbeiten",
        json={"group_ids": [REAL_ID_A], "bearbeiten": False, "grund": "zu klein"},
    )
    assert antwort.status_code == 200

    weiterleitung = client.get("/r/FB-SYR-BER-001")
    assert weiterleitung.status_code == 302

    with MarketingStore(bestand) as store:
        assert store.resolve_code("FB-SYR-BER-001") is not None


def test_ausschliessen_ruehrt_den_kooperationsweg_nicht_an(
    client: TestClient, bestand: Path
) -> None:
    """Zwei Achsen, zwei Antworten.

    ``REAL_ID_A`` steht auf ``contacted``. Waere der Ausschluss ueber
    ``marketing_status`` geloest, ginge diese Angabe verloren - und beim
    Wiederaufnehmen finge man bei ``not_contacted`` an.
    """
    client.post(
        "/bearbeiten",
        json={"group_ids": [REAL_ID_A], "bearbeiten": False, "grund": "zu klein"},
    )

    with MarketingStore(bestand) as store:
        eintrag = store.load_marketing(REAL_ID_A)

    assert eintrag.bearbeiten is False
    assert eintrag.ausschlussgrund == "zu klein"
    assert eintrag.marketing_status is MarketingStatus.CONTACTED


def test_wiederaufnahme_loescht_den_grund(client: TestClient, bestand: Path) -> None:
    """Sonst stuende bei einer bearbeiteten Gruppe, warum sie es nicht wird."""
    client.post(
        "/bearbeiten",
        json={"group_ids": [REAL_ID_A], "bearbeiten": False, "grund": "zu klein"},
    )
    client.post("/bearbeiten", json={"group_ids": [REAL_ID_A], "bearbeiten": True})

    with MarketingStore(bestand) as store:
        eintrag = store.load_marketing(REAL_ID_A)

    assert eintrag.bearbeiten is True
    assert eintrag.ausschlussgrund == ""


def test_mehrere_gruppen_in_einem_zug(client: TestClient, bestand: Path) -> None:
    """Bei 413 Gruppen ist Aussortieren ein Zug, keine 144 Klicks."""
    antwort = client.post(
        "/bearbeiten",
        json={"group_ids": [REAL_ID_A, REAL_ID_B], "bearbeiten": False, "grund": "Test"},
    )

    assert antwort.json()["anzahl"] == 2
    with MarketingStore(bestand) as store:
        assert store.load_marketing(REAL_ID_A).bearbeiten is False
        assert store.load_marketing(REAL_ID_B).bearbeiten is False


def test_unbekannte_gruppe_wird_abgewiesen(client: TestClient) -> None:
    """Kein stiller Eintrag fuer eine Kennung, die es nicht gibt."""
    antwort = client.post(
        "/bearbeiten", json={"group_ids": ["gibtesnicht"], "bearbeiten": False}
    )
    assert antwort.status_code == 404


def test_bearbeiten_ist_von_aussen_nicht_vorhanden(bestand: Path, config) -> None:
    """Wie die Uebersicht: 404 statt 403, kein Hinweis auf die Arbeitsliste."""
    from fbgroups.marketing.web import create_app

    app = create_app(config=config, db_path=bestand)
    with TestClient(app, client=("203.0.113.7", 1234)) as fremd:
        antwort = fremd.post(
            "/bearbeiten", json={"group_ids": [REAL_ID_A], "bearbeiten": False}
        )
    assert antwort.status_code == 404

# --- Kampagnen anlegen und steuern -------------------------------------

def test_anlegen_vergibt_keinen_einzigen_code(client: TestClient, bestand: Path) -> None:
    """Die wichtigste Trennung: Anlegen ist nicht Zuordnen.

    Ein Tracking-Code ist endgueltig - er steht spaeter in veroeffentlichten
    Beitraegen und wird nie zurueckgenommen. Ein Formular, das beim Speichern
    still Codes vergibt, waere ein Knopf mit unumkehrbarer Wirkung.
    """
    with MarketingStore(bestand) as store:
        vorher = len(store.assigned_codes())

    antwort = client.post("/kampagnen", json={"name": "Batreeq Iraqi Germany"})

    assert antwort.status_code == 201
    assert antwort.json()["campaign_id"] == "batreeq-iraqi-germany"
    assert antwort.json()["status"] == "draft"

    with MarketingStore(bestand) as store:
        assert len(store.assigned_codes()) == vorher
        neu = store.load_campaign("batreeq-iraqi-germany")
    assert neu is not None
    assert neu.auto_assign is False


def test_auswahlregel_bildet_die_beschreibung_ab(client: TestClient, bestand: Path) -> None:
    """Leer hiesse "keine Einschraenkung" - also der ganze Bestand.

    Das mag richtig sein, ist aber eine Entscheidung und keine Vorgabe fuer ein
    frisch ausgefuelltes Formular.
    """
    client.post(
        "/kampagnen",
        json={"name": "Syrer Berlin", "audiences": ["syrians"], "cities": ["berlin"]},
    )

    with MarketingStore(bestand) as store:
        campaign = store.load_campaign("syrer-berlin")

    assert campaign is not None
    assert campaign.target_audiences == ["syrians"]
    assert campaign.target_cities == ["berlin"]


def test_unbekannte_zielgruppe_wird_abgewiesen(client: TestClient) -> None:
    """Die Kennungen stehen in config/*.yaml - eine zweite Liste gaebe es nicht."""
    antwort = client.post(
        "/kampagnen", json={"name": "Test", "audiences": ["marsianer"]}
    )

    assert antwort.status_code == 422
    assert "marsianer" in antwort.json()["detail"]


def test_doppelte_kennung_wird_abgewiesen(client: TestClient) -> None:
    """Sonst uebernaehme die zweite Kampagne die Codes der ersten."""
    client.post("/kampagnen", json={"name": "Doppelt"})
    zweite = client.post("/kampagnen", json={"name": "Doppelt"})

    assert zweite.status_code == 409


def test_arabischer_name_verlangt_eine_kennung(client: TestClient) -> None:
    """Die Kennung steht im Tracking-Code und damit in einer URL.

    Aus einem rein arabischen Namen entsteht dabei nichts Brauchbares - das
    muss der Mensch entscheiden, nicht eine stille Ersatzkennung.
    """
    ohne = client.post("/kampagnen", json={"name": "حملة عربية"})
    mit = client.post(
        "/kampagnen", json={"name": "حملة عربية", "campaign_id": "arabi-2026"}
    )

    assert ohne.status_code == 422
    assert mit.status_code == 201
    assert mit.json()["campaign_id"] == "arabi-2026"


def test_status_laesst_die_codes_gueltig(client: TestClient, bestand: Path) -> None:
    """Eine pausierte Kampagne nimmt nichts Neues auf - ihre Links leben weiter.

    Sie stehen in Beitraegen, die niemand zurueckholt.
    """
    antwort = client.post("/kampagnen/batreeq/status", json={"status": "paused"})

    assert antwort.status_code == 200
    assert client.get("/r/FB-SYR-BER-001").status_code == 302
    with MarketingStore(bestand) as store:
        assert store.load_campaign("batreeq").status is CampaignStatus.PAUSED


def test_sync_rechnet_erst_und_schreibt_nichts(client: TestClient, bestand: Path) -> None:
    """``dry_run`` ist die Vorgabe: gezeigt wird erst, gehandelt danach."""
    client.post("/kampagnen", json={"name": "Vorschau"})
    with MarketingStore(bestand) as store:
        vorher = len(store.assigned_codes())

    plan = client.post("/kampagnen/vorschau/sync", json={"dry_run": True}).json()

    assert plan["neu"] > 0
    with MarketingStore(bestand) as store:
        assert len(store.assigned_codes()) == vorher


def test_sync_und_vorschau_nennen_dieselbe_zahl(client: TestClient) -> None:
    """Eine zweite Zaehlung koennte von der Ausfuehrung abweichen.

    Bei einer unumkehrbaren Vergabe waere das der schlechteste Fehler: Der
    Mensch bestaetigt eine Zahl und bekommt eine andere.
    """
    client.post("/kampagnen", json={"name": "Gleichstand"})

    vorschau = client.post("/kampagnen/gleichstand/sync", json={"dry_run": True}).json()
    ernstfall = client.post("/kampagnen/gleichstand/sync", json={"dry_run": False}).json()

    assert vorschau["neu"] == ernstfall["neu"]
    assert ernstfall["neu"] > 0


def test_sync_ist_wiederholbar(client: TestClient) -> None:
    """Ein zweiter Lauf ohne neue Gruppen legt nichts an."""
    client.post("/kampagnen", json={"name": "Zweimal"})
    client.post("/kampagnen/zweimal/sync", json={"dry_run": False})

    nochmal = client.post("/kampagnen/zweimal/sync", json={"dry_run": False}).json()

    assert nochmal["neu"] == 0
    assert nochmal["bereits_zugeordnet"] > 0


def test_kampagnenwege_sind_von_aussen_nicht_vorhanden(bestand: Path, config) -> None:
    """Wie die Uebersicht: 404, kein Hinweis auf die Arbeitsliste."""
    from fbgroups.marketing.web import create_app

    app = create_app(config=config, db_path=bestand)
    with TestClient(app, client=("203.0.113.7", 1234)) as fremd:
        assert fremd.post("/kampagnen", json={"name": "X"}).status_code == 404
        assert fremd.post("/kampagnen/batreeq/status", json={"status": "paused"}).status_code == 404
        assert fremd.post("/kampagnen/batreeq/sync", json={"dry_run": True}).status_code == 404


def test_javascript_der_seite_ist_syntaktisch_gueltig(bestand: Path, config) -> None:
    """Ein Syntaxfehler im Skript laesst die Tabelle leer - ohne jede Meldung.

    Genau das ist passiert: Ein als JS-Escape gemeintes ``\\n`` stand im
    Python-Quelltext einfach maskiert und wurde beim Uebersetzen zu einem
    echten Zeilenumbruch. Damit blieb ein String-Literal offen, das gesamte
    Skript war ungueltig, und die Seite zeigte nur noch die Kopfzeile der
    Tabelle - kein Eintrag, nicht einmal die Meldung "Keine Gruppe passt".

    Der Browser schreibt so etwas in die Konsole. Bei einer Seite, die man
    ueber einen SSH-Tunnel oeffnet, sieht dort niemand nach: Man haelt den
    leeren Bestand fuer die Wahrheit.
    """
    node = shutil.which("node")
    if node is None:  # pragma: no cover - haengt von der Umgebung ab
        pytest.skip("node nicht vorhanden - Syntaxpruefung uebersprungen")

    seite = render(sammle_daten(config, bestand))
    skript = seite.split("<script>")[1].split("</script>")[0]

    datei = Path(tempfile.gettempdir()) / "fbgroups_dashboard_pruefung.js"
    datei.write_text(skript, encoding="utf-8")
    try:
        lauf = subprocess.run(
            [node, "--check", str(datei)], capture_output=True, text=True, timeout=30
        )
    finally:
        datei.unlink(missing_ok=True)

    assert lauf.returncode == 0, lauf.stderr


def test_seite_hat_ausgeglichene_abschnitte(bestand: Path, config) -> None:
    """Ein Element an der falschen Stelle verschiebt das ganze Raster.

    Das Formular landete zwischen den beiden Spalten statt in einer davon und
    wurde damit zur dritten Spalte - die Gruppentabelle rutschte aus dem Bild.
    """
    seite = render(sammle_daten(config, bestand))

    # ``<section`` ohne schliessende Klammer: Der Kampagnenabschnitt traegt
    # eine Klasse, ``<section>`` allein zaehlte ihn nicht mit.
    assert seite.count("<section") == seite.count("</section>")
    assert seite.count("<details") == seite.count("</details>")

    # Die Kampagnen stehen seit dem Aufraeumen fuer sich und in voller Breite:
    # In der halben lief die Tabelle bei mehreren Kampagnen ueber, und
    # ausgerechnet die Knopfspalte verschwand im waagerechten Bildlauf.
    assert seite.index("kampagnen-block") < seite.index('class="spalten"')

    spalten = seite[seite.index('class="spalten"'):]
    assert spalten.count("<section") == 1  # nur noch der Trichter


# --- Auswahlregel je Kampagne ------------------------------------------

def test_regel_laesst_sich_je_kampagne_setzen(client: TestClient, bestand: Path) -> None:
    """Die Auswahl gehoert in die Kampagne, nicht nur ins Anlegen-Formular.

    Bisher liess sich die Regel nur beim Anlegen bestimmen und danach allein
    auf der Kommandozeile aendern (``campaign target``).
    """
    antwort = client.post(
        "/kampagnen/batreeq/auswahl",
        json={"audiences": ["syrians"], "cities": ["berlin"]},
    )

    assert antwort.status_code == 200
    with MarketingStore(bestand) as store:
        campaign = store.load_campaign("batreeq")
    assert campaign.target_audiences == ["syrians"]
    assert campaign.target_cities == ["berlin"]
    # Die Beschreibung der Kampagne bleibt davon unberuehrt: Sie sagt, wen die
    # Kampagne bewirbt - nicht, welche Gruppen einen Code bekommen.
    assert campaign.audiences == []


def test_regel_speichern_vergibt_keinen_code(client: TestClient, bestand: Path) -> None:
    """Speichern rechnet vor, mehr nicht.

    Ein Tracking-Code ist endgueltig - er steht spaeter in veroeffentlichten
    Beitraegen. Die Codes entstehen erst ueber "Zuordnen", und dort wird noch
    einmal gefragt.
    """
    with MarketingStore(bestand) as store:
        vorher = store.assigned_codes()

    antwort = client.post("/kampagnen/batreeq/auswahl", json={"audiences": ["syrians"]})

    assert antwort.json()["neu"] > 0
    with MarketingStore(bestand) as store:
        assert store.assigned_codes() == vorher


def test_leere_liste_hebt_die_einschraenkung_auf(client: TestClient) -> None:
    """Leer heisst "keine Einschraenkung" - "alle Gruppen" ist ein Normalfall.

    Ohne diesen Weg gaebe es keinen zurueck: Eine leere Liste ist von "nicht
    angegeben" nicht zu unterscheiden, deshalb schickt das Formular immer den
    vollstaendigen Stand beider Listen.
    """
    client.post("/kampagnen/batreeq/auswahl", json={"audiences": ["syrians"]})
    antwort = client.post(
        "/kampagnen/batreeq/auswahl",
        json={"audiences": [], "cities": [], "include_unscored": True, "min_score": -1},
    )

    assert antwort.json()["beschreibung"] == "alle Gruppen im Bestand"


def test_nicht_gesendete_felder_bleiben_stehen(client: TestClient, bestand: Path) -> None:
    """Das Formular zeigt nur Zielgruppen und Staedte.

    Ohne die Unterscheidung "None = unveraendert" loeschte jedes Speichern die
    auf der Kommandozeile gesetzte Kategorie- oder Statusregel gleich mit.
    """
    with MarketingStore(bestand) as store:
        campaign = store.load_campaign("batreeq")
        campaign.target_categories = ["community"]
        campaign.target_statuses = ["validated"]
        store.save_campaign(campaign)

    client.post("/kampagnen/batreeq/auswahl", json={"cities": ["berlin"]})

    with MarketingStore(bestand) as store:
        campaign = store.load_campaign("batreeq")
    assert campaign.target_categories == ["community"]
    assert campaign.target_statuses == ["validated"]


def test_mindestscore_laesst_sich_aufheben(client: TestClient, bestand: Path) -> None:
    """-1 hebt auf - dieselbe Vereinbarung wie ``campaign target --min-score -1``."""
    client.post("/kampagnen/batreeq/auswahl", json={"min_score": 90})
    with MarketingStore(bestand) as store:
        assert store.load_campaign("batreeq").target_min_score == 90

    client.post("/kampagnen/batreeq/auswahl", json={"min_score": -1})
    with MarketingStore(bestand) as store:
        assert store.load_campaign("batreeq").target_min_score is None


def test_regel_nennt_dieselbe_zahl_wie_das_zuordnen(client: TestClient) -> None:
    """Beide lesen denselben Plan aus ``selection.baue_plan``.

    Eine zweite Zaehlung koennte abweichen - der Mensch bestaetigte dann eine
    Zahl und bekaeme eine andere.
    """
    regel = client.post(
        "/kampagnen/batreeq/auswahl", json={"audiences": ["syrians"]}
    ).json()
    plan = client.post("/kampagnen/batreeq/sync", json={"dry_run": True}).json()

    assert regel["neu"] == plan["neu"]
    assert regel["bereits_zugeordnet"] == plan["bereits_zugeordnet"]


def test_unbekannte_zielgruppe_in_der_regel_wird_abgewiesen(client: TestClient) -> None:
    """Die Kennungen stehen in config/*.yaml - eine Regel auf einen Tippfehler
    traefe stillschweigend null Gruppen."""
    antwort = client.post("/kampagnen/batreeq/auswahl", json={"audiences": ["marsianer"]})

    assert antwort.status_code == 422
    assert "marsianer" in antwort.json()["detail"]


def test_regel_einer_unbekannten_kampagne(client: TestClient) -> None:
    antwort = client.post("/kampagnen/gibtsnicht/auswahl", json={"cities": ["berlin"]})
    assert antwort.status_code == 404


def test_seite_zeigt_die_regel_jeder_kampagne(bestand: Path, config) -> None:
    """Eine Regel, die man nicht nachlesen kann, aendert niemand gern."""
    daten = sammle_daten(config, bestand)
    kampagne = next(c for c in daten["kampagnen"] if c["id"] == "batreeq")

    assert kampagne["regel"]["beschreibung"]
    assert kampagne["regel"]["passend"] == 2  # zwei bewertete Gruppen
    assert kampagne["regel"]["bestand"] == 3
    assert "trifft 2 von 3" in render(daten)


def test_regelweg_ist_von_aussen_nicht_vorhanden(bestand: Path, config) -> None:
    from fbgroups.marketing.web import create_app

    app = create_app(config=config, db_path=bestand)
    with TestClient(app, client=("203.0.113.7", 1234)) as fremd:
        antwort = fremd.post("/kampagnen/batreeq/auswahl", json={"cities": ["berlin"]})

    assert antwort.status_code == 404


def test_regelzeile_zaehlt_statt_aufzuzaehlen(bestand: Path, config) -> None:
    """Alle Kennungen in der Zelle sprengten die Spalte.

    Bei 13 Zielgruppen und 23 Staedten schob die Aufzaehlung die Namensspalte
    auf Bildschirmhoehe auseinander und drueckte den Rest der Zeile zusammen.
    Sichtbar bleibt die Zahl; die vollstaendige Regel steht im ``title``.
    """
    import re

    with MarketingStore(bestand) as store:
        campaign = store.load_campaign("batreeq")
        campaign.target_audiences = sorted(config.audiences)
        campaign.target_cities = sorted(config.cities)
        store.save_campaign(campaign)

    seite = render(sammle_daten(config, bestand))
    treffer = re.search(r"<span class='regel-text'([^>]*)>(.*?)</span>", seite, re.S)
    assert treffer is not None
    attribute, sichtbar = treffer.group(1), treffer.group(2)

    assert "syrians" not in sichtbar
    assert "berlin" not in sichtbar
    assert f"{len(config.audiences)} Zielgruppen" in sichtbar
    assert f"{len(config.cities)} Städte" in sichtbar
    # Vollstaendig bleibt sie erreichbar - am Mauszeiger.
    assert "syrians" in attribute


def test_kurzfassung_nennt_die_einzahl(config) -> None:
    """"1 Stadt" statt "1 Städte" - die Zeile liest ein Mensch."""
    from fbgroups.marketing.dashboard import regel_kurzfassung
    from fbgroups.marketing.selection import Auswahl

    eine = Auswahl(cities=frozenset({"Berlin"}), include_unscored=True)
    zwei = Auswahl(cities=frozenset({"Berlin", "Hamburg"}), include_unscored=True)

    assert regel_kurzfassung(eine).startswith("1 Stadt ")
    assert regel_kurzfassung(zwei).startswith("2 Städte ")
    assert regel_kurzfassung(Auswahl(include_unscored=True)) == "alle Gruppen im Bestand"


# --- Lesender Zugang von aussen ----------------------------------------
#
# Die Uebersicht soll ohne SSH-Tunnel zu sehen sein. Sichtbar heisst hier
# ausdruecklich **nicht** bedienbar: Die schreibenden Wege vergeben
# Tracking-Codes, und ein vergebener Code wird nie zurueckgenommen - er steht
# spaeter in veroeffentlichten Beitraegen. Deshalb traegt der Zugang von
# aussen nur die Zahlen heraus, nicht die Knoepfe.

GEHEIMNIS = "prueftoken-nur-fuer-diesen-test"


@pytest.fixture()
def fremd_mit_passwort(bestand: Path, config, monkeypatch) -> TestClient:
    """Ein Aufruf von aussen, den nginx nach bestandener Passwortpruefung durchlaesst."""
    monkeypatch.setenv("UEBERSICHT_TOKEN", GEHEIMNIS)
    from fbgroups.marketing.web import create_app

    return TestClient(
        create_app(config=config, db_path=bestand),
        client=("203.0.113.7", 44321),
        headers={"X-Uebersicht-Token": GEHEIMNIS},
    )


def test_zahlen_sind_von_aussen_lesbar(fremd_mit_passwort: TestClient) -> None:
    """Dieselben Zahlen wie lokal - dafuer ist der Zugang da."""
    antwort = fremd_mit_passwort.get("/")

    assert antwort.status_code == 200
    assert "Syrer in Berlin" in antwort.text
    assert "FB-SYR-BER-001" in antwort.text


def test_von_aussen_ist_die_seite_schreibgeschuetzt(fremd_mit_passwort: TestClient) -> None:
    """Kein Bedienelement, das einen schreibenden Weg ruft.

    Ein Knopf, dessen Weg mit 404 antwortet, sieht aus wie ein Fehler der
    Seite. Deshalb wird er gar nicht erst gezeigt.
    """
    seite = fremd_mit_passwort.get("/").text

    assert '<body class="nur-lesen">' in seite
    assert "const NUR_LESEN = true;" in seite
    # Die Regel, die die uebrigen Knoepfe ausblendet, muss auch da sein.
    assert "body.nur-lesen .knopfzelle" in seite
    # Weiter reicht diese Ebene nicht: Die Tabellenzeilen entstehen erst im
    # Browser, ihr Bauplan steht also so oder so im Quelltext. Was die Seite
    # daraus macht, entscheidet NUR_LESEN; dass ein Klick trotzdem nichts
    # ausrichtet, sichert test_passwortnachweis_oeffnet_keinen_schreibenden_weg.


def test_lokal_bleibt_die_seite_bedienbar(client: TestClient) -> None:
    """Der Schreibschutz gilt nur fuer den Weg von aussen."""
    seite = client.get("/").text

    # Nicht auf "nur-lesen" pruefen: Die CSS-Regel steht in jeder Fassung der
    # Seite. Massgeblich ist, ob der Koerper die Klasse traegt.
    assert '<body class="nur-lesen">' not in seite
    assert "const NUR_LESEN = false;" in seite


def test_passwortnachweis_oeffnet_keinen_schreibenden_weg(
    fremd_mit_passwort: TestClient,
) -> None:
    """Der Kern der Trennung: lesen ja, schreiben nein.

    ``sync`` vergibt Tracking-Codes und ist damit unumkehrbar; ``bearbeiten``
    nimmt eine Liste entgegen und traefe im Ernstfall hunderte Datensaetze auf
    einmal. Ein abhandengekommenes Passwort soll Zahlen zeigen koennen und
    sonst nichts.
    """
    kopf = {"X-Uebersicht-Token": GEHEIMNIS}

    assert fremd_mit_passwort.post(
        "/stand", json={"group_id": REAL_ID_A, "status": "mitglied"}, headers=kopf
    ).status_code == 404
    assert fremd_mit_passwort.post(
        "/kampagnen/batreeq/sync", json={"dry_run": False}, headers=kopf
    ).status_code == 404
    assert fremd_mit_passwort.post(
        "/bearbeiten", json={"group_ids": [REAL_ID_A], "bearbeiten": False}, headers=kopf
    ).status_code == 404
    assert fremd_mit_passwort.post(
        "/kampagnen", json={"name": "Von aussen"}, headers=kopf
    ).status_code == 404


def test_falsches_geheimnis_bekommt_404(bestand: Path, config, monkeypatch) -> None:
    """Wie ohne Nachweis - kein Unterschied, den man ausprobieren koennte."""
    monkeypatch.setenv("UEBERSICHT_TOKEN", GEHEIMNIS)
    from fbgroups.marketing.web import create_app

    fremd = TestClient(
        create_app(config=config, db_path=bestand), client=("203.0.113.7", 44321)
    )

    assert fremd.get("/", headers={"X-Uebersicht-Token": "falsch"}).status_code == 404
    assert fremd.get("/").status_code == 404


def test_ohne_gesetztes_geheimnis_hilft_die_kopfzeile_nicht(
    bestand: Path, config, monkeypatch
) -> None:
    """Vorgabe ist zu: Ein leerer Wert darf kein leeres Passwort sein.

    Sonst genuegte auf einem Server, auf dem ``UEBERSICHT_TOKEN`` schlicht
    fehlt, eine erratene Kopfzeile - und der Ausfall waere unsichtbar, weil
    alles Uebrige weiterlaeuft.
    """
    monkeypatch.delenv("UEBERSICHT_TOKEN", raising=False)
    from fbgroups.marketing.web import create_app

    fremd = TestClient(
        create_app(config=config, db_path=bestand), client=("203.0.113.7", 44321)
    )

    assert fremd.get("/", headers={"X-Uebersicht-Token": ""}).status_code == 404
    assert fremd.get("/", headers={"X-Uebersicht-Token": "irgendwas"}).status_code == 404
