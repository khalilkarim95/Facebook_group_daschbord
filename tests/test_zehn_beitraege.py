"""Mindestens zehn Beitraege ansehen, Gescheitertes nicht wiederholen, dann ausschliessen.

Anweisung des Nutzers vom 24.09.2026, nach einem Lauf, in dem Runde fuer
Runde **derselbe** Beitrag scheiterte ("Kommentarfeld nicht beschreibbar",
"Found 1 post(s)", "1 Beitraege versucht"):

    bis 15 Mal herunterscrollen und mindestens 10 Beitraege ansehen - und
    wenn dann wirklich nichts zu machen ist, die Gruppe ausschliessen.

Drei Dinge hingen zusammen:

1. Die Suche hoerte beim **ersten** geeigneten Beitrag auf. Scheiterte der,
   gab es keinen zweiten. Jetzt: mindestens zehn (``MINDEST_BEITRAEGE``).
2. Ein gescheiterter Beitrag kam in jeder Runde wieder - ``post_versuche``
   traegt die Adresse nur bei Erfolg. Jetzt: ``gescheiterte_beitraege``,
   24 Stunden gesperrt.
3. Nach einer vollen Suche ohne kommentierbaren Beitrag wird die Gruppe
   ausgeschlossen (``nichts_zu_machen``) - nie wegen eines technischen
   Fehlschlags allein.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fbgroups.automation.actions import Kommentarausgang, fetch_top_posts
from fbgroups.marketing import automatik
from fbgroups.marketing.entscheidung import Anspruch, Erlaubnis
from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    CampaignStatus,
    GroupMarketing,
    MarketingStatus,
)
from fbgroups.marketing.store import SPERRE_GESCHEITERT_STUNDEN, MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore
from fbgroups.urls import beitragslinks

GRUPPE = "123"
UNPASSEND = "شقة للايجار في برلين"
PASSEND = "بدي ابعت غرض صغير لسوريا"


# --- Die nachgebaute Gruppenseite (wie in test_scroll_runden.py) -------------

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
    def __init__(self, nummer: int, text: str) -> None:
        self.url = f"https://www.facebook.com/groups/{GRUPPE}/posts/{nummer}/"
        self._text = text

    def locator(self, selektor: str) -> _Treffer:
        return _Treffer([self.url] if selektor == "a[href]" else [])

    def inner_text(self, *, timeout: float | None = None) -> str:
        return self._text


class _Liste:
    def __init__(self, seite: _Seite) -> None:
        self._seite = seite

    def all(self) -> list[_Artikel]:
        self._seite.gelesen += 1
        return self._seite.runden[min(self._seite.gescrollt, len(self._seite.runden) - 1)]


class _Seite:
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


def _seite(anzahl: int, *, passend_in: tuple[int, ...] = ()) -> _Seite:
    """Je Runde ein Beitrag - passend in den genannten Runden, sonst unpassend."""
    return _Seite(
        [[_Artikel(r, PASSEND if r in passend_in else UNPASSEND)] for r in range(1, anzahl + 1)]
    )


def _geeignet(config):  # noqa: ANN001, ANN202
    return automatik.geeignet_fuer(
        config, GRUPPE, erlaubnis=Erlaubnis(),
        anspruch=automatik.anspruch_aus_config(config), rueckfall="",
    )


# --- 1. Mindestens zehn Beitraege --------------------------------------------

def test_die_vorgaben(config) -> None:
    assert automatik.MINDEST_BEITRAEGE == 10
    assert automatik.MAX_BEITRAEGE_JE_SCHRITT == 5
    assert automatik.scroll_runden(config) == 15


def test_ein_frueher_treffer_beendet_die_suche_nicht_vor_zehn_beitraegen(config) -> None:
    """Der passende Beitrag steht in Runde 2 - angesehen werden trotzdem zehn."""
    seite = _seite(20, passend_in=(2,))
    bericht: dict = {}

    funde = fetch_top_posts(
        _Kontext(seite), "https://x", GRUPPE, runden=15,
        geeignet=_geeignet(config), mindestens=10, bericht=bericht,
    )

    assert len(funde) == 10
    assert seite.gelesen == 10, "vorher war nach Runde 2 Schluss"
    assert bericht == {"runden": 10, "runden_max": 15, "gesehen": 10, "geeignet": True}


def test_ohne_treffer_laeuft_die_suche_alle_fuenfzehn_runden(config) -> None:
    seite = _seite(20)
    bericht: dict = {}

    fetch_top_posts(
        _Kontext(seite), "https://x", GRUPPE, runden=15,
        geeignet=_geeignet(config), mindestens=10, bericht=bericht,
    )

    assert seite.gelesen == 15
    assert bericht["runden"] == bericht["runden_max"] == 15
    assert bericht["geeignet"] is False


def test_gesperrte_beitraege_zaehlen_als_gesehen_aber_nicht_als_fund(config) -> None:
    seite = _seite(3, passend_in=(1,))
    gesperrt = f"https://www.facebook.com/groups/{GRUPPE}/posts/1/"
    bericht: dict = {}

    funde = fetch_top_posts(
        _Kontext(seite), "https://x", GRUPPE, runden=3,
        bekannt={gesperrt}, geeignet=_geeignet(config), mindestens=10, bericht=bericht,
    )

    assert gesperrt not in {p["post_url"] for p in funde}
    assert bericht["gesehen"] == 3
    assert bericht["geeignet"] is False, "der einzige passende ist gesperrt"


def test_der_bildverweis_traegt_die_beitragsadresse() -> None:
    """Die Zeitangabe zeigt oft nur ``#`` - der Bildverweis kennt den Beitrag."""
    hrefs = [
        "#",
        "https://www.facebook.com/photo/?fbid=111&set=pcb.2145381866352825&__cft__=x",
        "https://www.facebook.com/photo/?fbid=222&set=gm.998877",
    ]

    assert beitragslinks(hrefs, GRUPPE) == [
        f"https://www.facebook.com/groups/{GRUPPE}/posts/2145381866352825/",
        f"https://www.facebook.com/groups/{GRUPPE}/posts/998877/",
    ]


# --- 2. Gescheiterte Beitraege werden nicht wiederholt ------------------------

def _roh(n: int) -> list[dict]:
    return [
        {"post_url": f"p/{i}", "text": PASSEND, "interactions": 10 - i, "comments": 0}
        for i in range(1, n + 1)
    ]


def test_nach_einem_nicht_beschreibbaren_feld_kommt_der_naechste_beitrag(config) -> None:
    versucht: list[str] = []

    def kommentieren(_c, post_url: str, _t: str) -> Kommentarausgang:
        versucht.append(post_url)
        if post_url == "p/1":
            return Kommentarausgang(False, hinweis="Kommentarfeld nicht beschreibbar")
        return Kommentarausgang(True)

    ergebnis = automatik.entscheide_und_kommentiere(
        None, config, _roh(10), GRUPPE, "",
        kommentieren=kommentieren, bisherige=[], erlaubnis=Erlaubnis(), anspruch=Anspruch(),
    )

    assert ergebnis.erfolg
    assert versucht == ["p/1", "p/2"]
    assert ergebnis.gescheiterte_posts == ("p/1",), "gemerkt, damit er nicht wiederkommt"


def test_hoechstens_fuenf_beitraege_je_schritt_und_alle_werden_gemerkt(config) -> None:
    versucht: list[str] = []

    def kommentieren(_c, post_url: str, _t: str) -> Kommentarausgang:
        versucht.append(post_url)
        return Kommentarausgang(False, hinweis="Kommentarfeld nicht beschreibbar")

    ergebnis = automatik.entscheide_und_kommentiere(
        None, config, _roh(10), GRUPPE, "",
        kommentieren=kommentieren, bisherige=[], erlaubnis=Erlaubnis(), anspruch=Anspruch(),
    )

    assert len(versucht) == 5
    assert set(ergebnis.gescheiterte_posts) == set(versucht)
    assert ergebnis.gruppe_beiseite and not ergebnis.ausschliessen


def test_ein_sitzungsfehler_sperrt_keinen_beitrag(config) -> None:
    versucht: list[str] = []

    def kommentieren(_c, post_url: str, _t: str) -> Kommentarausgang:
        versucht.append(post_url)
        return Kommentarausgang(False, hinweis="Target closed")

    ergebnis = automatik.entscheide_und_kommentiere(
        None, config, _roh(10), GRUPPE, "",
        kommentieren=kommentieren, bisherige=[], erlaubnis=Erlaubnis(), anspruch=Anspruch(),
    )

    assert versucht == ["p/1"], "der naechste Beitrag scheiterte genauso"
    assert ergebnis.gescheiterte_posts == ()


def test_gescheiterte_beitraege_sind_einen_tag_gesperrt(tmp_path: Path) -> None:
    pfad = tmp_path / "g.sqlite"
    with MarketingStore(pfad) as store:
        store.merke_gescheiterte_beitraege(GRUPPE, ["p/alt", "p/neu"])
        frueher = datetime.now(UTC) - timedelta(hours=SPERRE_GESCHEITERT_STUNDEN + 1)
        store.conn.execute(
            "UPDATE gescheiterte_beitraege SET am = ? WHERE post_url = 'p/alt'",
            (frueher.isoformat(),),
        )
        store.conn.commit()

        assert store.gesperrte_post_urls(GRUPPE) == {"p/neu"}
        assert store.bisherige_post_urls(GRUPPE) == set(), "nur der Erfolg gilt als kommentiert"


# --- 3. Ausschliessen, wenn wirklich nichts zu machen ist --------------------

VOLL = {"runden": 15, "runden_max": 15, "gesehen": 12, "geeignet": False}
KEIN = automatik.Schrittergebnis(erfolg=False, fehler="kein passender Beitrag", kein_anlass=True)


def test_nach_voller_suche_ohne_treffer_wird_ausgeschlossen() -> None:
    grund = automatik.nichts_zu_machen(VOLL, [{"text": UNPASSEND}], KEIN)

    assert grund.startswith("automatisch: 15 Scroll-Runden, 12 Beitraege angesehen")


@pytest.mark.parametrize(
    ("bericht", "roh", "ergebnis", "warum"),
    [
        ({**VOLL, "geeignet": True}, [{"text": PASSEND}], KEIN, "ein geeigneter war da"),
        ({**VOLL, "runden": 9}, [{"text": UNPASSEND}], KEIN, "Suche nicht zu Ende"),
        ({**VOLL, "gesehen": 0}, [], KEIN, "die Seite zeigte nichts"),
        (VOLL, [{"text": ""}], KEIN, "nichts gelesen"),
        (
            VOLL, [{"text": UNPASSEND}],
            automatik.Schrittergebnis(
                erfolg=False, fehler="Kommentarfeld nicht beschreibbar", gruppe_beiseite=True
            ),
            "technischer Fehlschlag",
        ),
        (VOLL, [{"text": UNPASSEND}], automatik.Schrittergebnis(erfolg=True), "Erfolg"),
    ],
)
def test_sonst_wird_nicht_ausgeschlossen(bericht, roh, ergebnis, warum) -> None:  # noqa: ANN001
    assert automatik.nichts_zu_machen(bericht, roh, ergebnis) == "", warum


def test_alles_gesperrt_nach_voller_suche_wird_ausgeschlossen() -> None:
    """Alle sichtbaren Beitraege sind kommentiert oder gescheitert."""
    grund = automatik.nichts_zu_machen(
        VOLL, [],
        automatik.Schrittergebnis(
            erfolg=False, fehler="keine Beitraege zum Kommentieren gefunden", kein_anlass=True
        ),
    )

    assert grund


# --- Beide Laeufe schliessen aus - und merken sich die Beitraege ------------

@pytest.fixture()
def bestand(tmp_path: Path) -> Path:
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=gid, url_canonical=f"https://www.facebook.com/groups/{gid}",
                    name=gid, score=float(100 - i), score_max=100.0,
                )
                for i, gid in enumerate(("g1", "g2"))
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(campaign_id="k", name="K", language="ar", status=CampaignStatus.ACTIVE)
        )
        for i, gid in enumerate(("g1", "g2"), start=1):
            store.save_marketing(
                GroupMarketing(group_id=gid, marketing_status=MarketingStatus.MEMBER)
            )
            store.add_link(
                CampaignGroup(
                    campaign_id="k", group_id=gid, tracking_code=f"FB-TST-BER-{i:03d}",
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


GRUND = "automatisch: 15 Scroll-Runden, 12 Beitraege angesehen - nichts Kommentierbares"


def test_der_oertliche_lauf_schliesst_aus_und_merkt_die_beitraege(bestand: Path) -> None:
    def ausfuehren(url, group_id, text, texttyp="kommentar", link_url=""):
        if group_id == "g1":
            return automatik.Schrittergebnis(
                erfolg=False, fehler="kein passender Beitrag", kein_anlass=True,
                gescheiterte_posts=("p/tot",), ausschliessen=GRUND,
            )
        return automatik.Schrittergebnis(erfolg=True, post_url=f"p/{group_id}")

    automatik.fuehre_lauf_aus(_Konfig(bestand), ausfuehren=ausfuehren, max_schritte=2)

    with MarketingStore(bestand) as store:
        g1 = store.load_marketing("g1")
        assert not g1.bearbeiten and g1.ausschlussgrund == GRUND
        assert store.load_marketing("g2").bearbeiten
        assert "p/tot" in store.gesperrte_post_urls("g1")
        assert store.link_for("k", "g1") is not None, "der Tracking-Code bleibt"


