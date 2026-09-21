"""Wie die Reisegruppen wirklich schreiben - und was trotzdem nichts ist.

Die zwoelf Proben des Nutzers (21.09.2026), eine je Zeile. Sie sind der
Abnahmetest des Wortschatzes: Die ersten acht und die zwoelfte sollen eine
Gelegenheit sein, die Nummern neun bis elf ausdruecklich nicht.

Der Grundsatz dahinter steht in der Anweisung: *"Nicht einfach jedes Wort wie
سوريا, الشام, دمشق, مسافر, وزن als alleinigen Anlass behandeln."* Ein Wort
ist ein Thema, eine **Kombination** ist eine Gelegenheit - Bewegung und Ziel
("نازل ع الشام") oder ein Halbsatz ueber freies Gepaeck ("معي وزن").
"""

from __future__ import annotations

import pytest

from fbgroups.marketing.entscheidung import Anspruch, Antwortart, Erlaubnis, entscheide
from fbgroups.marketing.inhalt import Anlass, Relevanz, lies

#: Eine Reisegruppe (Klasse A): Werbung erlaubt, Links erlaubt, Regeln gelesen.
ERLAUBT = Erlaubnis(kommentare=True, links=True, werbung=True, regeln_gelesen=True)
#: Ihre Schwelle laut ``settings.yaml`` - und der Schalter, wie er dort steht.
REISEGRUPPE = Anspruch(mindestrelevanz=Relevanz.MITTEL, anlass_pflicht=False)


def _antwortet(text: str) -> bool:
    return entscheide(lies(text), ERLAUBT, REISEGRUPPE).art is not Antwortart.NO_REPLY


@pytest.mark.parametrize(
    ("text", "anlass"),
    [
        ("نازل ع الشام يوم الجمعة", Anlass.BIETET_MITNAHME),
        ("مسافر ع سوريا الأسبوع الجاي", Anlass.BIETET_MITNAHME),
        ("مين مسافر ع دمشق؟", Anlass.SUCHT_REISENDEN),
        ("معي وزن متوفر", Anlass.PLATZ_IM_KOFFER),
        ("بقي معي كم كيلو", Anlass.PLATZ_IM_KOFFER),
        ("عندي مساحة بالشنطة", Anlass.PLATZ_IM_KOFFER),
        ("في مجال آخد غرض", Anlass.PLATZ_IM_KOFFER),
        ("رايح ع حلب بكرا", Anlass.BIETET_MITNAHME),
        ("مسافر الجمعة ومعي وزن", Anlass.PLATZ_IM_KOFFER),
    ],
)
def test_diese_beitraege_sind_eine_gelegenheit(text: str, anlass: Anlass) -> None:
    """Reiseankuendigung oder freies Gepaeck - und der Anlass sagt, welcher Text."""
    befund = lies(text)

    assert befund.anlass is anlass
    assert _antwortet(text), "in einer Reisegruppe geht darunter ein Kommentar"


@pytest.mark.parametrize(
    "text",
    [
        "سوريا حلوة",        # ein Ziel ohne Bewegung
        "دمشق",              # ein einzelnes Wort
        "وزن",               # ein einzelnes Wort
        "رايح ع الشغل بكرا",  # Bewegung ohne Ziel
        "في مجال العمل مناصب جديدة",  # "مجال" im ganz anderen Sinn
    ],
)
def test_ein_einzelnes_wort_ist_keine_gelegenheit(text: str) -> None:
    """**Die Schranke.** Sonst stuende unter jedem zweiten Beitrag Werbung.

    Geprüft wird beides: kein Anlass **und** keine Antwort. Das Erste ist
    die Regel, das Zweite ihre Wirkung - und nur das Zweite sieht ein
    Mensch in der Gruppe.
    """
    assert lies(text).anlass is Anlass.KEINER
    assert not _antwortet(text)


def test_in_der_gemeinschaftsgruppe_gilt_weiter_die_hoehere_schwelle() -> None:
    """Der Ort entscheidet, nicht der Wortschatz.

    Dieselbe Ankuendigung, ein anderer Ort: In einer Gemeinschaftsgruppe
    (Klasse B, Schwelle "hoch") bleibt es bei nichts, solange nicht die
    ganze Strecke dasteht. Der erweiterte Wortschatz hebt die Zielpriorität
    nicht auf.
    """
    gemeinschaft = Anspruch(mindestrelevanz=Relevanz.HOCH, anlass_pflicht=False)

    knapp = entscheide(lies("نازل ع الشام يوم الجمعة"), ERLAUBT, gemeinschaft)
    ganze_strecke = entscheide(
        lies("نازل من ألمانيا ع الشام يوم الجمعة"), ERLAUBT, gemeinschaft
    )

    assert knapp.art is Antwortart.NO_REPLY
    assert ganze_strecke.art is Antwortart.DIRECT_APP_RECOMMENDATION
