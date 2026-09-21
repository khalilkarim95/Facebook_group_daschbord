"""Unter denselben Beitrag kommt nie ein zweiter Kommentar von uns.

Die harte Regel aus der Anforderung vom 21.09.2026 (Punkte 21-26). Sie hat
zwei Haelften, und beide stehen hier:

* **Was gesperrt wird**: allein der Beitrag, unter dem wirklich etwas steht.
  Ein Fehlschlag sperrt nichts - der Beitrag darf beim naechsten Durchgang
  wieder angesehen werden.
* **Wie weit die Sperre reicht**: ueber **alle Kampagnen**, nicht je
  Kampagne. ``bisherige_post_urls`` fragt nach ``group_id`` und
  ``erfolg = 1``, ohne ``campaign_id`` - ein Leser der Gruppe sieht nicht,
  aus welcher Kampagne ein Kommentar stammt, und zwei Kommentare von uns
  unter einem Beitrag sind zwei Kommentare von uns.

Der Schluessel ist die **Beitragsadresse** (``post_versuche.post_url``),
nicht der Text: ``canonical_post_url`` schneidet ``__cft__`` und ``__tn__``
ab, damit derselbe Beitrag in jedem Durchgang gleich aussieht.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.automation.actions import Kommentarausgang
from fbgroups.marketing import automatik
from fbgroups.marketing.entscheidung import Anspruch, Erlaubnis
from fbgroups.marketing.models import Campaign, CampaignStatus, PostVersuch, Texttyp
from fbgroups.marketing.store import MarketingStore

GRUPPE = "g1"
VERSAND = "كيف فيني ابعت غرض صغير من ألمانيا لسوريا؟"
LINK_URL = "https://go.b-tarikak.de/r/k7m2x9q"


@pytest.fixture()
def config():
    from fbgroups.config import load_config

    return load_config()


def _post(url: str, *, laut: int = 0) -> dict:
    return {
        "post_url": url,
        "text": VERSAND,
        "interactions": laut,
        "comments": 0,
        "posted_at": None,
    }


class _Zaehler:
    """Kommentiert nicht, sondern merkt sich, wo kommentiert worden waere."""

    def __init__(self, *, erfolg: bool = True) -> None:
        self.erfolg = erfolg
        self.versucht: list[str] = []

    def __call__(self, _context, post_url: str, _text: str) -> Kommentarausgang:
        self.versucht.append(post_url)
        if self.erfolg:
            return Kommentarausgang(True)
        return Kommentarausgang(False, hinweis="Kommentarfeld nicht gefunden")


def _kern(config, posts, kommentieren, *, bisherige=()):
    return automatik.entscheide_und_kommentiere(
        None,
        config,
        posts,
        GRUPPE,
        "Rueckfall {link}",
        kommentieren=kommentieren,
        bisherige=list(bisherige),
        erlaubnis=Erlaubnis(links=True, werbung=True, regeln_gelesen=True),
        anspruch=Anspruch(anlass_pflicht=False),
        link_url=LINK_URL,
    )


# --- 1./5. Ein kommentierter Beitrag kommt nie wieder an die Reihe -------

def test_ein_kommentierter_beitrag_wird_nicht_erneut_kommentiert(config) -> None:
    """Runde 1 kommentiert, Runde 2 findet denselben Beitrag - und laesst ihn.

    Das ist derselbe Test wie Punkt 5 der Anforderung: Der zweite Durchgang
    sieht dieselbe Gruppenseite, und die Adresse steht in ``bisherige``.
    """
    posts = [_post("p/1", laut=100)]
    erste = _Zaehler()

    runde1 = _kern(config, posts, erste)

    assert runde1.erfolg is True
    assert erste.versucht == ["p/1"]

    zweite = _Zaehler()
    runde2 = _kern(config, posts, zweite, bisherige=[runde1.post_url])

    assert zweite.versucht == [], "kein zweiter Kommentar unter demselben Beitrag"
    assert runde2.erfolg is False
    assert runde2.erschoepft or runde2.kein_anlass, "kein Fehlschlag, nur nichts zu tun"


def test_der_naechste_unkommentierte_beitrag_wird_genommen(config) -> None:
    """Punkt 24: A ist kommentiert, B nicht - also B, und nicht nichts.

    Der Rang bleibt dabei unberuehrt: Waere B nicht da, wuerde gar nicht
    kommentiert - und nicht etwa A ein zweites Mal.
    """
    posts = [_post("p/A", laut=500), _post("p/B", laut=1)]
    zaehler = _Zaehler()

    ergebnis = _kern(config, posts, zaehler, bisherige=["p/A"])

    assert zaehler.versucht == ["p/B"], "der lautere ist gesperrt, also der andere"
    assert ergebnis.erfolg is True
    assert ergebnis.post_url == "p/B"


def test_sind_alle_passenden_kommentiert_wird_die_gruppe_uebersprungen(config) -> None:
    """Punkt 24, zweiter Teil: Dann ist hier nichts mehr zu holen.

    **Kein Fehlschlag** - es liegt nichts gegen die Gruppe vor, sie ist nur
    durch. Der Lauf geht zur naechsten, statt einen Versuch zu buchen.
    """
    posts = [_post("p/A"), _post("p/B")]
    zaehler = _Zaehler()

    ergebnis = _kern(config, posts, zaehler, bisherige=["p/A", "p/B"])

    assert zaehler.versucht == []
    assert ergebnis.erfolg is False
    assert ergebnis.erschoepft, "alle sichtbaren Beitraege sind bereits kommentiert"


# --- 4. Nur der Erfolg sperrt --------------------------------------------

def test_ein_fehlschlag_sperrt_den_beitrag_nicht(config, tmp_path: Path) -> None:
    """Punkt 23: Gescheitert ist nicht kommentiert.

    Geprueft wird beides - der Rueckgabewert (keine Adresse) und der
    Bestand: ``bisherige_post_urls`` liest ``erfolg = 1``, ein Fehlschlag
    steht also im Protokoll und nicht in der Sperre.
    """
    zaehler = _Zaehler(erfolg=False)

    ergebnis = _kern(config, [_post("p/1")], zaehler)

    assert zaehler.versucht, "versucht wurde es"
    assert ergebnis.erfolg is False
    assert ergebnis.post_url == "", "ohne Erfolg keine Adresse - und damit keine Sperre"

    pfad = tmp_path / "groups.sqlite"
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(campaign_id="k1", name="K1", status=CampaignStatus.ACTIVE)
        )
        for erfolg, url in ((False, "p/1"), (True, "p/2")):
            versuch_id = store.beginne_versuch(
                PostVersuch(
                    campaign_id="k1",
                    group_id=GRUPPE,
                    texttyp=Texttyp.KOMMENTAR.value,
                    nummer=1,
                )
            )
            store.beende_versuch(
                versuch_id,
                erfolg=erfolg,
                fehler="" if erfolg else "Kommentarfeld nicht gefunden",
                post_url=url if erfolg else "",
            )

        gesperrt = store.bisherige_post_urls(GRUPPE)

    assert gesperrt == {"p/2"}, "nur der veroeffentlichte Kommentar sperrt"


# --- 25. Die Sperre gilt ueber alle Kampagnen ----------------------------

def test_die_sperre_gilt_ueber_alle_kampagnen(tmp_path: Path) -> None:
    """Punkt 25, und die Entscheidung dazu steht im Modulkopf.

    Ein Leser der Gruppe sieht nicht, aus welcher Kampagne ein Kommentar
    stammt - zwei Kommentare von uns unter einem Beitrag sind zwei
    Kommentare von uns. ``bisherige_post_urls`` fragt deshalb nach der
    **Gruppe**, nicht nach dem Paar aus Kampagne und Gruppe.
    """
    pfad = tmp_path / "groups.sqlite"
    with MarketingStore(pfad) as store:
        for kennung in ("k1", "k2"):
            store.save_campaign(
                Campaign(campaign_id=kennung, name=kennung, status=CampaignStatus.ACTIVE)
            )
        versuch_id = store.beginne_versuch(
            PostVersuch(
                campaign_id="k1",
                group_id=GRUPPE,
                texttyp=Texttyp.KOMMENTAR.value,
                nummer=1,
            )
        )
        store.beende_versuch(versuch_id, erfolg=True, post_url="p/1")

        # Dieselbe Gruppe, andere Kampagne - dieselbe Sperre.
        assert "p/1" in store.bisherige_post_urls(GRUPPE)

    quelltext = Path("src/fbgroups/marketing/store.py").read_text(encoding="utf-8")
    abfrage = quelltext.split("def bisherige_post_urls", 1)[1].split("def ", 1)[0]
    assert "campaign_id" not in abfrage, "die Sperre kennt keine Kampagne"
    assert "erfolg = 1" in abfrage, "und nur den Erfolg"
