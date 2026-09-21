"""Warum nur vier von dreizehn Gruppen besucht wurden (21.09.2026).

Der Betriebsbefund war eine Schleife durch immer dieselben vier Gruppen, in
jeder Runde dieselbe Meldung:

    kein Anlass: kein passender Beitrag
      (Bezug zu schwach fuer diese Gruppe (hoch, verlangt hoch + Strecke))

"verlangt hoch + Strecke" ist die Anforderung der **Klasse C**. Dass eine
Gruppe namens "شركة شحن دولي سوريا" dort landete, hatte genau eine Ursache:
Seit dem 20.09.2026 beurteilte ``zielgruppe`` den Namen nicht mehr, sondern
nur noch die gepflegte Kategorie - und die Mitgliederliste traegt in
``category`` fast durchgehend "Unbekannt". Ohne Thema ist Klasse A
unerreichbar; wer dazu kein Syrienwort im Namen hat, fiel sogar auf ``D``
und wurde **nie besucht**.

Beide Haelften stehen hier: die Einstufung (dieses Modul) und die Frage, ob
eine Klasse ueberhaupt bearbeitet wird.
"""

from __future__ import annotations

from fbgroups.marketing import zielgruppe
from fbgroups.marketing.lauf import Gruppenfortschritt
from fbgroups.marketing.models import PostStatus
from fbgroups.marketing.zielgruppe import Merkmale, Regeln, Zielprioritaet, einstufe

#: Die Regeln, wie sie ``settings.yaml`` seit dem 21.09.2026 hergibt -
#: verkuerzt auf das, was diese Tests brauchen.
REGELN = Regeln(
    kategorien=frozenset({"versand", "reise"}),
    kategoriebegriffe=("شحن", "نازل", "مسافر", "بطريقك", "Versand"),
    ziele=("سوريا", "الشام", "دمشق", "Syrien"),
    herkunft=("المانيا", "ألمانيا", "برلين", "Deutschland", "Berlin"),
    europa=("أوروبا", "هولندا"),
    ausserhalb=("لبنان", "بيروت"),
    audiences=frozenset({"syrians", "arabs"}),
    audiencebegriffe=("سوريين", "السوريون", "الجالية", "Syrer"),
)


def _merkmale(name: str, **felder: object) -> Merkmale:
    return Merkmale(name=name, **felder)  # type: ignore[arg-type]


# --- Die Einstufung: der Name zaehlt wieder mit ---------------------------


def test_eine_versandgruppe_ohne_gepflegte_kategorie_ist_wieder_a() -> None:
    """Der Fall aus dem Protokoll - vorher C, und damit "hoch + Strecke"."""
    befund = einstufe(_merkmale("شركة شحن دولي سوريا"), REGELN)
    assert befund.prioritaet is Zielprioritaet.A


def test_eine_reisegruppe_ohne_gepflegte_kategorie_ist_wieder_a() -> None:
    befund = einstufe(_merkmale("نازل على الشام طالع لالمانيا"), REGELN)
    assert befund.prioritaet is Zielprioritaet.A
    assert befund.region is zielgruppe.Region.DE


def test_eine_gemeinschaftsgruppe_ohne_tag_ist_wieder_b() -> None:
    """Vorher ``D`` - und ``D`` wurde nie besucht."""
    befund = einstufe(_merkmale("السوريون في ألمانيا"), REGELN)
    assert befund.prioritaet is Zielprioritaet.B


def test_die_gepflegte_kategorie_geht_dem_namen_vor() -> None:
    """Handarbeit schlaegt Worterkennung - die Liste ergaenzt, sie ersetzt nicht.

    Der Name nennt kein Thema, die gepflegte Kategorie schon: Die Gruppe ist
    trotzdem ``A``. Umgekehrt bleibt eine gepflegte Kategorie unberuehrt -
    sie steht im Grund und nicht das geratene Wort.
    """
    befund = einstufe(
        _merkmale("مجموعة الأصدقاء في دمشق", kategorie="versand"), REGELN
    )
    assert befund.prioritaet is Zielprioritaet.A
    assert "versand" in befund.treffer


def test_ein_thema_allein_macht_noch_keine_a_gruppe() -> None:
    """Ohne genanntes Ziel bleibt es bei ``C`` - "Reisen nach Thailand"."""
    befund = einstufe(_merkmale("مشاوير وسفر حول العالم"), REGELN)
    assert befund.prioritaet is not Zielprioritaet.A


def test_ohne_wortlisten_bleibt_alles_beim_alten() -> None:
    """Die Listen sind Konfiguration: Wer sie leert, bekommt das alte Verhalten."""
    ohne = Regeln(
        kategorien=frozenset({"versand"}),
        ziele=("سوريا", "الشام"),
        audiences=frozenset({"syrians"}),
    )
    assert einstufe(_merkmale("شركة شحن دولي سوريا"), ohne).prioritaet is (
        Zielprioritaet.C
    )


# --- Die Runde: keine Klasse verschwindet unbemerkt -----------------------


def _stand(klasse: Zielprioritaet, klassen: frozenset) -> Gruppenfortschritt:
    return Gruppenfortschritt(
        campaign_id="k1",
        group_id="1",
        name="Gruppe 1",
        veroeffentlicht=0,
        ziel=10,
        mitglied=True,
        regeln_gelesen=True,
        post_status=PostStatus.VEROEFFENTLICHT,
        zielprioritaet=klasse,
        bearbeitbare_klassen=klassen,
    )


def test_d_wird_ohne_eintrag_weiterhin_nicht_bearbeitet() -> None:
    """Die Vorgabe im Code bleibt die vorsichtige."""
    stand = _stand(Zielprioritaet.D, zielgruppe.BEARBEITBAR)
    assert not stand.bearbeitbar


def test_mit_eintrag_bleibt_auch_d_in_der_runde(config) -> None:
    """Mit ``mindestrelevanz.d`` in ``settings.yaml`` wird D besucht.

    Die Schwelle dort ist die strengste, die es gibt - geantwortet wird nur
    auf den klaren Einzelfall. Zugeordnet hat die Gruppe ein Mensch; "kein
    erkennbarer Bezug" ist dagegen ein Schluss aus einem Namen.
    """
    klassen = zielgruppe.bearbeitbare_klassen(config)
    assert Zielprioritaet.D in klassen
    assert _stand(Zielprioritaet.D, klassen).bearbeitbar


def test_eine_uebergangene_klasse_wird_gezaehlt() -> None:
    """Die Zahl, die am 21.09.2026 gefehlt hat.

    Neun von dreizehn Gruppen fielen aus der Runde, und nirgends stand eine
    Zahl dazu - im Protokoll standen nur die uebrigen vier, immer wieder.
    """
    from fbgroups.marketing.lauf import Kampagnenfortschritt

    kampagne = Kampagnenfortschritt(
        campaign_id="k1",
        name="K1",
        gruppen=[
            _stand(Zielprioritaet.A, zielgruppe.BEARBEITBAR),
            _stand(Zielprioritaet.D, zielgruppe.BEARBEITBAR),
            _stand(Zielprioritaet.D, zielgruppe.BEARBEITBAR),
        ],
    )
    assert kampagne.gruppen_ausserhalb == 2


def test_die_beitrittsanfrage_bleibt_a_und_b_vorbehalten() -> None:
    """Die riskanteste Handlung des Projekts wird hier nicht mitgeoeffnet."""
    assert set(zielgruppe.BEITRITT_WERT) == {Zielprioritaet.A, Zielprioritaet.B}
