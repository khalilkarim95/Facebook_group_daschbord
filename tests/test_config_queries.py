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


def test_passungs_gewichte_ergeben_100(config) -> None:
    """Die Passung ergibt 100 - die Resonanz kommt getrennt obendrauf.

    Zwei Bloecke mit verschiedener Aufgabe: Zielgruppe, Stadt, Kategorie, Name
    und Groesse beurteilen, ob die Gruppe zu uns gehoert; die
    ``resonanz_*``-Gewichte, was sie tatsaechlich gebracht hat. Beide zusammen
    auf 100 zu pruefen hiesse, das Einschalten der Resonanz als Fehler zu
    melden - oder umgekehrt die Passung zu verwaessern, sobald ein
    Resonanz-Gewicht steigt.
    """
    weights = config.get("scoring", "weights")
    passung = {k: float(v) for k, v in weights.items() if not k.startswith("resonanz_")}
    assert sum(passung.values()) == 100.0


def test_resonanz_gewichte_sind_abschaltbar(config) -> None:
    """Sie duerfen fehlen oder 0 sein - dann ist die Bewertung die bisherige.

    Geprueft wird die Rechnung, nicht der aktuelle Ausbaustand: Ob der Nutzer
    die Resonanz eingeschaltet hat, ist seine Entscheidung und kein Testfall.
    """
    weights = config.get("scoring", "weights")
    resonanz = {k: float(v) for k, v in weights.items() if k.startswith("resonanz_")}
    assert all(v >= 0 for v in resonanz.values())


def test_anzahl_geplanter_anfragen(config) -> None:
    """Bundesweite Anfragen + ein Muster je freigeschalteter Stadt.

    Auch die Zahl der Muster kommt aus der Konfiguration, nicht aus dem Test:
    Geprueft wird die Rechnung - jedes Muster kostet so viele Credits, wie es
    freigeschaltete Staedte gibt -, nicht ein bestimmter Ausbaustand. Sonst
    braeche jede neue Formulierung einen Test, ohne dass etwas kaputt waere.
    """
    staedte = len(config.cities_for_phase(1))
    bundesweit = len(config.queries["nationwide"])
    muster = len(config.queries["city_patterns"])
    planned = build_queries(config, phase=1)

    assert len([q for q in planned if q.scope == "nationwide"]) == bundesweit
    assert len([q for q in planned if q.scope == "city"]) == muster * staedte
    assert len(planned) == bundesweit + muster * staedte


def test_bundesweite_anfragen_stehen_in_der_konfiguration(config) -> None:
    """Die vereinbarten Anfragen kommen aus der YAML, nicht aus dem Code.

    Verglichen wird gegen die Datei selbst statt gegen eine Liste im Test -
    eine zweite Liste hier waere eine zweite Wahrheit ueber dieselben Anfragen.
    """
    aus_datei = {eintrag["text"] for eintrag in config.queries["nationwide"]}
    gebaut = {q.text for q in build_queries(config, phase=1) if q.scope == "nationwide"}

    assert gebaut == aus_datei
    assert "Syrer Deutschland" in gebaut  # Grundanfrage bleibt bestehen
    assert "السوريين في ألمانيا" in gebaut


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


def test_deutsch_arabisch_und_umschrift_vertreten(config) -> None:
    """Die drei Schreibweisen des Zielmarkts bleiben abgedeckt.

    Teilmenge statt Gleichheit: Eine weitere Sprache (etwa englische
    Gruppennamen) ist eine Erweiterung, kein Fehler. Faellt dagegen eine der
    drei weg, ist ein ganzer Teil des Marktes unsichtbar.
    """
    sprachen = {q.lang for q in build_queries(config, phase=1)}
    assert {"de", "ar", "translit"} <= sprachen


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
