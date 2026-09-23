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
    LINKMODUS,
    Antwortart,
    Erlaubnis,
    Linkmodus,
    entscheide,
    soll_antworten,
    soll_app_nennen,
    soll_link_nutzen,
    soll_privat_anbieten,
)
from fbgroups.marketing.inhalt import Absicht, Relevanz, Thema, lies
from fbgroups.marketing.qualifikation import Qualifikation, Regelbefund

# --- Erlaubnisse, wie sie aus echten Gruppen entstehen ---------------------
OFFEN = Erlaubnis.aus_regeln(Regelbefund(gelesen=True), Qualifikation.GEEIGNET)
UNGELESEN = Erlaubnis.aus_regeln(None, Qualifikation.BEWERTUNG)
OHNE_LINKS = Erlaubnis.aus_regeln(
    Regelbefund(gelesen=True, keine_links=True), Qualifikation.OHNE_LINKS
)
#: Eine Gruppe, deren Regeln Werbung verbieten. Seit dem 21.09.2026 ist das
#: eine Auskunft und keine Sperre mehr: ``beurteile`` macht daraus kein
#: ``UNGEEIGNET``, also bleibt die Erlaubnis die einer gewoehnlichen Gruppe.
OHNE_WERBUNG = Erlaubnis.aus_regeln(
    Regelbefund(gelesen=True, keine_werbung=True), Qualifikation.GEEIGNET
)
OHNE_KOMMENTARE = Erlaubnis.aus_regeln(
    Regelbefund(gelesen=True), Qualifikation.OHNE_KOMMENTARE
)

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


def test_ungelesene_regeln_kosten_den_link_nicht_den_kommentar() -> None:
    """Was von "UNKNOWN heisst nicht erlaubt" bleibt - und was nicht.

    Bis zum 21.09.2026 fuehrten ungelesene Regeln zum privaten Hinweis, und
    weil es fuer ``Linkmodus.NO_LINK`` keinen Textvorrat gibt, hiess das:
    **gar kein Kommentar**. Genau diese Kette ist auf Anweisung des Nutzers
    entfernt.

    Vorsichtig bleibt die Erlaubnis dort, wo es um die **Annahme** geht: Der
    Link braucht weiterhin eine gelesene Regel (``Erlaubnis.links``), denn
    "Link im Kommentar" ist der haeufigste Ablehnungsgrund. Die App wird also
    genannt, aber nicht verlinkt.
    """
    entscheidung = entscheide(lies(PAKET), UNGELESEN)

    assert entscheidung.art is Antwortart.CONTEXTUAL_APP_MENTION
    assert not entscheidung.mit_link
    assert soll_app_nennen(lies(PAKET), UNGELESEN)
    assert not soll_link_nutzen(lies(PAKET), UNGELESEN)


def test_verbotene_links_nehmen_den_link_nicht_die_antwort() -> None:
    """Punkt 3 und 28: Die Regel bindet, die Antwort bleibt moeglich.

    Eine Gruppe ohne Links nimmt denselben Hinweis ohne Link - die Regeln zu
    umgehen ist ausdruecklich nicht das Ziel, auf die Antwort zu verzichten
    aber auch nicht noetig.
    """
    entscheidung = entscheide(lies(PAKET), OHNE_LINKS)

    assert entscheidung.art is Antwortart.CONTEXTUAL_APP_MENTION
    assert not entscheidung.mit_link
    assert not soll_link_nutzen(lies(PAKET), OHNE_LINKS)


def test_ein_werbeverbot_haelt_den_kommentar_nicht_mehr_auf() -> None:
    """Umgekehrt zum Stand bis zum 21.09.2026 - Anweisung des Nutzers.

    Bis dahin machte ``qualifikation.beurteile`` aus ``keine_werbung`` ein
    ``UNGEEIGNET``, und damit fiel die Gruppe ganz aus: keine Kommentare,
    keine Beitraege, nichts. Im Betrieb war das die haeufigste Ursache
    dafuer, dass eine Runde durch dreizehn Gruppen **null** Kommentare
    schrieb.

    Die Gruppen einer Kampagne hat ein Mensch ausgesucht und eingestuft; ob
    dort geworben werden darf, ist damit beantwortet. Was die **Annahme**
    betrifft, bindet unveraendert weiter - die Linkregeln und jede
    Beobachtung.
    """
    entscheidung = entscheide(lies(REISENDER), OHNE_WERBUNG)

    assert OHNE_WERBUNG.kommentare
    assert entscheidung.art is not Antwortart.NO_REPLY
    assert LINKMODUS[entscheidung.art] is not Linkmodus.NO_LINK


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


def test_die_vorgabe_einer_erlaubnis_ist_die_vorsichtige() -> None:
    """Wer sie ohne Angaben baut, hat nichts gelesen - und darf wenig.

    Genau die Verwechslung, die Punkt 4 verbietet: Ein Objekt, dessen
    Vorgaben "alles erlaubt" hiessen, machte aus fehlendem Wissen eine
    Erlaubnis.
    """
    leer = Erlaubnis()

    assert leer.links is False
    assert leer.regeln_gelesen is False
