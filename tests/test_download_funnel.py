"""Tests fuer den Download-Trichter und die Zuordnung ueber den Konto-Uebergang.

Der Fall, an dem die vorige Fassung gescheitert ist: Ein Mensch kommt ueber
einen Tracking-Link, sieht sich die Seite anonym an, registriert sich (und
heisst ab da anders) und laedt danach die App. Der Download stand ohne Gruppe
da - zwei Kennungen, und die Zuordnung haengt am ersten Besuch.

Zwei Dinge werden hier getrennt geprueft:

* **Unabhaengigkeit** - jede Stufe zaehlt fuer sich. Registrierung ohne
  Download, Download ohne Registrierung, beides, alles drei.
* **Zuordnung** - welcher Facebook-Gruppe ein Ereignis zu verdanken ist. Das
  ist eine zusaetzliche Frage zu jedem Ereignis, keine Bedingung fuer eines.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fbgroups.marketing.analytics import code_bericht, kennzahlen, top_groups
from fbgroups.marketing.models import Campaign, CampaignGroup, EventType
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

fastapi = pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
from fastapi.testclient import TestClient  # noqa: E402

# Echte Kennungen im Aufbau von Facebook-Gruppen - die Platzhaltererkennung
# soll sie nicht aussortieren.
GID_KOELN = "482910573829104"
GID_BERLIN = "739201847362915"
CODE_KOELN = "FB-SYR-KLN-002"
CODE_BERLIN = "FB-SYR-BER-001"


@pytest.fixture()
def bestand(tmp_path: Path) -> Path:
    """Zwei Gruppen in einer Kampagne, jede mit ihrem eigenen Code."""
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=GID_KOELN,
                    url_canonical=f"https://www.facebook.com/groups/{GID_KOELN}",
                    name="Syrer in Koeln",
                ),
                Group(
                    group_id=GID_BERLIN,
                    url_canonical=f"https://www.facebook.com/groups/{GID_BERLIN}",
                    name="Syrer in Berlin",
                ),
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(
                campaign_id="batreeq",
                name="Batreeq Syrian Germany",
                landing_page="https://b-tarikak.de/",
                # Ausdruecklich der Landingpage-Weg: Diese Tests pruefen ihn,
                # und sie sollen das sagen statt von der Projektvorgabe
                # (marketing.ziel) abzuhaengen. Dieselbe Ueberlegung wie bei
                # ``ohne_ollama``: Was der Test braucht, gehoert in den Test.
                ziel="landing",
            )
        )
        store.add_link(
            CampaignGroup(
                campaign_id="batreeq", group_id=GID_KOELN, tracking_code=CODE_KOELN
            )
        )
        store.add_link(
            CampaignGroup(
                campaign_id="batreeq", group_id=GID_BERLIN, tracking_code=CODE_BERLIN
            )
        )
    return pfad


@pytest.fixture()
def client(bestand: Path, config) -> TestClient:
    from fbgroups.marketing.web import create_app

    return TestClient(create_app(config=config, db_path=bestand), follow_redirects=False)


def melde(client: TestClient, **felder: object) -> dict:
    """Ein Ereignis melden - wie die Zielanwendung es tut."""
    antwort = client.post(
        "/events",
        headers={"x-events-token": os.environ.get("EVENTS_TOKEN", "")},
        json=felder,
    )
    assert antwort.status_code == 200, antwort.text
    return dict(antwort.json())


def zahlen(bestand: Path, code: str) -> dict[str, int]:
    """Die Stufen eines Codes als ``{"download": 1, ...}``."""
    with MarketingStore(bestand) as store:
        return dict(code_bericht(store, code).zahlen)


# --- Der ganze Weg -----------------------------------------------------

def test_ganzer_weg_von_der_gruppe_bis_zur_aktivierung(
    client: TestClient, bestand: Path
) -> None:
    """Klick -> Landung -> Registrierung -> Download -> Aktivierung.

    Mit dem Kennungswechsel in der Mitte, so wie es wirklich laeuft: Die
    Landung kennt nur den Browser, ab der Registrierung gibt es ein Konto.
    """
    client.get(f"/r/{CODE_KOELN}")
    melde(client, event_type="landing_visit", anon_ref="anon-ABC",
          tracking_code=CODE_KOELN)
    melde(client, event_type="registration", user_ref="user-8472", anon_ref="anon-ABC")
    melde(client, event_type="download", user_ref="user-8472")
    melde(client, event_type="activation", user_ref="user-8472")

    assert zahlen(bestand, CODE_KOELN) == {
        "click": 1,
        "landing_visit": 1,
        "registration": 1,
        "download": 1,
        "activation": 1,
    }


def test_download_kennt_die_facebook_gruppe(client: TestClient, bestand: Path) -> None:
    """Der Zweck des Ganzen: vom Download zurueck zur Gruppe, die ihn gebracht hat."""
    melde(client, event_type="landing_visit", anon_ref="anon-ABC",
          tracking_code=CODE_KOELN)
    melde(client, event_type="registration", user_ref="user-8472", anon_ref="anon-ABC")
    melde(client, event_type="download", user_ref="user-8472")

    with MarketingStore(bestand) as store:
        zeile = store.conn.execute(
            "SELECT * FROM tracking_events WHERE event_type = 'download'"
        ).fetchone()

    assert zeile["tracking_code"] == CODE_KOELN
    assert zeile["group_id"] == GID_KOELN
    assert zeile["campaign_id"] == "batreeq"


def test_antwort_nennt_den_code_als_beleg(client: TestClient) -> None:
    """Die meldende Anwendung soll sehen, wohin ihr Ereignis gebucht wurde."""
    melde(client, event_type="landing_visit", anon_ref="anon-ABC",
          tracking_code=CODE_KOELN)
    melde(client, event_type="registration", user_ref="user-8472", anon_ref="anon-ABC")

    antwort = melde(client, event_type="download", user_ref="user-8472")

    assert antwort["tracking_code"] == CODE_KOELN
    assert antwort["gezaehlt"] is True


def test_bericht_belegt_die_zuordnung_je_mensch(client: TestClient, bestand: Path) -> None:
    """Nicht nur ein Zaehler: nachvollziehbar, *warum* der Download hierher gehoert."""
    melde(client, event_type="landing_visit", anon_ref="anon-ABC",
          tracking_code=CODE_KOELN)
    melde(client, event_type="registration", user_ref="user-8472", anon_ref="anon-ABC")
    melde(client, event_type="download", user_ref="user-8472")
    melde(client, event_type="activation", user_ref="user-8472")

    with MarketingStore(bestand) as store:
        bericht = code_bericht(store, CODE_KOELN, {GID_KOELN: "Syrer in Koeln"})

    assert bericht.group_name == "Syrer in Koeln"
    assert len(bericht.benutzer) == 1
    weg = bericht.benutzer[0]
    # Beide Kennungen stehen beim selben Menschen - das ist die Begruendung.
    assert set(weg.kennungen) == {"anon-ABC", "user-8472"}
    assert weg.stufen == [
        EventType.LANDING_VISIT,
        EventType.REGISTRATION,
        EventType.DOWNLOAD,
        EventType.ACTIVATION,
    ]


# --- Fall A: zwei Menschen, zwei Codes ---------------------------------

def test_zwei_benutzer_ueber_zwei_codes_werden_nicht_vertauscht(
    client: TestClient, bestand: Path
) -> None:
    melde(client, event_type="landing_visit", anon_ref="anon-A", tracking_code=CODE_KOELN)
    melde(client, event_type="registration", user_ref="user-A", anon_ref="anon-A")
    melde(client, event_type="download", user_ref="user-A")

    melde(client, event_type="landing_visit", anon_ref="anon-B", tracking_code=CODE_BERLIN)
    melde(client, event_type="registration", user_ref="user-B", anon_ref="anon-B")
    melde(client, event_type="download", user_ref="user-B")
    melde(client, event_type="activation", user_ref="user-B")

    koeln, berlin = zahlen(bestand, CODE_KOELN), zahlen(bestand, CODE_BERLIN)

    assert koeln["download"] == 1
    assert berlin["download"] == 1
    assert koeln.get("activation", 0) == 0
    assert berlin["activation"] == 1

    with MarketingStore(bestand) as store:
        je_gruppe = {z.schluessel: z for z in top_groups(store, {})}
    assert je_gruppe[GID_KOELN].downloads == 1
    assert je_gruppe[GID_BERLIN].downloads == 1


def test_zahlen_werden_je_code_nicht_vermischt(client: TestClient, bestand: Path) -> None:
    """Zwei Downloads hier, fuenf dort - und nie sieben irgendwo."""
    for nummer in range(2):
        melde(client, event_type="landing_visit", anon_ref=f"k-{nummer}",
              tracking_code=CODE_KOELN)
        melde(client, event_type="download", anon_ref=f"k-{nummer}")
    for nummer in range(5):
        melde(client, event_type="landing_visit", anon_ref=f"b-{nummer}",
              tracking_code=CODE_BERLIN)
        melde(client, event_type="download", anon_ref=f"b-{nummer}")

    assert zahlen(bestand, CODE_KOELN)["download"] == 2
    assert zahlen(bestand, CODE_BERLIN)["download"] == 5

    with MarketingStore(bestand) as store:
        assert kennzahlen(store)["downloads"] == 7   # die Gesamtzahl darf summieren


# --- Fall B: spaeterer Download ----------------------------------------

def test_download_lange_nach_der_registrierung_behaelt_die_zuordnung(
    client: TestClient, bestand: Path
) -> None:
    """Zwischen Registrierung und Download liegen Tage - der Code ist laengst weg."""
    melde(client, event_type="landing_visit", anon_ref="anon-ABC",
          tracking_code=CODE_KOELN)
    melde(client, event_type="registration", user_ref="user-8472", anon_ref="anon-ABC",
          occurred_at="2026-08-01T10:00:00Z")

    # Tage spaeter, aus der mobilen App: nur noch die Benutzerkennung.
    melde(client, event_type="download", user_ref="user-8472",
          occurred_at="2026-08-09T18:30:00Z")

    assert zahlen(bestand, CODE_KOELN)["download"] == 1


def test_registrierung_ohne_code_erbt_vom_anonymen_besuch(
    client: TestClient, bestand: Path
) -> None:
    """Der Bruch der vorigen Fassung: Die Registrierung selbst trug keinen Code.

    Vorher endete die Zuordnung hier - die Registrierung stand ohne Gruppe da,
    und alles danach erbte folglich nichts.
    """
    melde(client, event_type="landing_visit", anon_ref="anon-ABC",
          tracking_code=CODE_KOELN)
    melde(client, event_type="registration", user_ref="user-8472", anon_ref="anon-ABC")

    assert zahlen(bestand, CODE_KOELN)["registration"] == 1


# --- Fall C: Download ohne Zuordnung -----------------------------------

def test_download_ohne_vorgeschichte_bekommt_keinen_fremden_code(
    client: TestClient, bestand: Path
) -> None:
    """Lieber ohne Gruppe als bei der falschen."""
    melde(client, event_type="landing_visit", anon_ref="anon-ABC",
          tracking_code=CODE_KOELN)
    melde(client, event_type="download", user_ref="user-fremd")

    assert zahlen(bestand, CODE_KOELN).get("download", 0) == 0
    with MarketingStore(bestand) as store:
        assert store.event_counts()["download"] == 1      # gezaehlt wurde er
        zeile = store.conn.execute(
            "SELECT * FROM tracking_events WHERE event_type = 'download'"
        ).fetchone()
    assert zeile["tracking_code"] == ""                    # nur eben ohne Gruppe
    assert zeile["group_id"] == ""


def test_ganz_anonymer_download_wird_nicht_zugeordnet(
    client: TestClient, bestand: Path
) -> None:
    """Ohne jede Kennung ist ein Download eine Zahl ohne Herkunft."""
    melde(client, event_type="download")

    assert zahlen(bestand, CODE_KOELN).get("download", 0) == 0
    with MarketingStore(bestand) as store:
        assert store.event_counts()["download"] == 1


def test_unbekannter_code_wird_nicht_als_code_gespeichert(
    client: TestClient, bestand: Path
) -> None:
    """Ein Tippfehler darf keine Spalte in der Auswertung erfinden."""
    melde(client, event_type="download", user_ref="user-8472",
          tracking_code="FB-GIBTS-NICHT-999")

    with MarketingStore(bestand) as store:
        codes = {
            row["tracking_code"]
            for row in store.conn.execute("SELECT tracking_code FROM tracking_events")
        }
    assert "FB-GIBTS-NICHT-999" not in codes


# --- Fall D: mehrfacher Download ---------------------------------------

def test_zweiter_download_desselben_menschen_zaehlt_nicht_doppelt(
    client: TestClient, bestand: Path
) -> None:
    """Neu laden, zweites Geraet, Neuinstallation - ein Mensch bleibt einer."""
    melde(client, event_type="landing_visit", anon_ref="anon-ABC",
          tracking_code=CODE_KOELN)
    melde(client, event_type="registration", user_ref="user-8472", anon_ref="anon-ABC")

    erste = melde(client, event_type="download", user_ref="user-8472")
    zweite = melde(client, event_type="download", user_ref="user-8472")

    assert erste["gezaehlt"] is True
    assert zweite["gezaehlt"] is False
    assert zweite["grund"] == "bereits gezaehlt"
    assert zahlen(bestand, CODE_KOELN)["download"] == 1


def test_download_vor_und_nach_der_anmeldung_ist_derselbe_mensch(
    client: TestClient, bestand: Path
) -> None:
    """Erst anonym geladen, dann registriert, dann noch einmal geladen."""
    melde(client, event_type="landing_visit", anon_ref="anon-ABC",
          tracking_code=CODE_KOELN)
    melde(client, event_type="download", anon_ref="anon-ABC")
    melde(client, event_type="registration", user_ref="user-8472", anon_ref="anon-ABC")
    zweite = melde(client, event_type="download", user_ref="user-8472")

    assert zweite["gezaehlt"] is False
    assert zahlen(bestand, CODE_KOELN)["download"] == 1


def test_zwei_menschen_derselben_gruppe_zaehlen_zweimal(
    client: TestClient, bestand: Path
) -> None:
    """Die Schranke gilt je Mensch, nicht je Code."""
    melde(client, event_type="landing_visit", anon_ref="anon-A", tracking_code=CODE_KOELN)
    melde(client, event_type="download", anon_ref="anon-A")
    melde(client, event_type="landing_visit", anon_ref="anon-B", tracking_code=CODE_KOELN)
    melde(client, event_type="download", anon_ref="anon-B")

    assert zahlen(bestand, CODE_KOELN)["download"] == 2


# --- Unabhaengigkeit der Stufen ----------------------------------------

def test_registrierung_ohne_download(client: TestClient, bestand: Path) -> None:
    melde(client, event_type="landing_visit", anon_ref="anon-ABC",
          tracking_code=CODE_KOELN)
    melde(client, event_type="registration", user_ref="user-8472", anon_ref="anon-ABC")

    stufen = zahlen(bestand, CODE_KOELN)
    assert stufen["registration"] == 1
    assert stufen.get("download", 0) == 0
    assert stufen.get("activation", 0) == 0


def test_download_ohne_registrierung(client: TestClient, bestand: Path) -> None:
    """Ein gueltiger Zustand: Die App laesst sich ohne Konto holen."""
    melde(client, event_type="landing_visit", anon_ref="anon-ABC",
          tracking_code=CODE_KOELN)
    melde(client, event_type="download", anon_ref="anon-ABC")

    stufen = zahlen(bestand, CODE_KOELN)
    assert stufen.get("registration", 0) == 0
    assert stufen["download"] == 1


def test_aktivierung_ohne_registrierung(client: TestClient, bestand: Path) -> None:
    """Auch die letzte Stufe setzt keine der vorigen voraus."""
    melde(client, event_type="landing_visit", anon_ref="anon-ABC",
          tracking_code=CODE_KOELN)
    melde(client, event_type="download", anon_ref="anon-ABC")
    melde(client, event_type="activation", anon_ref="anon-ABC")

    stufen = zahlen(bestand, CODE_KOELN)
    assert stufen.get("registration", 0) == 0
    assert stufen["download"] == 1
    assert stufen["activation"] == 1


def test_ein_download_erzeugt_keine_registrierung(client: TestClient, bestand: Path) -> None:
    """Kein Ereignis zieht ein anderes nach sich - auch nicht bequemerweise."""
    melde(client, event_type="landing_visit", anon_ref="anon-ABC",
          tracking_code=CODE_KOELN)
    antwort = melde(client, event_type="download", anon_ref="anon-ABC")

    assert "referral_code" not in antwort      # das gehoert zur Registrierung
    with MarketingStore(bestand) as store:
        assert store.event_counts().get("registration") is None


def test_mehr_registrierungen_als_downloads_ist_kein_fehler(
    client: TestClient, bestand: Path
) -> None:
    """10 Registrierungen, 3 Downloads, 2 Aktivierungen - alles gueltig."""
    for nummer in range(10):
        melde(client, event_type="landing_visit", anon_ref=f"anon-{nummer}",
              tracking_code=CODE_KOELN)
        melde(client, event_type="registration", user_ref=f"user-{nummer}",
              anon_ref=f"anon-{nummer}")
    for nummer in range(3):
        melde(client, event_type="download", user_ref=f"user-{nummer}")
    for nummer in range(2):
        melde(client, event_type="activation", user_ref=f"user-{nummer}")

    stufen = zahlen(bestand, CODE_KOELN)
    assert (stufen["registration"], stufen["download"], stufen["activation"]) == (10, 3, 2)


# --- Kennungen ---------------------------------------------------------

def test_die_kennungen_eines_menschen_finden_zusammen(bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        store.verknuepfe_kennung("anon-ABC", "user-8472")

        assert store.identitaet("anon-ABC") == "user-8472"
        assert store.kennungen("anon-ABC") == ["anon-ABC", "user-8472"]
        assert store.kennungen("user-8472") == ["anon-ABC", "user-8472"]


def test_dritte_kennung_haengt_sich_an_dieselbe_identitaet(bestand: Path) -> None:
    """Zwei Geraete, ein Konto: Das zweite darf das erste nicht abhaengen."""
    with MarketingStore(bestand) as store:
        store.verknuepfe_kennung("anon-handy", "user-8472")
        store.verknuepfe_kennung("anon-laptop", "user-8472")

        assert store.kennungen("anon-handy") == ["anon-handy", "anon-laptop", "user-8472"]


def test_verknuepfung_veraendert_kein_gespeichertes_ereignis(
    client: TestClient, bestand: Path
) -> None:
    """Ereignisse behalten die Kennung, unter der sie gemeldet wurden.

    Zusammengefuehrt wird beim Lesen. Eine Zeile, die einmal in der Datenbank
    steht, ist die Grundlage von Zahlen, die jemand schon gesehen hat.
    """
    melde(client, event_type="landing_visit", anon_ref="anon-ABC",
          tracking_code=CODE_KOELN)
    melde(client, event_type="registration", user_ref="user-8472", anon_ref="anon-ABC")

    with MarketingStore(bestand) as store:
        kennungen = [
            row["user_ref"]
            for row in store.conn.execute(
                "SELECT user_ref FROM tracking_events WHERE user_ref <> '' "
                "ORDER BY event_id"
            )
        ]
    assert kennungen == ["anon-ABC", "user-8472"]


def test_keine_personenbezogenen_felder_bei_der_kennung(bestand: Path) -> None:
    """Gespeichert wird nur, dass zwei undurchsichtige Kennungen zusammengehoeren."""
    with MarketingStore(bestand) as store:
        store.verknuepfe_kennung("anon-ABC", "user-8472")
        spalten = {
            zeile["name"]
            for zeile in store.conn.execute("PRAGMA table_info(user_identities)")
        }
    assert spalten == {"user_ref", "identity", "created_at"}


# --- Migration ---------------------------------------------------------

def test_bestandsdatei_holt_die_kennungstabelle_nach(
    client: TestClient, bestand: Path
) -> None:
    """Die Datei auf dem Server stammt aus der Fassung davor.

    ``GET /r/{code}`` und ``POST /events`` oeffnen nur den MarketingStore - er
    muss den fehlenden Schritt selbst nachholen, sonst stirbt die Weiterleitung
    an einer Stelle, an der niemand eine Migration vermutet.
    """
    import sqlite3

    melde(client, event_type="landing_visit", anon_ref="anon-ABC",
          tracking_code=CODE_KOELN)

    # Die Datei auf den Stand von vorher zuruecksetzen.
    conn = sqlite3.connect(bestand)
    conn.executescript("DROP TABLE user_identities; PRAGMA user_version = 9;")
    conn.commit()
    conn.close()

    with MarketingStore(bestand) as store:
        vorhandene = {
            row["name"]
            for row in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "user_identities" in vorhandene
        # Der Bestand ist unangetastet: dasselbe Ereignis, derselbe Code.
        assert store.event_counts()["landing_visit"] == 1
        assert store.erste_zuordnung("anon-ABC")[2] == CODE_KOELN

    # Und der Weg funktioniert danach ohne weiteres Zutun.
    melde(client, event_type="registration", user_ref="user-8472", anon_ref="anon-ABC")
    melde(client, event_type="download", user_ref="user-8472")
    assert zahlen(bestand, CODE_KOELN)["download"] == 1


def test_frische_datei_hat_dasselbe_layout_wie_eine_migrierte(tmp_path: Path) -> None:
    """Egal welcher Speicher die Datei zuerst anlegt - das Layout ist dasselbe.

    ``SqliteStore`` stempelt die Schema-Version. Legte er die Kennungstabelle
    nicht mit an, traege eine frische Datei die Nummer 10 ohne deren Inhalt -
    und ``_migrate`` holte sie nie nach, weil die Nummer ja schon stimmt.
    """
    import sqlite3

    zuerst_bestand = tmp_path / "a.sqlite"
    zuerst_marketing = tmp_path / "b.sqlite"
    SqliteStore(zuerst_bestand).close()
    MarketingStore(zuerst_marketing).close()

    def tabellen(pfad: Path) -> set[str]:
        conn = sqlite3.connect(pfad)
        try:
            return {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        finally:
            conn.close()

    assert "user_identities" in tabellen(zuerst_bestand)
    # Nur die Marketing-Tabellen vergleichen: groups, runs und group_sources
    # gehoeren zu Recht allein dem Bestandsspeicher.
    gemeinsam = tabellen(zuerst_marketing)
    assert gemeinsam <= tabellen(zuerst_bestand)
