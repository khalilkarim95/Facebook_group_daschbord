"""Tests der Marketing-Erweiterung.

Zwei Dinge stehen im Mittelpunkt: Der bestehende Bestand darf nicht leiden,
und ein einmal vergebener Tracking-Code darf sich nie wieder aendern - er steht
in veroeffentlichten Beitraegen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    CampaignStatus,
    MarketingStatus,
    PermissionStatus,
)
from fbgroups.marketing.store import MarketingStore, UnknownCampaignError, UnknownGroupError
from fbgroups.marketing.tracking import (
    app_base_url,
    code_prefix,
    ist_gueltiger_code,
    next_tracking_code,
    tracking_url,
)
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

REAL_ID_A = "482910573829104"
REAL_ID_B = "739201847362915"


def _group(group_id: str = REAL_ID_A, **kwargs) -> Group:
    defaults = {
        "group_id": group_id,
        "url_canonical": f"https://www.facebook.com/groups/{group_id}",
        "name": "Syrer in Berlin",
        "audience_tags": ["syrians"],
        "city": "Berlin",
    }
    return Group(**{**defaults, **kwargs})


@pytest.fixture()
def bestand(tmp_path: Path) -> Path:
    """Eine Datenbank mit zwei echten Gruppen - wie nach einem Suchlauf."""
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                _group(),
                _group(
                    REAL_ID_B,
                    name="Araber in Hamburg",
                    audience_tags=["arabs"],
                    city="Hamburg",
                ),
            ]
        )
    return pfad


def _kampagne(store: MarketingStore, campaign_id: str = "batreeq-syrian-germany") -> Campaign:
    campaign = Campaign(campaign_id=campaign_id, name="Batreeq Syrian Germany")
    store.save_campaign(campaign)
    return campaign


# --- Tracking-Codes ----------------------------------------------------

def test_code_folgt_dem_vereinbarten_muster(config) -> None:
    code = next_tracking_code(_group(), config, vergeben=set())
    assert code == "FB-SYR-BER-001"
    assert ist_gueltiger_code(code)


def test_code_kuerzel_kommen_aus_der_konfiguration(config) -> None:
    assert code_prefix(_group(city="München", audience_tags=["arabs"]), config) == "FB-ARA-MUE"


def test_ohne_zielgruppe_oder_stadt_gibt_es_ersatzkuerzel(config) -> None:
    """Ein Code muss immer entstehen - sonst gaebe es keinen Link."""
    assert code_prefix(_group(audience_tags=[], city=None), config) == "FB-GEN-DE"


def test_laufende_nummer_zaehlt_je_kuerzel_hoch(config) -> None:
    vergeben = {"FB-SYR-BER-001", "FB-SYR-BER-002"}
    assert next_tracking_code(_group(), config, vergeben) == "FB-SYR-BER-003"
    # Eine andere Stadt beginnt wieder bei 001.
    assert next_tracking_code(_group(city="Hamburg"), config, vergeben) == "FB-SYR-HAM-001"


def test_tracking_url_nutzt_die_basis_url(config, monkeypatch) -> None:
    monkeypatch.setenv("APP_BASE_URL", "https://batreeq.example/")
    assert tracking_url("FB-SYR-BER-001", config) == "https://batreeq.example/r/FB-SYR-BER-001"


def test_umgebungsvariable_schlaegt_die_konfiguration(config, monkeypatch) -> None:
    monkeypatch.setenv("APP_BASE_URL", "https://echte-domain.example")
    assert app_base_url(config) == "https://echte-domain.example"

    # Ohne Variable gilt wieder settings.yaml - welcher Wert dort steht, ist
    # eine Frage des Betriebs und keine des Verfahrens. Frueher stand hier der
    # damalige Wert fest verdrahtet; der Wechsel auf die echte Domain machte
    # den Test rot, obwohl die Vorrangregel unveraendert richtig war.
    monkeypatch.delenv("APP_BASE_URL")
    aus_datei = str(config.get("marketing", "app_base_url", default="")).rstrip("/")
    assert app_base_url(config) == aus_datei


def test_ohne_basis_url_bleibt_der_link_leer(config, monkeypatch) -> None:
    """Ein halber Link waere in einem Beitrag schlimmer als gar keiner."""
    monkeypatch.setenv("APP_BASE_URL", "")
    leer = lambda self, *keys, default=None: "" if "app_base_url" in keys else default  # noqa: E731
    monkeypatch.setattr(type(config), "get", leer)
    assert tracking_url("FB-SYR-BER-001", config) == ""


# --- Kampagnen und Zuordnung -------------------------------------------

def test_kampagne_wird_gespeichert_und_gelesen(bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        campaign = Campaign(
            campaign_id="test-kampagne",
            name="Test",
            audiences=["syrians"],
            cities=["berlin"],
            message_template="Hallo {link}",
        )
        store.save_campaign(campaign)
        wieder = store.load_campaign("test-kampagne")

    assert wieder is not None
    assert wieder.name == "Test"
    assert wieder.audiences == ["syrians"]
    assert wieder.status is CampaignStatus.DRAFT


def test_zuordnung_erhaelt_ihren_code_fuer_immer(bestand: Path, config) -> None:
    """Regressionstest: Der Code steht in veroeffentlichten Beitraegen."""
    with MarketingStore(bestand) as store:
        _kampagne(store)
        erst = CampaignGroup(
            campaign_id="batreeq-syrian-germany",
            group_id=REAL_ID_A,
            tracking_code="FB-SYR-BER-001",
            tracking_url="http://localhost:3000/r/FB-SYR-BER-001",
        )
        assert store.add_link(erst) is True

        # Zweiter Versuch mit anderem Code - die Zuordnung bleibt, wie sie war.
        nochmal = CampaignGroup(
            campaign_id="batreeq-syrian-germany",
            group_id=REAL_ID_A,
            tracking_code="FB-SYR-BER-999",
        )
        assert store.add_link(nochmal) is False

        gespeichert = store.link_for("batreeq-syrian-germany", REAL_ID_A)

    assert gespeichert is not None
    assert gespeichert.tracking_code == "FB-SYR-BER-001"


def test_zuordnung_nur_zu_bekannten_gruppen(bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        _kampagne(store)
        with pytest.raises(UnknownGroupError):
            store.add_link(
                CampaignGroup(
                    campaign_id="batreeq-syrian-germany",
                    group_id="gibtesnicht",
                    tracking_code="FB-SYR-BER-002",
                )
            )


def test_zuordnung_nur_zu_bekannten_kampagnen(bestand: Path) -> None:
    with MarketingStore(bestand) as store, pytest.raises(UnknownCampaignError):
        store.add_link(
            CampaignGroup(
                campaign_id="gibtesnicht",
                group_id=REAL_ID_A,
                tracking_code="FB-SYR-BER-003",
            )
        )


def test_code_ist_ueber_alle_kampagnen_eindeutig(bestand: Path) -> None:
    """Sonst waere ein eingehender Klick nicht eindeutig zuzuordnen."""
    import sqlite3

    with MarketingStore(bestand) as store:
        _kampagne(store, "kampagne-a")
        _kampagne(store, "kampagne-b")
        store.add_link(
            CampaignGroup(campaign_id="kampagne-a", group_id=REAL_ID_A, tracking_code="FB-X-1")
        )
        with pytest.raises(sqlite3.IntegrityError):
            store.add_link(
                CampaignGroup(campaign_id="kampagne-b", group_id=REAL_ID_B, tracking_code="FB-X-1")
            )


def test_basis_url_wechsel_laesst_die_codes_unberuehrt(bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        _kampagne(store)
        store.add_link(
            CampaignGroup(
                campaign_id="batreeq-syrian-germany",
                group_id=REAL_ID_A,
                tracking_code="FB-SYR-BER-001",
                tracking_url="http://localhost:3000/r/FB-SYR-BER-001",
            )
        )
        geaendert = store.refresh_tracking_urls(
            "batreeq-syrian-germany", lambda code: f"https://batreeq.example/r/{code}"
        )
        link = store.link_for("batreeq-syrian-germany", REAL_ID_A)

    assert geaendert == 1
    assert link.tracking_code == "FB-SYR-BER-001"
    assert link.tracking_url == "https://batreeq.example/r/FB-SYR-BER-001"


# --- Arbeitsstand ------------------------------------------------------

def test_arbeitsstand_hat_einen_anfangszustand(bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        stand = store.load_marketing(REAL_ID_A)
    assert stand.marketing_status is MarketingStatus.NOT_CONTACTED
    assert stand.permission_status is PermissionStatus.UNKNOWN


def test_arbeitsstand_ueberlebt_einen_erneuten_suchlauf(bestand: Path) -> None:
    """Der Kern der Trennung: 'groups' wird neu geschrieben, der Stand nicht."""
    with MarketingStore(bestand) as store:
        stand = store.load_marketing(REAL_ID_A)
        stand.marketing_status = MarketingStatus.APPROVED
        stand.permission_status = PermissionStatus.APPROVED
        stand.notes = "Admin hat zugestimmt"
        store.save_marketing(stand)

    # Ein Suchlauf findet dieselbe Gruppe erneut und schreibt sie neu.
    with SqliteStore(bestand) as gruppen_store:
        gruppen_store.upsert_groups([_group(name="Syrer in Berlin (neu gefunden)")])

    with MarketingStore(bestand) as store:
        wieder = store.load_marketing(REAL_ID_A)

    assert wieder.marketing_status is MarketingStatus.APPROVED
    assert wieder.notes == "Admin hat zugestimmt"


# --- Der Bestand bleibt unangetastet -----------------------------------

def test_bestehende_tabellen_bleiben_unveraendert(bestand: Path) -> None:
    import sqlite3

    con = sqlite3.connect(bestand)
    vorher = {
        row[0]: row[1]
        for row in con.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")
    }
    con.close()

    with MarketingStore(bestand) as store:
        _kampagne(store)

    con = sqlite3.connect(bestand)
    nachher = {
        row[0]: row[1]
        for row in con.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")
    }
    gruppen = con.execute("SELECT COUNT(*) FROM groups").fetchone()[0]
    con.close()

    for name, ddl in vorher.items():
        assert nachher[name] == ddl, f"Tabelle {name} wurde veraendert"
    assert gruppen == 2
    assert {"campaigns", "campaign_groups", "group_marketing"} <= set(nachher)


def test_alte_datenbank_bekommt_die_neuen_tabellen(tmp_path: Path) -> None:
    """Saubere Migration: vorhandene Daten bleiben, Tabellen kommen dazu."""
    import sqlite3

    from fbgroups.storage.sqlite_store import SCHEMA_VERSION

    pfad = tmp_path / "alt.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups([_group()])

    # Zurueck auf die Fassung vor der Marketing-Erweiterung. Die Zahl steht
    # fest bei 3 und nicht bei SCHEMA_VERSION - 1: Gemeint ist der Stand, ab
    # dem Schritt 3 die Marketing-Tabellen anlegt. Mit jedem spaeteren Schritt
    # waere "eins zurueck" ein anderer, hier unmoeglicher Zustand - eine
    # Datenbank ohne Marketing-Tabellen, die sich als neuere Fassung ausgibt.
    VOR_MARKETING = 3
    assert VOR_MARKETING < SCHEMA_VERSION

    con = sqlite3.connect(pfad)
    con.executescript(
        "DROP TABLE campaigns; DROP TABLE campaign_groups; DROP TABLE group_marketing;"
        f"PRAGMA user_version = {VOR_MARKETING};"
    )
    con.commit()
    con.close()

    with SqliteStore(pfad) as store:      # oeffnet und migriert
        gruppen = store.load_groups()

    con = sqlite3.connect(pfad)
    tabellen = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    version = con.execute("PRAGMA user_version").fetchone()[0]
    con.close()

    assert len(gruppen) == 1                                   # Daten unversehrt
    assert version == SCHEMA_VERSION
    # Die Tabelle entstand in Schritt 3 aus dem aktuellen Schema und bringt die
    # spaeter ergaenzten Spalten schon mit; Schritt 5 darf daran nicht
    # scheitern.
    con = sqlite3.connect(pfad)
    spalten = {r[1] for r in con.execute("PRAGMA table_info(group_marketing)")}
    con.close()
    assert "join_requested_at" in spalten
    assert {"campaigns", "campaign_groups", "group_marketing"} <= tabellen
