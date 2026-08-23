"""Den Beitrag vorbereiten - Text, Zwischenablage, Browser.

Dieses Modul veroeffentlicht nichts. Es legt den fertigen Text bereit und
oeffnet die Gruppe im Browser des Nutzers; Einfuegen und Absenden bleibt
Handarbeit. Der Unterschied ist nicht kosmetisch: Ein Programm, das 300
Beitraege selbst absetzt, ist genau das, was Facebooks Spam-Erkennung sucht,
und gesperrt wird das Konto des Nutzers - samt aller Gruppen, in die er
aufgenommen wurde.

Beide Helfer kommen ohne zusaetzliche Abhaengigkeit aus: ``webbrowser`` steht
in der Standardbibliothek, und fuer die Zwischenablage hat jedes System sein
eigenes kleines Programm. Ein Paket mehr waere fuer zwei Zeilen zu viel.
"""

from __future__ import annotations

import subprocess
import sys
import webbrowser

from fbgroups.marketing.models import Campaign, CampaignGroup

# Je Plattform das Programm, das von der Standardeingabe in die Zwischenablage
# schreibt. Wayland vor X11: Auf einer Wayland-Sitzung ist ``xclip`` oft
# vorhanden, schreibt aber ins Leere.
_ZWISCHENABLAGE: dict[str, tuple[tuple[str, ...], ...]] = {
    "win32": (("clip",),),
    "darwin": (("pbcopy",),),
    "linux": (
        ("wl-copy",),
        ("xclip", "-selection", "clipboard"),
        ("xsel", "--clipboard", "--input"),
    ),
}


def beitragstext(campaign: Campaign, link: CampaignGroup) -> str:
    """Setzt die Vorlage der Kampagne mit dem Link **dieser** Gruppe zusammen.

    Die einzige Stelle, an der ein Beitragstext entsteht - ``campaign
    message``, ``queue``, ``next`` und die Uebersicht lesen alle hier. Eine
    zweite Fassung koennte abweichen, und der Unterschied fiele erst auf,
    wenn ein Beitrag mit dem falschen Code veroeffentlicht ist; zurueckholen
    laesst er sich dann nicht mehr.

    ``{link}`` ist der Platzhalter, um den es geht. Der Code steht bewusst
    nirgends fest im Text: Bei 300 Gruppen sind das 300 verschiedene Links,
    und jeder einzelne von Hand eingetragen waere eine Fehlerquelle je Gruppe.
    """
    text = campaign.message_template or ""
    return (
        text.replace("{link}", link.tracking_url)
        .replace("{tracking_code}", link.tracking_code)
        .replace("{landing_page}", campaign.landing_page)
    )


def in_zwischenablage(text: str) -> bool:
    """Legt den Text in die Zwischenablage. Returns: ob es geklappt hat.

    Kein Fehler nach aussen: Klappt es nicht, wird der Text ohnehin auf dem
    Bildschirm angezeigt und laesst sich von dort kopieren. Ein Abbruch waere
    an dieser Stelle unverhaeltnismaessig - die Arbeit geht weiter, nur eine
    Bequemlichkeit fehlt.
    """
    if not text:
        return False

    for befehl in _ZWISCHENABLAGE.get(sys.platform, ()):
        try:
            # ``text=True`` mit ausdruecklicher Kodierung: Die Vorlagen sind
            # arabisch, und die Windows-Vorgabe cp1252 macht daraus Fragezeichen.
            subprocess.run(
                befehl,
                input=text,
                text=True,
                encoding="utf-8",
                check=True,
                capture_output=True,
            )
            return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


def oeffne_im_browser(url: str) -> bool:
    """Oeffnet die Gruppe im Standardbrowser. Returns: ob es geklappt hat.

    Ein Aufruf von ``webbrowser.open`` ist keine Automatisierung von Facebook -
    es passiert dasselbe wie beim Anklicken eines Links. Was danach im Browser
    geschieht, tut ein Mensch.
    """
    if not url:
        return False
    try:
        return webbrowser.open(url, new=2)
    except (OSError, webbrowser.Error):
        return False
