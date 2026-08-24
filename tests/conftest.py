from __future__ import annotations

import copy
from collections.abc import Iterator
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


@pytest.fixture(autouse=True)
def _ohne_gemerkten_ki_stand() -> Iterator[None]:
    """Leert den Status-Zwischenspeicher der KI vor und nach jedem Test.

    ``ki.factory`` merkt sich den Stand eines Anbieters zehn Sekunden lang,
    damit die Uebersicht ihn nicht bei jedem Seitenaufbau neu erfragt.
    Geschluesselt ist er nach dem **Anbieternamen**, nicht nach der Adresse -
    im Betrieb richtig, im Test verhaengnisvoll: Ein Test, der bei laufendem
    Ollama die Uebersicht baut, hinterlaesst dort "erreichbar", und der
    naechste, der ausdruecklich den Fall "nichts laeuft" prueft, liest diesen
    Stand statt seinen eigenen. Das Ergebnis haengt dann an der Reihenfolge
    der Tests - ein Fehler, der beim Ausfuehren einer einzelnen Datei
    verschwindet und im vollen Lauf wiederkommt.
    """
    from fbgroups.marketing.ki import factory

    factory._STATUS_CACHE.clear()
    yield
    factory._STATUS_CACHE.clear()


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
