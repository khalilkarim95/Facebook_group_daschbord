"""Tests der oeffentlichen Basis-URL fuer Tracking-Links.

Der Anlass: ``APP_BASE_URL`` in ``.env`` blieb wirkungslos. ``load_dotenv``
wurde ausschliesslich in ``providers/factory.py`` aufgerufen - in der
Schluesselabfrage der Suchanbieter. Kein ``campaign``-Befehl kam dort vorbei,
und die Tracking-Links zeigten still weiter auf ``localhost``, obwohl
``.env.example`` genau diesen Eintrag vorschlaegt.

Das faellt niemandem auf: Ein Link auf den eigenen Rechner sieht aus wie jeder
andere. Erst im veroeffentlichten Beitrag landet damit jeder Leser auf seinem
eigenen Computer.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fbgroups import cli
from fbgroups.config import load_config
from fbgroups.marketing import cli as marketing_cli
from fbgroups.marketing.models import Campaign, CampaignGroup
from fbgroups.marketing.store import MarketingStore
from fbgroups.marketing.tracking import (
    app_base_url,
    app_base_url_quelle,
    ist_lokale_basis,
    tracking_url,
)
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

runner = CliRunner()

PROJEKT = Path(__file__).resolve().parents[1]
REAL_ID_A = "482910573829104"
CODE_A = "FB-ARA-BER-001"


@pytest.fixture(autouse=True)
def ohne_umgebungsvariable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jeder Test startet ohne gesetzte Variable - sonst faerbt die Umgebung ab."""
    monkeypatch.delenv("APP_BASE_URL", raising=False)


@pytest.fixture()
def projekt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Vollstaendiges Projekt im Testverzeichnis, mit Kampagne und Code.

    Die Basis-URL wird auf ``http://localhost:3000`` festgeschrieben. Diese
    Tests pruefen das *Verfahren* - Umgebung schlaegt Datei, kein doppelter
    Schraegstrich, Warnung bei einer lokalen Adresse. Wohin das Projekt gerade
    zeigt, ist dafuer belanglos: Als die echte Domain in ``settings.yaml``
    eingetragen wurde, gingen drei Tests kaputt, ohne dass am Verfahren
    irgendetwas falsch gewesen waere.
    """
    shutil.copytree(PROJEKT / "config", tmp_path / "config")

    settings = tmp_path / "config" / "settings.yaml"
    settings.write_text(
        re.sub(
            r"^(\s*app_base_url:).*$",
            r"\1 http://localhost:3000",
            settings.read_text(encoding="utf-8"),
            count=1,
            flags=re.MULTILINE,
        ),
        encoding="utf-8",
    )

    pfad = tmp_path / "data" / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=REAL_ID_A,
                    url_canonical=f"https://www.facebook.com/groups/{REAL_ID_A}",
                    name="Araber in Berlin",
                    audience_tags=["arabs"],
                    city="Berlin",
                )
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(Campaign(campaign_id="batreeq", name="Batreeq"))
        store.add_link(
            CampaignGroup(
                campaign_id="batreeq",
                group_id=REAL_ID_A,
                tracking_code=CODE_A,
                tracking_url=f"http://localhost:3000/r/{CODE_A}",
            )
        )

    for modul in (cli, marketing_cli):
        monkeypatch.setattr(modul, "load_config", lambda: load_config(tmp_path))
    return tmp_path


def _link(projekt: Path) -> CampaignGroup:
    with MarketingStore(projekt / "data" / "groups.sqlite") as store:
        return store.links_for_campaign("batreeq")[0]


# --- Herkunft der Basis-URL --------------------------------------------

def test_umgebungsvariable_ergibt_den_oeffentlichen_link(
    projekt: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Fall aus der Anforderung."""
    monkeypatch.setenv("APP_BASE_URL", "https://example.test")
    config = load_config(projekt)

    assert app_base_url(config) == "https://example.test"
    assert tracking_url(CODE_A, config) == "https://example.test/r/FB-ARA-BER-001"


def test_app_base_url_wird_aus_der_env_datei_gelesen(projekt: Path) -> None:
    """Regressionstest: Genau das ging nicht.

    ``load_dotenv`` lief nur in der Schluesselabfrage der Suchanbieter; ein
    ``campaign``-Befehl kam dort nie vorbei.
    """
    (projekt / ".env").write_text("APP_BASE_URL=https://example.test\n", encoding="utf-8")
    config = load_config(projekt)

    assert app_base_url(config) == "https://example.test"
    assert tracking_url(CODE_A, config) == "https://example.test/r/FB-ARA-BER-001"


def test_umgebung_schlaegt_die_env_datei(projekt: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Im Betrieb wird die Umgebung gesetzt - die Datei darf sie nicht ueberstimmen."""
    (projekt / ".env").write_text("APP_BASE_URL=https://aus-der-datei.test\n", encoding="utf-8")
    monkeypatch.setenv("APP_BASE_URL", "https://aus-der-umgebung.test")

    assert app_base_url(load_config(projekt)) == "https://aus-der-umgebung.test"


def test_umgebung_schlaegt_settings_yaml(projekt: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """localhost aus settings.yaml darf nicht gewinnen, wenn die Variable steht."""
    config = load_config(projekt)
    assert config.get("marketing", "app_base_url") == "http://localhost:3000"

    monkeypatch.setenv("APP_BASE_URL", "https://example.test")
    assert app_base_url(load_config(projekt)) == "https://example.test"


def test_ohne_angabe_bleibt_der_lokale_betrieb(projekt: Path) -> None:
    """Entwicklung auf dem eigenen Rechner muss weiter funktionieren."""
    config = load_config(projekt)

    assert app_base_url(config) == "http://localhost:3000"
    assert tracking_url(CODE_A, config) == "http://localhost:3000/r/FB-ARA-BER-001"


def test_abschliessender_schraegstrich_erzeugt_keinen_doppelten(
    projekt: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_BASE_URL", "https://example.test/")

    assert tracking_url(CODE_A, load_config(projekt)) == "https://example.test/r/FB-ARA-BER-001"


@pytest.mark.parametrize(
    ("basis", "lokal"),
    [
        ("http://localhost:3000", True),
        ("http://127.0.0.1:3000", True),
        ("http://0.0.0.0:3000", True),
        ("https://example.test", False),
        ("https://batreeq.de", False),
    ],
)
def test_lokale_basis_wird_erkannt(basis: str, lokal: bool) -> None:
    assert ist_lokale_basis(basis) is lokal


def test_quelle_wird_benannt(projekt: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_config(projekt)
    assert app_base_url_quelle(config) == "config/settings.yaml"

    monkeypatch.setenv("APP_BASE_URL", "https://example.test")
    assert app_base_url_quelle(config) == "Umgebung (APP_BASE_URL)"


# --- refresh-urls -------------------------------------------------------

def test_refresh_urls_erzeugt_die_links_neu(
    projekt: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _link(projekt).tracking_url == f"http://localhost:3000/r/{CODE_A}"

    monkeypatch.setenv("APP_BASE_URL", "https://example.test")
    ergebnis = runner.invoke(cli.app, ["campaign", "refresh-urls", "batreeq"])

    assert ergebnis.exit_code == 0
    assert _link(projekt).tracking_url == "https://example.test/r/FB-ARA-BER-001"


def test_refresh_urls_laesst_den_code_unveraendert(
    projekt: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Code steht in veroeffentlichten Beitraegen - nur der Vorspann wechselt."""
    monkeypatch.setenv("APP_BASE_URL", "https://example.test")
    runner.invoke(cli.app, ["campaign", "refresh-urls", "batreeq"])

    link = _link(projekt)
    assert link.tracking_code == CODE_A
    assert link.group_id == REAL_ID_A


def test_alter_link_bleibt_bedienbar(projekt: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein schon veroeffentlichter Link muss weiter aufloesbar sein.

    Der Dienst schaut den Code nach, nicht die Adresse, unter der er ankam -
    ein Beitrag mit dem alten Link fuehrt deshalb weiterhin zur richtigen
    Gruppe, sobald die alte Adresse noch erreichbar ist.
    """
    monkeypatch.setenv("APP_BASE_URL", "https://example.test")
    runner.invoke(cli.app, ["campaign", "refresh-urls", "batreeq"])

    with MarketingStore(projekt / "data" / "groups.sqlite") as store:
        aufgeloest = store.resolve_code(CODE_A)

    assert aufgeloest is not None
    assert aufgeloest.group_id == REAL_ID_A


# --- campaign show ------------------------------------------------------

def test_show_zeigt_basis_url_und_status(
    projekt: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_BASE_URL", "https://example.test")
    runner.invoke(cli.app, ["campaign", "refresh-urls", "batreeq"])

    ergebnis = runner.invoke(cli.app, ["campaign", "show", "batreeq"])
    ausgabe = ergebnis.stdout.replace("\n", "")

    assert ergebnis.exit_code == 0
    assert "Basis-URL" in ausgabe
    assert "https://example.test" in ausgabe
    assert "Umgebung (APP_BASE_URL)" in ausgabe
    assert "draft" in ausgabe                       # Status
    assert CODE_A in ausgabe                        # Tracking-URL/Code


def test_show_warnt_bei_lokaler_basis(projekt: Path) -> None:
    """Ohne Warnung sieht ein unbrauchbarer Link aus wie ein brauchbarer."""
    ergebnis = runner.invoke(cli.app, ["campaign", "show", "batreeq"])
    ausgabe = ergebnis.stdout.replace("\n", "")

    assert "zeigt auf diesen Rechner" in ausgabe
    assert "APP_BASE_URL" in ausgabe
