from __future__ import annotations

from fbgroups.providers import ProviderState, SearchProvider
from fbgroups.query.builder import build_queries, max_results_per_query


def test_phase1_zielgruppen(config) -> None:
    assert [a.id for a in config.audiences_for_phase(1)] == ["arabs", "syrians"]


def test_phase1_staedte(config) -> None:
    """Die vier Startstaedte bleiben freigeschaltet, Phase 2 bleibt draussen.

    Bewusst keine feste Gesamtliste: Eine Ausweitung erfolgt allein ueber
    ``phase: 1`` und soll keinen Test brechen. Was der Test sichert, ist die
    Trennung selbst - dass ``cities_for_phase`` ueberhaupt filtert.
    """
    namen = {c.name_de for c in config.cities_for_phase(1)}

    assert {"Berlin", "Hamburg", "München", "Stuttgart"} <= namen
    assert namen < {c.name_de for c in config.cities.values()}


def test_scoring_gewichte_ergeben_100(config) -> None:
    weights = config.get("scoring", "weights")
    assert sum(float(v) for v in weights.values()) == 100.0


def test_anzahl_geplanter_anfragen(config) -> None:
    """7 bundesweit + 9 Muster je freigeschalteter Stadt.

    Die Zahl wird aus der Konfiguration abgeleitet statt festgeschrieben:
    Jede neue Stadt kostet 9 Credits, und diese Rechnung muss stimmen -
    nicht ein bestimmter Ausbaustand.
    """
    staedte = len(config.cities_for_phase(1))
    planned = build_queries(config, phase=1)

    assert len([q for q in planned if q.scope == "nationwide"]) == 7
    assert len([q for q in planned if q.scope == "city"]) == 9 * staedte
    assert len(planned) == 7 + 9 * staedte


def test_bundesweite_anfragen_stehen_in_der_konfiguration(config) -> None:
    """Die vereinbarten Anfragen kommen aus der YAML, nicht aus dem Code."""
    texte = {q.text for q in build_queries(config, phase=1) if q.scope == "nationwide"}
    assert texte == {
        "Syrer Deutschland",
        "Syrer in Deutschland",
        "سوريين في ألمانيا",
        "السوريين في ألمانيا",
        "Araber Deutschland",
        "Araber in Deutschland",
        "عرب في ألمانيا",
    }


def test_stadtplatzhalter_werden_ersetzt(config) -> None:
    planned = build_queries(config, phase=1)
    texte = {q.text for q in planned}

    assert "Syrer Berlin" in texte
    assert "Syrer in Berlin" in texte
    assert "سوريين برلين" in texte
    assert "سوريين في برلين" in texte
    assert "Suriyeen Berlin" in texte
    assert "عرب في هامبورغ" in texte
    # Kein Platzhalter darf uebrig bleiben
    assert not any("{" in text for text in texte)


def test_alle_drei_sprachen_vertreten(config) -> None:
    sprachen = {q.lang for q in build_queries(config, phase=1)}
    assert sprachen == {"de", "ar", "translit"}


def test_builder_ist_deterministisch(config) -> None:
    assert build_queries(config, 1) == build_queries(config, 1)


def test_ergebnisgrenze(config) -> None:
    """Hoechstens zehn Ergebnisse je Anfrage.

    Ab dem elften Ergebnis berechnet Serper zwei Credits statt einem - eine
    stillschweigende Verdopplung der Kosten des gesamten Laufs.
    """
    assert max_results_per_query(config) == 10


def test_registrierte_provider() -> None:
    from fbgroups.providers.base import available_providers

    assert set(available_providers()) == {"brave", "fixture", "serper"}
    assert isinstance(SearchProvider, type(SearchProvider))
    assert ProviderState.CLOSED_TO_NEW == "closed_to_new"


def test_kein_modul_ausserhalb_providers_kennt_einen_anbieter() -> None:
    """Architektur-Invariante: die Anwendung haengt an keinem Suchdienst.

    Nur ``providers/`` darf anbieterspezifische Module importieren. Waere das
    verletzt, liesse sich ein abgekuendigter Dienst nicht mehr folgenlos
    austauschen - genau das Risiko, das die Provider-Schicht abwenden soll.
    """
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "fbgroups"
    muster = re.compile(r"^\s*(from|import)\s+.*\b(brave|serper|google_cse)\b", re.MULTILINE)

    verstoesse = [
        py.relative_to(src).as_posix()
        for py in src.rglob("*.py")
        if py.parent.name != "providers" and muster.search(py.read_text(encoding="utf-8"))
    ]

    assert verstoesse == [], f"Anbieterspezifischer Import ausserhalb providers/: {verstoesse}"
