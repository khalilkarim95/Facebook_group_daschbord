from __future__ import annotations

from fbgroups.classify import classify_audience, classify_category, classify_city


def test_deutsche_zielgruppe(config) -> None:
    result = classify_audience("Syrer in Berlin", None, config)
    assert "syrians" in result.tags
    assert result.confidence == 1.0


def test_arabische_zielgruppe(config) -> None:
    result = classify_audience("مجموعة السوريين في برلين", None, config)
    assert "syrians" in result.tags


def test_transliterierte_zielgruppe(config) -> None:
    result = classify_audience("Suriyeen Hamburg", None, config)
    assert "syrians" in result.tags


def test_mehrere_tags_gleichzeitig(config) -> None:
    """Eine Gruppe darf mehrere Zielgruppen tragen."""
    result = classify_audience("Syrer und Araber in Deutschland", None, config)
    assert set(result.tags) == {"syrians", "arabs"}


def test_treffer_im_snippet_zaehlt_schwaecher(config) -> None:
    im_namen = classify_audience("Syrer Berlin", None, config)
    im_snippet = classify_audience("Community Treff", "Gruppe fuer Syrer", config)
    assert im_snippet.confidence < im_namen.confidence
    assert "syrians" in im_snippet.tags


def test_keine_zielgruppe(config) -> None:
    result = classify_audience("Flohmarkt Hannover", None, config)
    assert result.tags == []
    assert result.confidence == 0.0


def test_phase2_zielgruppe_erst_ab_phase2(config) -> None:
    """Phase 2 ist reine Konfiguration - in Phase 1 darf sie nicht greifen."""
    assert "lebanese" not in classify_audience("Libanesen in Berlin", None, config, phase=1).tags
    assert "lebanese" in classify_audience("Libanesen in Berlin", None, config, phase=2).tags


def test_stadt_deutsch(config) -> None:
    result = classify_city("Syrer in Hamburg", None, config)
    assert result.city == "Hamburg"
    assert result.bundesland == "Hamburg"
    assert result.confidence == 1.0


def test_stadt_arabisch(config) -> None:
    result = classify_city("سوريين في برلين", None, config)
    assert result.city == "Berlin"


def test_stadt_alias_muenchen(config) -> None:
    for variante in ["München", "Muenchen", "Munich", "ميونخ"]:
        result = classify_city(f"Araber in {variante}", None, config)
        assert result.city == "München", variante


def test_stadt_ausserhalb_phase1(config) -> None:
    """Eine noch nicht freigeschaltete Stadt wird nicht zugeordnet.

    Die Stadt kommt aus der Konfiguration, nicht aus dem Test: Wird Leipzig
    eines Tages freigeschaltet, prueft der Test die naechste Phase-2-Stadt
    statt fehlzuschlagen.
    """
    spaeter = next(
        c for c in config.cities.values() if c not in config.cities_for_phase(1)
    )
    name = f"Syrer in {spaeter.name_de}"

    assert classify_city(name, None, config, phase=1).city is None
    assert classify_city(name, None, config, phase=2).city == spaeter.name_de


def test_keine_stadt(config) -> None:
    result = classify_city("Syrer in Deutschland", None, config)
    assert result.city is None
    assert result.confidence == 0.0


def test_kategorie_jobs(config) -> None:
    assert classify_category("Jobs und Arbeit in Berlin", None, config).category == "jobs"


def test_kategorie_wohnen_arabisch(config) -> None:
    assert classify_category("شقق للايجار في برلين", None, config).category == "wohnen"


def test_kategorie_unbekannt(config) -> None:
    ergebnis = classify_category("xyz", None, config)
    assert ergebnis.category is None
    assert ergebnis.confidence == 0.0


def test_kategorie_im_namen_zaehlt_staerker_als_im_text(config) -> None:
    im_namen = classify_category("Jobs in Berlin", None, config)
    im_text = classify_category("Syrer in Berlin", "Hier gibt es Jobs und Arbeit", config)

    assert im_namen.category == im_text.category == "jobs"
    assert im_text.confidence < im_namen.confidence


def test_namenstreffer_schlaegt_mehrere_texttreffer(config) -> None:
    """Ein Beschreibungstext stammt oft aus einem einzelnen Beitrag.

    Er darf die Ausrichtung der Gruppe nicht ueberstimmen, auch nicht mit
    mehreren Stichworten.
    """
    ergebnis = classify_category(
        "Wohnungssuche Berlin",
        "Jobs, Arbeit, Minijob, Ausbildung und Praktikum",
        config,
    )
    assert ergebnis.category == "wohnen"
