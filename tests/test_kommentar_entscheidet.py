"""Vor jedem Kommentar steht die ganze Kette - Inhalt, Anlass, Erlaubnis.

Der Anlass vom 14.09.2026 ist eine Forderung des Nutzers: *"Ich möchte nicht,
dass wir einen erfolgreichen CLI-Test haben, während der echte Facebook-Runner
diese Prüfungen noch gar nicht durchführt."* Damals nahm der Fernbetrieb den
**lautesten** Beitrag und den vorbereiteten Text, ohne ``inhalt.lies`` und
ohne ``entscheide``. Den Fernbetrieb gibt es seit dem Umzug (25.09.2026)
nicht mehr; die Datei haelt fest, dass die Kette selbst tut, was sie soll.
"""

from __future__ import annotations

import pytest

from fbgroups.marketing import automatik
from fbgroups.marketing.entscheidung import Anspruch, Antwortart, Erlaubnis, Linkmodus
from fbgroups.marketing.inhalt import Relevanz
from fbgroups.urls import adresse_im_text

#: Die fertige Adresse der Gruppe - im echten Lauf kommt sie vom Server
#: (``link_url``). Ohne sie weist der Kern den Text zurueck, statt ihn
#: mit rohem ``{link}`` abzusenden.
LINK_URL = "https://go.b-tarikak.de/r/k7m2x9q"

WOHNUNG = "Suche dringend 2-Zimmer-Wohnung in Stuttgart"
VERSAND = "كيف فيني ابعت غرض صغير من ألمانيا لسوريا؟"


def _post(url: str, text: str, *, laut: int = 0) -> dict:
    return {
        "post_url": url,
        "text": text,
        "interactions": laut,
        "comments": 0,
        "posted_at": None,
    }


class _Kommentator:
    """Statt Playwright: haelt fest, wohin kommentiert wurde und womit."""

    def __init__(self) -> None:
        self.aufrufe: list[tuple[str, str]] = []

    def __call__(self, _context, post_url: str, text: str) -> bool:
        self.aufrufe.append((post_url, text))
        return True


@pytest.fixture()
def config():
    from fbgroups.config import load_config

    return load_config()


# --- Die Grundlagen reisen mit ---------------------------------------------

# --- Die Kette selbst ------------------------------------------------------

def test_der_passendste_beitrag_schlaegt_den_lautesten(config) -> None:
    """**Die Zusicherung dieser Datei.**

    Vorher entschied allein ``interactions + comments``. Der Kommentar ueber
    Paketmitnahme landete dann unter dem Wohnungsgesuch mit hundert
    Reaktionen - also ausgerechnet dort, wo ihn die meisten sehen.
    """
    kommentator = _Kommentator()
    ergebnis = automatik.entscheide_und_kommentiere(
        None,
        config,
        [_post("p/laut", WOHNUNG, laut=100), _post("p/passend", VERSAND)],
        "g1",
        "Rueckfalltext {link}",
        kommentieren=kommentator,
        bisherige=[],
        # ``werbung`` erlaubt: Sonst bliebe es beim privaten Hinweis, und
        # fuer den gibt es bewusst keinen Vorrat - dann wird gar nicht
        # kommentiert. Hier geht es um die **Auswahl**, nicht um die Stufe.
        erlaubnis=Erlaubnis(),
        anspruch=Anspruch(),
        link_url=LINK_URL,
    )

    assert ergebnis.erfolg is True
    assert [url for url, _ in kommentator.aufrufe] == ["p/passend"]


def test_ohne_anlass_wird_nicht_kommentiert(config) -> None:
    """``no_reply`` ist ein Ergebnis, kein Ausfall.

    Steht in der Gruppe heute nichts, worauf eine Antwort etwas beitruege,
    geht auch nichts hinaus - und es zaehlt weder gegen die Fassung noch
    gegen die Gruppe (``kein_anlass``).
    """
    kommentator = _Kommentator()
    ergebnis = automatik.entscheide_und_kommentiere(
        None,
        config,
        [_post("p/1", WOHNUNG, laut=100), _post("p/2", "شو أحسن مطعم عربي بشتوتغارت؟")],
        "g1",
        "Rueckfalltext {link}",
        kommentieren=kommentator,
        bisherige=[],
        erlaubnis=Erlaubnis(),
        anspruch=Anspruch(),
        link_url=LINK_URL,
    )

    assert ergebnis.erfolg is False
    assert ergebnis.kein_anlass is True
    assert kommentator.aufrufe == [], "es darf nichts abgesetzt worden sein"


def test_eine_gruppe_ohne_links_bekommt_einen_text_ohne_link(config) -> None:
    """Die Regeln der Gruppe binden - auch wenn der Beitrag passt.

    Punkt 4 der Anforderung: Der Runner darf keinen Link setzen, wo Links
    verboten sind. Reagiert wird trotzdem, nur ohne Adresse.
    """
    kommentator = _Kommentator()
    automatik.entscheide_und_kommentiere(
        None,
        config,
        [_post("p/1", VERSAND)],
        "g1",
        "Rueckfall {link}",
        kommentieren=kommentator,
        bisherige=[],
        erlaubnis=Erlaubnis(links=False),
        anspruch=Anspruch(),
        link_url=LINK_URL,
    )

    assert len(kommentator.aufrufe) == 1
    _, text = kommentator.aufrufe[0]
    assert "{link}" not in text
    assert "http" not in text


# --- Beide Wege, eine Kette ------------------------------------------------

def test_die_entscheidung_faellt_vor_dem_kommentar(config) -> None:
    """Punkt 6: erst pruefen, dann handeln - nicht umgekehrt.

    Der Nachweis ist, dass bei ``no_reply`` **gar nichts** abgesetzt wird:
    Wuerde erst kommentiert und danach geprueft, stuende der Kommentar schon
    in der Gruppe.
    """
    kommentator = _Kommentator()
    automatik.entscheide_und_kommentiere(
        None,
        config,
        [_post("p/1", WOHNUNG)],
        "g1",
        "Rueckfall {link}",
        kommentieren=kommentator,
        bisherige=[],
        erlaubnis=Erlaubnis(),
        anspruch=Anspruch(mindestrelevanz=Relevanz.HOCH),
        link_url=LINK_URL,
    )

    assert kommentator.aufrufe == []


def test_die_entscheidung_steht_im_ergebnis(config) -> None:
    """Protokolliert wird, was entschieden wurde - nicht nur, was geschah.

    ``vorlage_key`` nennt die benutzte Fassung; der Text geht als
    ``Schrittergebnis.text`` zurueck, damit die Buchung genau den festhaelt,
    der wirklich hinausging.
    """
    ergebnis = automatik.entscheide_und_kommentiere(
        None,
        config,
        [_post("p/1", VERSAND)],
        "g1",
        "Rueckfall {link}",
        kommentieren=_Kommentator(),
        bisherige=[],
        erlaubnis=Erlaubnis(links=True),
        anspruch=Anspruch(),
        link_url=LINK_URL,
    )

    assert ergebnis.erfolg is True
    assert ergebnis.text, "der abgesetzte Text gehoert in den Ausgang"
    assert ergebnis.vorlage_key.startswith("ar/anlaesse/")


def test_die_antwortarten_sind_die_der_anforderung() -> None:
    """Die Namen aus Punkt 6 - sie sind bereits im Projekt definiert."""
    vorhanden = {a.value for a in Antwortart}

    assert {
        "no_reply",
        "private_contact_suggestion",
        "contextual_app_mention",
        "direct_app_recommendation",
    } <= vorhanden
    assert {m.value for m in Linkmodus} == {"no_link", "app_name_only", "tracking_link"}


def test_der_server_erfaehrt_welche_fassung_hinausging(config) -> None:
    """Eine Kennung reist zurueck, kein Text - und sie genuegt.

    Seit der Fernbetrieb seinen Kommentar selbst waehlt, weiss der Server
    nicht mehr aus seiner eigenen Vorbereitung, was in der Gruppe steht.
    Ohne diesen Weg stuende dort weiter die vorbereitete Fassung, und die
    Uebersicht zeigte einen Satz, der nie abgesetzt wurde.

    Der Text selbst bleibt draussen: Der Server baut ihn aus der Kennung neu
    (beide fahren dieselbe ``textvorlagen.yaml``) - dieselbe Sparsamkeit wie
    beim Regelbefund, und dieselbe Sicherheit wie beim Ergebnisformular der
    Arbeitsseite.
    """
    from fbgroups.marketing.vorlagen import Personalisierung, anlasstext, anlasstext_zu

    treffer = anlasstext(
        config,
        sprache="ar",
        anlass="gegenstand",
        group_id="g1",
        daten=Personalisierung(zielgruppe="", stadt=""),
        mit_link=True,
    )
    assert treffer is not None, "Aufbau: es sollte einen Vorrat geben"
    schluessel, text = treffer

    assert anlasstext_zu(config, schluessel, mit_link=True) == text
    # Ohne Link ergibt dieselbe Kennung einen anderen Satz - deshalb faehrt
    # ``mit_link`` mit.
    assert anlasstext_zu(config, schluessel, mit_link=False) != text
    assert "{link}" not in anlasstext_zu(config, schluessel, mit_link=False)


def test_eine_unbekannte_kennung_verhindert_keine_buchung(config) -> None:
    """Wer eine Vorlage entfernt, soll damit keinen Ausgang verschlucken.

    ``""`` heisst "nicht aufloesbar" und ist kein Fehler: Festgehalten wird
    dann eben nur der Ausgang - das ist weniger, aber nichts Falsches.
    """
    assert anlasstext_zu_leer(config) == ""


def anlasstext_zu_leer(config) -> str:
    from fbgroups.marketing.vorlagen import anlasstext_zu

    return anlasstext_zu(config, "ar/anlaesse/gibtsnicht/x", mit_link=False)


# --- Der Platzhalter geht nie in eine Gruppe -------------------------------

def test_der_abgesetzte_text_traegt_weder_adresse_noch_platzhalter(
    config,
) -> None:
    """**Der Fehler vom 14.09.2026, in einer Zusicherung** - und seit dem
    23.09.2026 ohne Link.

    In einer Gruppe stand:

        فيك تنشر طلبك على بطريقك وتشوف إذا في مسافر مناسب.
        {link}

    Der Anlasstext (seit 13.09.2026) ersetzt den vorbereiteten Text, und der
    vorbereitete war aufgeloest - der neue nicht. Zwischen "Text waehlen" und
    "Text absenden" fehlte die Ersetzung ganz.

    Seit dem 23.09.2026 traegt ein Kommentar **keinen** Link (Anweisung des
    Nutzers): Der Platzhalter faellt samt Hinfuehrung weg, und die
    mitgeschickte Adresse wird nicht mehr eingesetzt - auch nicht, wenn die
    Erlaubnis einen Link zuliesse.
    """
    kommentator = _Kommentator()
    ergebnis = automatik.entscheide_und_kommentiere(
        None,
        config,
        [_post("p/1", VERSAND)],
        "g1",
        "Rueckfall {link}",
        kommentieren=kommentator,
        bisherige=[],
        erlaubnis=Erlaubnis(links=True),
        anspruch=Anspruch(),
        link_url=LINK_URL,
    )

    assert ergebnis.erfolg is True
    _, abgesetzt = kommentator.aufrufe[0]
    assert "{link}" not in abgesetzt
    assert LINK_URL not in abgesetzt
    assert adresse_im_text(abgesetzt) == ""

    # Gespeichert wird, was hinausging - ohne Platzhalter und ohne Adresse.
    assert "{link}" not in ergebnis.text
    assert LINK_URL not in ergebnis.text


def test_ohne_adresse_geht_der_kommentar_ohne_link_hinaus(config) -> None:
    """Keine Adresse ist seit dem 23.09.2026 der Regelfall - kein Fehler.

    Bis dahin hiess eine fehlende Adresse "lieber kein Kommentar als einer
    mit ``{link}``". Jetzt faellt der Platzhalter samt Hinfuehrung weg
    (``beitrag.ohne_link``), und der Kommentar geht ohne Link hinaus.
    """
    kommentator = _Kommentator()
    ergebnis = automatik.entscheide_und_kommentiere(
        None,
        config,
        [_post("p/1", VERSAND)],
        "g1",
        "Rueckfall {link}",
        kommentieren=kommentator,
        bisherige=[],
        erlaubnis=Erlaubnis(links=True),
        anspruch=Anspruch(),
        link_url="",
    )

    assert ergebnis.erfolg is True
    _, abgesetzt = kommentator.aufrufe[0]
    assert "{" not in abgesetzt
    assert adresse_im_text(abgesetzt) == ""


def test_ein_text_ohne_link_geht_weiterhin_hinaus(config) -> None:
    """Eine linkscheue Gruppe bekommt einen Kommentar ohne Adresse.

    Die Pruefung darf nicht zum Linkzwang werden: Was keinen Platzhalter
    traegt, braucht auch keine Adresse.
    """
    kommentator = _Kommentator()
    ergebnis = automatik.entscheide_und_kommentiere(
        None,
        config,
        [_post("p/1", VERSAND)],
        "g1",
        "Rueckfall {link}",
        kommentieren=kommentator,
        bisherige=[],
        erlaubnis=Erlaubnis(links=False),
        anspruch=Anspruch(),
        link_url="",
    )

    assert ergebnis.erfolg is True
    _, abgesetzt = kommentator.aufrufe[0]
    assert "{" not in abgesetzt


# --- "Kein Anlass" ist auch im Fernbetrieb kein Fehlschlag -----------------

