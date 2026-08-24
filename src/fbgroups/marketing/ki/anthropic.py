"""Claude ueber die Anthropic-API - der optionale Anbieter.

**Wird nicht gebraucht.** Der Standard ist Ollama; dieses Modul laeuft nur,
wenn jemand ``AI_PROVIDER=anthropic`` setzt *und* das Paket installiert *und*
einen Schluessel hinterlegt hat. Fehlt eines davon, funktioniert das gesamte
uebrige System unveraendert - es gibt keinen Weg, auf dem ein fehlender
Anthropic-Schluessel etwas anderes blockiert als diesen einen Anbieter.

Der Grund, warum es ihn ueberhaupt noch gibt: Ein grosses Modell schreibt
besseres Arabisch als ein kleines lokales, und wer fuer die wichtigsten
dreissig Gruppen einmal die bessere Fassung will, soll sie holen koennen,
ohne dass dafuer Code geaendert werden muss. Jede Anfrage kostet dann Geld -
deshalb ist es eine Entscheidung und keine Voreinstellung.

Das Paket ``anthropic`` ist eine **optionale** Abhaengigkeit:

    pip install -e ".[ki]"
"""

from __future__ import annotations

import os
from typing import Any

from fbgroups.config import AppConfig
from fbgroups.marketing.ki.basis import (
    KINichtVerfuegbar,
    Status,
    UngueltigerVorschlag,
    Vorschlaege,
)

DEFAULT_MODELL = "claude-opus-5"
DEFAULT_MAX_TOKENS = 8000


def modellname(config: AppConfig) -> str:
    aus_umgebung = os.environ.get("ANTHROPIC_MODEL", "").strip()
    if aus_umgebung:
        return aus_umgebung
    aus_datei = str(
        config.get("marketing", "posting", "ki", "anthropic", "modell", default="")
    ).strip()
    return aus_datei or DEFAULT_MODELL


class ClaudeModell:
    """Der Aufruf an die Anthropic-API. Duenn - hier steht keine Fachlichkeit.

    Kein ``temperature``: Claude Opus 5 nimmt den Parameter nicht mehr an (die
    Anfrage schluege mit 400 fehl). Die Verschiedenheit der Fassungen kommt
    deshalb aus dem Auftrag - das Modell schreibt sie in *einem* Aufruf und
    sieht dabei, was es schon geschrieben hat. Das ist auch der Unterschied
    zum Ollama-Weg, der je Fassung einmal fragt: Ein grosses Modell haelt das
    Schema ein, ein kleines nicht.
    """

    def __init__(
        self,
        config: AppConfig | None = None,
        *,
        api_key: str | None = None,
        modell: str = "",
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        if config is not None:
            modell = modell or modellname(config)
            max_tokens = int(
                config.get(
                    "marketing", "posting", "ki", "anthropic", "max_tokens",
                    default=max_tokens,
                )
            )
        self.name = modell or DEFAULT_MODELL
        self.max_tokens = max_tokens

        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - haengt an der Installation
            raise KINichtVerfuegbar(
                "Anthropic ist nicht installiert - fuer die lokale KI wird es auch "
                'nicht gebraucht.\n'
                "Entweder AI_PROVIDER=ollama setzen (Standard, kostenlos),\n"
                'oder installieren mit:  pip install -e ".[ki]"'
            ) from exc

        schluessel = api_key or os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not schluessel:
            raise KINichtVerfuegbar(
                "ANTHROPIC_API_KEY ist nicht gesetzt. Fuer die lokale KI wird kein "
                "Schluessel gebraucht - AI_PROVIDER=ollama ist die Voreinstellung.\n"
                "Wer Anthropic wirklich will: Der Schluessel gehoert in die "
                ".env-Datei (per .gitignore ausgeschlossen), nie ins Repository."
            )
        self._client: Any = anthropic.Anthropic(api_key=schluessel)

    def status(self) -> Status:
        """Eingerichtet? Es wird ausdruecklich **nichts** abgerufen.

        Ein Statusabruf, der eine Anfrage stellt, kostete bei jedem Aufruf der
        Uebersicht Geld. Gemeldet wird deshalb nur, ob Paket und Schluessel da
        sind - mehr laesst sich ohne Bezahlung nicht wissen, und mehr braucht
        die Anzeige auch nicht.
        """
        return Status(
            anbieter="anthropic",
            erreichbar=True,
            modell=self.name,
            adresse="api.anthropic.com",
            meldung="Eingerichtet. Jede Erzeugung kostet - der Status prueft "
                    "deshalb nur die Einrichtung, nicht die Verbindung.",
        )

    def erzeuge(self, system: str, auftrag: str, *, varianten: int) -> Vorschlaege:
        antwort = self._client.messages.parse(
            model=self.name,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": auftrag}],
            output_format=Vorschlaege,
        )
        ergebnis = antwort.parsed_output
        if ergebnis is None:
            raise UngueltigerVorschlag(
                f"Das Modell hat nichts Verwertbares geliefert (stop_reason: "
                f"{getattr(antwort, 'stop_reason', 'unbekannt')})."
            )
        return ergebnis

    def teste(self) -> tuple[bool, str]:
        """Eine sehr kurze echte Anfrage - kostet ein paar Cent."""
        try:
            antwort = self._client.messages.create(
                model=self.name,
                max_tokens=200,
                system="Antworte knapp und nur mit dem Verlangten.",
                messages=[
                    {"role": "user", "content": "Schreibe einen kurzen Testsatz auf Arabisch."}
                ],
            )
        except Exception as exc:  # noqa: BLE001 - jeder Fehler ist hier dieselbe Auskunft
            return False, f"{type(exc).__name__}: {exc}"
        text = "".join(
            block.text for block in antwort.content if getattr(block, "type", "") == "text"
        )
        return bool(text), text or "Keine Textantwort erhalten."
