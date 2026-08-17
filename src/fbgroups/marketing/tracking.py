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
