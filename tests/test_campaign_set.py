"""Tests fuer 'campaign set'.

Der Grund fuer diesen Befehl: Eine Kampagne darf nicht neu angelegt werden,
um etwa die Landingpage zu korrigieren. Beim Loeschen faellt ueber den
Fremdschluessel auch die Zuordnung der Gruppen weg - und damit die vergebenen
Tracking-Codes, die moeglicherweise schon in veroeffentlichten Beitraegen
stehen.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fbgroups import cli
from fbgroups.config import load_config
from fbgroups.marketing import cli as marketing_cli
from fbgroups.marketing.models import Campaign, CampaignGroup
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

runner = CliRunner()

REAL_ID_A = "482910573829104"
CODE_A = "FB-SYR-BER-001"


@pytest.fixture()
def projekt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Kampagne mit Platzhalter-Landingpage und einem vergebenen Code."""
    pfad = tmp_path / "data" / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=REAL_ID_A,
                    url_canonical=f"https://www.facebook.com/groups/{REAL_ID_A}",
                    name="Syrer in Berlin",
                )
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(
                campaign_id="batreeq",
                name="Batreeq",
                landing_page="https://batreeq.example/start",
                message_template="مرحبا! {link}",
            )
        )
        store.add_link(
            CampaignGroup(campaign_id="batreeq", group_id=REAL_ID_A, tracking_code=CODE_A)
        )

    echt = load_config()
    settings = {
        **echt.settings,
        "paths": {**echt.settings["paths"], "sqlite_path": "data/groups.sqlite"},
    }
    test_config = replace(echt, root=tmp_path, settings=settings)
    monkeypatch.setattr(cli, "load_config", lambda: test_config)
    monkeypatch.setattr(marketing_cli, "load_config", lambda: test_config)
    return pfad


def _kampagne(pfad: Path) -> Campaign:
    with MarketingStore(pfad) as store:
        campaign = store.load_campaign("batreeq")
    assert campaign is not None
    return campaign


def test_landingpage_laesst_sich_korrigieren(projekt: Path) -> None:
    ergebnis = runner.invoke(
        cli.app, ["campaign", "set", "batreeq", "--landingpage", "https://batreeq.de/start"]
    )

    assert ergebnis.exit_code == 0
    assert _kampagne(projekt).landing_page == "https://batreeq.de/start"


def test_tracking_code_ueberlebt_die_aenderung(projekt: Path) -> None:
    """Der Kern der Sache: Der Code steht in veroeffentlichten Beitraegen."""
    runner.invoke(cli.app, ["campaign", "set", "batreeq", "--landingpage", "https://batreeq.de/"])

    with MarketingStore(projekt) as store:
        links = store.links_for_campaign("batreeq")

    assert [link.tracking_code for link in links] == [CODE_A]


def test_nicht_genannte_felder_bleiben(projekt: Path) -> None:
    runner.invoke(cli.app, ["campaign", "set", "batreeq", "--landingpage", "https://batreeq.de/"])

    campaign = _kampagne(projekt)
    assert campaign.name == "Batreeq"
    assert campaign.message_template == "مرحبا! {link}"


def test_vorlage_aus_datei_wird_als_utf8_gelesen(projekt: Path, tmp_path: Path) -> None:
    """Notepad schreibt ein BOM - ohne utf-8-sig steckt es im ersten Zeichen."""
    datei = tmp_path / "vorlage.txt"
    datei.write_text("مرحبا بكم في بطريق!\n{link}", encoding="utf-8-sig")

    runner.invoke(cli.app, ["campaign", "set", "batreeq", "--vorlage-datei", str(datei)])

    assert _kampagne(projekt).message_template.startswith("مرحبا")


def test_ohne_angabe_passiert_nichts(projekt: Path) -> None:
    ergebnis = runner.invoke(cli.app, ["campaign", "set", "batreeq"])

    assert ergebnis.exit_code == 0
    assert "nichts geaendert" in ergebnis.stdout
    assert _kampagne(projekt).landing_page == "https://batreeq.example/start"


def test_unbekannte_kampagne_wird_gemeldet(projekt: Path) -> None:
    ergebnis = runner.invoke(
        cli.app, ["campaign", "set", "gibtsnicht", "--landingpage", "https://x.de"]
    )

    assert ergebnis.exit_code == 1
    assert "Unbekannte Kampagne" in ergebnis.stdout
