"""Lokales Modell ueber Ollama - der Standardanbieter.

Laeuft auf dem eigenen Rechner. Es entstehen keine Kosten je Anfrage, und die
Angaben ueber die Gruppen verlassen den Rechner nicht - beides sind Gruende,
warum dies die Voreinstellung ist und nicht die Ausweichloesung.

**Keine neue Abhaengigkeit.** Ollama spricht HTTP, und ``httpx`` ist seit
jeher Kernabhaengigkeit dieses Projekts. Ein eigenes Client-Paket dafuer waere
ein Paket mehr fuer zwei Aufrufe.

Zwei Entscheidungen, die man dem Code sonst nicht ansieht:

**Eine Fassung je Anfrage, als reiner Text.** Der Anthropic-Weg holt drei
Fassungen in *einem* Aufruf als JSON. Bei einem kleinen lokalen Modell ist das
der sichere Weg ins Verderben: Es haelt das Schema nicht ein, liefert
abgeschnittenes JSON oder schreibt die Erklaerung mit hinein, und dann ist
nicht eine Fassung unbrauchbar, sondern alle drei. Drei einfache Anfragen
kosten hier nichts ausser Zeit - der Rechner steht ohnehin daneben.

**Verschiedenheit ueber ``temperature``.** Anders als Claude Opus 5 nimmt
Ollama den Parameter an. Zusammen mit den bereits geschriebenen Fassungen im
Auftrag trennt das die Varianten zuverlaessig genug.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from fbgroups.config import AppConfig
from fbgroups.marketing.ki.basis import (
    KINichtVerfuegbar,
    Status,
    Variante,
    Vorschlaege,
)

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODELL = "qwen3.5:4b"

# Grosszuegig: Ein lokales Modell auf einer kleinen Karte braucht fuer einen
# arabischen Beitrag durchaus eine Minute, und ein Abbruch mittendrin sieht
# aus wie ein Fehler, obwohl nur die Geduld fehlte.
DEFAULT_TIMEOUT = 180.0
# Der Statusabruf dagegen muss schnell sein: Die Uebersicht fragt ihn bei
# jedem Aufruf, und eine Seite, die auf einen nicht laufenden Dienst wartet,
# ist unbenutzbar. Ollama laeuft auf demselben Rechner - antwortet es nicht
# innerhalb einer Sekunde, laeuft es nicht.
STATUS_TIMEOUT = 1.0

NICHT_ERREICHBAR = """\
Ollama ist unter {adresse} nicht erreichbar.

Bitte pruefe:
  1. Laeuft Ollama?          ollama serve      (oder das Symbol im Infobereich)
  2. Stimmt die Adresse?     OLLAMA_BASE_URL in .env  (aktuell: {adresse})
  3. Ist das Modell da?      ollama pull {modell}

Pruefen laesst sich das jederzeit mit:  fbgroups ki status
"""


def base_url(config: AppConfig) -> str:
    """Adresse des Dienstes.

    Reihenfolge wie bei ``APP_BASE_URL``: Umgebungsvariable vor
    ``config/settings.yaml``. So laesst sich dieselbe Konfiguration auf dem
    Arbeitsrechner und auf dem Server benutzen, ohne eine Datei zu aendern -
    und die Adresse steht nirgends fest im Programm.
    """
    aus_umgebung = os.environ.get("OLLAMA_BASE_URL", "").strip()
    if aus_umgebung:
        return aus_umgebung.rstrip("/")
    aus_datei = str(
        config.get("marketing", "posting", "ki", "ollama", "base_url", default="")
    ).strip()
    return (aus_datei or DEFAULT_BASE_URL).rstrip("/")


def modellname(config: AppConfig) -> str:
    """Welches Modell benutzt wird - Umgebung vor Datei, wie bei der Adresse."""
    aus_umgebung = os.environ.get("OLLAMA_MODEL", "").strip()
    if aus_umgebung:
        return aus_umgebung
    aus_datei = str(
        config.get("marketing", "posting", "ki", "ollama", "modell", default="")
    ).strip()
    return aus_datei or DEFAULT_MODELL


class OllamaModell:
    """Spricht mit einem lokal laufenden Ollama."""

    def __init__(
        self,
        config: AppConfig | None = None,
        *,
        adresse: str = "",
        modell: str = "",
        timeout: float = DEFAULT_TIMEOUT,
        temperature: float = 0.8,
    ) -> None:
        if config is not None:
            adresse = adresse or base_url(config)
            modell = modell or modellname(config)
        self.adresse = (adresse or DEFAULT_BASE_URL).rstrip("/")
        self.name = modell or DEFAULT_MODELL
        self.timeout = timeout
        self.temperature = temperature

    # -- Status ----------------------------------------------------------
    def status(self) -> Status:
        """Antwortet der Dienst, und liegt das Modell dort?

        Wirft nie. Die Uebersicht ruft das bei jedem Seitenaufruf, und ein
        nicht laufender Ollama darf kein Grund sein, dass die Seite nicht
        erscheint - das Marketing-Werkzeug funktioniert ohne KI vollstaendig
        weiter.
        """
        try:
            antwort = httpx.get(f"{self.adresse}/api/tags", timeout=STATUS_TIMEOUT)
            antwort.raise_for_status()
            daten = antwort.json()
        except (httpx.HTTPError, ValueError) as exc:
            return Status(
                anbieter="ollama",
                erreichbar=False,
                modell=self.name,
                adresse=self.adresse,
                meldung=self._nicht_erreichbar_text(exc),
            )

        modelle = [str(m.get("name", "")) for m in daten.get("models", []) if m.get("name")]
        stand = Status(
            anbieter="ollama",
            erreichbar=True,
            modell=self.name,
            adresse=self.adresse,
            verfuegbare_modelle=sorted(modelle),
        )
        if not stand.modell_vorhanden:
            # Der haeufigste Fehler nach der Einrichtung: Der Dienst laeuft,
            # aber das Modell wurde nie geholt. "Verbunden" allein waere hier
            # eine irrefuehrende Auskunft.
            stand.meldung = (
                f"Verbunden, aber das Modell '{self.name}' liegt nicht vor.\n"
                f"Holen mit:  ollama pull {self.name}"
            )
        return stand

    def _nicht_erreichbar_text(self, exc: Exception) -> str:
        return NICHT_ERREICHBAR.format(adresse=self.adresse, modell=self.name) + (
            f"\n[{type(exc).__name__}] {exc}" if str(exc) else ""
        )

    # -- Erzeugen ---------------------------------------------------------
    def erzeuge(self, system: str, auftrag: str, *, varianten: int) -> Vorschlaege:
        """Holt ``varianten`` Fassungen - eine Anfrage je Fassung.

        Die bereits erhaltenen Fassungen gehen in die naechste Anfrage ein.
        Ohne das schriebe ein Modell bei gleicher Eingabe dreimal nahezu
        dasselbe, und die Varianten waeren keine.
        """
        fassungen: list[Variante] = []
        for nummer in range(max(1, varianten)):
            zusatz = ""
            if fassungen:
                zusatz = "\n\nDiese Fassungen hast du schon geschrieben. Schreibe eine " \
                         "deutlich andere:\n" + "\n".join(
                             f"- {v.text[:300]}" for v in fassungen
                         )
            text = self._eine_anfrage(system, auftrag + zusatz)
            if text:
                fassungen.append(Variante(text=text, stil=f"Fassung {nummer + 1}"))

        if not fassungen:
            raise KINichtVerfuegbar(
                f"{self.name} hat auf {max(1, varianten)} Anfragen nichts geliefert. "
                f"Laeuft das Modell? Pruefen mit:  fbgroups ki status"
            )
        return Vorschlaege(varianten=fassungen)

    def _eine_anfrage(self, system: str, auftrag: str) -> str:
        """Ein Aufruf an ``/api/generate``. Returns: der Text, oder leer.

        ``stream: false`` - der Aufrufer will eine fertige Fassung, keine
        Zeichen einzeln. Und ``raw`` bleibt aus, damit Ollama die Vorlage des
        Modells anwendet; ohne sie antworten die meisten Modelle mit einer
        Fortsetzung des Prompts statt mit einem Beitrag.
        """
        rumpf: dict[str, Any] = {
            "model": self.name,
            "system": system,
            "prompt": auftrag,
            "stream": False,
            "options": {"temperature": self.temperature},
        }
        try:
            antwort = httpx.post(
                f"{self.adresse}/api/generate", json=rumpf, timeout=self.timeout
            )
        except httpx.HTTPError as exc:
            raise KINichtVerfuegbar(self._nicht_erreichbar_text(exc)) from exc

        if antwort.status_code == 404:
            # Ollama antwortet mit 404, wenn das Modell nicht vorliegt. Das ist
            # etwas ganz anderes als "Dienst laeuft nicht", und die Meldung
            # soll den Unterschied nennen statt beides zu vermengen.
            raise KINichtVerfuegbar(
                f"Ollama laeuft, aber das Modell '{self.name}' liegt nicht vor.\n"
                f"Holen mit:  ollama pull {self.name}"
            )
        try:
            antwort.raise_for_status()
            daten = antwort.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise KINichtVerfuegbar(
                f"Ollama antwortet unerwartet ({antwort.status_code}): {exc}"
            ) from exc

        return str(daten.get("response", "")).strip()

    # -- Kurzer Selbsttest -------------------------------------------------
    def teste(self) -> tuple[bool, str]:
        """Eine sehr kurze echte Anfrage. Returns: (geklappt, Text oder Grund).

        Bewusst eine winzige Aufgabe auf Arabisch: Sie beantwortet in einem
        Zug, ob der Dienst laeuft, ob das Modell da ist und ob es arabische
        Schrift ueberhaupt sauber ausgibt. Das letzte ist bei kleinen Modellen
        keine Selbstverstaendlichkeit und faellt sonst erst beim ersten
        richtigen Beitrag auf.
        """
        try:
            text = self._eine_anfrage(
                "Antworte knapp und nur mit dem Verlangten.",
                "Schreibe einen kurzen Testsatz auf Arabisch.",
            )
        except KINichtVerfuegbar as exc:
            return False, str(exc)
        if not text:
            return False, f"{self.name} hat nichts geantwortet."
        return True, text
