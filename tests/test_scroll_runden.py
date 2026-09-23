"""Bis zu 15 Scroll-Runden je Gruppe - mit dem Urteil nach jeder Runde.

Anweisung des Nutzers vom 23.09.2026: nicht "Gruppe oeffnen, einmal
nachsehen, nichts gefunden, weiter", sondern

    Gruppe oeffnen -> Runde 1 scrollen + Beitraege beurteilen -> Runde 2
    -> ... -> Runde 15 -> sobald ein geeigneter Beitrag da ist: kommentieren
    -> sonst naechste Gruppe.

Sein Analyseskript (``GroupPostAnalyzer.scan_and_analyze_current_group``)
stand nie im Projekt; sein Kern - scrollen, ``div[role='article']`` lesen,
Text und Bildtexte zusammen auswerten - steckte schon in **einer**
Funktion, ``actions.fetch_top_posts``. Die wurde hier ausgebaut, keine
zweite daneben gestellt:

* hoechstens ``automatik.scroll_runden`` (15) statt fest 5 Runden,
* nach jeder Runde das Urteil des Laufs (``automatik.geeignet_fuer``) -
  und erst wenn ein Beitrag es besteht, wird aufgehoert,
* schon kommentierte Beitraege zaehlen nicht mit (``bekannt``).

Die Seite ist hier nachgebaut: Jede Runde zeigt andere Artikel, so wie der
virtualisierte Beitragsstrom von Facebook.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.automation.actions import SCROLL_RUNDEN, fetch_top_posts
from fbgroups.marketing import automatik, lauf
from fbgroups.marketing.entscheidung import Anspruch, Erlaubnis
from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    CampaignStatus,
    GroupMarketing,
    MarketingStatus,
)
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

GRUPPE = "123"
UNPASSEND = "شقة للايجار في برلين"
PASSEND = "بدي ابعت غرض صغير لسوريا"
IM_BILD = "قد تكون صورة نص 'نازل ع الشام يوم الجمعة معي وزن'"


# --- Die nachgebaute Gruppenseite --------------------------------------------

class _Treffer:
    def __init__(self, werte: list) -> None:
        self._werte = werte

    def evaluate_all(self, _js: str, _name: str) -> list:
        return list(self._werte)

    @property
    def first(self) -> _Treffer:
        return self

    def count(self) -> int:
        return len(self._werte)


class _Artikel:
    def __init__(self, nummer: int, text: str = "", alt: str = "") -> None:
        self.url = f"https://www.facebook.com/groups/{GRUPPE}/posts/{nummer}/"
        self._text = text
        self._alt = [alt] if alt else []

    def locator(self, selektor: str) -> _Treffer:
        if selektor == "a[href]":
            return _Treffer([self.url])
        if selektor == "img[alt]":
            return _Treffer(self._alt)
        return _Treffer([])

    def inner_text(self, *, timeout: float | None = None) -> str:
        return self._text


class _Liste:
    def __init__(self, seite: _Seite) -> None:
        self._seite = seite

    def all(self) -> list[_Artikel]:
        self._seite.gelesen += 1
        runde = min(self._seite.gescrollt, len(self._seite.runden) - 1)
        return self._seite.runden[runde]


class _Seite:
    """Runde fuer Runde andere Artikel - wie Facebooks virtualisierter Strom."""

    def __init__(self, runden: list[list[_Artikel]]) -> None:
        self.runden = runden
        self.gescrollt = 0
        self.gelesen = 0

    def goto(self, *_a, **_k) -> None: ...
    def wait_for_selector(self, *_a, **_k) -> None: ...
    def wait_for_timeout(self, *_a, **_k) -> None: ...
    def close(self) -> None: ...

    def evaluate(self, js: str) -> None:
        if "scrollBy" in js:
            self.gescrollt += 1

    def eval_on_selector_all(self, *_a) -> list:
        return []

    def locator(self, _selektor: str) -> _Liste:
        return _Liste(self)


class _Kontext:
    def __init__(self, seite: _Seite) -> None:
        self.seite = seite

    def new_page(self) -> _Seite:
        return self.seite


def _seite(anzahl: int, *, passend_in: int = 0, text: str = PASSEND, alt: str = "") -> _Seite:
    """``anzahl`` Runden, in jeder ein unpassender Beitrag; in ``passend_in`` der passende."""
    runden = []
    for runde in range(1, anzahl + 1):
        artikel = [_Artikel(runde, UNPASSEND)]
        if runde == passend_in:
            artikel.append(_Artikel(1000 + runde, text, alt))
        runden.append(artikel)
    return _Seite(runden)


def _geeignet(config):  # noqa: ANN001, ANN202
    return automatik.geeignet_fuer(
        config,
        GRUPPE,
        erlaubnis=Erlaubnis(),
        anspruch=automatik.anspruch_aus_config(config),
        rueckfall="",
    )


# --- 1. bis 5.: die Suche in einer Gruppe ------------------------------------

def test_die_vorgabe_sind_fuenfzehn_runden(config) -> None:
    assert SCROLL_RUNDEN == 15
    assert automatik.scroll_runden(config) == 15


def test_ohne_geeigneten_beitrag_werden_alle_15_runden_gelesen(config) -> None:
    """1. und 5.: Erst nach der fuenfzehnten Runde gibt die Gruppe auf."""
    seite = _seite(20)

    funde = fetch_top_posts(
        _Kontext(seite), "https://x", GRUPPE, limit=10, runden=15, geeignet=_geeignet(config)
    )

    assert seite.gelesen == 15, "jede Runde wird gelesen - nicht einmal und fertig"
    assert len(funde) == 15, "und ``limit`` beendet die Suche nicht mehr"
    assert not any(_geeignet(config)(p) for p in funde)


def test_ein_beitrag_aus_einer_spaeten_runde_wird_gefunden(config) -> None:
    """2.: Der passende Beitrag steht erst in Runde 12 - und dort hoert die Suche auf."""
    seite = _seite(20, passend_in=12)

    funde = fetch_top_posts(
        _Kontext(seite), "https://x", GRUPPE, limit=10, runden=15, geeignet=_geeignet(config)
    )

    assert seite.gelesen == 12
    assert any(p["text"].startswith(PASSEND) for p in funde)


def test_ein_beitrag_nur_im_bild_wird_gefunden(config) -> None:
    """3.: Der Artikeltext ist leer, die Ankuendigung steht im Bildtext."""
    seite = _seite(20, passend_in=4, text="", alt=IM_BILD)

    funde = fetch_top_posts(
        _Kontext(seite), "https://x", GRUPPE, limit=10, runden=15, geeignet=_geeignet(config)
    )

    assert seite.gelesen == 4
    treffer = [p for p in funde if "نازل ع الشام" in p["text"]]
    assert treffer and _geeignet(config)(treffer[0])


def test_ein_ungeeigneter_beitrag_beendet_die_suche_nicht(config) -> None:
    """4.: Runde 1 bringt nur Unpassendes - gesucht wird weiter bis Runde 3."""
    seite = _seite(20, passend_in=3)

    fetch_top_posts(
        _Kontext(seite), "https://x", GRUPPE, limit=1, runden=15, geeignet=_geeignet(config)
    )

    assert seite.gelesen == 3, "limit=1 war schon nach Runde 1 erreicht - und zaehlt nicht"


def test_schon_kommentierte_beitraege_zaehlen_nicht_und_kommen_nicht_zurueck(config) -> None:
    """8.: Was schon einen Kommentar von uns traegt, ist kein Fund."""
    seite = _seite(20, passend_in=2)
    passend = f"https://www.facebook.com/groups/{GRUPPE}/posts/1002/"

    funde = fetch_top_posts(
        _Kontext(seite),
        "https://x",
        GRUPPE,
        limit=10,
        runden=5,
        bekannt={passend},
        geeignet=_geeignet(config),
    )

    assert passend not in {p["post_url"] for p in funde}
    assert seite.gelesen == 5, "der bekannte passende Beitrag haelt die Suche nicht an"


def test_ohne_urteil_bleibt_es_bei_der_menge(config) -> None:
    """Die Wege von Hand (``campaign auto``, Arbeitsseite) behalten ihr ``limit``."""
    seite = _seite(20)

    funde = fetch_top_posts(_Kontext(seite), "https://x", GRUPPE, limit=3)

    assert seite.gelesen == 3
    assert len(funde) == 3


# --- Die Kommentarkette: der naechste Beitrag statt Schluss -------------------

class _Mit:
    """Echte Konfiguration mit ``anlass_pflicht`` - ein Beitrag ohne Anlass hat dann keinen Text."""

    def __init__(self, echt) -> None:  # noqa: ANN001
        self._echt = echt

    def __getattr__(self, name: str):
        return getattr(self._echt, name)

    def get(self, *pfad, default=None):
        if pfad == ("marketing", "anlass_pflicht"):
            return True
        return self._echt.get(*pfad, default=default)


def test_ohne_text_fuer_den_besten_beitrag_kommt_der_naechste_dran(config) -> None:
    """4. in der Kette: Der lauteste Beitrag hat keinen Text - der zweite bekommt den Kommentar.

    Bis zum 23.09.2026 endete der Schritt hier mit "kein Anlass", obwohl der
    naechste Beitrag gepasst haette.
    """
    from fbgroups.automation.actions import Kommentarausgang

    versucht: list[str] = []

    def kommentieren(_context, post_url: str, _text: str) -> Kommentarausgang:
        versucht.append(post_url)
        return Kommentarausgang(True)

    ergebnis = automatik.entscheide_und_kommentiere(
        None,
        _Mit(config),
        [
            {"post_url": "p/laut", "text": "شحن من ألمانيا إلى سوريا",
             "interactions": 50, "comments": 9},
            {"post_url": "p/leise", "text": PASSEND, "interactions": 1, "comments": 0},
        ],
        GRUPPE,
        "",
        kommentieren=kommentieren,
        bisherige=[],
        erlaubnis=Erlaubnis(),
        anspruch=Anspruch(anlass_pflicht=True),
    )

    assert ergebnis.erfolg, ergebnis.fehler
    assert versucht == ["p/leise"]


# --- 6., 7., 9.: der Kampagnenzyklus -----------------------------------------

GRUPPEN = ["g1", "g2", "g3", "g4"]


@pytest.fixture()
def bestand(tmp_path: Path) -> Path:
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=gid,
                    url_canonical=f"https://www.facebook.com/groups/{gid}",
                    name=gid,
                    score=float(100 - i),
                    score_max=100.0,
                )
                for i, gid in enumerate(GRUPPEN)
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(campaign_id="k", name="K", language="ar", status=CampaignStatus.ACTIVE)
        )
        for i, gid in enumerate(GRUPPEN, start=1):
            store.save_marketing(
                GroupMarketing(group_id=gid, marketing_status=MarketingStatus.MEMBER)
            )
            store.add_link(
                CampaignGroup(
                    campaign_id="k",
                    group_id=gid,
                    tracking_code=f"FB-TST-BER-{i:03d}",
                    tracking_url=f"https://go.b-tarikak.de/r/FB-TST-BER-{i:03d}",
                )
            )
    return pfad


class _Konfig:
    def __init__(self, pfad: Path) -> None:
        from fbgroups.config import load_config

        self._echt = load_config()
        self._pfad = pfad

    def __getattr__(self, name: str):
        return getattr(self._echt, name)

    def path(self, name: str) -> Path:
        return self._pfad if name == "sqlite_path" else self._echt.path(name)

    def get(self, *pfad, default=None):
        if pfad[:2] == ("kaltmodus", "aktiv"):
            return False
        if pfad[:1] == ("delays",):
            return 0
        return self._echt.get(*pfad, default=default)


def _vorspulen(pfad: Path, sekunden: int) -> None:
    from datetime import datetime, timedelta

    with MarketingStore(pfad) as store:
        for z in store.conn.execute(
            "SELECT rowid, wiederholen_ab FROM automatik_lauf_uebersprungen "
            "WHERE wiederholen_ab IS NOT NULL"
        ).fetchall():
            neu = datetime.fromisoformat(z["wiederholen_ab"]) - timedelta(seconds=sekunden)
            store.conn.execute(
                "UPDATE automatik_lauf_uebersprungen SET wiederholen_ab = ? WHERE rowid = ?",
                (neu.isoformat(), z["rowid"]),
            )
        store.conn.commit()


def test_eine_fehlerhafte_gruppe_blockiert_die_anderen_nicht(bestand: Path) -> None:
    """6. und 7.: Gruppe 2 hat einen technischen Fehler - der Zyklus bleibt g1 -> g4 -> g1 -> g4.

    Gemeldet wie im Betrieb: Der Arbeitsrechner faengt den Fehler im Browser
    ab und meldet ihn als Ausgang (``cli.campaign automatik``). Die Runde hat
    die Gruppe schon als besucht vermerkt, die naechste ist dran, und in der
    naechsten Runde ist sie wieder dabei. Nur Erfolge zaehlen (9.).
    """
    besucht: list[str] = []

    def ausfuehren(url, group_id, text, texttyp="kommentar", link_url=""):
        besucht.append(group_id)
        _vorspulen(bestand, 60)
        if group_id == "g2":
            return automatik.Schrittergebnis(
                erfolg=False, fehler="Page.goto: Timeout 60000ms exceeded"
            )
        if group_id == "g3":
            return automatik.Schrittergebnis(
                erfolg=False, fehler="kein passender Beitrag nach 15 Runden", kein_anlass=True
            )
        return automatik.Schrittergebnis(
            erfolg=True, post_url=f"https://www.facebook.com/groups/{group_id}/posts/{len(besucht)}"
        )

    fortschritt = automatik.fuehre_lauf_aus(
        _Konfig(bestand),
        ausfuehren=ausfuehren,
        max_schritte=8,
        warte=lambda s: _vorspulen(bestand, int(s)),
    )

    assert besucht[:8] == GRUPPEN + GRUPPEN
    assert fortschritt.kommentare_veroeffentlicht == besucht.count("g1") + besucht.count("g4")
    assert lauf.ZIEL_JE_GRUPPE == 20
