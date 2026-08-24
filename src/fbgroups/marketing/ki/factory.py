"""Welcher KI-Anbieter benutzt wird - eine Entscheidung, eine Stelle.

Die Reihenfolge ist ueberall dieselbe wie bei ``APP_BASE_URL``:
Umgebungsvariable (``AI_PROVIDER``) vor ``config/settings.yaml``. Damit laesst
sich der Anbieter wechseln, ohne eine Datei im Repository zu aendern - und die
Wahl steht nirgends fest im Programm.

**Ollama ist die Voreinstellung**, und zwar ausdruecklich: Es laeuft auf dem
eigenen Rechner, kostet nichts je Anfrage, und die Angaben ueber die Gruppen
verlassen den Rechner nicht. Anthropic ist der Sonderfall, den jemand
absichtlich einschaltet.

**Es wird nie stillschweigend gewechselt.** Ein Ausweichen von Ollama auf
Anthropic, wenn der lokale Dienst gerade nicht laeuft, waere bequem und
falsch: Es verwandelte einen abgeschalteten Rechner in eine Rechnung. Wer
Anthropic will, sagt es - dieselbe Ueberlegung wie bei ``fallback_chain`` in
der Suchschicht, die aus genau diesem Grund leer ist.
"""

from __future__ import annotations

import os
import time

from fbgroups.config import AppConfig
from fbgroups.marketing.ki.basis import KINichtVerfuegbar, Modell, Status
from fbgroups.marketing.ki.ollama import OllamaModell

OLLAMA = "ollama"
ANTHROPIC = "anthropic"
ANBIETER: tuple[str, ...] = (OLLAMA, ANTHROPIC)
DEFAULT_ANBIETER = OLLAMA


def gewaehlter_anbieter(config: AppConfig) -> str:
    """Welcher Anbieter eingestellt ist - Umgebung vor Datei.

    Ein unbekannter Wert faellt auf Ollama zurueck, statt den Lauf abzubrechen:
    Ein Tippfehler in einer Einstellung soll nicht dazu fuehren, dass jemand
    unversehens bei einem kostenpflichtigen Dienst landet, und die kostenlose
    Voreinstellung ist die harmlose Richtung.
    """
    wert = os.environ.get("AI_PROVIDER", "").strip().lower()
    if not wert:
        wert = str(config.get("marketing", "posting", "ki", "anbieter", default="")).strip().lower()
    return wert if wert in ANBIETER else DEFAULT_ANBIETER


def baue_modell(config: AppConfig, anbieter: str = "") -> Modell:
    """Liefert den eingestellten Anbieter - oder sagt, was fehlt.

    Wirft ``KINichtVerfuegbar`` mit einer Meldung, die den naechsten Schritt
    nennt. Der Aufrufer faengt sie und gibt sie aus; nichts anderes im System
    haengt daran.
    """
    name = (anbieter or gewaehlter_anbieter(config)).lower()
    if name == ANTHROPIC:
        # Erst hier importiert: Das Paket ist optional, und ein Import auf
        # Modulebene machte die gesamte Kommandozeile davon abhaengig.
        from fbgroups.marketing.ki.anthropic import ClaudeModell

        return ClaudeModell(config)
    if name != OLLAMA:
        raise KINichtVerfuegbar(
            f"Unbekannter KI-Anbieter: {name}. Moeglich: {', '.join(ANBIETER)}"
        )
    return OllamaModell(config)


# Kurzlebiger Zwischenspeicher fuer den Statusabruf.
#
# Die Uebersicht fragt ihn bei jedem Seitenaufbau. Laeuft Ollama nicht, kostet
# jede Frage die volle Zeitgrenze - und ein Mensch, der die Seite dreimal
# neu laedt, wartet dreimal. Zehn Sekunden sind kurz genug, dass ein gerade
# gestartetes Ollama sofort auffaellt, und lang genug, dass Neuladen nichts
# kostet. Der Zwischenspeicher haelt bewusst nur den *Stand*, nie ein
# Ergebnis: Erzeugt wird nie etwas hier.
_STATUS_CACHE: dict[str, tuple[float, Status]] = {}
STATUS_CACHE_SEKUNDEN = 10.0


def status(config: AppConfig, anbieter: str = "", *, frisch: bool = False) -> Status:
    """Stand des eingestellten Anbieters - ohne dass etwas erzeugt wird.

    Wirft nie. Die Uebersicht ruft das bei jedem Seitenaufruf: Ein nicht
    laufender Ollama darf kein Grund sein, dass die Seite nicht erscheint. Das
    Marketing-Werkzeug funktioniert ohne KI vollstaendig weiter - sie ist ein
    Aufsatz, keine Voraussetzung.

    ``frisch=True`` umgeht den Zwischenspeicher - fuer ``fbgroups ki status``,
    wo jemand ausdruecklich nachsieht und die Antwort von eben nicht will.
    """
    name = (anbieter or gewaehlter_anbieter(config)).lower()

    if not frisch:
        gemerkt = _STATUS_CACHE.get(name)
        if gemerkt is not None and (time.monotonic() - gemerkt[0]) < STATUS_CACHE_SEKUNDEN:
            return gemerkt[1]

    stand = _ermittle(config, name)
    _STATUS_CACHE[name] = (time.monotonic(), stand)
    return stand


def _ermittle(config: AppConfig, name: str) -> Status:
    try:
        return baue_modell(config, name).status()
    except KINichtVerfuegbar as exc:
        return Status(anbieter=name, erreichbar=False, meldung=str(exc))
    except Exception as exc:  # noqa: BLE001 - die Uebersicht darf an nichts sterben
        return Status(
            anbieter=name,
            erreichbar=False,
            meldung=f"Unerwarteter Fehler beim Statusabruf: {type(exc).__name__}: {exc}",
        )


def teste(config: AppConfig, anbieter: str = "") -> tuple[bool, str]:
    """Eine sehr kurze echte Anfrage. Returns: (geklappt, Text oder Grund).

    Anders als ``status`` wird hier wirklich etwas erzeugt - deshalb steht es
    hinter einem eigenen Aufruf und laeuft nie von selbst.
    """
    try:
        modell = baue_modell(config, anbieter)
    except KINichtVerfuegbar as exc:
        return False, str(exc)

    pruefer = getattr(modell, "teste", None)
    if pruefer is None:
        return False, f"{type(modell).__name__} kennt keinen Selbsttest."
    ergebnis: tuple[bool, str] = pruefer()
    return ergebnis
