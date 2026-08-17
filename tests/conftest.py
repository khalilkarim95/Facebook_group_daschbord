from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from fbgroups.config import AppConfig, load_config


@pytest.fixture(scope="session")
def config() -> AppConfig:
    """Echte Projektkonfiguration - die Tests pruefen auch deren Inhalt."""
    return load_config()


@pytest.fixture(scope="session")
def config_mit_mitgliederzahl(config: AppConfig) -> AppConfig:
    """Projektkonfiguration mit eingeschalteter Mitgliederzahl.

    Im Projekt steht ``member_count: 0``: Die Zahl ist ohne Zugriff auf
    facebook.com nicht zu beschaffen und fehlte damit ausnahmslos jeder Gruppe.
    Der Mechanismus dahinter bleibt aber vollstaendig in Kraft - wer die Zahl
    von Hand pflegt, schaltet ihn allein ueber das Gewicht wieder ein. Genau
    diesen Fall pruefen die Tests, die diese Fixture nutzen; sie duerfen nicht
    davon abhaengen, wie das Projekt den Schalter gerade stehen hat.

    Die Werte entsprechen der Gewichtung vor dem Abschalten.
    """
    settings = copy.deepcopy(config.settings)
    settings.setdefault("scoring", {})["weights"] = {
        "member_count": 45,
        "audience_match": 25,
        "city_match": 15,
        "category_match": 8,
        "name_quality": 7,
    }
    return replace(config, settings=settings)
