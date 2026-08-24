"""KI-Anbieter fuer Beitragsvorschlaege.

    AI Provider
       |
       +-- Ollama (Standard, lokal, kostenlos)
       |
       +-- Anthropic (optional, kostet je Anfrage)

Der Aufbau trennt drei Dinge, die frueher in einer Datei standen:

``basis``      die Fachlichkeit - Prompt, Pruefung, Entwuerfe. Kennt keinen
               Anbieter und aendert sich nicht, wenn einer dazukommt.
``ollama``     der lokale Standardweg, ueber das vorhandene ``httpx``.
``anthropic``  der optionale Weg. Fehlt das Paket oder der Schluessel, wirkt
               sich das auf nichts anderes aus.
``factory``    welcher davon - ``AI_PROVIDER`` vor ``config/settings.yaml``.

Alles, was frueher aus ``fbgroups.marketing.ki`` kam, kommt weiter von hier:
Der Wechsel vom Modul zum Paket ist fuer die Aufrufer unsichtbar.
"""

from fbgroups.marketing.ki.basis import (
    DEFAULT_VARIANTEN,
    PLATZHALTER,
    REPARATUR_PROMPT,
    SYSTEM_PROMPT,
    Auftrag,
    KINichtVerfuegbar,
    Modell,
    Status,
    UngueltigerVorschlag,
    Variante,
    Vorschlaege,
    auftrag_aus_gruppe,
    erzeuge_entwuerfe,
    pruefe_platzhalter,
)
from fbgroups.marketing.ki.factory import (
    ANBIETER,
    ANTHROPIC,
    DEFAULT_ANBIETER,
    OLLAMA,
    baue_modell,
    gewaehlter_anbieter,
    status,
    teste,
)
from fbgroups.marketing.ki.ollama import OllamaModell, base_url, modellname

__all__ = [
    "ANBIETER",
    "ANTHROPIC",
    "DEFAULT_ANBIETER",
    "DEFAULT_VARIANTEN",
    "OLLAMA",
    "PLATZHALTER",
    "REPARATUR_PROMPT",
    "SYSTEM_PROMPT",
    "Auftrag",
    "KINichtVerfuegbar",
    "Modell",
    "OllamaModell",
    "Status",
    "UngueltigerVorschlag",
    "Variante",
    "Vorschlaege",
    "auftrag_aus_gruppe",
    "base_url",
    "baue_modell",
    "erzeuge_entwuerfe",
    "gewaehlter_anbieter",
    "modellname",
    "pruefe_platzhalter",
    "status",
    "teste",
]
