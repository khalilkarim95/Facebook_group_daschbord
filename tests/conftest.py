from __future__ import annotations

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
def _kein_langer_schlaf(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein Test, der wirklich minutenlang schlafen will, scheitert - statt zu haengen.

    Die Automatik wartet im Betrieb auf Takt und Ruhezeit
    (``automatik._schlafe``, bis zu einer Viertelstunde). Ein Test, der dem
    Treiber kein ``warte=`` mitgibt und in eine Ruhezeit laeuft, schlief
    deshalb echt - am 23.09.2026 blieb die ganze Testfolge so stehen, ohne
    eine einzige Meldung. Kurze Pausen (der zweite Anlauf einer Buchung,
    drei Sekunden) bleiben erlaubt.
    """
    from fbgroups.marketing import automatik

    echt = automatik._schlafe

    def schlafe(sekunden: float) -> None:
        if sekunden > 10:
            raise AssertionError(
                f"Der Test wollte {sekunden:.0f} s schlafen - dem Treiber fehlt ein warte=."
            )
        echt(sekunden)

    monkeypatch.setattr(automatik, "_schlafe", schlafe)


@pytest.fixture(scope="session")
def config() -> AppConfig:
    """Echte Projektkonfiguration - die Tests pruefen auch deren Inhalt."""
    return load_config()

