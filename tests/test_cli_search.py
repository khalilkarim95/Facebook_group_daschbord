"""Tests des Suchbefehls auf der Kommandozeile.

Alles laeuft in einem eigenen Projektverzeichnis mit eigenem Datenbestand -
kein Test schreibt in die echte Datenbank oder in den echten Anfragespeicher.
Der aktive Provider ist ``fixture``: offline, ohne Schluessel, ohne Kosten.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from fbgroups import cli
from fbgroups.config import load_config
from fbgroups.query.builder import build_queries
from fbgroups.storage.query_cache import QueryCache

PROJEKT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "search"

runner = CliRunner()


@pytest.fixture
def projekt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Ein vollstaendiges Projekt im Testverzeichnis."""
    shutil.copytree(PROJEKT / "config", tmp_path / "config")

    providers = yaml.safe_load((tmp_path / "config" / "providers.yaml").read_text("utf-8"))
    providers["active"] = "fixture"
    providers["providers"]["fixture"]["fixtures_dir"] = str(FIXTURES)
    (tmp_path / "config" / "providers.yaml").write_text(
        yaml.safe_dump(providers, allow_unicode=True), encoding="utf-8"
    )

    monkeypatch.setattr(cli, "load_config", lambda: load_config(tmp_path))
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    return tmp_path


def _cache_pfad(projekt: Path) -> Path:
    return projekt / "data" / "query_cache.sqlite"


def test_ohne_mengenangabe_wird_nichts_gesucht(projekt: Path) -> None:
    """Ein vollstaendiger Deutschland-Scan startet nie beilaeufig."""
    ergebnis = runner.invoke(cli.app, ["search"])

    assert ergebnis.exit_code == 2
    assert "--limit" in ergebnis.stdout
    assert not (projekt / "data" / "groups.sqlite").exists()
    assert not _cache_pfad(projekt).exists()


def test_dry_run_beziffert_den_verbrauch(projekt: Path) -> None:
    ergebnis = runner.invoke(cli.app, ["search", "--dry-run", "--limit", "5"])

    assert ergebnis.exit_code == 0
    assert "Geschaetzter Verbrauch" in ergebnis.stdout
    assert "Es wurde nichts abgefragt" in ergebnis.stdout

    # Kein Eintrag: es ging nichts hinaus.
    with QueryCache(_cache_pfad(projekt)) as cache:
        assert cache.count() == 0
        assert cache.history() == []


def test_dry_run_kurz_zeigt_nur_die_zusammenfassung(projekt: Path) -> None:
    """Fuer das Portionsskript: Zahlen ohne die Liste aller Anfragen."""
    lang = runner.invoke(cli.app, ["search", "--dry-run", "--limit", "5"])
    kurz = runner.invoke(cli.app, ["search", "--dry-run", "--limit", "5", "--kurz"])

    assert kurz.exit_code == 0
    assert "Geschaetzter Verbrauch" in kurz.stdout
    assert "nw_01" not in kurz.stdout          # keine Anfrageliste
    assert len(kurz.stdout) < len(lang.stdout)


def test_dry_run_ohne_schluessel_moeglich(projekt: Path) -> None:
    """Gerade ohne Schluessel muss man den Plan ansehen koennen."""
    ergebnis = runner.invoke(cli.app, ["search", "--dry-run", "--provider", "serper"])

    assert ergebnis.exit_code == 0
    assert "SERPER_API_KEY" in ergebnis.stdout


def test_ohne_schluessel_kein_livelauf(projekt: Path) -> None:
    """Klare Meldung statt Fehlversuch - und kein einziger Aufruf."""
    ergebnis = runner.invoke(cli.app, ["search", "--provider", "serper", "--limit", "1"])

    assert ergebnis.exit_code == 1
    assert "SERPER_API_KEY" in ergebnis.stdout
    assert ".env" in ergebnis.stdout
    assert not _cache_pfad(projekt).exists()


def test_lauf_speichert_und_wiederholt_keine_anfrage(projekt: Path) -> None:
    erster = runner.invoke(cli.app, ["search", "--limit", "2"])
    assert erster.exit_code == 0

    with QueryCache(_cache_pfad(projekt)) as cache:
        assert cache.count() == 2

    zweiter = runner.invoke(cli.app, ["search", "--limit", "2"])
    assert zweiter.exit_code == 0

    with QueryCache(_cache_pfad(projekt)) as cache:
        # Zwei aus dem Speicher, zwei neue - die ersten beiden kosten nichts mehr.
        assert cache.count() == 4
        gesendet = [e for e in cache.history(50) if not e["from_cache"] and e["success"]]
        assert len(gesendet) == 4


def test_gefundene_gruppen_landen_im_bestand(projekt: Path) -> None:
    """Bis cp_02__berlin liefert die Offline-Antwort Gruppen.

    Die Menge wird aus dem Plan abgeleitet statt festgeschrieben: Kommt eine
    bundesweite Anfrage hinzu, rueckt cp_02__berlin nach hinten. Eine feste
    Zahl liesse den Test dann umkippen, obwohl an der Suche nichts kaputt ist.
    """
    planned = build_queries(load_config(projekt), phase=1)
    bis = next(i for i, q in enumerate(planned, 1) if q.query_id == "cp_02__berlin")

    ergebnis = runner.invoke(cli.app, ["search", "--limit", str(bis)])

    assert ergebnis.exit_code == 0
    assert (projekt / "data" / "groups.sqlite").exists()
    assert "Gespeichert:" in ergebnis.stdout


def test_anfrageprotokoll_ist_abrufbar(projekt: Path) -> None:
    runner.invoke(cli.app, ["search", "--limit", "1"])
    ergebnis = runner.invoke(cli.app, ["search-log"])

    assert ergebnis.exit_code == 0
    assert "fixture" in ergebnis.stdout


def test_leeres_protokoll_meldet_sich_verstaendlich(projekt: Path) -> None:
    ergebnis = runner.invoke(cli.app, ["search-log"])
    assert ergebnis.exit_code == 0
    assert "Noch keine Anfrage" in ergebnis.stdout
