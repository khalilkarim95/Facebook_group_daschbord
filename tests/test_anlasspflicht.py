"""Der Schalter ``anlass_pflicht`` wirkt bis in die Entscheidung.

Der Anlass ist ein Lauf vom 21.09.2026 in einer Reisegruppe, in der noch
fuenf Kommentare offen waren und sichtbar passende Beitraege standen:

    kein Anlass: kein passender Beitrag
      (kein Bezug zum Angebot (sonstiges),
       keine passende Form (reise/unbekannt (سفر)))

Zwei Ursachen, eine je Zeile:

1. **"sonstiges"** - die Beitraege der Reisegruppen schreiben "نازلة من
   ألمانيا عالشام، متوفر وزن خفيف". Kein einziges Wort davon stand in
   ``_REISE``; der Beitrag, fuer den es die App gibt, galt als themenlos.
2. **"keine passende Form"** - ``marketing.anlass_pflicht`` stand seit dem
   13.09.2026 auf ``false``, wirkte aber nur in
   ``automatik.text_zur_gelegenheit``. Die Entscheidung war da laengst bei
   ``NO_LINK``, und dort gibt es keinen Vorrat. Der Schalter versprach *"der
   vorbereitete Text geht hinaus, sobald die Relevanz reicht"* und hielt es
   nicht.
"""

from __future__ import annotations

from fbgroups.marketing.entscheidung import (
    Anspruch,
    Antwortart,
    Erlaubnis,
    entscheide,
    soll_app_nennen,
)
from fbgroups.marketing.inhalt import Relevanz, Thema, lies

ERLAUBT = Erlaubnis(kommentare=True, links=True, werbung=True, regeln_gelesen=True)

#: Ein Reisender mit freiem Gepaeck - der haeufigste Beitrag dieser Gruppen.
FREIES_GEPAECK = "مرحبا نازلة من ألمانيا فيسبادن عالشام ب 27/9 متوفر وزن خفيف وأوراق"
#: Reisethema mit Ziel, aber **keine** Gelegenheit: eine Preisfrage. Keine
#: Bewegung ("نازل", "رايح"), kein freies Gepaeck - genau der Fall, fuer den
#: es den Schalter gibt.
NUR_REISE = "أسعار الطيران ع سوريا غالية هالسنة"
#: Und die Ankuendigung selbst, die seit dem 21.09.2026 eine Gelegenheit ist.
REISE_MIT_ZIEL = "نازل ع الشام يوم الجمعة"
WOHNUNG = "مرحبا بدي غرفة للايجار في برلين بسعر مناسب"


def _anspruch(*, pflicht: bool, stufe: Relevanz = Relevanz.MITTEL) -> Anspruch:
    return Anspruch(mindestrelevanz=stufe, anlass_pflicht=pflicht)


# --- 1. Die Reisenden schreiben anders, als die Liste dachte -------------

def test_ein_reisender_mit_freiem_gepaeck_traegt_ein_thema() -> None:
    """"نازلة ... متوفر وزن خفيف" ist eine Reise, keine "sonstiges"."""
    befund = lies(FREIES_GEPAECK)

    assert befund.thema is Thema.REISE
    assert befund.anlass.value == "platz_im_koffer", "und es ist der Platz im Koffer"


def test_freies_gepaeck_bekommt_auch_mit_anlasspflicht_einen_kommentar() -> None:
    """Der Halbsatz steht da - hier war nie ein Schalter noetig.

    Genau deshalb steht die Wortliste **neben** dem Schalter und nicht statt
    seiner: Sie bringt den richtigen Text (den zum Platz im Koffer), er
    bringt ueberhaupt einen.
    """
    entscheid = entscheide(lies(FREIES_GEPAECK), ERLAUBT, _anspruch(pflicht=True))

    assert entscheid.art is Antwortart.DIRECT_APP_RECOMMENDATION


# --- 2. Der Schalter reicht jetzt bis in die Entscheidung ----------------

def test_ohne_anlasspflicht_genuegt_die_schwelle_der_gruppe() -> None:
    """**Die Forderung vom 21.09.2026**: In dieser Gruppe wird weitergearbeitet."""
    befund = lies(NUR_REISE)
    assert befund.anlass.value == "keiner", "kein Halbsatz - das ist der Fall"
    assert befund.relevanz is Relevanz.MITTEL

    mit = entscheide(befund, ERLAUBT, _anspruch(pflicht=True))
    ohne = entscheide(befund, ERLAUBT, _anspruch(pflicht=False))

    assert mit.art is Antwortart.NO_REPLY, "eingeschaltet bleibt es bei der alten Regel"
    assert ohne.art is Antwortart.DIRECT_APP_RECOMMENDATION


def test_der_schalter_hebt_die_schwelle_der_gruppe_nicht_auf() -> None:
    """Der **Ort** entscheidet weiter - sonst waere er ein Schalter fuer Spam.

    In einer Gemeinschaftsgruppe (Klasse B) verlangt der Anspruch ``hoch``.
    Eine blosse Reiseankuendigung traegt ``mittel`` und bekommt dort auch
    ohne Anlasspflicht nichts.
    """
    ohne = entscheide(
        lies(NUR_REISE), ERLAUBT, _anspruch(pflicht=False, stufe=Relevanz.HOCH)
    )

    assert ohne.art is Antwortart.NO_REPLY
    assert "zu schwach" in ohne.grund


def test_ohne_bezug_bleibt_es_bei_nichts() -> None:
    """Eine Wohnungssuche hat mit uns nichts zu tun - mit Schalter wie ohne."""
    for pflicht in (True, False):
        entscheid = entscheide(lies(WOHNUNG), ERLAUBT, _anspruch(pflicht=pflicht))
        assert entscheid.art is Antwortart.NO_REPLY
        assert "kein Bezug" in entscheid.grund


def test_bewegung_und_ziel_sind_eine_gelegenheit() -> None:
    """**Die Anweisung vom 21.09.2026**, und die Schranke bleibt die Kombination.

    "نازل ع الشام" ist in einer Gruppe namens "مسافر من أوروبا إلى سوريا"
    kein Small Talk, sondern die Haelfte des Marktplatzes. Ein Wort allein
    genuegt dafuer weiterhin nicht.
    """
    from fbgroups.marketing.inhalt import Anlass

    assert lies(REISE_MIT_ZIEL).anlass is Anlass.BIETET_MITNAHME
    assert lies("رايح ع الشغل بكرا").anlass is Anlass.KEINER, "Bewegung ohne Ziel"
    assert lies("سوريا حلوة").anlass is Anlass.KEINER, "Ziel ohne Bewegung"


def test_die_vorgabe_bleibt_die_strenge_regel() -> None:
    """Wer nichts angibt, bekommt die Regel vom 13.09.2026.

    Dieselbe Aufteilung wie bei ``mitgliedschaft_pflicht`` und
    ``regeln_zuerst``: Der Schutz gilt im Code, gesagt wird es in
    ``settings.yaml``.
    """
    assert Anspruch().anlass_pflicht is True
    assert not soll_app_nennen(lies(NUR_REISE), ERLAUBT)
    assert soll_app_nennen(lies(NUR_REISE), ERLAUBT, _anspruch(pflicht=False))


def test_der_schalter_reist_zum_arbeitsrechner_mit() -> None:
    """Der Stand liegt auf dem Server, also auch der Schalter.

    Ohne das Feld entschiede im Fernbetrieb - dem Regelfall - die
    ``settings.yaml`` des Arbeitsrechners, und die ist dort belanglos.
    """
    from pathlib import Path

    from fbgroups.marketing.automatik import vorgaben_lesen

    web = Path("src/fbgroups/marketing/web.py").read_text(encoding="utf-8")
    assert '"anlass_pflicht": anspruch.anlass_pflicht,' in web, "gesendet"

    _erlaubnis, anspruch, _verbraucht = vorgaben_lesen(
        {"anspruch": {"mindestrelevanz": "mittel", "anlass_pflicht": False}}
    )
    assert anspruch.anlass_pflicht is False, "und gelesen"

    # Ein aelterer Server sendet das Feld nicht - dann gilt die Vorgabe.
    _e, alt, _v = vorgaben_lesen({"anspruch": {"mindestrelevanz": "mittel"}})
    assert alt.anlass_pflicht is True
