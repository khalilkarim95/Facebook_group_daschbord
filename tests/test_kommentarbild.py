"""Bild und Schlusssatz statt Adresse im Kommentar (24.09.2026).

Wunsch des Nutzers: Die ausgeschriebene Adresse (``https://b-tarikak.de/home``)
machte bei Facebook Probleme. Jeder automatische Kommentar endet jetzt mit

    الرابط المباشر للتحميل موجود في البايو (أعلى الصفحة) 👇

und traegt das Bild ``config/bilder/kommentar.jpg`` - darauf stehen Name,
Adresse und der Weg zur App.
"""

from __future__ import annotations

from pathlib import Path

from fbgroups.automation import actions
from fbgroups.marketing import automatik
from fbgroups.marketing.beitrag import kommentar_adresse, kommentar_bild, mit_kommentaradresse

SCHLUSS = "الرابط المباشر للتحميل موجود في البايو (أعلى الصفحة) 👇"


def test_der_kommentar_endet_mit_dem_satz_und_ohne_adresse(config) -> None:
    text = mit_kommentaradresse("شفت تطبيق بطريقك من سوريا.", kommentar_adresse(config))

    assert text == f"شفت تطبيق بطريقك من سوريا. {SCHLUSS}"
    assert "http" not in text


def test_das_bild_liegt_im_projekt(config) -> None:
    bild = kommentar_bild(config)

    assert bild is not None and bild.is_file()
    assert bild.suffix == ".jpg"
    assert bild.is_relative_to(config.root / "config"), "kommt mit jedem Ausrollen mit"


def test_ohne_datei_kein_bild(config) -> None:
    class _Ohne:
        root = config.root

        def get(self, *pfad, default=None):
            return "config/bilder/gibt-es-nicht.jpg" if pfad[-1] == "kommentar_bild" else default

    assert kommentar_bild(_Ohne()) is None


def test_der_lauf_reicht_das_bild_an_den_browser(config) -> None:
    aufrufe: list[dict] = []

    def kommentieren(_c, _url, _text, bild=None):
        aufrufe.append({"bild": bild})

    automatik._mit_bild(kommentieren, config)(None, "p/1", "t")

    assert aufrufe == [{"bild": kommentar_bild(config)}]


# --- Das Anhaengen im Browser, nachgebaut ------------------------------------

class _Menge:
    def __init__(self, zahl=lambda: 0, beim_hochladen=None) -> None:  # noqa: ANN001
        self._zahl = zahl
        self._hochladen = beim_hochladen

    def count(self) -> int:
        return self._zahl()

    @property
    def first(self) -> _Menge:
        return self

    @property
    def last(self) -> _Menge:
        return self

    def set_input_files(self, pfad: str, timeout: float = 0) -> None:
        self._hochladen(pfad)


class _Formular:
    def __init__(self) -> None:
        self.hochgeladen: list[str] = []

    def count(self) -> int:
        return 1

    def locator(self, selektor: str) -> _Menge:
        if selektor == "input[type='file']":
            return _Menge(lambda: 1, self.hochgeladen.append)
        return _Menge(lambda: len(self.hochgeladen))  # die Vorschau: ein Bild mehr


class _Feld:
    def __init__(self, formular: _Formular) -> None:
        self.formular = formular

    def locator(self, _selektor: str) -> _Formular:
        return self.formular


class _Seite:
    def wait_for_timeout(self, *_a) -> None: ...

    def locator(self, _selektor: str) -> _Menge:
        raise AssertionError("das Dateifeld des Formulars genuegt - nicht das der Seite")


def test_das_bild_geht_in_das_formular_des_kommentarfelds(tmp_path: Path) -> None:
    bild = tmp_path / "b.jpg"
    bild.write_bytes(b"x")
    formular = _Formular()

    assert actions._bild_anhaengen(_Seite(), _Feld(formular), bild)
    assert formular.hochgeladen == [str(bild)]


def test_ohne_vorschau_gilt_das_bild_als_nicht_angekommen(tmp_path: Path) -> None:
    class _Stumm(_Formular):
        def locator(self, selektor: str) -> _Menge:
            if selektor == "input[type='file']":
                return _Menge(lambda: 1, self.hochgeladen.append)
            return _Menge(lambda: 0)

    assert not actions._bild_anhaengen(_Seite(), _Feld(_Stumm()), tmp_path / "b.jpg")
