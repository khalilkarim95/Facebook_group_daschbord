"""Tracking-Codes und Tracking-Links.

Aufbau eines Codes: ``FB-SYR-BER-001``

===========  ==================================================
``FB``       Kanal - hier immer Facebook
``SYR``      Zielgruppe der Gruppe (aus ``config/audiences.yaml``)
``BER``      Stadt der Gruppe (aus ``config/cities.yaml``)
``001``      laufende Nummer innerhalb der Kampagne je Kuerzel-Paar
===========  ==================================================

Die Kuerzel stehen nicht im Code: Sie kommen aus dem optionalen Feld ``code``
der Konfiguration, sonst aus den ersten drei Buchstaben der Kennung. Eine neue
Stadt bringt damit ihr Kuerzel selbst mit - so wie eine neue Stadt auch sonst
ohne Codeaenderung dazukommt.

Der fertige Code ist unveraenderlich. Er steht in veroeffentlichten Beitraegen;
eine spaetere Neuberechnung wuerde alte Links auf eine andere Gruppe zeigen
lassen. ``next_tracking_code`` erzeugt deshalb nur *neue* Codes - vergebene
liest der Aufrufer aus dem Bestand.
"""

from __future__ import annotations

import os
import re
from urllib.parse import quote, urlparse

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


def audience_code(group: Group, config: AppConfig) -> str:
    """Kuerzel der Zielgruppe einer Gruppe."""
    if not group.audience_tags:
        return FALLBACK_AUDIENCE
    tag = group.audience_tags[0]
    audience = config.audiences.get(tag)
    eigenes = getattr(audience, "code", "") if audience else ""
    return _kuerzel(eigenes or tag)


def city_code(group: Group, config: AppConfig) -> str:
    """Kuerzel der Stadt einer Gruppe."""
    if not group.city:
        return FALLBACK_CITY
    for city in config.cities.values():
        if city.name_de == group.city:
            return _kuerzel(getattr(city, "code", "") or city.id)
    return _kuerzel(group.city)


def code_prefix(group: Group, config: AppConfig) -> str:
    """Der Teil des Codes ohne laufende Nummer, z. B. ``FB-SYR-BER``."""
    kanal = str(config.get("marketing", "tracking", "prefix", default=DEFAULT_PREFIX))
    return "-".join([_kuerzel(kanal, 4), audience_code(group, config), city_code(group, config)])


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

    Nur ASCII: Die Kennung steht spaeter im Tracking-Code und in URLs. Ein
    rein arabischer Name ergibt hier nichts Brauchbares - dann muss die
    Kennung von Hand kommen, und der Aufrufer prueft das.
    """
    klein = re.sub(r"[^a-z0-9]+", "-", text.lower().strip())
    return klein.strip("-")


def app_base_url(config: AppConfig) -> str:
    """Basis-URL der Zielanwendung.

    Reihenfolge: Umgebungsvariable ``APP_BASE_URL`` vor
    ``marketing.app_base_url`` aus ``config/settings.yaml``. So laesst sich
    dieselbe Konfiguration lokal und auf der echten Domain benutzen, ohne die
    gespeicherten Codes anzufassen - die Codes stehen fest, nur ihr Vorspann
    aendert sich.
    """
    aus_umgebung = os.environ.get("APP_BASE_URL", "").strip()
    if aus_umgebung:
        return aus_umgebung.rstrip("/")
    return str(config.get("marketing", "app_base_url", default="")).strip().rstrip("/")


def app_base_url_quelle(config: AppConfig) -> str:
    """Woher die Basis-URL stammt - fuer die Anzeige, nicht fuer die Auswahl.

    Ein Link, der auf ``localhost`` zeigt, sieht aus wie jeder andere; in einem
    Facebook-Beitrag fuehrt er aber jeden Leser auf dessen eigenen Rechner.
    Deshalb soll ``campaign show`` nicht nur den Wert nennen, sondern auch,
    welche Quelle ihn gesetzt hat.
    """
    if os.environ.get("APP_BASE_URL", "").strip():
        return "Umgebung (APP_BASE_URL)"
    if str(config.get("marketing", "app_base_url", default="")).strip():
        return "config/settings.yaml"
    return "nicht gesetzt"


def ist_lokale_basis(basis_url: str) -> bool:
    """Zeigt die Basis-URL auf den eigenen Rechner?

    Rein zur Warnung. Der lokale Betrieb bleibt ausdruecklich moeglich - er ist
    fuer die Entwicklung genau richtig, nur eben nicht fuer veroeffentlichte
    Links.
    """
    rechner = (urlparse(basis_url).hostname or "").lower()
    return rechner in {"localhost", "127.0.0.1", "::1", "0.0.0.0"} or rechner.endswith(".local")


def tracking_url(tracking_code: str, config: AppConfig) -> str:
    """Vollstaendiger Link: ``{APP_BASE_URL}/r/{code}``.

    Ohne hinterlegte Basis-URL bleibt der Link leer statt zu raten - ein
    halber Link waere in einem Beitrag schlimmer als gar keiner.
    """
    basis = app_base_url(config)
    if not basis or not tracking_code:
        return ""
    pfad = str(config.get("marketing", "tracking", "path", default="/r")).strip("/")
    return f"{basis}/{pfad}/{quote(tracking_code, safe='')}"


def ist_gueltiger_code(tracking_code: str) -> bool:
    """Formale Pruefung - Grossbuchstaben, Ziffern, Bindestriche."""
    return bool(tracking_code) and bool(_CODE_RE.match(tracking_code))
