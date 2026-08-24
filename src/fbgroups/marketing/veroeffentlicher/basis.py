"""Der Vertrag fuer das Absetzen eines Beitrags - und sonst nichts.

Dieses Modul kennt **keine** Umsetzung. Genau wie ``providers/base.py`` in der
Suchschicht steht hier nur, was ein Adapter koennen muss; wer einen baut,
schreibt eine Klasse, haengt ``@register_veroeffentlicher(...)`` daran und ist
fertig. Kein Modul ausserhalb dieses Pakets darf einen konkreten Adapter
importieren - dafuer gibt es einen Test.

Der Grund fuer die Trennung ist derselbe wie ueberall in diesem Projekt: Der
Teil, der ueber Tageslimit, Reihenfolge und Abbruch entscheidet
(``marketing/worker.py``), muss ohne Netz, ohne Browser und ohne Facebook
pruefbar sein. Ein Regelwerk, das nebenbei einen Browser steuert, laesst sich
nicht mehr fuer sich testen - und genau diese Regeln verhindern, dass 310
Beitraege an einem Nachmittag hinausgehen.

**Ein Adapter bekommt nie eine Anmeldung.** Weder Passwort noch Cookie noch
Token wird ihm durchgereicht, und ``PostVersuch`` hat fuer beides kein Feld -
``browser_session`` ist ein *Name* wie ``standard``. Wie ein Adapter zu einer
angemeldeten Sitzung kommt, ist seine Sache und bleibt ausserhalb dieses
Projekts; hier wird nichts gespeichert, was eine Anmeldung waere.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from fbgroups.marketing.models import CampaignGroup
from fbgroups.models import Group


class UnbekannterVeroeffentlicher(KeyError):
    """Diesen Adapter gibt es nicht. Die Meldung nennt die vorhandenen mit."""


@dataclass(frozen=True)
class Ergebnis:
    """Was bei einem einzelnen Beitrag herauskam.

    ``abbrechen`` ist der Weg des Adapters, den **ganzen** Lauf zu beenden -
    etwa weil der Mensch im assistierten Betrieb aufhoeren will oder weil die
    Gegenseite eine Sperre meldet, die den naechsten Versuch sinnlos machte.
    Ein Adapter, der das nicht koennte, liefe nach einem Fehler weiter gegen
    die Wand; einer, der bei jedem Fehler abbraeche, verloere den Rest des
    Tages wegen einer einzelnen zickigen Gruppe. Deshalb ist es ein eigenes
    Feld und nicht aus ``erfolg`` abgeleitet.

    ``uebersprungen`` ist ebenso wenig ein Fehlschlag: "passt nicht" ist ein
    Urteil ueber die Gruppe. Es wandert nach ``cancelled``, nicht nach
    ``failed`` - ``campaign retry`` holt eine bewusste Entscheidung damit nicht
    versehentlich zurueck.
    """

    erfolg: bool
    fehler: str = ""
    post_url: str = ""
    abbrechen: bool = False
    uebersprungen: bool = False


@runtime_checkable
class Veroeffentlicher(Protocol):
    """Was ein Adapter koennen muss - mehr nicht.

    Ein Protokoll statt einer Basisklasse, aus demselben Grund wie bei
    ``ki.basis.Modell``: Der pruefenswerte Teil des Ablaufs muss ohne Browser
    laufen, und die Tests setzen deshalb eine eigene Fassung ein.

    ``veroeffentliche`` **wirft nicht**. Fehler stehen im ``Ergebnis``; ein
    Adapter, der doch wirft, wird vom Arbeiter aufgefangen (sonst stuende der
    Job fuer immer auf ``processing``), aber verlassen sollte sich darauf
    niemand - der Grund im Protokoll ist dann nur ein Ausnahmename.
    """

    #: Fuer die Anzeige und fuers Protokoll, z. B. "assistiert".
    name: str

    #: Ein Satz fuer die Hilfe der Kommandozeile.
    beschreibung: str

    def veroeffentliche(
        self, *, gruppe: Group | None, text: str, link: CampaignGroup
    ) -> Ergebnis:
        """Setzt **einen** Beitrag ab."""
        ...


# Name -> Bauer. Ein Bauer statt einer Klasse, weil ein Adapter beim Erzeugen
# etwas brauchen kann (der assistierte etwa die Ein- und Ausgabe der Konsole).
_REGISTRY: dict[str, Callable[..., Veroeffentlicher]] = {}
_BESCHREIBUNGEN: dict[str, str] = {}


def register_veroeffentlicher(
    name: str, beschreibung: str = ""
) -> Callable[[Callable[..., Veroeffentlicher]], Callable[..., Veroeffentlicher]]:
    """Traegt einen Adapter unter seinem Namen ein.

    Ein neuer Adapter besteht damit aus einer Klasse und einer Zeile darueber -
    weder Arbeiter noch Kommandozeile noch Uebersicht muessen davon wissen.
    """

    def eintragen(bauer: Callable[..., Veroeffentlicher]) -> Callable[..., Veroeffentlicher]:
        _REGISTRY[name] = bauer
        _BESCHREIBUNGEN[name] = beschreibung
        return bauer

    return eintragen


def verfuegbare() -> dict[str, str]:
    """Alle eingetragenen Adapter: Name -> Beschreibung."""
    return dict(sorted(_BESCHREIBUNGEN.items()))


def baue_veroeffentlicher(name: str, **kwargs: object) -> Veroeffentlicher:
    """Baut den Adapter zu diesem Namen.

    Wirft ``UnbekannterVeroeffentlicher`` mit den vorhandenen Namen in der
    Meldung: Bei einem Tippfehler ist die naechste Frage immer sofort, welche
    es denn gibt.
    """
    bauer = _REGISTRY.get(name)
    if bauer is None:
        vorhanden = ", ".join(sorted(_REGISTRY)) or "keine"
        raise UnbekannterVeroeffentlicher(
            f"Unbekannter Veroeffentlicher: {name}. Vorhanden: {vorhanden}"
        )
    return bauer(**kwargs)
