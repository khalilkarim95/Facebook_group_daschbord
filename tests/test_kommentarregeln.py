"""Die Kommentarregeln des Nutzers vom 20.09.2026.

Zehn Regeln, und ihr Kern ist eine einzige Unterscheidung: **Ein Kommentar
zaehlt erst, wenn er wirklich in der Gruppe steht.**

    SUCCESS         -> zaehlen (Tagesmenge, Gruppenmenge) + Takt abwarten
    FAILED/SKIPPED  -> nicht zaehlen, kein Takt, sofort zur naechsten Gruppe

Bis zum 20.09.2026 zaehlte jeder **Versuch**, und jeder Versuch setzte die
Uhr. Das war der teuerste Leerlauf des Laufs: Ein Kommentar, den Facebook
nicht annahm, hielt den naechsten volle zehn bis zwanzig Minuten auf, obwohl
nichts hinausgegangen war - eine Gruppe, in der gerade nichts geht, kostete so
eine Viertelstunde je Fehlschlag.

Die Regeln 1, 5, 6 und 9 waren bereits erfuellt; sie stehen hier trotzdem,
denn eine Regel ohne Test ist eine Absichtserklaerung.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fbgroups.marketing import grenzen, lauf
from fbgroups.marketing.models import Campaign, CampaignGroup, PostVersuch, Texttyp
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

KAMPAGNE = "batreeq"
GID = "100000000000001"
HEUTE = datetime.now(UTC).date().isoformat()


@pytest.fixture()
def store(tmp_path: Path):
    pfad = tmp_path / "groups.sqlite"
    # ``add_link`` nimmt keine Zuordnung zu einer Gruppe an, die es nicht
    # gibt - der Bestand ist zuerst dran.
    with SqliteStore(pfad) as gruppen:
        gruppen.upsert_groups(
            [
                Group(
                    group_id=gid,
                    url_canonical=f"https://www.facebook.com/groups/{gid}",
                    name=f"Gruppe {gid[-1]}",
                )
                for gid in (GID, "200000000000002", "300000000000003")
            ]
        )
    with MarketingStore(pfad) as s:
        s.save_campaign(
            Campaign(campaign_id=KAMPAGNE, name="B", language="ar",
                     landing_page="https://b-tarikak.de/")
        )
        for nummer, gid in enumerate((GID, "200000000000002", "300000000000003"), 1):
            s.add_link(
                CampaignGroup(
                    campaign_id=KAMPAGNE,
                    group_id=gid,
                    tracking_code=f"FB-SYR-DUE-00{nummer}",
                    tracking_url=f"https://go.b-tarikak.de/r/FB-SYR-DUE-00{nummer}",
                )
            )
        yield s


def _versuch(store: MarketingStore, *, erfolg: bool, fehler: str = "", gid: str = GID) -> int:
    versuch_id = store.beginne_versuch(
        PostVersuch(
            campaign_id=KAMPAGNE,
            group_id=gid,
            texttyp=Texttyp.KOMMENTAR.value,
            nummer=1,
            tracking_code="FB-SYR-DUE-001",
        )
    )
    store.beende_versuch(versuch_id, erfolg=erfolg, fehler=fehler)
    return versuch_id


# --- Regel 3 und 4: nur Erfolge zaehlen ------------------------------------


def test_ein_fehlschlag_zaehlt_nicht_gegen_die_tagesmenge(store: MarketingStore) -> None:
    """Regel 4. Was nie in einer Gruppe stand, hat nichts verbraucht."""
    for _ in range(5):
        _versuch(store, erfolg=False, fehler="Kommentarfeld nicht gefunden")

    assert store.versuche_heute(HEUTE, Texttyp.KOMMENTAR.value) == 0


def test_ein_erfolg_zaehlt_gegen_die_tagesmenge(store: MarketingStore) -> None:
    """Regel 3. Die Gegenprobe - sonst zaehlte der Test nur das Nichts."""
    _versuch(store, erfolg=True)
    _versuch(store, erfolg=True)
    _versuch(store, erfolg=False, fehler="abgelehnt")

    assert store.versuche_heute(HEUTE, Texttyp.KOMMENTAR.value) == 2


def test_auch_eine_ablehnung_der_gruppe_zaehlt_nicht(store: MarketingStore) -> None:
    """Die Regel gilt fuer **jeden** Misserfolg, nicht nur den technischen.

    Vorher war allein der technische Fehlschlag ausgenommen, und die
    Begruendung lautete: Ein abgelehnter Kommentar sei trotzdem einer
    gewesen, den die Gruppe gesehen hat. Das trifft auf die Moderation zu -
    aber nicht auf einen, den Facebook gar nicht erst angenommen hat.
    """
    _versuch(store, erfolg=False, fehler="Dein Kommentar wurde als Spam eingestuft")

    assert store.versuche_heute(HEUTE, Texttyp.KOMMENTAR.value) == 0


def test_die_gruppenmenge_zaehlt_ebenfalls_nur_erfolge(store: MarketingStore) -> None:
    """``je_gruppe_taeglich`` schuetzt die Leser der Gruppe.

    Bei ``1`` war ein einziger Fehlschlag frueher der ganze Tag: Die Gruppe
    galt als bedient, obwohl dort nichts stand.
    """
    _versuch(store, erfolg=False, fehler="Kommentarfeld nicht gefunden")
    assert store.versuche_heute_je_gruppe(HEUTE, Texttyp.KOMMENTAR.value).get(GID, 0) == 0

    _versuch(store, erfolg=True)
    assert store.versuche_heute_je_gruppe(HEUTE, Texttyp.KOMMENTAR.value)[GID] == 1


# --- Regel 7 und 8: der Takt gilt nur nach einem Erfolg --------------------


def test_ein_fehlschlag_setzt_den_takt_nicht(store: MarketingStore) -> None:
    """Regel 7 - und der teuerste Leerlauf des Laufs.

    Ein Kommentar, den Facebook nicht annahm, hielt den naechsten volle zehn
    bis zwanzig Minuten auf. Der Takt ist der Abstand zwischen zwei Dingen,
    die **in einer Gruppe stehen**; wo nichts steht, gibt es nichts
    abzuwarten.
    """
    _versuch(store, erfolg=False, fehler="Kommentarfeld nicht gefunden")

    assert store.letzter_versuch(Texttyp.KOMMENTAR.value) == ""


def test_ein_erfolg_setzt_den_takt(store: MarketingStore) -> None:
    """Regel 8. Nach einem veroeffentlichten Kommentar wird gewartet."""
    _versuch(store, erfolg=True)

    assert store.letzter_versuch(Texttyp.KOMMENTAR.value) != ""


def test_nach_einem_erfolg_zaehlt_ein_fehlschlag_die_uhr_nicht_weiter(
    store: MarketingStore,
) -> None:
    """Der Fall, der im Betrieb vorkommt: Erfolg, dann drei Fehlschlaege.

    Wuerde der letzte Fehlschlag die Uhr setzen, verschoebe jeder erfolglose
    Anlauf den naechsten echten Kommentar weiter nach hinten - der Lauf
    bremste sich an seinen eigenen Fehlversuchen aus.
    """
    _versuch(store, erfolg=True)
    nach_erfolg = store.letzter_versuch(Texttyp.KOMMENTAR.value)

    for _ in range(3):
        _versuch(store, erfolg=False, fehler="kein Kommentarfeld")

    assert store.letzter_versuch(Texttyp.KOMMENTAR.value) == nach_erfolg


# --- Regel 5: eine Ablehnung legt die Gruppe beiseite ---------------------


def test_eine_ablehnung_legt_die_gruppe_fuer_diesen_lauf_beiseite() -> None:
    """Regel 5. Nein heisst nein - auch fuer die naechste Fassung.

    Vorher kam die Gruppe gleich wieder an die Reihe, nur mit einer anderen
    Fassung: In einer Gruppe, die gerade nichts annimmt, verbrauchte der Lauf
    so eine Fassung nach der anderen, bis sie als **erschoepft** galt - ein
    dauerhaftes Urteil aus einer einzigen Stunde.

    ``gruppe_beiseite`` ist kein Urteil: Es gilt fuer diesen Lauf, morgen
    wird die Gruppe neu beurteilt.
    """
    from fbgroups.marketing.automatik import Schrittergebnis, ist_technisch

    abgelehnt = "Dein Kommentar wurde als Spam eingestuft"
    technisch = "Kommentarfeld nicht gefunden"

    assert not ist_technisch(abgelehnt), "eine Ablehnung ist kein technischer Fehler"
    assert ist_technisch(technisch)

    # Die Unterscheidung, um die es geht - beide landen beiseite, aber aus
    # verschiedenen Gruenden, und das steht im Protokoll.
    for fehler in (abgelehnt, technisch):
        ergebnis = Schrittergebnis(erfolg=False, fehler=fehler, gruppe_beiseite=True)
        assert ergebnis.gruppe_beiseite
        assert not ergebnis.erfolg


def test_ein_erfolg_legt_die_gruppe_nicht_beiseite() -> None:
    """Die Gegenprobe: Nach einem Kommentar bleibt die Gruppe in der Liste.

    Sie hat noch neun offene Fassungen - sie beiseitezulegen hiesse, aus
    zehn Kommentaren je Gruppe einen zu machen.
    """
    from fbgroups.marketing.automatik import Schrittergebnis

    ergebnis = Schrittergebnis(erfolg=True)

    assert not ergebnis.gruppe_beiseite


# --- Regel 2 und 10: hundert am Tag, harte Obergrenze ----------------------


def test_das_tageslimit_ist_eine_harte_obergrenze() -> None:
    """Regel 10. Bei 100 ist Schluss - nicht bei 100 "ungefaehr"."""
    grenze = grenzen.Grenze(pro_tag=100, abstand_min=8, abstand_max=20)

    assert grenzen.darf(grenze, 99) is True
    assert grenzen.darf(grenze, 100) is False
    assert grenzen.darf(grenze, 101) is False
    assert grenzen.rest(grenze, 100) == 0


def test_hundert_kommentare_stehen_in_der_konfiguration(config) -> None:
    """Regel 2. Die Zahl steht in settings.yaml und nicht im Programm."""
    gelesen = grenzen.einstellungen(config).fuer(grenzen.Aktion.KOMMENTAR)

    assert gelesen.pro_tag == 100


# --- Regel 1: zehn je Gruppe und Kampagne ---------------------------------


def test_zehn_kommentare_je_gruppe_und_kampagne() -> None:
    """Regel 1. ``ZIEL_JE_GRUPPE`` zaehlt **veroeffentlichte** Fassungen.

    Der Fortschritt wird aus ``campaign_group_texte.status`` gelesen und
    nicht im Lauf gefuehrt - ein Fehlschlag bringt die Gruppe damit nie
    naeher an ihre zehn.
    """
    assert lauf.ZIEL_JE_GRUPPE == 10

    def stand(veroeffentlicht: int) -> lauf.Gruppenfortschritt:
        return lauf.Gruppenfortschritt(
            campaign_id=KAMPAGNE, group_id=GID, name="Test",
            veroeffentlicht=veroeffentlicht,
        )

    assert stand(0).offen == 10
    assert stand(9).offen == 1
    assert stand(10).offen == 0


# --- Regel 9: eine Gruppe beendet nie den ganzen Lauf ---------------------


def test_die_uhr_haengt_nicht_an_der_gruppe(store: MarketingStore) -> None:
    """Regel 5/9 von der Zaehlseite her.

    Scheitert es in einer Gruppe, bleibt der Takt dort, wo der letzte
    **erfolgreiche** Kommentar stand - die naechste Gruppe erbt keine
    Wartezeit aus einem fremden Fehlschlag.
    """
    _versuch(store, erfolg=False, fehler="kein Kommentarfeld", gid="200000000000002")
    _versuch(store, erfolg=False, fehler="kein Kommentarfeld", gid="300000000000003")

    assert store.letzter_versuch(Texttyp.KOMMENTAR.value) == ""
    assert store.versuche_heute(HEUTE, Texttyp.KOMMENTAR.value) == 0


# --- Der Takt selbst -------------------------------------------------------


def test_nach_einem_erfolg_wird_wirklich_gewartet() -> None:
    """Regel 8 zu Ende gedacht: Der Abstand greift, wenn er greifen soll."""
    grenze = grenzen.Grenze(pro_tag=100, abstand_min=8, abstand_max=20)
    jetzt = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

    frei_ab = grenzen.naechster_zeitpunkt(jetzt - timedelta(minutes=2), grenze, jetzt=jetzt)
    assert frei_ab is not None and frei_ab > jetzt

    # Lange genug her: der Takt haelt nichts mehr auf.
    assert grenzen.naechster_zeitpunkt(jetzt - timedelta(hours=3), grenze, jetzt=jetzt) is None
    # Und ohne einen einzigen Erfolg gibt es gar nichts abzuwarten.
    assert grenzen.naechster_zeitpunkt(None, grenze, jetzt=jetzt) is None
