"""Antworten oder nicht - und in welcher Form.

Die beiden reinen Module ``inhalt.py`` und ``entscheidung.py`` zusammen. Kein
Netz, keine Datenbank, kein Browser: Was hier geprueft wird, ist die
folgenreichste Entscheidung des Programms - ob unter einem fremden Beitrag
etwas von uns steht.

Die Beispieltexte stammen aus der Anforderung vom 12.09.2026 (Punkt 44) und
aus den Gruppen des Bestands. Sie sind **Eingaben** dieser Tests und werden
nirgends gespeichert; genau darin liegt die Grenze, die das Projekt zieht.
"""

from __future__ import annotations

import pytest

from fbgroups.marketing.entscheidung import (
    Antwortart,
    Erlaubnis,
    entscheide,
    soll_antworten,
    soll_link_nutzen,
    soll_privat_anbieten,
)
from fbgroups.marketing.inhalt import Absicht, Relevanz, Thema, lies

# --- Erlaubnisse ----------------------------------------------------------
#: Seit dem 23.09.2026 fuer jede Gruppe dieselbe - mit Link.
OFFEN = Erlaubnis()
#: Gezielt abgeschaltet, wie es der Fernbetrieb uebertragen kann.
OHNE_LINKS = Erlaubnis(links=False)
OHNE_KOMMENTARE = Erlaubnis(kommentare=False)

PAKET = "كيف فيني ابعت غرض صغير من ألمانيا لسوريا؟"
REISENDER = "مسافر من برلين إلى دمشق الأسبوع الجاي، عندي مكان بالشنطة"
WOHNUNG = "Suche dringend eine 2-Zimmer-Wohnung in Stuttgart."
RESTAURANT = "شو أحسن مطعم عربي بشتوتغارت؟"
JOB = "Suche Arbeit in Stuttgart, habe Erfahrung als Fahrer."


# --- Der Inhalt: worum geht es? -------------------------------------------
def test_ein_paketbeitrag_wird_erkannt() -> None:
    """Der Kern des Angebots - und er steht auf Arabisch da.

    Arabisch wird als Teilstring verglichen, weil Artikel und Praepositionen
    am Wort haengen. Wer das vereinheitlicht, zerstoert die Erkennung.
    """
    befund = lies(PAKET)

    assert befund.thema is Thema.VERSAND
    assert befund.absicht is Absicht.FRAGT
    assert befund.relevanz is Relevanz.HOCH
    assert befund.strecke, "Deutschland und ein Ziel - genau unsere Strecke"


def test_ein_reisender_ist_die_andere_haelfte_des_marktplatzes() -> None:
    """Er sucht nichts, er bietet - und genau deshalb ist er relevant."""
    befund = lies(REISENDER)

    assert befund.thema is Thema.REISE
    assert befund.absicht is Absicht.BIETET
    assert befund.relevanz is Relevanz.HOCH


@pytest.mark.parametrize(
    ("text", "thema"),
    [
        (WOHNUNG, Thema.WOHNUNG),
        (JOB, Thema.JOB),
        (RESTAURANT, Thema.EMPFEHLUNG),
        ("Verkaufe Sofa, 50 Euro, Abholung Bremen", Thema.KAUF_VERKAUF),
        ("Hat jemand einen Termin beim Jobcenter bekommen?", Thema.BEHOERDE),
    ],
)
def test_die_analyse_ist_nicht_auf_versand_beschraenkt(text: str, thema: Thema) -> None:
    """Punkt 14: Eine Gemeinschaftsgruppe redet ueber alles Moegliche.

    Ein Runner, der davon nur ein Thema erkennt, haelt alles Uebrige
    faelschlich fuer seine Gelegenheit.
    """
    assert lies(text).thema is thema


def test_jobcenter_ist_eine_behoerde_und_kein_stellenangebot() -> None:
    """Die eine echte Kollision der Wortlisten.

    Der lateinische Abgleich erlaubt die Wortfortsetzung ("arab" trifft
    "araber"), also trifft ``job`` auch ``jobcenter``. Deshalb steht Behoerde
    in der Reihenfolge **vor** Job - dieselbe Art Kollision wie zwischen der
    Stadt "Essen" und dem Kategoriebegriff fuer Speisen.
    """
    assert lies("Wer kennt einen Termin beim Jobcenter?").thema is Thema.BEHOERDE


def test_ein_zu_kurzer_text_ist_unlesbar_und_kein_thema() -> None:
    """Eine Aussage ueber **uns**, nicht ueber den Beitrag.

    Dieselbe Trennung wie bei ``Regelbefund.gelesen``: Aus einem Bild mit
    einem Smiley ein Thema zu raten hiesse, den Zufall zu befragen.
    """
    befund = lies("🙂")

    assert befund.thema is Thema.UNLESBAR
    assert not befund.lesbar
    assert befund.relevanz is Relevanz.KEINE


# --- Die Entscheidung: antworten? -----------------------------------------
def test_ohne_bezug_wird_nicht_geantwortet() -> None:
    """Punkt 45: ``NO_REPLY`` ist ein Ergebnis, kein Fehlschlag.

    Die Wohnungssuche ist der Fall, an dem sich das ganze Modul entscheidet:
    Sie ist eine echte Gelegenheit fuer **jemanden**, nur nicht fuer uns.
    """
    entscheidung = entscheide(lies(WOHNUNG), OFFEN)

    assert entscheidung.art is Antwortart.NO_REPLY
    assert not entscheidung.antwortet
    assert "kein Bezug" in entscheidung.grund
    assert not soll_antworten(lies(RESTAURANT))


def test_bei_klarem_bezug_darf_die_app_genannt_werden() -> None:
    """Und nur dann - mit Link, wenn die Gruppe Links erlaubt."""
    entscheidung = entscheide(lies(PAKET), OFFEN)

    assert entscheidung.art is Antwortart.DIRECT_APP_RECOMMENDATION
    assert entscheidung.mit_link


def test_verbotene_links_nehmen_den_link_nicht_die_antwort() -> None:
    """Ohne Link-Erlaubnis bleibt die Antwort moeglich - ohne Adresse."""
    entscheidung = entscheide(lies(PAKET), OHNE_LINKS)

    assert entscheidung.art is Antwortart.CONTEXTUAL_APP_MENTION
    assert not entscheidung.mit_link
    assert not soll_link_nutzen(lies(PAKET), OHNE_LINKS)


def test_ohne_kommentarerlaubnis_wird_gar_nichts_geschrieben() -> None:
    """Die erste Pruefung, und sie eruebrigt alle weiteren."""
    entscheidung = entscheide(lies(PAKET), OHNE_KOMMENTARE)

    assert entscheidung.art is Antwortart.NO_REPLY
    assert "keine Kommentare" in entscheidung.grund


def test_wer_selbst_anbietet_bekommt_keine_private_anfrage() -> None:
    """Punkt 20: Private Kontaktaufnahme ist kein Freifahrtschein.

    Der Reisende sucht nichts - er ist das Angebot. Eine Nachricht an ihn
    waere ein Gespraech ueber **sein** Angebot, nicht ueber unseres.
    """
    assert not soll_privat_anbieten(lies(REISENDER), OFFEN)
    assert soll_privat_anbieten(lies(PAKET), OFFEN)


def test_eine_jobsuche_fuehrt_zu_keiner_erfundenen_stelle() -> None:
    """Punkt 19: keine erfundenen Angebote.

    Das Modul kann gar nichts anderes: Es entscheidet ueber die **Naehe**
    einer Antwort, nie ueber den Inhalt eines Angebots. Eine Jobsuche hat mit
    einer Mitnahme-App nichts zu tun, also steht dort nichts.
    """
    entscheidung = entscheide(lies(JOB), OFFEN)

    assert entscheidung.art is Antwortart.NO_REPLY


def test_jede_gruppe_bekommt_den_link() -> None:
    """Seit dem 23.09.2026 (Anweisung des Nutzers): Die Gruppenregeln werden
    nicht mehr gelesen, und die Erlaubnis ist fuer jede Gruppe dieselbe - mit
    Link, damit jeder Kommentar seine Klicks der Gruppe gutschreibt."""
    leer = Erlaubnis()

    assert leer.links is True
    assert leer.kommentare is True
    assert soll_link_nutzen(lies(PAKET), leer)
