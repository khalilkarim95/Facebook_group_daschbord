"""Der Paar-Code einer Zuordnung - frueher der Tracking-Code.

Aufbau: ``FB-SYR-BER-001``

===========  ==================================================
``FB``       Kanal - hier immer Facebook
``SYR``      Zielgruppe der Gruppe (``audience_tags`` im Bestand)
``BER``      Stadt der Gruppe (``city`` im Bestand)
``001``      laufende Nummer innerhalb der Kampagne je Kuerzel-Paar
===========  ==================================================

**Seit dem 25.09.2026 geht der Code nirgends mehr hinaus.** Bis dahin stand
er - als Kurzcode verkleidet - in jedem Tracking-Link (``go.b-tarikak.de/r/
...``), und jeder Klick wurde unter ihm gezaehlt. Das Tracking ist entfernt;
der Code ist geblieben, weil er in der Datenbank die Zuordnung aus Kampagne
und Gruppe kennzeichnet (``campaign_groups.tracking_code``, eindeutig, nie
leer) und das Versuchsprotokoll auf ihn verweist. Daher auch der Spaltenname:
Migrationen sind additiv, umbenannt wird nichts.

Die Kuerzel entstehen aus den ersten drei Buchstaben dessen, was an der
Gruppe steht. Ein vergebener Code bleibt, wie er ist - auch eine frei
gewordene Nummer wird nicht wieder ausgegeben (``CodeAllocator``).
"""

from __future__ import annotations

import re

from fbgroups.config import AppConfig
from fbgroups.models import Group

DEFAULT_PREFIX = "FB"
DEFAULT_NUMBER_WIDTH = 3
FALLBACK_AUDIENCE = "GEN"      # keine Zielgruppe erkannt
FALLBACK_CITY = "DE"           # bundesweit, keine Stadt erkannt

_CODE_RE = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$")

# Zerlegt einen fertigen Code in Kuerzelteil und laufende Nummer.
_NUMMER_RE = re.compile(r"^(.*)-(\d+)$")


def _kuerzel(rohwert: str, laenge: int = 3) -> str:
    """Macht aus einer Kennung ein Kuerzel: ``muenchen`` -> ``MUE``."""
    sauber = re.sub(r"[^A-Za-z0-9]", "", rohwert or "")
    return sauber[:laenge].upper()


def audience_code(group: Group) -> str:
    """Kuerzel der Zielgruppe einer Gruppe."""
    if not group.audience_tags:
        return FALLBACK_AUDIENCE
    return _kuerzel(group.audience_tags[0])


def city_code(group: Group) -> str:
    """Kuerzel der Stadt einer Gruppe."""
    if not group.city:
        return FALLBACK_CITY
    return _kuerzel(group.city)


def code_prefix(group: Group, config: AppConfig) -> str:
    """Der Teil des Codes ohne laufende Nummer, z. B. ``FB-SYR-BER``."""
    kanal = str(config.get("marketing", "tracking", "prefix", default=DEFAULT_PREFIX))
    return "-".join([_kuerzel(kanal, 4), audience_code(group), city_code(group)])


def next_tracking_code(
    group: Group,
    config: AppConfig,
    vergeben: set[str],
) -> str:
    """Naechster freier Code fuer diese Gruppe innerhalb einer Kampagne.

    ``vergeben`` sind die bereits benutzten Codes derselben Kampagne. Die
    laufende Nummer zaehlt je Kuerzel-Paar hoch, damit ``FB-SYR-BER-002``
    tatsaechlich die zweite Berliner Syrer-Gruppe derselben Kampagne ist.
    """
    breite = int(config.get("marketing", "tracking", "number_width", default=DEFAULT_NUMBER_WIDTH))
    prefix = code_prefix(group, config)

    nummer = 1
    while True:
        kandidat = f"{prefix}-{nummer:0{breite}d}"
        if kandidat not in vergeben:
            return kandidat
        nummer += 1


class CodeAllocator:
    """Vergibt die Codes eines ganzen Laufs.

    ``next_tracking_code`` prueft fuer jede Gruppe von ``001`` an aufwaerts, ob
    eine Nummer frei ist. Bei acht Gruppen faellt das nicht auf; bei 1000
    Gruppen im selben Kuerzelpaar sind es eine halbe Million Vergleiche, und
    der Aufrufer muss ausserdem selbst mitzaehlen, was er gerade vergeben hat.
    Diese Klasse merkt sich je Kuerzelpaar die hoechste vergebene Nummer und
    zaehlt von dort weiter - der Aufwand haengt damit an der Zahl der neuen
    Codes, nicht am Quadrat der vorhandenen.

    Eine frei gewordene Nummer wird bewusst **nicht** wieder ausgegeben. Wird
    eine Zuordnung entfernt, bleibt ihr Code verbraucht: Er kann in einem
    veroeffentlichten Beitrag stehen, und ein zweites Mal vergeben wuerde er
    dort auf eine fremde Gruppe zeigen.
    """

    def __init__(self, config: AppConfig, vergeben: set[str]) -> None:
        self.config = config
        self.breite = int(
            config.get("marketing", "tracking", "number_width", default=DEFAULT_NUMBER_WIDTH)
        )
        self._vergeben = set(vergeben)
        self._hoechste: dict[str, int] = {}
        for code in self._vergeben:
            treffer = _NUMMER_RE.match(code)
            if treffer is None:
                continue
            prefix, nummer = treffer.group(1), int(treffer.group(2))
            if nummer > self._hoechste.get(prefix, 0):
                self._hoechste[prefix] = nummer

    def next_for(self, group: Group) -> str:
        """Naechster freier Code fuer diese Gruppe - und merkt ihn sich."""
        prefix = code_prefix(group, self.config)
        nummer = self._hoechste.get(prefix, 0)

        while True:
            nummer += 1
            kandidat = f"{prefix}-{nummer:0{self.breite}d}"
            # Die Schleife greift nur, wenn ein vorhandener Code eine andere
            # Stellenzahl hat als die aktuelle Einstellung ("...-7" neben
            # "...-007"). Dann ist die hoechste Nummer kein verlaesslicher
            # Anhaltspunkt mehr, und es wird wieder einzeln geprueft.
            if kandidat not in self._vergeben:
                self._hoechste[prefix] = nummer
                self._vergeben.add(kandidat)
                return kandidat

    @property
    def vergeben(self) -> set[str]:
        """Alle Codes - die vorgefundenen und die in diesem Lauf vergebenen."""
        return set(self._vergeben)


def slug(text: str) -> str:
    """Aus "Batreeq Syrian Germany" wird "batreeq-syrian-germany".

    Nur ASCII: Die Kennung steht in Adressen der Uebersicht und auf der
    Kommandozeile. Ein rein arabischer Name ergibt hier nichts Brauchbares -
    dann muss die Kennung von Hand kommen, und der Aufrufer prueft das.
    """
    klein = re.sub(r"[^a-z0-9]+", "-", text.lower().strip())
    return klein.strip("-")


def ist_gueltiger_code(tracking_code: str) -> bool:
    """Formale Pruefung - Grossbuchstaben, Ziffern, Bindestriche."""
    return bool(tracking_code) and bool(_CODE_RE.match(tracking_code))
