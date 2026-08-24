"""Text bereitlegen, Gruppe oeffnen, den Menschen entscheiden lassen.

Der Adapter, der dem Projekt seit jeher zugrunde liegt und der der
Rueckfallweg bleibt, gleich was sonst noch dazukommt: Das Programm sieht
Facebook nie, es weiss nur, was der Mensch ihm sagt. ``webbrowser.open`` ist
dabei keine Automatisierung - es passiert dasselbe wie beim Anklicken eines
Links.

Die Wartezeit des Arbeiters ist hier nebenbei sinnvoll: Sie ist ungefaehr die
Zeit, die ein Mensch fuer einen Beitrag braucht.
"""

from __future__ import annotations

from collections.abc import Callable

from fbgroups.marketing.beitrag import in_zwischenablage, oeffne_im_browser
from fbgroups.marketing.models import CampaignGroup
from fbgroups.marketing.veroeffentlicher.basis import Ergebnis, register_veroeffentlicher
from fbgroups.models import Group

BESCHREIBUNG = "Text in der Zwischenablage, Gruppe im Browser, Absenden von Hand."


@register_veroeffentlicher("assistiert", BESCHREIBUNG)
class AssistierterVeroeffentlicher:
    """Bereitet vor und fragt nach dem Ausgang."""

    name = "assistiert"
    beschreibung = BESCHREIBUNG

    def __init__(
        self,
        *,
        frage: Callable[[str], str] | None = None,
        melde: Callable[[str], None] | None = None,
        browser: bool = True,
        zwischenablage: bool = True,
        **_: object,
    ) -> None:
        self._frage = frage or input
        self._melde = melde or (lambda _: None)
        self._browser = browser
        self._zwischenablage = zwischenablage

    def veroeffentliche(
        self, *, gruppe: Group | None, text: str, link: CampaignGroup
    ) -> Ergebnis:
        if self._zwischenablage:
            in_zwischenablage(text)
        if self._browser and gruppe is not None:
            oeffne_im_browser(gruppe.url_canonical)

        name = (gruppe.name if gruppe else "") or link.group_id
        self._melde(f"{name}  [{link.tracking_code}]")

        antwort = (
            self._frage("[Enter] veroeffentlicht - f Fehler - u uebersprungen - q Schluss: ")
            .strip()
            .lower()
        )

        if antwort in ("q", "quit", "ende"):
            return Ergebnis(erfolg=False, abbrechen=True)
        if antwort in ("u", "ueberspringen", "skip"):
            return Ergebnis(erfolg=False, uebersprungen=True)
        if antwort in ("f", "fehler"):
            grund = self._frage("Grund: ").strip()
            return Ergebnis(erfolg=False, fehler=grund or "ohne Angabe")
        return Ergebnis(erfolg=True)
