"""Was ``post_to_group`` meldet, wenn die Adresse nicht verschwinden will.

Die Zusicherung dieser Datei ist eine einzige: **Kein stiller Erfolg.** Steht
die nackte Adresse am Ende doch im Beitrag, weil Facebook die Vorschaukarte
ohne sie nicht gehalten hat, dann sagt der Ausgang das - und der Lauf schreibt
es ins Protokoll.

Dieselbe Lehre wie am 12.09.2026 bei ``comment_on_post``: Die Funktion meldete
jeden Versuch als Erfolg, bei dem das Feld beschreibbar war - auch den, den
die Gruppe nie angenommen hat. Was nicht nachgesehen wird, faellt Wochen
spaeter auf, und dann steht es dreihundertmal in Gruppen.

Gefahren wird gegen eine nachgebaute Seite. Sie ist absichtlich duenn: Was
hier geprueft wird, ist die **Entscheidung** - haelt die Karte, was steht dann
im Feld, was kommt heraus -, und nicht Facebooks Oberflaeche.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from fbgroups.automation.actions import Beitragsausgang, post_to_group

TEXT = "مرحبا شباب\nحمّله من هنا:\nhttps://go.b-tarikak.de/r/k7m2x9q"
OHNE = "مرحبا شباب\nحمّله من هنا:"


class Seite:
    """Eine Facebook-Seite, die genau so viel kann wie der Ablauf fragt.

    ``karte_haelt`` ist der Schalter, um den es geht: Er entscheidet, ob die
    Vorschaukarte stehenbleibt, nachdem die Adresse aus dem Entwurf
    verschwunden ist.
    """

    def __init__(self, *, karte_haelt: bool) -> None:
        self.karte_haelt = karte_haelt
        self.entwurf = ""
        self.verlauf: list[str] = []
        self.gepostet = False

    # -- was der Ablauf an der Seite tut ---------------------------------
    def _karte_da(self) -> bool:
        if "http" in self.entwurf:
            return True
        return self.karte_haelt and bool(self.verlauf)

    def locator(self, selektor: str):  # noqa: ANN202 - Nachbau, kein Vertrag
        treffer = 0
        if "img" in selektor:
            # Ein Bild mehr als vorher ist das zweite Zeichen fuer die Karte.
            treffer = 1 if self._karte_da() else 0
        elif "Vorschau" in selektor or "preview" in selektor:
            treffer = 1 if self._karte_da() else 0
        else:
            treffer = 1
        return Element(self, treffer)

    def wait_for_timeout(self, _ms: int) -> None:
        return None

    def goto(self, *_a, **_k) -> None:
        return None

    def evaluate(self, *_a, **_k) -> None:
        return None

    def close(self) -> None:
        return None

    @property
    def keyboard(self):  # noqa: ANN202
        return Tastatur(self)

    def inner_text(self, _was: str) -> str:
        return ""


class Tastatur:
    def __init__(self, seite: Seite) -> None:
        self.seite = seite

    def insert_text(self, text: str) -> None:
        self.seite.entwurf = text
        self.seite.verlauf.append(text)

    def press(self, taste: str) -> None:
        if taste == "Control+A":
            # Alles markiert - der naechste ``insert_text`` ersetzt es.
            self.seite.entwurf = ""


class Element:
    def __init__(self, seite: Seite, treffer: int) -> None:
        self.seite = seite
        self.treffer = treffer

    @property
    def first(self):  # noqa: ANN202
        return self

    def count(self) -> int:
        return self.treffer

    def wait_for(self, **_k) -> None:
        return None

    def click(self, **_k) -> None:
        self.seite.gepostet = True


def _kontext(seite: Seite) -> MagicMock:
    kontext = MagicMock()
    kontext.new_page.return_value = seite
    return kontext


def test_haelt_die_karte_verschwindet_die_adresse_aus_dem_text() -> None:
    """Der Regelfall - und das Ziel des ganzen Handgriffs.

    Die Karte bleibt anklickbar und fuehrt weiterhin auf ``/r/{code}``, der
    Klick wird also weiterhin dieser Gruppe gutgeschrieben. Was verschwindet,
    ist allein das Aktenzeichen im Text.
    """
    seite = Seite(karte_haelt=True)

    ausgang = post_to_group(_kontext(seite), "https://facebook.com/groups/1", TEXT)

    assert ausgang == Beitragsausgang(
        erfolg=True, hinweis="", karte=True, link_sichtbar=False
    )
    assert seite.entwurf == OHNE
    assert "go.b-tarikak.de" not in seite.entwurf


def test_haelt_die_karte_nicht_bleibt_der_link_stehen_und_es_wird_gesagt() -> None:
    """**Die Zusicherung dieser Datei.**

    Kein Fehlschlag: Der Beitrag steht, und sein Link wird gezaehlt. Aber auch
    kein stiller Erfolg - ``link_sichtbar`` und ``hinweis`` sagen, was in der
    Gruppe zu sehen ist, und der Lauf schreibt es ins Protokoll.
    """
    seite = Seite(karte_haelt=False)

    ausgang = post_to_group(_kontext(seite), "https://facebook.com/groups/1", TEXT)

    assert ausgang.erfolg is True
    assert ausgang.link_sichtbar is True
    assert ausgang.hinweis
    # Wiederhergestellt, nicht halbiert: Ein Beitrag ohne Link waere schlimmer
    # als einer mit sichtbarer Adresse - seine Gruppe bekaeme nie einen Klick.
    assert seite.entwurf == TEXT


def test_ohne_karte_wird_gar_nicht_erst_gekuerzt() -> None:
    """Ohne Karte naehme das Entfernen dem Beitrag seinen Link ersatzlos."""
    seite = Seite(karte_haelt=False)
    seite.karte_haelt = False

    ausgang = post_to_group(
        _kontext(seite), "https://facebook.com/groups/1", "Text ohne Adresse"
    )

    assert ausgang.erfolg is True
    assert ausgang.karte is False
    assert ausgang.link_sichtbar is True
    assert seite.entwurf == "Text ohne Adresse"


def test_der_schalter_laesst_den_text_unangetastet() -> None:
    """``link_verbergen=False`` ist der Ausweg ohne Codeaenderung.

    Fuer den Fall, dass Facebook den Handgriff einmal nicht mehr mitmacht -
    eine Zahl statt einer Zeile Programm, wie ueberall hier.
    """
    seite = Seite(karte_haelt=True)

    ausgang = post_to_group(
        _kontext(seite), "https://facebook.com/groups/1", TEXT, link_verbergen=False
    )

    assert ausgang.erfolg is True
    assert ausgang.link_sichtbar is True
    assert seite.entwurf == TEXT


def test_der_lauf_traegt_den_hinweis_ins_protokoll() -> None:
    """``browser_schritt_post`` ist der Weg, den die Automatik geht.

    Er meldet Erfolg **mit** dem Hinweis. Ihn dort fallenzulassen hiesse, ihn
    genau an der Stelle zu verlieren, an der dreihundert Beitraege entstehen.
    """
    from fbgroups.marketing.automatik import browser_schritt_post

    seite = Seite(karte_haelt=False)
    ergebnis = browser_schritt_post(_kontext(seite), "https://facebook.com/groups/1", TEXT)

    assert ergebnis.erfolg is True
    assert "Link sichtbar" in ergebnis.fehler
