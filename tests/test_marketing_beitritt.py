"""Tests des Beitritts-Schrittes.

Bei Facebook muss man erst aufgenommen sein, bevor man posten oder die
Gruppenleitung ansprechen kann. Dieser Schritt betrifft jede Gruppe im
Bestand - das Ansprechen der Leitung nur eine Handvoll.

Das Programm stellt keine Beitrittsanfrage: facebook.com wird nie aufgerufen.
Es schreibt nur mit, was ein Mensch im Browser getan hat.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fbgroups import cli
from fbgroups.config import load_config
from fbgroups.marketing import cli as marketing_cli
from fbgroups.marketing.cli import _ist_schon_weiter
from fbgroups.marketing.models import Campaign, CampaignGroup, MarketingStatus
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

runner = CliRunner()

REAL_ID_A = "482910573829104"
REAL_ID_B = "739201847362915"
REAL_ID_C = "615043928175306"


@pytest.fixture()
def projekt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config) -> Path:
    """Eigenes Projektverzeichnis mit drei Gruppen und einer Kampagne."""
    pfad = tmp_path / "data" / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=gid,
                    url_canonical=f"https://www.facebook.com/groups/{gid}",
                    name=name,
                    audience_tags=["syrians"],
                    city="Berlin",
                    score=score,
                    score_max=100.0,
                )
                for gid, name, score in [
                    (REAL_ID_A, "Syrer in Berlin", 95.0),
                    (REAL_ID_B, "Araber in Hamburg", 80.0),
                    (REAL_ID_C, "Syrer in Köln", 60.0),
                ]
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(Campaign(campaign_id="batreeq", name="Batreeq"))
        store.add_link(
            CampaignGroup(campaign_id="batreeq", group_id=REAL_ID_A, tracking_code="FB-SYR-BER-001")
        )
        store.add_link(
            CampaignGroup(campaign_id="batreeq", group_id=REAL_ID_B, tracking_code="FB-ARA-HAM-001")
        )

    # Dieselbe Projektkonfiguration, aber mit dem Bestand im Testverzeichnis -
    # der echte Bestand wird nie geoeffnet.
    echt = load_config()
    settings = {
        **echt.settings,
        "paths": {**echt.settings["paths"], "sqlite_path": "data/groups.sqlite"},
    }
    test_config = replace(echt, root=tmp_path, settings=settings)

    monkeypatch.setattr(cli, "load_config", lambda: test_config)
    monkeypatch.setattr(marketing_cli, "load_config", lambda: test_config)
    return pfad


def _stand(pfad: Path, group_id: str) -> MarketingStatus:
    with MarketingStore(pfad) as store:
        return store.load_marketing(group_id).marketing_status


# --- Der Sammelbefehl --------------------------------------------------

def test_beitritt_markiert_eine_einzelne_gruppe(projekt: Path) -> None:
    ergebnis = runner.invoke(cli.app, ["marketing", "beitritt", REAL_ID_A])

    assert ergebnis.exit_code == 0
    assert _stand(projekt, REAL_ID_A) is MarketingStatus.JOIN_REQUESTED
    assert _stand(projekt, REAL_ID_B) is MarketingStatus.NOT_CONTACTED


def test_beitritt_markiert_eine_ganze_kampagne(projekt: Path) -> None:
    ergebnis = runner.invoke(cli.app, ["marketing", "beitritt", "--kampagne", "batreeq"])

    assert ergebnis.exit_code == 0
    assert _stand(projekt, REAL_ID_A) is MarketingStatus.JOIN_REQUESTED
    assert _stand(projekt, REAL_ID_B) is MarketingStatus.JOIN_REQUESTED
    # Nicht Teil der Kampagne.
    assert _stand(projekt, REAL_ID_C) is MarketingStatus.NOT_CONTACTED


def test_beitritt_nimmt_die_besten_n(projekt: Path) -> None:
    ergebnis = runner.invoke(cli.app, ["marketing", "beitritt", "--top", "2"])

    assert ergebnis.exit_code == 0
    assert _stand(projekt, REAL_ID_A) is MarketingStatus.JOIN_REQUESTED   # 95 Punkte
    assert _stand(projekt, REAL_ID_B) is MarketingStatus.JOIN_REQUESTED   # 80 Punkte
    assert _stand(projekt, REAL_ID_C) is MarketingStatus.NOT_CONTACTED    # 60 Punkte


def test_zeitpunkt_wird_festgehalten(projekt: Path) -> None:
    """Eigenes Feld: Eine Anfrage an die Gruppe ist keine Ansprache der Leitung."""
    runner.invoke(cli.app, ["marketing", "beitritt", REAL_ID_A])

    with MarketingStore(projekt) as store:
        eintrag = store.load_marketing(REAL_ID_A)

    assert eintrag.join_requested_at is not None
    assert eintrag.last_contacted_at is None


def test_ohne_auswahl_passiert_nichts(projekt: Path) -> None:
    """Ein Sammelbefehl ohne Auswahl duerfte sonst den ganzen Bestand treffen."""
    ergebnis = runner.invoke(cli.app, ["marketing", "beitritt"])

    assert ergebnis.exit_code == 2
    assert _stand(projekt, REAL_ID_A) is MarketingStatus.NOT_CONTACTED


def test_dry_run_schreibt_nichts(projekt: Path) -> None:
    ergebnis = runner.invoke(cli.app, ["marketing", "beitritt", "--top", "3", "--dry-run"])

    assert ergebnis.exit_code == 0
    assert "--dry-run" in ergebnis.stdout
    # Der eigentliche Nachweis: im Bestand steht unveraendert der Anfangszustand.
    assert _stand(projekt, REAL_ID_A) is MarketingStatus.NOT_CONTACTED
    assert _stand(projekt, REAL_ID_B) is MarketingStatus.NOT_CONTACTED


def test_unbekannte_gruppe_wird_gemeldet_statt_angelegt(projekt: Path) -> None:
    ergebnis = runner.invoke(cli.app, ["marketing", "beitritt", "gibtsnicht"])

    assert ergebnis.exit_code == 0
    assert "unbekannt" in ergebnis.stdout


# --- Kein Rückschritt ---------------------------------------------------

def test_erreichter_stand_wird_nicht_zurueckgedreht(projekt: Path) -> None:
    """Wer schon Mitglied ist, verliert das nicht durch einen zweiten Durchlauf."""
    runner.invoke(cli.app, ["marketing", "set", REAL_ID_A, "--status", "mitglied"])

    ergebnis = runner.invoke(cli.app, ["marketing", "beitritt", "--top", "3"])

    assert ergebnis.exit_code == 0
    assert _stand(projekt, REAL_ID_A) is MarketingStatus.MEMBER
    assert "bereits weiter" in ergebnis.stdout


def test_abgelehnte_aufnahme_wird_nicht_stillschweigend_erneuert(projekt: Path) -> None:
    """Eine Ablehnung ist ein Ergebnis - sie erneut anzufragen ist Handarbeit."""
    runner.invoke(cli.app, ["marketing", "set", REAL_ID_A, "--status", "beitritt_abgelehnt"])

    runner.invoke(cli.app, ["marketing", "beitritt", "--top", "3"])

    assert _stand(projekt, REAL_ID_A) is MarketingStatus.JOIN_REJECTED


@pytest.mark.parametrize(
    ("stand", "erwartet"),
    [
        (MarketingStatus.NOT_CONTACTED, False),
        (MarketingStatus.JOIN_REQUESTED, True),
        (MarketingStatus.MEMBER, True),
        (MarketingStatus.CONTACTED, True),
        (MarketingStatus.APPROVED, True),
        (MarketingStatus.ACTIVE, True),
        # Ausserhalb der Ablaufreihenfolge: ein Ergebnis, kein Zwischenstand.
        (MarketingStatus.JOIN_REJECTED, True),
        (MarketingStatus.REJECTED, True),
        (MarketingStatus.INACTIVE, True),
    ],
)
def test_ist_schon_weiter(stand: MarketingStatus, erwartet: bool) -> None:
    assert _ist_schon_weiter(stand, MarketingStatus.JOIN_REQUESTED) is erwartet
