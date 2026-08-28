"""Tests fuer die Arbeitsliste: welcher Beitrag steht noch aus?

Der Kern ist eine einzige Zusage: Ein veroeffentlichter Beitrag darf nicht
noch einmal in der Liste auftauchen. Alles andere - Reihenfolge, Farben,
Zaehler - ist Bequemlichkeit; ein doppelt geposteter Link in derselben Gruppe
ist ein Fehler, den man in der Gruppe sieht und nicht zurueckholen kann.

Kein Test hier ruft facebook.com auf. Das Modul kann es nicht.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.marketing.beitrag import beitragstext
from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    GroupMarketing,
    MarketingStatus,
    PostStatus,
)
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

REAL_ID_A = "482910573829104"
REAL_ID_B = "739201847362915"
REAL_ID_C = "615043928175306"


@pytest.fixture()
def bestand(tmp_path: Path) -> Path:
    """Eine Kampagne mit drei zugeordneten Gruppen, alle Beitraege offen."""
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=REAL_ID_A,
                    url_canonical=f"https://www.facebook.com/groups/{REAL_ID_A}",
                    name="Syrer in Berlin",
                    score=92.0,
                    score_max=100.0,
                ),
                Group(
                    group_id=REAL_ID_B,
                    url_canonical=f"https://www.facebook.com/groups/{REAL_ID_B}",
                    name="تجمع السوريين في ميونخ",
                    score=100.0,
                    score_max=100.0,
                ),
                Group(
                    group_id=REAL_ID_C,
                    url_canonical=f"https://www.facebook.com/groups/{REAL_ID_C}",
                    name="Araber in Koeln",
                    score=60.0,
                    score_max=100.0,
                ),
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(
                campaign_id="batreeq",
                name="Batreeq Syrian Germany",
                message_template="مرحبا! {link}",
                landing_page="https://b-tarikak.de/",
            )
        )
        for gid, code in (
            (REAL_ID_A, "FB-SYR-BER-001"),
            (REAL_ID_B, "FB-SYR-MUE-001"),
            (REAL_ID_C, "FB-ARA-KOE-001"),
        ):
            store.add_link(
                CampaignGroup(
                    campaign_id="batreeq",
                    group_id=gid,
                    tracking_code=code,
                    tracking_url=f"https://go.b-tarikak.de/r/{code}",
                )
            )
    return pfad


# --- Der Text --------------------------------------------------------------

def test_der_text_traegt_den_link_dieser_gruppe(bestand: Path) -> None:
    """Der Code steht nirgends im Code - er kommt aus der Zuordnung.

    Das ist der ganze Zweck: Bei 300 Gruppen sind das 300 verschiedene Links,
    und jeder von Hand eingetragen waere eine Fehlerquelle je Gruppe.
    """
    with MarketingStore(bestand) as store:
        campaign = store.load_campaign("batreeq")
        assert campaign is not None
        texte = {
            link.group_id: beitragstext(campaign, link)
            for link in store.links_for_campaign("batreeq")
        }

    assert texte[REAL_ID_A] == "مرحبا! https://go.b-tarikak.de/r/FB-SYR-BER-001"
    assert texte[REAL_ID_B] == "مرحبا! https://go.b-tarikak.de/r/FB-SYR-MUE-001"
    # Jede Gruppe ihren eigenen Link - sonst zaehlten alle Klicks auf eine.
    assert len(set(texte.values())) == 3


def test_arabischer_text_bleibt_unveraendert(bestand: Path) -> None:
    """Keine Kodierungsschleife auf dem Weg durch die Datenbank."""
    with MarketingStore(bestand) as store:
        campaign = store.load_campaign("batreeq")
        assert campaign is not None
        link = store.link_for("batreeq", REAL_ID_A)
        assert link is not None
        assert beitragstext(campaign, link).startswith("مرحبا!")


# --- Die Zusage: nichts doppelt --------------------------------------------

def test_veroeffentlichter_beitrag_verschwindet_aus_der_liste(bestand: Path) -> None:
    """Die eigentliche Zusage dieser Erweiterung."""
    with MarketingStore(bestand) as store:
        assert len(store.offene_links("batreeq")) == 3

        store.set_post_status("batreeq", REAL_ID_A, PostStatus.VEROEFFENTLICHT)

        offen = {link.group_id for link in store.offene_links("batreeq")}
        assert REAL_ID_A not in offen
        assert offen == {REAL_ID_B, REAL_ID_C}


def test_fehlgeschlagener_beitrag_bleibt_in_der_liste(bestand: Path) -> None:
    """Ein Fehlschlag ist offene Arbeit, kein erledigter Posten."""
    with MarketingStore(bestand) as store:
        store.set_post_status(
            "batreeq", REAL_ID_A, PostStatus.FEHLGESCHLAGEN, "Gruppe erlaubt keine Links"
        )
        offen = {link.group_id for link in store.offene_links("batreeq")}

    assert REAL_ID_A in offen


def test_uebersprungene_gruppe_verschwindet_und_bleibt_weg(bestand: Path) -> None:
    """"Passt nicht" ist ein Urteil - ein Sammelbefehl hebt es nicht auf."""
    with MarketingStore(bestand) as store:
        store.set_post_status("batreeq", REAL_ID_A, PostStatus.UEBERSPRUNGEN)
        store.set_post_status("batreeq", REAL_ID_B, PostStatus.FEHLGESCHLAGEN, "Netzfehler")

        assert {link.group_id for link in store.offene_links("batreeq")} == {
            REAL_ID_B,
            REAL_ID_C,
        }

        zurueck = store.fehlgeschlagene_zuruecksetzen("batreeq")
        offen = {link.group_id for link in store.offene_links("batreeq")}

    assert zurueck == 1
    # B kommt zurueck, A bleibt draussen.
    assert offen == {REAL_ID_B, REAL_ID_C}


def test_ausgeschlossene_gruppe_steht_nicht_in_der_liste(bestand: Path) -> None:
    """``bearbeiten = 0`` heisst: daran arbeiten wir nicht.

    Der Tracking-Code bleibt davon unberuehrt gueltig - er steht
    moeglicherweise in einem veroeffentlichten Beitrag.
    """
    with MarketingStore(bestand) as store:
        eintrag = store.load_marketing(REAL_ID_C)
        eintrag.bearbeiten = False
        eintrag.ausschlussgrund = "keine Daten"
        store.save_marketing(eintrag)

        offen = {link.group_id for link in store.offene_links("batreeq")}
        link = store.link_for("batreeq", REAL_ID_C)

    assert REAL_ID_C not in offen
    assert link is not None and link.tracking_code == "FB-ARA-KOE-001"


# --- Das Protokoll ---------------------------------------------------------

def test_zeitpunkt_der_veroeffentlichung_wird_nie_ueberschrieben(bestand: Path) -> None:
    """Die Klicks gehen auf den Beitrag zurueck, der zuerst stand.

    Ein Datum, das bei jedem erneuten Posten mitwandert, machte die Frage
    "seit wann laeuft dieser Link?" unbeantwortbar.
    """
    with MarketingStore(bestand) as store:
        erst = store.set_post_status("batreeq", REAL_ID_A, PostStatus.VEROEFFENTLICHT)
        assert erst is not None and erst.posted_at is not None
        zweit = store.set_post_status("batreeq", REAL_ID_A, PostStatus.VEROEFFENTLICHT)

    assert zweit is not None
    assert zweit.posted_at == erst.posted_at
    # Angefasst wurde es trotzdem zweimal.
    assert zweit.post_attempts == 2


def test_erfolg_loescht_den_alten_fehlergrund(bestand: Path) -> None:
    """Sonst stuende neben einem veroeffentlichten Beitrag, warum er scheiterte."""
    with MarketingStore(bestand) as store:
        store.set_post_status("batreeq", REAL_ID_A, PostStatus.FEHLGESCHLAGEN, "Netzfehler")
        danach = store.set_post_status("batreeq", REAL_ID_A, PostStatus.VEROEFFENTLICHT)

    assert danach is not None
    assert danach.post_error == ""
    assert danach.post_status == PostStatus.VEROEFFENTLICHT


def test_versuche_zaehlen_jeden_ausgang(bestand: Path) -> None:
    """Die Zahl beantwortet "wie oft angefasst?", nicht "wie oft schiefgegangen?"."""
    with MarketingStore(bestand) as store:
        store.set_post_status("batreeq", REAL_ID_A, PostStatus.FEHLGESCHLAGEN, "a")
        store.set_post_status("batreeq", REAL_ID_A, PostStatus.FEHLGESCHLAGEN, "b")
        link = store.set_post_status("batreeq", REAL_ID_A, PostStatus.VEROEFFENTLICHT)

    assert link is not None and link.post_attempts == 3


def test_unbekannte_zuordnung_meldet_sich_statt_still_zu_scheitern(bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        assert store.set_post_status("batreeq", "999999999", PostStatus.VEROEFFENTLICHT) is None


def test_zaehler_nennt_immer_alle_staende(bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        store.set_post_status("batreeq", REAL_ID_A, PostStatus.VEROEFFENTLICHT)
        zaehler = store.post_counts("batreeq")

    assert zaehler == {
        "offen": 2,
        "veroeffentlicht": 1,
        "fehlgeschlagen": 0,
        "uebersprungen": 0,
    }


# --- Grenzen ---------------------------------------------------------------

def test_das_protokoll_beruehrt_die_tracking_daten_nicht(bestand: Path) -> None:
    """Code und URL bleiben, was sie waren - in jedem Ausgang.

    Sie stehen moeglicherweise schon in einem veroeffentlichten Beitrag.
    """
    with MarketingStore(bestand) as store:
        vorher = store.link_for("batreeq", REAL_ID_A)
        assert vorher is not None
        for status in PostStatus:
            store.set_post_status("batreeq", REAL_ID_A, status, "Grund")
        nachher = store.link_for("batreeq", REAL_ID_A)

    assert nachher is not None
    assert nachher.tracking_code == vorher.tracking_code
    assert nachher.tracking_url == vorher.tracking_url
    assert nachher.added_at == vorher.added_at


def test_zwei_kampagnen_teilen_sich_den_stand_nicht(bestand: Path) -> None:
    """Der Stand gehoert zum Paar, nicht zur Gruppe.

    Waere er an der Gruppe gespeichert, meldete der Beitrag der einen
    Kampagne den der anderen als erledigt - und die Arbeitsliste verschwiege
    eine offene Aufgabe.
    """
    with MarketingStore(bestand) as store:
        store.save_campaign(Campaign(campaign_id="zweite", name="Zweite Kampagne"))
        store.add_link(
            CampaignGroup(
                campaign_id="zweite",
                group_id=REAL_ID_A,
                tracking_code="FB-SYR-BER-002",
                tracking_url="https://go.b-tarikak.de/r/FB-SYR-BER-002",
            )
        )
        store.set_post_status("batreeq", REAL_ID_A, PostStatus.VEROEFFENTLICHT)

        offen_zweite = {link.group_id for link in store.offene_links("zweite")}
        offen_batreeq = {link.group_id for link in store.offene_links("batreeq")}

    assert REAL_ID_A in offen_zweite
    assert REAL_ID_A not in offen_batreeq


def test_bestehende_zuordnungen_starten_offen(bestand: Path) -> None:
    """Die vorsichtige Richtung der Migration.

    Eine Gruppe zu viel in der Liste kostet einen Blick, eine zu wenig kostet
    einen Beitrag. ``last_posted_at`` der Gruppe wird deshalb nicht als
    "erledigt" gedeutet - es gilt fuer die Gruppe, der Stand fuer das Paar.
    """
    with MarketingStore(bestand) as store:
        eintrag = GroupMarketing(group_id=REAL_ID_A)
        eintrag.marketing_status = MarketingStatus.ACTIVE
        store.save_marketing(eintrag)
        link = store.link_for("batreeq", REAL_ID_A)

    assert link is not None
    assert link.post_status == PostStatus.OFFEN
    assert link.posted_at is None
    assert link.post_attempts == 0


# --- Die Befehle ------------------------------------------------------------
#
# Gegen eine eigene Datei im tmp-Verzeichnis: ``AppConfig.path`` haengt jeden
# Pfad an ``root``, also genuegt eine Kopie der echten Konfiguration mit
# anderer Wurzel. Der echte Bestand wird dabei nie geoeffnet.

from dataclasses import replace  # noqa: E402

from typer.testing import CliRunner  # noqa: E402

from fbgroups.marketing.cli import campaign_app  # noqa: E402

runner = CliRunner()


@pytest.fixture()
def cli(bestand: Path, config, tmp_path: Path, monkeypatch):
    """Die Befehle, auf die Testdatei gerichtet."""
    ziel = tmp_path / "data" / "groups.sqlite"
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_bytes(bestand.read_bytes())
    eigener = replace(config, root=tmp_path)
    monkeypatch.setattr("fbgroups.marketing.cli._config", lambda: eigener)
    # rich kuerzt Tabellenspalten auf die Breite der Umgebung. Ohne feste
    # Breite haengt es vom Fenster des Ausfuehrenden ab, ob ein Tracking-Code
    # vollstaendig in der Ausgabe steht - der Test schluege dann mal an und
    # mal nicht, ohne dass sich am Programm etwas geaendert haette.
    monkeypatch.setenv("COLUMNS", "220")

    def lauf(*args: str, eingabe: str = ""):
        return runner.invoke(campaign_app, list(args), input=eingabe)

    return lauf


def test_queue_zeigt_nur_die_offenen(cli) -> None:
    ergebnis = cli("queue", "batreeq")

    assert ergebnis.exit_code == 0
    assert "Syrer in Berlin" in ergebnis.output
    assert "FB-SYR-BER-001" in ergebnis.output


def test_queue_verschweigt_den_erledigten_beitrag(cli) -> None:
    cli("posted", "batreeq", REAL_ID_A)
    ergebnis = cli("queue", "batreeq")

    assert "FB-SYR-BER-001" not in ergebnis.output
    # Die anderen beiden stehen weiter da.
    assert "FB-SYR-MUE-001" in ergebnis.output


def test_queue_mit_alle_zeigt_auch_den_erledigten(cli) -> None:
    cli("posted", "batreeq", REAL_ID_A)
    ergebnis = cli("queue", "batreeq", "--alle")

    assert "FB-SYR-BER-001" in ergebnis.output
    assert "veroeffentlicht" in ergebnis.output


def test_next_protokolliert_jeden_ausgang(cli) -> None:
    """Enter, Fehler mit Grund, ueberspringen - in einem Durchlauf."""
    ergebnis = cli(
        "next", "batreeq", "--kein-browser", "--keine-zwischenablage",
        eingabe="\nf\nGruppe erlaubt keine Links\nu\n",
    )

    assert ergebnis.exit_code == 0
    assert "veroeffentlicht" in ergebnis.output
    assert "fehlgeschlagen" in ergebnis.output
    assert "uebersprungen" in ergebnis.output

    ergebnis = cli("queue", "batreeq", "--alle")
    assert "Gruppe erlaubt keine Links" in ergebnis.output


def test_next_setzt_den_link_dieser_gruppe_in_den_text(cli) -> None:
    """Der Kern: kein Code steht fest im Programm."""
    ergebnis = cli(
        "next", "batreeq", "--kein-browser", "--keine-zwischenablage", "--limit", "1",
        eingabe="q\n",
    )

    assert "go.b-tarikak.de/r/FB-SYR-MUE-001" in ergebnis.output.replace("\n", "")


def test_next_haelt_bei_q_an_und_laesst_die_gruppe_offen(cli) -> None:
    """Abbrechen ist kein Ausgang - die Gruppe bleibt zu erledigen."""
    cli("next", "batreeq", "--kein-browser", "--keine-zwischenablage", eingabe="q\n")
    ergebnis = cli("fortschritt", "batreeq")

    assert "3 offen" in ergebnis.output


def test_next_ohne_vorlage_bricht_ab_statt_leer_zu_posten(cli, bestand: Path) -> None:
    """Ein leerer Beitrag waere in der Gruppe sichtbar und nicht zurueckholbar."""
    ergebnis = cli("posted", "batreeq", REAL_ID_A)
    assert ergebnis.exit_code == 0

    # Kampagne ohne Vorlage
    ergebnis = cli(
        "next", "zweite", "--kein-browser", "--keine-zwischenablage", eingabe="\n"
    )
    assert ergebnis.exit_code != 0


def test_retry_holt_nur_die_fehlgeschlagenen_zurueck(cli) -> None:
    cli("posted", "batreeq", REAL_ID_A, "--fehler", "Netzfehler")
    cli("posted", "batreeq", REAL_ID_B, "--ueberspringen")

    ergebnis = cli("retry", "batreeq")
    assert "1 wieder offen" in ergebnis.output.replace("\n", " ")

    ergebnis = cli("queue", "batreeq")
    assert "FB-SYR-BER-001" in ergebnis.output
    assert "FB-SYR-MUE-001" not in ergebnis.output


def test_posted_meldet_eine_fremde_gruppe(cli) -> None:
    ergebnis = cli("posted", "batreeq", "999999999")
    assert ergebnis.exit_code == 1


def test_fehler_und_ueberspringen_schliessen_einander_aus(cli) -> None:
    ergebnis = cli("posted", "batreeq", REAL_ID_A, "--fehler", "x", "--ueberspringen")
    assert ergebnis.exit_code == 2


# --- Uebersicht -------------------------------------------------------------
#
# Der Beitragsstand steht in derselben Zeile wie Score, Arbeitsstand und
# Trichterzahlen - nicht als zweite Liste daneben. Sonst gaebe es einen
# zweiten Satz Zahlen, den man getrennt filtern muesste.

fastapi = pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
from fastapi.testclient import TestClient  # noqa: E402

from fbgroups.marketing.dashboard import render, sammle_daten  # noqa: E402


@pytest.fixture()
def web(bestand: Path, config):
    from fbgroups.marketing.web import create_app

    return TestClient(create_app(config=config, db_path=bestand), follow_redirects=False)


def test_uebersicht_zeigt_den_beitragsstand_je_gruppe(bestand: Path, config) -> None:
    daten = sammle_daten(config, bestand)
    zeile = next(z for z in daten["gruppen"] if z["id"] == REAL_ID_A)

    assert zeile["beitrag_status"] == "offen"
    assert len(zeile["beitraege"]) == 1
    assert zeile["beitraege"][0]["code"] == "FB-SYR-BER-001"
    # Schmal mit Absicht: Der fertige Beitragstext stand hier einmal mit, fuer
    # einen Kopierknopf in der Zelle. Bei 310 Gruppen sind das 310 Texte im
    # Dokument fuer einen Knopf, den es nicht mehr gibt.
    assert "text" not in zeile["beitraege"][0]


def test_gruppe_ohne_zuordnung_faellt_aus_dem_beitragsfilter(
    bestand: Path, config
) -> None:
    """"ohne" statt "offen": Ohne Link gibt es keinen Beitrag zu schreiben."""
    with MarketingStore(bestand) as store:
        store.remove_link("batreeq", REAL_ID_C)

    daten = sammle_daten(config, bestand)
    zeile = next(z for z in daten["gruppen"] if z["id"] == REAL_ID_C)

    assert zeile["beitrag_status"] == "ohne"
    assert zeile["beitraege"] == []


def test_offener_beitrag_schlaegt_einen_erledigten(bestand: Path, config) -> None:
    """Eine Gruppe mit einer offenen Aufgabe gilt als offen.

    Waere es umgekehrt, verschwaende eine offene Aufgabe aus dem Filter,
    sobald irgendein anderer Beitrag derselben Gruppe erledigt ist.
    """
    with MarketingStore(bestand) as store:
        store.save_campaign(Campaign(campaign_id="zweite", name="Zweite"))
        store.add_link(
            CampaignGroup(
                campaign_id="zweite",
                group_id=REAL_ID_A,
                tracking_code="FB-SYR-BER-002",
                tracking_url="https://go.b-tarikak.de/r/FB-SYR-BER-002",
            )
        )
        store.set_post_status("batreeq", REAL_ID_A, PostStatus.VEROEFFENTLICHT)

    daten = sammle_daten(config, bestand)
    zeile = next(z for z in daten["gruppen"] if z["id"] == REAL_ID_A)

    assert zeile["beitrag_status"] == "offen"


def test_kennzahlen_zaehlen_beitraege_nicht_gruppen(bestand: Path, config) -> None:
    with MarketingStore(bestand) as store:
        store.set_post_status("batreeq", REAL_ID_A, PostStatus.VEROEFFENTLICHT)

    k = sammle_daten(config, bestand)["kennzahlen"]

    assert k["beitraege_veroeffentlicht"] == 1
    assert k["beitraege_offen"] == 2


def test_die_seite_traegt_spalte_und_filter(bestand: Path, config) -> None:
    seite = render(sammle_daten(config, bestand))

    assert "beitragZelle" in seite
    assert 'id="f-beitrag"' in seite
    assert "FB-SYR-BER-001" in seite


def test_beitrag_eintragen_ueber_die_seite(web: TestClient) -> None:
    antwort = web.post(
        "/beitrag",
        json={"campaign_id": "batreeq", "group_id": REAL_ID_A, "status": "veroeffentlicht"},
    )

    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["status"] == "veroeffentlicht"
    assert daten["versuche"] == 1
    assert daten["gesamtstand"] == "veroeffentlicht"


def test_fehlschlag_ueber_die_seite_behaelt_den_grund(web: TestClient) -> None:
    antwort = web.post(
        "/beitrag",
        json={
            "campaign_id": "batreeq",
            "group_id": REAL_ID_A,
            "status": "fehlgeschlagen",
            "grund": "Gruppe erlaubt keine Links",
        },
    )

    assert antwort.json()["fehler"] == "Gruppe erlaubt keine Links"
    assert antwort.json()["gesamtstand"] == "fehlgeschlagen"


def test_unbekannte_zuordnung_meldet_404(web: TestClient) -> None:
    antwort = web.post(
        "/beitrag",
        json={"campaign_id": "batreeq", "group_id": "999999999", "status": "veroeffentlicht"},
    )
    assert antwort.status_code == 404


def test_uebersprungen_ist_kein_weg_fuer_die_seite(web: TestClient) -> None:
    """Ein Urteil ueber die Gruppe gehoert nicht hinter einen kleinen Knopf."""
    antwort = web.post(
        "/beitrag",
        json={"campaign_id": "batreeq", "group_id": REAL_ID_A, "status": "uebersprungen"},
    )
    assert antwort.status_code == 422


def test_von_aussen_laesst_sich_kein_beitrag_eintragen(bestand: Path, config) -> None:
    """Derselbe Riegel wie bei jedem anderen schreibenden Weg."""
    from fbgroups.marketing.web import create_app

    fremd = TestClient(
        create_app(config=config, db_path=bestand), client=("203.0.113.7", 44321)
    )
    antwort = fremd.post(
        "/beitrag",
        json={"campaign_id": "batreeq", "group_id": REAL_ID_A, "status": "veroeffentlicht"},
    )
    assert antwort.status_code == 404
