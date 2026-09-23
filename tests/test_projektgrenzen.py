"""Die harten Projektgrenzen, am Quelltext festgehalten.

Automatisches Posten und Kommentieren ist erlaubt - aber sichtbar und mit
einer Sitzung, die ein Mensch von Hand angelegt hat (``auth login``). Kein
stiller Login, keine Umgehung von Sperren: kein unsichtbarer Browser, kein
nachgeahmter Browser, kein Proxy, keine uebernommenen Cookies.

Die beiden Tests, die das frueher hielten, sind mit der Suchschicht am
20.09.2026 verschwunden; ohne Test haelt eine Grenze nur, solange sich
jemand an sie erinnert.
"""

from __future__ import annotations

from pathlib import Path

import pytest

AUTOMATION = Path("src/fbgroups/automation")


def _quelltexte(ordner: Path) -> dict[Path, str]:
    return {p: p.read_text(encoding="utf-8") for p in ordner.rglob("*.py")}


def test_der_browser_laeuft_immer_sichtbar() -> None:
    """Kein Aufruf startet ihn unsichtbar - und die Vorgabe ist sichtbar."""
    for datei, text in _quelltexte(Path("src/fbgroups")).items():
        assert "headless=True" not in text, datei
    browser = (AUTOMATION / "browser.py").read_text(encoding="utf-8")
    assert "headless: bool = False" in browser


@pytest.mark.parametrize(
    "spur",
    [
        "user_agent=",  # nachgeahmter Browser
        "proxy=",  # Proxywechsel
        "add_cookies",  # uebernommene Sitzung
        "storage_state=",  # eingespielte Sitzung
        "type=password",  # Anmeldung durch das Programm
        "[name=pass",
    ],
)
def test_keine_umgehung_und_kein_programmierter_login(spur: str) -> None:
    for datei, text in _quelltexte(AUTOMATION).items():
        assert spur not in text, (datei, spur)
