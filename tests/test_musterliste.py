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

from fbgroups.automation.actions import _artikel_auswerten, mit_bildtexten
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


# --- Die Verdrahtung: kommen die Bildtexte wirklich an? ---------------------
#
# Die Tests oben pruefen ``mit_bildtexten`` fuer sich - und genau deshalb fiel
# nicht auf, dass der Artikel die Bildtexte nie lieferte: Gelesen wurden sie
# mit ``eval_on_selector_all``, und das hat ein Playwright-``Locator`` nicht.
# Der Fehler verschwand im ``except``, und "keine Bildtexte" sieht genauso aus
# wie ein Beitrag ohne Bild.


class _Treffer:
    """Die Treffer eines Selektors - wie ein ``Locator``, nur mit festen Werten."""

    def __init__(self, werte: list[str | None]) -> None:
        self._werte = werte

    def evaluate_all(self, _js: str, _name: str) -> list[str | None]:
        return list(self._werte)

    def all(self):  # noqa: ANN201 - der alte Weg, und er darf nicht mehr vorkommen
        raise AssertionError(
            "jedes Element einzeln zu fragen wartet bis zu 30 Sekunden je Element"
        )

    @property
    def first(self) -> _Treffer:
        return self

    def count(self) -> int:
        return len(self._werte)


class _Artikel:
    """Ein Artikel mit genau den Methoden eines Playwright-``Locator``.

    Ausdruecklich **ohne** ``eval_on_selector_all``: Ein Aufruf dorthin
    scheitert hier wie im Browser.
    """

    def __init__(self, *, hrefs: list[str], alts: list[str], text: str) -> None:
        self._hrefs = hrefs
        self._alts = alts
        self._text = text

    def locator(self, selektor: str) -> _Treffer:
        if selektor == "a[href]":
            return _Treffer(self._hrefs)
        if selektor == "img[alt]":
            return _Treffer(self._alts)
        return _Treffer([])  # keine Reaktionen

    def inner_text(self, *, timeout: float | None = None) -> str:
        assert timeout is not None, "ohne Frist wartet Playwright 30 Sekunden"
        return self._text


def test_die_bildtexte_kommen_wirklich_im_artikel_an() -> None:
    """Die Ankuendigung steht nur im Bild - und erreicht die Inhaltspruefung."""
    artikel = _Artikel(
        hrefs=["https://www.facebook.com/groups/123/posts/456/?__cft__=x"],
        alts=["قد تكون صورة نص 'نازل ع الشام يوم الجمعة معي وزن'"],
        text="",
    )

    daten = _artikel_auswerten(artikel, "123")

    assert daten is not None
    assert daten["post_url"] == "https://www.facebook.com/groups/123/posts/456/"
    assert "نازل ع الشام" in daten["text"]
    assert lies(daten["text"]).anlass is Anlass.PLATZ_IM_KOFFER


def test_ein_artikel_ohne_beitragsadresse_ergibt_nichts() -> None:
    artikel = _Artikel(hrefs=["/groups/123/user/9"], alts=[], text="Hallo")

    assert _artikel_auswerten(artikel, "123") is None
