"""Eine aktive Kampagne bleibt aktiv - Rangfolge, Beitritt, Wiederaufnahme.

Die Nachpruefung vom 15.09.2026. Die Korrekturen an Warteschlange, Takt und
Abbruch sind das eine; die Frage, ob der **Ablauf** danach noch stimmt, ist
das andere. Geprueft wird deshalb nicht, was geaendert wurde, sondern was
gelten soll:

* Eine Kampagne wird nur ``completed``, wenn sie ihr Ziel **erreicht** hat.
* Eine gescheiterte Gruppe haelt die naechste nicht auf. Die Rangfolge
  A-Deutschland vor A-Europa vor B ist seit dem 23.09.2026 entfallen
  (Bezuege statt Zielklassen, ``tests/test_bezuege.py``).
* Vor dem Schreiben steht die Frage, ob wir hier ueberhaupt duerfen.
* Der naechste Lauf setzt fort, statt von vorn zu beginnen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.marketing import automatik, lauf
from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    CampaignStatus,
    LaufStatus,
    MarketingStatus,
    PostStatus,
)
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

KAMPAGNE = "test_neu"


def _gruppe(
    gid: str,
    *,
    uebersprungen: bool = False,
    mitglied: bool = True,
    mitgliedschaft_noetig: bool = False,
    veroeffentlicht: int = 0,
    ziel: int = 10,
    post_status: PostStatus = PostStatus.VEROEFFENTLICHT,
    beitritt_noetig: bool = False,
    erschoepft: bool = False,
):
    return lauf.Gruppenfortschritt(
        campaign_id=KAMPAGNE,
        group_id=gid,
        name=gid,
        veroeffentlicht=veroeffentlicht,
        ziel=ziel,
        mitglied=mitglied,
        mitgliedschaft_noetig=mitgliedschaft_noetig,
        regeln_noetig=False,
        uebersprungen=uebersprungen,
        post_status=post_status,
        beitritt_noetig=beitritt_noetig,
        erschoepft=erschoepft,
    )


def _kampagne(gruppen: list, *, bewertet: bool = True):
    return lauf.Kampagnenfortschritt(
        campaign_id=KAMPAGNE, name=KAMPAGNE, gruppen=gruppen, bewertet=bewertet
    )


# --- 1. Die Kampagne bleibt aktiv -----------------------------------------

def test_nur_das_erreichte_ziel_beendet_eine_kampagne() -> None:
    """``fertig`` ist nicht ``abgeschlossen`` - und nur das Zweite zaehlt.

    ``fertig`` heisst "der Lauf versucht hier nichts mehr" und zaehlt eine
    erschoepfte Gruppe mit. Auf ``completed`` gesetzt verschwaende das die
    Kampagne fuer jeden kuenftigen Lauf, obwohl in denselben Gruppen morgen
    neue Beitraege stehen.
    """
    kampagne = _kampagne([_gruppe("g1", veroeffentlicht=2, ziel=10, erschoepft=True)])

    assert kampagne.fertig is True, "der Lauf versucht hier nichts mehr"
    assert kampagne.abgeschlossen is False, "erreicht ist etwas anderes"


def test_eine_volle_kampagne_gilt_als_abgeschlossen() -> None:
    """Und nur dann. Jede Gruppe voll, kein Beitrag offen."""
    kampagne = _kampagne([_gruppe("g1", veroeffentlicht=10, ziel=10)])

    assert kampagne.abgeschlossen is True


@pytest.mark.parametrize(
    "grund",
    ["uebersprungen", "kein_mitglied", "gescheitert"],
)
def test_kein_fehlschlag_macht_eine_kampagne_abgeschlossen(grund: str) -> None:
    """Punkt 1 der Anforderung, als Zusicherung.

    Weder ein Uebersprung, noch eine fehlende Mitgliedschaft, noch eine
    gescheiterte Kampagne darf zu ``completed`` fuehren - sonst waere sie
    fuer jeden kuenftigen Lauf verloren.
    """
    if grund == "uebersprungen":
        gruppen = [_gruppe("g1", uebersprungen=True)]
        kampagne = _kampagne(gruppen)
    elif grund == "kein_mitglied":
        gruppen = [_gruppe("g1", mitglied=False, mitgliedschaft_noetig=True)]
        kampagne = _kampagne(gruppen)
    else:
        # Eine Kampagne ohne jede Zuordnung: ``abgeschlossen`` verlangt
        # ``bool(self.gruppen)`` - sonst waere sie im selben Augenblick
        # "erfolgreich", in dem sie angelegt wurde.
        kampagne = lauf.Kampagnenfortschritt(
            campaign_id=KAMPAGNE, name=KAMPAGNE, gruppen=[]
        )

    assert kampagne.abgeschlossen is False


def test_ein_abbruch_setzt_den_lauf_auf_angehalten_nicht_auf_fertig() -> None:
    """Damit ``offener_lauf`` ihn beim naechsten Start wieder aufnimmt.

    Das ist die Wiederaufnahme: Der Lauf behaelt seine eingefrorene
    Kampagnenliste und seinen Fortschritt, und ``campaign automatik`` setzt
    dort auf, wo er stand - statt von vorn zu beginnen.
    """
    import inspect

    quelle = inspect.getsource(automatik._stand_fortschreiben)

    assert "LaufStatus.FERTIG.value if erledigt else LaufStatus.ANGEHALTEN.value" in quelle
    assert "fortschritt.fertig or not fortschritt.kampagnen" in quelle


def test_ein_offener_lauf_wird_fortgesetzt(tmp_path: Path) -> None:
    """**Der Nachweis, dass nichts von vorn beginnt.**

    Derselbe Lauf, dieselbe Kennung, dieselbe eingefrorene Liste - und
    ``neu`` ist beim zweiten Mal falsch.
    """
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [Group(group_id="111", url_canonical="https://f/groups/111", name="G")]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(campaign_id=KAMPAGNE, name=KAMPAGNE, status=CampaignStatus.ACTIVE)
        )
        store.add_link(
            CampaignGroup(campaign_id=KAMPAGNE, group_id="111", tracking_code="FB-T-001")
        )

        erste, neu1 = automatik.hole_oder_starte_lauf(store, ziel_je_gruppe=10)
        store.setze_lauf_status(erste, LaufStatus.ANGEHALTEN.value, meldung="Abbruch")
        zweite, neu2 = automatik.hole_oder_starte_lauf(store, ziel_je_gruppe=10)

    assert neu1 is True and neu2 is False
    assert erste == zweite, "derselbe Lauf, nicht ein neuer"


# --- 2. Die Rangfolge ------------------------------------------------------

def test_eine_gescheiterte_gruppe_haelt_die_naechste_nicht_auf() -> None:
    """Faellt eine Gruppe aus, kommt die naechste in der Reihenfolge dran."""
    kampagne = _kampagne(
        [
            _gruppe("g1", uebersprungen=True),
            _gruppe("g2"),
            _gruppe("g3"),
        ]
    )

    assert kampagne.naechste_gruppe is not None
    assert kampagne.naechste_gruppe.group_id == "g2"


def test_die_rangfolge_haengt_nicht_am_lauf() -> None:
    """``uebersprungen`` haengt an der ``lauf_id``; ein neuer Lauf findet
    dieselben Gruppen in derselben Reihenfolge wieder vor."""
    kampagne = _kampagne([_gruppe("g1"), _gruppe("g2"), _gruppe("g3")])

    assert [g.group_id for g in kampagne.arbeitsliste] == ["g1", "g2", "g3"]
    assert kampagne.naechste_gruppe is not None
    assert kampagne.naechste_gruppe.group_id == "g1", "wieder von vorn"


# --- 3. Beitrittsanfragen --------------------------------------------------

def test_ohne_mitgliedschaft_wird_nicht_geschrieben() -> None:
    """Solange die Mitgliedschaft Vorbedingung ist, wird nichts versucht.

    ``bearbeitbar`` ist falsch, ``beitritt_noetig`` wahr - also steht die
    Anfrage an und nicht der Kommentar.
    """
    wartend = _gruppe(
        "g1", mitglied=False, mitgliedschaft_noetig=True, beitritt_noetig=True
    )

    assert wartend.bearbeitbar is False
    assert wartend.beitritt_noetig is True
    assert _kampagne([wartend]).naechste_gruppe is None


def test_jede_gruppe_kann_eine_anfrage_bekommen() -> None:
    """Die Anfrage hing an den Zielklassen A und B - die gibt es nicht mehr.

    Der Weg gilt seit dem 23.09.2026 fuer jede Gruppe; abgeschaltet ist er
    bis auf Weiteres in ``settings.yaml`` (``limits.join_requests.daily: 0``),
    siehe ``tests/test_bezuege.py``.
    """
    kampagne = _kampagne(
        [
            _gruppe(
                "a1", mitglied=False, mitgliedschaft_noetig=True, beitritt_noetig=True,
            ),
        ]
    )

    assert [g.group_id for g in kampagne.beitritt_offen] == ["a1"]


@pytest.mark.parametrize(
    ("ausgang", "vermerkt", "stand"),
    [
        ("angefragt", True, MarketingStatus.JOIN_REQUESTED),
        ("bereits_mitglied", True, MarketingStatus.MEMBER),
        ("fragen", False, MarketingStatus.NOT_CONTACTED),
        ("fehler", False, MarketingStatus.NOT_CONTACTED),
    ],
)
def test_jeder_ausgang_einer_anfrage_wird_richtig_vermerkt(
    tmp_path: Path, ausgang: str, vermerkt: bool, stand: MarketingStatus
) -> None:
    """Vier unterscheidbare Faelle - und zwei davon vermerken **nichts**.

    ``fragen`` heisst: Die Gruppe stellt Beitrittsfragen, der Versuch wurde
    abgebrochen, es ist nichts abgeschickt worden. ``beitritt_angefragt`` zu
    setzen waere die Behauptung, es sei etwas hinausgegangen - und beim
    naechsten Lauf ginge die Anfrage nie wieder hinaus.
    """
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [Group(group_id="111", url_canonical="https://f/groups/111", name="G")]
        )

    with MarketingStore(pfad) as store:
        if ausgang in ("angefragt", "bereits_mitglied"):
            store.merke_anfrage("111", mitglied=ausgang == "bereits_mitglied")
        eintrag = store.load_marketing("111")

    assert eintrag.marketing_status is stand
    assert bool(eintrag.join_requested_at) is (ausgang == "angefragt")


def test_eine_gestellte_anfrage_wird_nicht_wiederholt(tmp_path: Path) -> None:
    """``beitritt_noetig`` faellt weg, sobald der Stand weiter ist.

    Facebook laesst Anfragen oft wochenlang offen; sie jeden Lauf erneut zu
    stellen waere genau das Muster, das zur Sperre fuehrt.
    """
    angefragt = _gruppe(
        "g1", mitglied=False, mitgliedschaft_noetig=True, beitritt_noetig=False
    )

    assert angefragt.beitritt_noetig is False
    assert _kampagne([angefragt]).beitritt_offen == []


def test_ein_gescheiterter_beitritt_vermerkt_nichts_im_bestand() -> None:
    """Aber er legt die Gruppe fuer diesen Lauf beiseite.

    Sonst boete der naechste Durchgang dieselbe Gruppe wieder an, und der
    Lauf kaeme nicht zur naechsten.
    """
    web = Path("src/fbgroups/marketing/web.py").read_text(encoding="utf-8")
    endpunkt = web.split("def automatik_beitritt_ergebnis(", 1)[1].split("\n    @app.", 1)[0]

    assert 'if meldung.ausgang in ("angefragt", "bereits_mitglied"):' in endpunkt
    assert "ueberspringe_gruppe" in endpunkt
    assert endpunkt.index("merke_anfrage") < endpunkt.index("ueberspringe_gruppe")


# --- 4. Drei Versuche heissen drei verschiedene Beitraege -------------------

def test_drei_versuche_sind_drei_verschiedene_beitraege() -> None:
    """Nicht dreimal derselbe. ``waehle_gelegenheit`` bekommt die gescheiterten
    Adressen herein und laesst sie aus."""
    import inspect

    # Der Kern; ``entscheide_und_kommentiere`` haengt seit dem 23.09.2026 nur
    # noch die Bezuege an den Ausgang.
    quelle = inspect.getsource(automatik._entscheide_und_kommentiere)

    assert "gescheitert.add(gewaehlt.post_url)" in quelle
    assert "waehle_gelegenheit(gelegenheiten, gescheitert)" in quelle
    assert "MAX_BEITRAEGE_JE_SCHRITT" in quelle


def test_ein_gescheiterter_beitrag_wird_im_selben_schritt_nicht_wiederholt() -> None:
    """``waehle_gelegenheit`` laesst aus, was schon gescheitert ist."""
    from fbgroups.marketing.automatik import Gelegenheit, waehle_gelegenheit
    from fbgroups.marketing.entscheidung import Antwortart, Entscheidung
    from fbgroups.marketing.inhalt import Inhaltsbefund

    def gelegenheit(url: str, laut: int) -> Gelegenheit:
        return Gelegenheit(
            post_url=url,
            interactions=laut,
            comments=0,
            befund=Inhaltsbefund(),
            entscheidung=Entscheidung(art=Antwortart.CONTEXTUAL_APP_MENTION, grund="x"),
        )

    alle = [gelegenheit("p/1", 100), gelegenheit("p/2", 50)]

    assert waehle_gelegenheit(alle, set()).post_url == "p/1"
    assert waehle_gelegenheit(alle, {"p/1"}).post_url == "p/2"
    assert waehle_gelegenheit(alle, {"p/1", "p/2"}) is None
