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
from pathlib import Path

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
    return mit_link(campaign, link, text, config=config, texttyp=texttyp)


def mit_link(
    campaign: Campaign,
    link: CampaignGroup,
    text: str,
    *,
    config: AppConfig,
    ziel: str = "store",
    texttyp: Texttyp = Texttyp.POST,
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

    **Ein Kommentar bekommt keinen Tracking-Link** (``texttyp``,
    23.09.2026). Der Platzhalter wird dort samt seiner Hinfuehrung
    herausgenommen (``ohne_link``), und ans Ende kommt die **freie** Adresse
    ``marketing.kommentar_adresse`` (``https://b-tarikak.de/home``) - ohne
    Code, sie zaehlt nichts. Der Beitrag behaelt seinen Tracking-Link; das
    Tracking selbst bleibt unberuehrt.
    """
    if texttyp is Texttyp.KOMMENTAR:
        text = mit_kommentaradresse(ohne_link(text), kommentar_adresse(config))

    # 1. Erst Spintax aufloesen (z. B. {Hallo|Hi}), Platzhalter bleiben stehen
    text = parse_spintax(text)
    
    # 2. Dann die festen Platzhalter ersetzen
    return (
        # Das Ziel entscheidet, welcher der beiden Codes hineinkommt. Ohne
        # Angabe der Store-Code - das ist das Verhalten, das bis zum
        # 31.08.2026 fuer alle Links galt, und ein Aufrufer, der nichts sagt,
        # soll nichts veraendern.
        setze_adresse(text, link.url_fuer(ziel))
        # **Der oeffentliche Code, nicht der innere.** Was hier eingesetzt
        # wird, steht gleich in einer Facebook-Gruppe; "FB-SYR-DUE-004-B"
        # nennt jedem Leser Kanal, Zielgruppe, Stadt und laufende Nummer -
        # das ist unsere Buchhaltung und keine Auskunft fuer ihn. Gezaehlt
        # wird weiterhin unter dem inneren Code: Die Weiterleitung loest den
        # Decknamen auf, bevor sie ein Ereignis schreibt.
        .replace("{tracking_code}", link.oeffentlicher_code_fuer(ziel))
        .replace("{landing_page}", campaign.landing_page)
        .replace("{datum}", monat_jetzt(config, sprache_der_kampagne(campaign, config)))
    )

#: Was nach der Ersetzung noch in geschweiften Klammern stehen darf: nichts.
#: Ein Platzhalter, der es bis in die Gruppe schafft, ist kein Schoenheits-
#: fehler - er ist ein Beitrag, dessen Gruppe nie einen Klick bekommt.
_OFFENER_PLATZHALTER = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")


#: Platzhalter, die in einem Kommentar eine Adresse ergaeben. ``{tracking_code}``
#: steht dabei: Er ist keine Adresse, aber der Deckname der Adresse.
_ADRESS_PLATZHALTER = ("{link}", "{landing_page}", "{tracking_code}")

#: Wo ein Satz oder Satzteil vor dem Link endet. Gedankenstrich und Komma
#: stehen dabei, weil die deutschen Vorlagen den Link so anhaengen ("... im
#: Koffer haben - App laden oder Website oeffnen: {link}"); die arabischen
#: tragen ihn in einem eigenen Schlusssatz ("... من هنا: {link}"), und dort
#: genuegt der Punkt davor. Das arabische Komma "،" steht bewusst nicht darin.
_SATZGRENZE = re.compile(r"[.!?؟]|\s[-–]\s|,")

#: Der Name der App - er darf nicht mit dem Link verschwinden. In
#: "أنا كمان شفت هالتطبيق ... تطبيق بطريقك للتحميل ... من هنا: {link}" steht er
#: nur im Satz des Links; faellt der ganz, bleibt "dieses App" ohne Namen.
_APP_NAMEN = ("بطريقك", "B-Tarikak")

#: Die Woerter, die auf den Link zeigen ("von hier") - sie gehen mit ihm, auch
#: wenn der Satz bleibt.
_ZEIGER = re.compile(r"\s*(?:من\s+هنا|من\s+هون|هون|هنا|hier)\s*$", re.IGNORECASE)


def ohne_link(text: str) -> str:
    """Nimmt die Adress-Platzhalter samt ihrer Hinfuehrung aus einem Kommentar.

    **Ein Kommentar traegt keinen Link** (23.09.2026, Anweisung des Nutzers).
    Die Vorlagen bleiben, wie sie sind; nur geht der Teil, der auf den Link
    hinfuehrt, mit ihm: Aus "... بنفس الاتجاه. حمّل تطبيق بطريقك أو زور الموقع
    من هنا: {link}" wird "... بنفس الاتجاه." - der Satz davor nennt die App
    bereits. Bloss ``{link}`` zu streichen liesse "من هنا:" ins Leere zeigen.

    Entscheidend ist der **Doppelpunkt davor**, nicht die Zeile:

    * Endet der Text vor dem Platzhalter mit ":", fuehrt er auf den Link hin
      und faellt ab der letzten Satzgrenze mit weg; ein Gedankenstrich oder
      Komma wird dabei zum Punkt.
    * **Ausser, damit ginge der Name der App.** Steht "بطريقك" nur im Satz
      des Links, bleibt der Satz; es fallen nur die Woerter, die auf den
      Link zeigen ("من هنا", "hier"), und der Doppelpunkt.
    * Sonst faellt allein der Platzhalter weg - so haengt
      ``vorlagen.anlasstext`` ihn an, in eigener Zeile nach einem Satz.

    Mechanisch und ohne Modell - dieselbe Zurueckhaltung wie ``anlasstext``,
    das den Link ebenso mechanisch anhaengt.
    """
    for platzhalter in _ADRESS_PLATZHALTER:
        while platzhalter in text:
            stelle = text.index(platzhalter)
            kopf, danach = text[:stelle].rstrip(), text[stelle + len(platzhalter):]
            if kopf.endswith(":"):
                kopf = _ohne_hinfuehrung(kopf[:-1])
            text = kopf + danach
    return "\n".join(z.rstrip() for z in text.splitlines() if z.strip()).strip()


def kommentar_adresse(config: AppConfig) -> str:
    """Die freie Adresse fuer Kommentare - ``""``, wenn keine oder eine mit Tracking.

    ``marketing.kommentar_adresse`` (23.09.2026, Wunsch des Nutzers: "diese
    URL in Kommentaren lassen"). Eine Tracking-Adresse wird hier gar nicht
    erst angenommen: ``/r/``, ``/t/`` oder ``?ref=`` ergeben ``""`` - sonst
    kaeme ueber die Konfiguration zurueck, was aus dem Kommentar heraus soll.

    **Seit dem 24.09.2026 geht ``marketing.kommentar_schluss`` vor** - ein
    Satz statt einer Adresse ("الرابط المباشر للتحميل موجود في البايو ...").
    Die ausgeschriebene Adresse machte bei Facebook Probleme; sie steht jetzt
    im Bild, das jedem Kommentar beiliegt (``kommentar_bild``). Der Satz
    reist auf demselben Weg wie vorher die Adresse (``link_url``) und wird
    genauso angehaengt - einmal, hinter den letzten Satz.
    """
    from fbgroups.urls import tracking_adresse_im_text

    for schluessel in ("kommentar_schluss", "kommentar_adresse"):
        wert = str(config.get("marketing", schluessel, default="") or "").strip()
        if wert and not tracking_adresse_im_text(wert):
            return wert
    return ""


def kommentar_bild(config: AppConfig) -> Path | None:
    """Das Bild, das jedem Kommentar beiliegt (``marketing.kommentar_bild``) - oder ``None``.

    Seit dem 24.09.2026 (Wunsch des Nutzers): Statt der ausgeschriebenen
    Adresse, die bei Facebook Probleme machte, traegt das Bild die Adresse
    und den Weg zur App. Der Pfad ist relativ zum Projekt; die Datei liegt
    unter ``config/`` und kommt damit mit jedem Ausrollen auf beide Rechner.
    Fehlt sie, geht der Kommentar ohne Bild hinaus.
    """
    wert = str(config.get("marketing", "kommentar_bild", default="") or "").strip()
    if not wert:
        return None
    pfad = Path(wert) if Path(wert).is_absolute() else config.root / wert
    return pfad if pfad.is_file() else None


def mit_kommentaradresse(text: str, adresse: str) -> str:
    """Haengt die freie Adresse an einen Kommentar - einmal, und nie eine mit Tracking.

    Hinter den letzten Satz, mit einem Leerzeichen: "... من سوريا.
    https://b-tarikak.de/home" (das Beispiel des Nutzers). Steht sie schon
    darin, geschieht nichts; der vorbereitete Text vom Server traegt sie
    bereits, und der Lauf haengt sie nicht ein zweites Mal an.
    """
    from fbgroups.urls import tracking_adresse_im_text

    adresse = (adresse or "").strip()
    if not adresse or tracking_adresse_im_text(adresse) or adresse in text:
        return text
    return f"{text.rstrip()} {adresse}" if text.strip() else adresse


def _ohne_hinfuehrung(hinfuehrung: str) -> str:
    """Der Text vor ``:{link}`` - ohne den Teil, der auf den Link hinfuehrt."""
    grenzen = [
        m for m in _SATZGRENZE.finditer(hinfuehrung) if hinfuehrung[: m.start()].strip()
    ]
    gekuerzt = ""
    if grenzen:
        grenze = grenzen[-1]
        if grenze.group().strip() in (".", "!", "?", "؟"):
            gekuerzt = hinfuehrung[: grenze.end()]
        else:
            gekuerzt = hinfuehrung[: grenze.start()].rstrip() + "."
    name_da = any(name in hinfuehrung for name in _APP_NAMEN)
    if gekuerzt and (not name_da or any(name in gekuerzt for name in _APP_NAMEN)):
        return gekuerzt
    # Der Satz bleibt - nur das "von hier" geht mit dem Link.
    rest = _ZEIGER.sub("", hinfuehrung).rstrip()
    if grenzen or not rest:
        return rest + ("." if rest and rest[-1] not in ".!?؟" else "")
    return rest


def setze_adresse(text: str, adresse: str) -> str:
    """Setzt ``{link}`` durch eine **fertige** Adresse. Die eine Ersetzung.

    Herausgeloest aus ``mit_link`` am 14.09.2026, und der Anlass stand in
    einer Facebook-Gruppe: Dort erschien ein Kommentar mit dem Wortlaut

        فيك تنشر طلبك على بطريقك وتشوف إذا في مسافر مناسب.
        {link}

    Der Anlasstext (``vorlagen.anlasstext``, seit 13.09.2026) haengt
    ``{link}`` mechanisch an und wird im Lauf **anstelle** des vorbereiteten
    Textes abgesetzt - der vorbereitete war aufgeloest, der neue nicht.
    Zwischen "Text waehlen" und "Text absenden" fehlte die Ersetzung ganz.

    Getrennt von ``mit_link``, weil es zwei verschiedene Ausgangslagen sind:
    Jene baut die Adresse aus Kampagne und Zuordnung, diese bekommt sie
    fertig - der Arbeitsrechner im Fernbetrieb hat keine Zuordnung, nur die
    Adresse. **Ersetzt wird an beiden Stellen hier**, damit es eine
    Ersetzung bleibt und nicht zwei werden.
    """
    return text.replace("{link}", adresse)


def offene_platzhalter(text: str) -> list[str]:
    """Was nach der Ersetzung noch in geschweiften Klammern steht.

    Die letzte Frage vor dem Absenden, und sie ist kein Luxus: Ein Text mit
    ``{link}`` sieht richtig aus, und seine Gruppe bekommt nie einen Klick
    gutgeschrieben. Dass er gar nicht erst hinausgeht, ist die einzige
    Antwort, die den Fehler nicht in eine Gruppe traegt.

    Spintax (``{a|b}``) faellt nicht darunter - es ist zu diesem Zeitpunkt
    ohnehin aufgeloest, und ein ``|`` passt nicht auf das Muster.
    """
    return _OFFENER_PLATZHALTER.findall(text)


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
