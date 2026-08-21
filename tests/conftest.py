from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from fbgroups.config import AppConfig, load_config

# Geheimnisse und Adressen, die ``config.load_config`` aus ``.env`` in die
# Prozessumgebung hebt (``load_dotenv``). Fuer den Betrieb ist das richtig; im
# Test macht es das Ergebnis davon abhaengig, was auf diesem Rechner in ``.env``
# steht. Wer einen ``EVENTS_TOKEN`` eingetragen hat - auf dem Arbeitsrechner
# der Normalfall -, sah neun Tests scheitern, die ausdruecklich den Fall "kein
# Schluessel gesetzt" pruefen. Umgekehrt liefe auf einem Rechner ohne ``.env``
# nie ein Test durch den Fall "Schluessel gesetzt", ohne dass es auffiele.
UMGEBUNG_AUS_ENV = ("EVENTS_TOKEN", "UEBERSICHT_TOKEN", "APP_BASE_URL")


@pytest.fixture(autouse=True)
def _ohne_env_datei(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setzt die Werte aus ``.env`` fuer jeden Test auf leer.

    Leer statt geloescht: ``load_dotenv`` laeuft mit ``override=False`` und
    laesst einen bereits vorhandenen Schluessel in Ruhe - auch einen leeren.
    Geloescht wuerde er beim naechsten ``load_config`` wieder eingetragen.

    Ein Test, der einen Schluessel braucht, setzt ihn selbst; das ist dann eine
    Angabe im Test und keine Eigenschaft des Rechners.
    """
    for name in UMGEBUNG_AUS_ENV:
        monkeypatch.setenv(name, "")


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
