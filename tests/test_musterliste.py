"""Die Musterliste des Nutzers (23.09.2026) - was davon uebernommen wurde.

Die Liste kam als eigenes Skript mit rund hundert Mustern, jedes allein ein
Treffer. Uebernommen sind zwei Dinge: die **Bildbeschreibungen** als zweite
Textquelle und die Formen, die dem Wortschatz in ``inhalt.py`` fehlten.
**Nicht** uebernommen ist die Regel "ein Wort genuegt": "شي", "مكان",
"محل", "نقل" und "وصل" stehen in fast jedem Beitrag, und der arabische
Teilstringabgleich machte daraus in jeder Gruppe eine Gelegenheit - genau
die zu breiten Schlagwoerter, die die Anweisung vom 21.09.2026 ausschliesst.
"""

from __future__ import annotations

import pytest

from fbgroups.automation.actions import mit_bildtexten
from fbgroups.marketing.inhalt import Anlass, Thema, lies


def test_ein_beitrag_nur_aus_bild_wird_lesbar() -> None:
    """Die Ankuendigung steht im Bild, der Artikeltext ist leer."""
    text = mit_bildtexten("", ["قد تكون صورة نص 'نازل ع الشام يوم الجمعة معي وزن'"])

    befund = lies(text)

    assert befund.lesbar
    assert befund.anlass is Anlass.PLATZ_IM_KOFFER


def test_bildtexte_ergaenzen_und_doppelte_fallen_weg() -> None:
    text = mit_bildtexten("Hallo", ["Bild A", None, "", "Bild A", "Bild B"])

    assert text == "Hallo\nBild A Bild B"


def test_ohne_bildtexte_bleibt_der_text_unveraendert() -> None:
    assert mit_bildtexten("Hallo", []) == "Hallo"
    assert mit_bildtexten("Hallo", [None, "  "]) == "Hallo"


def test_bildtexte_sind_gedeckelt() -> None:
    text = mit_bildtexten("", ["x" * 1000])

    assert len(text) == 300


@pytest.mark.parametrize(
    "text",
    [
        "رحلات ع سوريا كل اسبوع",
        "عندي حقائب فاضية للبيع رخيصة",  # Thema Reise, keine Strecke
        "شنط سفر كبيرة",
    ],
)
def test_die_mehrzahlformen_tragen_das_reisethema(text: str) -> None:
    assert lies(text).thema is Thema.REISE


def test_ein_pass_zum_mitgeben_ist_ein_gegenstand() -> None:
    befund = lies("بدي ابعت جواز سفر ع دمشق")

    assert befund.thema is Thema.VERSAND
    assert befund.anlass is Anlass.GEGENSTAND


@pytest.mark.parametrize(
    "text",
    [
        "بدي محل بالشارع الرئيسي للايجار",
        "في شي حدا بيعرف مكان منيح للأكل",
        "نقل مدرسة الولاد لبرلين",
        "وصل الطقس البارد لعندنا",
    ],
)
def test_die_breiten_woerter_der_liste_bleiben_ohne_wirkung(text: str) -> None:
    """Jedes davon waere in der Musterliste allein ein Treffer gewesen."""
    assert lies(text).anlass is Anlass.KEINER
