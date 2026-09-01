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

import random
import re
import subprocess
import sys
import webbrowser

from fbgroups.config import AppConfig
from fbgroups.marketing.models import Campaign, CampaignGroup, Texttyp
from fbgroups.marketing.vorlagen import monat_jetzt, sprache_der_kampagne

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


def beitragstext(
    campaign: Campaign,
    link: CampaignGroup,
    texttyp: Texttyp = Texttyp.POST,
    *,
    config: AppConfig,
) -> str:
    """Setzt den gespeicherten Text mit dem Link **dieser** Gruppe zusammen.

    Die einzige Stelle, an der ein fertiger Text entsteht - ``campaign
    message``, ``queue``, ``next``, die Arbeitsseite und die Uebersicht lesen
    alle hier. Eine zweite Fassung koennte abweichen, und der Unterschied
    fiele erst auf, wenn ein Beitrag mit dem falschen Code veroeffentlicht
    ist; zurueckholen laesst er sich dann nicht mehr.

    ``texttyp`` waehlt **nur**, welches Feld gelesen wird - Beitrag oder
    Kommentar. Die Ersetzung bleibt fuer beide dieselbe und an dieser einen
    Stelle: Ein zweiter Weg fuer den Kommentar waere ein zweiter Ort, an dem
    ein Tracking-Code in einen Text kommt.

    ``{link}`` ist der Platzhalter, um den es geht. Der Code steht bewusst
    nirgends fest im Text: Bei 300 Gruppen sind das 300 verschiedene Links,
    und jeder einzelne von Hand eingetragen waere eine Fehlerquelle je Gruppe.

    **Der Text der Zuordnung geht der Vorlage vor.** Steht in
    ``link.post_text`` etwas, ist das der Text, den ein Mensch fuer *diese*
    Gruppe geschrieben oder freigegeben hat - er darf nicht von der
    allgemeinen Vorlage ueberstimmt werden. Ohne diesen Vorrang gaebe ein
    Mensch eine Fassung frei und eine andere ginge hinaus, und der
    Unterschied fiele erst in der Gruppe auf.

    Die Ersetzung bleibt dieselbe, gleich woher der Text stammt: Ein
    Vorschlag von Claude enthaelt ``{link}`` und sonst nichts Linkartiges -
    ``ki.pruefe_platzhalter`` laesst nichts anderes durch.
    """
    # Die Vorlage der Kampagne faengt nur den **Beitrag** auf. Sie ist als
    # Beitrag geschrieben; unter einem fremden Beitrag stuende sie als
    # Kommentar da, den niemand dafuer vorgesehen hat. Ohne Kommentartext
    # bleibt der Text leer - und die Arbeitsseite zeigt dann keinen.
    if texttyp is Texttyp.KOMMENTAR:
        text = link.kommentar_text
    else:
        text = link.post_text or campaign.message_template or ""
    return mit_link(campaign, link, text, config=config)


def mit_link(
    campaign: Campaign,
    link: CampaignGroup,
    text: str,
    *,
    config: AppConfig,
    ziel: str = "store",
) -> str:
    """Setzt die spaeten Platzhalter in einen **beliebigen** Text dieser Gruppe.

    Herausgeloest aus ``beitragstext``, seit eine Gruppe nicht mehr einen Text
    hat, sondern fuenf: Die Fassungen liegen in ``campaign_group_texte`` und
    nicht in ``link.post_text``, gebraucht wird aber genau dieselbe Ersetzung.
    Sie ein zweites Mal hinzuschreiben waere ein zweiter Ort, an dem ein
    Tracking-Code in einen Text kommt - und der Unterschied zwischen beiden
    fiele erst auf, wenn ein Beitrag mit dem falschen Code in einer Gruppe
    steht.

    ``beitragstext`` ist damit nur noch die Frage "welches Feld?"; die
    Ersetzung selbst steht hier, an einer Stelle.

    **``{datum}`` steht hier und nicht in ``vorlagen.fuelle``.** Es traegt den
    laufenden Monat, und der aendert sich - eingesetzt und mitgespeichert
    stuende in einem Beitrag, der drei Wochen nach dem Erzeugen hinausgeht,
    der Monat von damals; eine Frage nach Reisenden im letzten Monat ist
    schlicht falsch. Deshalb ist das ``config`` verpflichtend und nicht
    optional: Ein Aufrufer, der es vergessen darf, laesst ``{datum}`` in
    geschweiften Klammern im Beitrag stehen, und das faellt erst in der Gruppe
    auf.
    """
    # 1. Erst Spintax aufloesen (z. B. {Hallo|Hi}), Platzhalter bleiben stehen
    text = parse_spintax(text)
    
    # 2. Dann die festen Platzhalter ersetzen
    return (
        # Das Ziel entscheidet, welcher der beiden Codes hineinkommt. Ohne
        # Angabe der Store-Code - das ist das Verhalten, das bis zum
        # 31.08.2026 fuer alle Links galt, und ein Aufrufer, der nichts sagt,
        # soll nichts veraendern.
        text.replace("{link}", link.url_fuer(ziel))
        .replace("{tracking_code}", link.code_fuer(ziel))
        .replace("{landing_page}", campaign.landing_page)
        .replace("{datum}", monat_jetzt(config, sprache_der_kampagne(campaign, config)))
    )

def parse_spintax(text: str) -> str:
    """Loest Spintax-Muster wie {Hallo|Hi} in eine zufaellige Variante auf.
    
    Wertet verschachtelte Muster von innen nach aussen aus.
    Platzhalter wie {link} bleiben unberuehrt, da sie kein '|' enthalten.
    """
    pattern = re.compile(r"\{([^{}]*\|[^{}]*)\}")
    while match := pattern.search(text):
        choices = match.group(1).split("|")
        text = text[:match.start()] + random.choice(choices) + text[match.end():]
    return text


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
