from __future__ import annotations

import pytest

from fbgroups.config import AppConfig, load_config


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

