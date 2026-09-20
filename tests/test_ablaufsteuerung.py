"""Ein Fehlschlag kostet einen Beitrag - nicht die Gruppe, nicht den Lauf.

Der Anlass ist ein Lauf vom 14.09.2026, der in einer knappen Stunde **null**
Kommentare schrieb und sich dann selbst beendete:

    Kommentar 1/10 (Fassung 1) → Kommentarfeld nicht gefunden
    Takt: noch 58 Min
    Kommentar 1/10 (Fassung 1) → Kommentarfeld nicht gefunden      (derselbe!)
    Takt: noch 41 Min
    ...
    Abgebrochen: 5 technische Fehlschlaege hintereinander

Drei Fehler auf einmal, und jeder fuer sich harmlos aussehend:

1. **Derselbe Beitrag wurde wiederholt.** ``bisherige_post_urls`` traegt nur,
   worunter wirklich etwas steht; ein Fehlschlag aendert nichts, und die
   Rangfolge ist deterministisch. Also fiel die Wahl jedes Mal gleich aus.
2. **Gewartet wurde auf die falsche Aktion.** ``wartet_auf_takt`` hob alle
   Bremsen auf einmal auf und nahm die Aktion des Schrittes, der dabei
   herauskam - und ``naechster_schritt`` prueft den Beitrag vor dem
   Kommentar. Also gewann der Beitragstakt (120-240 Min), obwohl der
   Kommentar in 11 Minuten frei gewesen waere.
3. **Fuenf technische Fehlschlaege beendeten den Lauf.** "Kommentarfeld nicht
   gefunden" ist aber eine Aussage ueber **einen Beitrag**, nicht ueber den
   Rechner - fuenfmal hintereinander bleibt es das.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fbgroups.automation.actions import Kommentarausgang
from fbgroups.marketing import automatik, lauf
from fbgroups.marketing.entscheidung import Anspruch, Erlaubnis
from fbgroups.marketing.grenzen import Aktion, Grenze, pruefe
from fbgroups.marketing.models import PostStatus

JETZT = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
LINK_URL = "https://go.b-tarikak.de/r/k7m2x9q"
VERSAND = "كيف فيني ابعت غرض صغير من ألمانيا لسوريا؟"

KEIN_FELD = "Kommentarfeld nicht gefunden"
KEIN_FORMULAR = "Beitragsformular nicht gefunden oder blockiert"
BROWSER_ZU = "Target closed: the browser has been closed"


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


class _Feldsuche:
    """Ein Kommentarfeld, das nur unter bestimmten Beitraegen existiert."""

    def __init__(self, *, klappt_bei: set[str]) -> None:
        self.klappt_bei = klappt_bei
        self.versucht: list[str] = []

    def __call__(self, _context, post_url: str, _text: str) -> Kommentarausgang:
        self.versucht.append(post_url)
        if post_url in self.klappt_bei:
            return Kommentarausgang(True)
        return Kommentarausgang(False, hinweis=KEIN_FELD)


def _kern(config, posts, kommentieren, **kwargs):
    return automatik.entscheide_und_kommentiere(
        None,
        config,
        posts,
        "g1",
        "Rueckfall {link}",
        kommentieren=kommentieren,
        bisherige=[],
        erlaubnis=Erlaubnis(links=True, werbung=True, regeln_gelesen=True),
        anspruch=Anspruch(),
        link_url=LINK_URL,
        **kwargs,
    )


# --- 1./2. Kein Kommentarfeld: der naechste Beitrag wird versucht ----------

def test_ohne_kommentarfeld_wird_der_naechste_beitrag_versucht(config) -> None:
    """**Der Kern der Beschwerde.**

    Vorher endete der Schritt beim ersten Fehlschlag, und der naechste
    Durchgang waehlte **denselben** Beitrag. Jetzt geht es im selben Schritt
    weiter - genau das, was ein Mensch taete.
    """
    feld = _Feldsuche(klappt_bei={"p/3"})
    ergebnis = _kern(
        config, [_post("p/1", laut=100), _post("p/2", laut=50), _post("p/3")], feld
    )

    assert ergebnis.erfolg is True
    assert feld.versucht == ["p/1", "p/2", "p/3"], "der Reihe nach, nicht immer derselbe"
    assert ergebnis.post_url == "p/3"


def test_nach_drei_erfolglosen_beitraegen_wird_die_gruppe_beiseitegelegt(config) -> None:
    """Und zwar **ohne Urteil**: ``gruppe_beiseite``, nicht ``erschoepft``.

    Sonst boete der naechste Durchgang dieselbe Gruppe und dieselben
    Beitraege wieder an - genau die Schleife vom 14.09.2026.
    """
    feld = _Feldsuche(klappt_bei=set())
    ergebnis = _kern(config, [_post(f"p/{i}") for i in range(1, 6)], feld)

    assert ergebnis.erfolg is False
    assert ergebnis.gruppe_beiseite is True
    assert ergebnis.erschoepft is False, "kein Urteil ueber die Gruppe"
    assert len(feld.versucht) == automatik.MAX_BEITRAEGE_JE_SCHRITT
    assert len(set(feld.versucht)) == automatik.MAX_BEITRAEGE_JE_SCHRITT


def test_eine_ablehnung_wird_nicht_am_naechsten_beitrag_wiederholt(config) -> None:
    """Sie gilt fuer den naechsten Beitrag genauso.

    Der Unterschied zur Technik ist der ganze Punkt: Ein fehlendes
    Kommentarfeld liegt am Beitrag, eine Ablehnung an uns oder der Gruppe.
    Sie dreimal zu wiederholen hiesse, gegen die Gruppe zu arbeiten.

    **Seit dem 20.09.2026 legt sie die Gruppe ausserdem beiseite** (Regel 5
    des Nutzers). Vorher blieb die Gruppe in der Liste und kam gleich wieder
    an die Reihe, nur mit einer anderen Fassung - in einer Gruppe, die gerade
    nichts annimmt, verbrauchte der Lauf so eine Fassung nach der anderen,
    bis sie als erschoepft galt. Beiseitegelegt ist kein Urteil: Es gilt fuer
    diesen Lauf, morgen wird sie neu beurteilt.
    """
    versucht: list[str] = []

    def abgelehnt(_context, post_url: str, _text: str) -> Kommentarausgang:
        versucht.append(post_url)
        return Kommentarausgang(False, hinweis="Dein Kommentar wurde abgelehnt: Spam")

    ergebnis = _kern(config, [_post("p/1"), _post("p/2"), _post("p/3")], abgelehnt)

    assert ergebnis.erfolg is False
    assert ergebnis.gruppe_beiseite is True, "Regel 5: die Gruppe wird uebersprungen"
    assert len(versucht) == 1, "eine Ablehnung wird nicht dreimal geholt"


def test_ein_gruppenlimit_haelt_den_schritt_sofort_an(config) -> None:
    """"Zu viele Beitraege warten auf Freigabe" gilt fuer die ganze Gruppe."""
    versucht: list[str] = []

    def limit(_context, post_url: str, _text: str) -> Kommentarausgang:
        versucht.append(post_url)
        return Kommentarausgang(False, hinweis="Limit erreicht", gruppenlimit=True)

    _kern(config, [_post("p/1"), _post("p/2")], limit)

    assert len(versucht) == 1


# --- 3. Der Takt: die kuerzeste Bremse gewinnt -----------------------------

def _gruppe():
    return lauf.Gruppenfortschritt(
        campaign_id="k",
        group_id="g1",
        name="Gruppe",
        veroeffentlicht=0,
        ziel=10,
        mitglied=True,
        mitgliedschaft_noetig=False,
        regeln_noetig=False,
        post_status=PostStatus.OFFEN,
        post_fassungen=frozenset({1}),
    )


def _takt(aktion: Aktion, *, abstand: int, vor_minuten: int):
    lage = pruefe(
        aktion,
        Grenze(pro_tag=100, abstand_min=abstand, abstand_max=abstand),
        heute=1,
        letzte=JETZT - timedelta(minutes=vor_minuten),
        jetzt=JETZT,
    )
    assert lage.nur_takt, "Aufbau: es sollte der Takt sein"
    return lage


def test_gewartet_wird_auf_die_kuerzeste_bremse_nicht_auf_die_erste() -> None:
    """**Der Grund fuer "Takt: noch 58 Min".**

    ``naechster_schritt`` prueft den Beitrag vor dem Kommentar. Wer alle
    Bremsen auf einmal aufhebt und nimmt, was herauskommt, wartet deshalb
    immer auf den Beitrag - und der ist mit 120-240 Minuten der langsamste
    Takt im Haus. Der Kommentar waere in 11 Minuten frei gewesen.
    """
    fortschritt = lauf.Lauffortschritt(
        lauf_id=1,
        status=lauf.LaufStatus.LAEUFT,
        kampagnen=[
            lauf.Kampagnenfortschritt(
                campaign_id="k", name="k", gruppen=[_gruppe()], bewertet=True
            )
        ],
        aktionen={
            Aktion.POST: _takt(Aktion.POST, abstand=120, vor_minuten=62),
            Aktion.KOMMENTAR: _takt(Aktion.KOMMENTAR, abstand=20, vor_minuten=9),
        },
    )

    assert lauf.naechster_schritt(fortschritt) is None
    assert fortschritt.wartet_auf_takt == "noch 11 Min", "nicht 58"


# --- 4./5. Sitzungsfehler von gewoehnlicher Technik trennen ---------------

@pytest.mark.parametrize(
    ("fehler", "sitzung"),
    [
        (KEIN_FELD, False),
        (KEIN_FORMULAR, False),
        ("Seite nicht geladen (Timeout)", False),
        (BROWSER_ZU, True),
        ("Playwright wurde beendet", True),
        ("Are you logged in and a member of the group?", True),
    ],
)
def test_nur_ein_sitzungsfehler_ist_ein_sitzungsfehler(fehler, sitzung) -> None:
    assert automatik.ist_technisch(fehler) is True, "beides ist technisch"
    assert automatik.ist_sitzungsfehler(fehler) is sitzung


def test_ein_geschlossener_browser_haelt_sofort_an() -> None:
    """Kein Zaehlen: Beim ersten Mal so eindeutig wie beim fuenften.

    Hier hilft keine naechste Gruppe - jeder weitere Schritt scheiterte
    genauso, und am Ende staende ein Lauf voller Vermerke ueber Gruppen, an
    denen nichts liegt.
    """
    waechter = automatik._Technikwaechter()

    assert waechter.melde(automatik.Schrittergebnis(erfolg=False, fehler=BROWSER_ZU))
    assert "Anmeldung" in waechter.meldung()
    assert "aktiv" in waechter.meldung(), "die Kampagne endet nicht"


def test_fuenf_fehlende_kommentarfelder_beenden_den_lauf_nicht() -> None:
    """**Die Forderung vom 15.09.2026, als Zusicherung.**

    Eine aktive Kampagne endet nicht an einem gewoehnlichen Tag. "Kein
    Kommentarfeld" ist in fuenf Gruppen fuenfmal eine Aussage ueber einen
    Beitrag - nicht ueber den Rechner.
    """
    waechter = automatik._Technikwaechter()

    for _ in range(5):
        assert not waechter.melde(
            automatik.Schrittergebnis(erfolg=False, fehler=KEIN_FELD)
        )


def test_die_notbremse_bleibt_aber_weit_hinten() -> None:
    """Scheitern **verschiedene** Gruppen in Folge, liegt es doch am Rechner."""
    waechter = automatik._Technikwaechter()
    ausgang = automatik.Schrittergebnis(erfolg=False, fehler=KEIN_FELD)

    aufgehoert = [waechter.melde(ausgang) for _ in range(automatik._Technikwaechter.GRENZE)]

    assert aufgehoert[-1] is True
    assert not any(aufgehoert[:-1])
    assert automatik._Technikwaechter.GRENZE >= 12


def test_ein_erfolg_setzt_die_zaehlung_zurueck() -> None:
    """Dann arbeitet der Browser ja."""
    waechter = automatik._Technikwaechter()
    for _ in range(5):
        waechter.melde(automatik.Schrittergebnis(erfolg=False, fehler=KEIN_FELD))

    waechter.melde(automatik.Schrittergebnis(erfolg=True))

    assert waechter.folge == 0


def test_eine_ablehnung_der_gruppe_setzt_ebenfalls_zurueck() -> None:
    """Sie ist kein technischer Fehlschlag - der Browser hat gearbeitet."""
    waechter = automatik._Technikwaechter()
    waechter.melde(automatik.Schrittergebnis(erfolg=False, fehler=KEIN_FELD))

    waechter.melde(
        automatik.Schrittergebnis(erfolg=False, fehler="Dein Kommentar wurde abgelehnt")
    )

    assert waechter.folge == 0


# --- 6./7. Beide Laeufe legen die Gruppe beiseite --------------------------

def test_beide_laeufe_legen_die_gruppe_beiseite() -> None:
    """Eine zweite Zaehlweise waere ein zweiter Lauf mit anderem Ausgang."""
    from pathlib import Path

    quelltext = Path("src/fbgroups/marketing/automatik.py").read_text(encoding="utf-8")
    web = Path("src/fbgroups/marketing/web.py").read_text(encoding="utf-8")

    assert "if ergebnis.gruppe_beiseite:" in quelltext, "oertlich"
    assert '"gruppe_beiseite": ergebnis.gruppe_beiseite' in quelltext, "gemeldet"
    assert "if meldung.gruppe_beiseite and meldung.lauf_id:" in web, "auf dem Server"
    assert "gruppe_beiseite" in web


def test_der_ausgang_wird_trotzdem_gebucht() -> None:
    """Beiseitelegen heisst nicht verschweigen.

    Der Versuch gehoert ins Protokoll - sonst sieht ein Mensch spaeter nicht,
    dass in dieser Gruppe etwas nicht ging. Nur ``kein_anlass`` bucht nichts:
    Dort ist gar nichts versucht worden.
    """
    from pathlib import Path

    web = Path("src/fbgroups/marketing/web.py").read_text(encoding="utf-8")
    endpunkt = web.split("def automatik_ergebnis(", 1)[1].split("\n    @app.", 1)[0]

    assert endpunkt.index("melde_vorschlag(") < endpunkt.index("if meldung.gruppe_beiseite")


# --- 8./9./10. Mehrere Kampagnen --------------------------------------------

def _kampagne(name: str, *, gruppen: list, bewertet: bool = True):
    return lauf.Kampagnenfortschritt(
        campaign_id=name, name=name, gruppen=gruppen, bewertet=bewertet
    )


def test_eine_kampagne_ohne_arbeit_haelt_die_naechste_nicht_auf() -> None:
    """Punkt 6 der Anforderung: Kampagnen blockieren sich nicht gegenseitig."""
    leer = _kampagne("A", gruppen=[])
    voll = _kampagne("B", gruppen=[_gruppe()])

    fortschritt = lauf.Lauffortschritt(
        lauf_id=1, status=lauf.LaufStatus.LAEUFT, kampagnen=[leer, voll]
    )

    assert fortschritt.naechste_kampagne is not None
    assert fortschritt.naechste_kampagne.campaign_id == "B"


def test_der_lauf_friert_alle_aktiven_kampagnen_ein() -> None:
    """Nicht eine - alle. ``Kampagnen: 0 / 1`` heisst: eine war aktiv."""
    import inspect

    quelle = inspect.getsource(automatik.hole_oder_starte_lauf)

    assert "aktive_kampagnen(store)" in quelle
    assert "starte_lauf(kampagnen" in quelle


def test_die_zielprioritaet_bleibt_beim_ueberspringen_erhalten() -> None:
    """Eine beiseitegelegte Gruppe aendert die Rangfolge der uebrigen nicht."""
    from fbgroups.marketing.zielgruppe import Region, Zielprioritaet

    def g(gid: str, prio: Zielprioritaet, *, uebersprungen: bool = False):
        return lauf.Gruppenfortschritt(
            campaign_id="k",
            group_id=gid,
            name=gid,
            veroeffentlicht=0,
            mitglied=True,
            mitgliedschaft_noetig=False,
            regeln_noetig=False,
            zielprioritaet=prio,
            zielregion=Region.DE,
            uebersprungen=uebersprungen,
            post_status=PostStatus.VEROEFFENTLICHT,
        )

    kampagne = _kampagne(
        "k",
        gruppen=[
            g("a1", Zielprioritaet.A, uebersprungen=True),
            g("b1", Zielprioritaet.B),
            g("a2", Zielprioritaet.A),
        ],
    )

    assert [x.group_id for x in kampagne.arbeitsliste] == ["a1", "a2", "b1"]
    # Die uebersprungene faellt aus der Arbeit, nicht aus der Rangfolge.
    assert kampagne.naechste_gruppe is not None
    assert kampagne.naechste_gruppe.group_id == "a2"
