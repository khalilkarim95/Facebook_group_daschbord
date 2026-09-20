"""Deutschland zuerst, dann Europa - die zweite Achse der Rangfolge.

Die Anforderung vom 14.09.2026 in Tests. Sie ergaenzt die Zielprioritaet um
eine Frage, die diese nicht beantwortet: *wo* arbeitet die Gruppe?

Der Anlass steht in einer Zeile aus dem Bestand:

    FB-SYR-DE-024   نقل من لبنان إلى سورية   Versand & Mitnahme

Eine lupenreine Versandgruppe mit genanntem Ziel - und eine Strecke, auf der
diese App niemandem hilft, denn ihre Nutzer sitzen in Europa. Nach Klasse
allein sortiert stand sie vor den deutschen Gruppen.

Geprueft wird deshalb wieder nicht die Einstufung allein, sondern was sie
**bewirkt**: die Reihenfolge der Arbeit und wer eine Beitrittsanfrage bekommt.
"""

from __future__ import annotations

import pytest

from fbgroups.marketing import lauf, zielgruppe
from fbgroups.marketing.models import PostStatus
from fbgroups.marketing.zielgruppe import Merkmale, Region, Zielprioritaet

#: Derselbe kleine Regelsatz wie in ``test_zielprioritaet.py``, um die beiden
#: neuen Listen erweitert. Vier Zeilen statt einer YAML-Datei - genau dafuer
#: steht ``Regeln`` neben ``einstufe``.
REGELN = zielgruppe.Regeln(
    kategorien=frozenset({"reise", "versand"}),
    ziele=("Syrien", "Damaskus", "سوريا", "سورية", "الشام", "دمشق"),
    # Die Staedtenamen stehen seit dem 20.09.2026 in ``herkunft`` - eigene
    # Liste gab es, solange sie aus ``cities.yaml`` kamen.
    herkunft=("Deutschland", "المانيا", "ألمانيا", "Berlin", "Hamburg", "برلين", "هامبورغ"),
    europa=("Österreich", "النمسا", "Schweden", "السويد", "Wien"),
    ausserhalb=("Libanon", "لبنان", "Türkei", "تركيا", "بيروت"),
    audiences=frozenset({"syrians", "arabs"}),
)


def _befund(name: str, kategorie: str = "versand", stadt: str | None = None):
    return zielgruppe.einstufe(
        Merkmale(name=name, kategorie=kategorie, stadt=stadt), REGELN
    )


# --- Die Region selbst -----------------------------------------------------

@pytest.mark.parametrize(
    ("name", "erwartet"),
    [
        # Deutschland - am Land oder an einem Staedtenamen aus ``herkunft``.
        ("شحن من ألمانيا إلى سوريا", Region.DE),
        ("Versand Hamburg nach Damaskus", Region.DE),
        ("مسافرين من برلين الى دمشق", Region.DE),
        # Uebriges Europa - zweite Wahl, nicht dritte.
        ("شحن من النمسا الى سوريا", Region.EU),
        ("Versand Wien - Damaskus", Region.EU),
        # Ausserhalb - der Fall, wegen dem es die Achse gibt.
        ("نقل من لبنان إلى سورية", Region.AUSSERHALB),
        ("شحن من تركيا الى سوريا", Region.AUSSERHALB),
        # Kein Land genannt - der Regelfall und kein Mangel.
        ("أسرع شركة شحن الى سوريا", Region.UNBEKANNT),
    ],
)
def test_die_vier_regionen(name, erwartet) -> None:
    assert _befund(name).region is erwartet


def test_ein_europaeisches_wort_schlaegt_ein_aussereuropaeisches() -> None:
    """"Berlin - Beirut - Damaskus" ist eine Berliner Gruppe.

    Sie wegen des Zwischenstopps herabzustufen hiesse, eine richtige Gruppe
    an ein Detail zu verlieren. Nur wer **allein** die andere Seite nennt,
    meint die andere Strecke.
    """
    befund = _befund("مشاوير برلين - بيروت - دمشق", kategorie="reise")

    assert befund.region is Region.DE
    assert befund.prioritaet is Zielprioritaet.A


def test_das_ziel_zaehlt_nicht_als_aussereuropaeisch() -> None:
    """Syrien steht in ``ziele`` und in keiner Laenderliste.

    Stuende es in ``ausserhalb``, fiele der **ganze** Zielmarkt aus Klasse A -
    denn jede Gruppe darin nennt ihr Ziel. ``config-check`` meldet die
    Kollision; hier steht die Regel dahinter.
    """
    assert "سوريا" not in REGELN.ausserhalb
    assert _befund("شحن الى سوريا").prioritaet is Zielprioritaet.A


# --- Was sie mit der Klasse macht -----------------------------------------

def test_eine_strecke_ausserhalb_europas_faellt_aus_klasse_a() -> None:
    """**Der Kern der Anforderung.**

    Thema und Ziel genuegen nicht mehr: "نقل من لبنان إلى سورية" traegt
    beides und meint trotzdem eine andere Strecke.
    """
    befund = _befund("نقل من لبنان إلى سورية")

    assert befund.prioritaet is Zielprioritaet.C
    assert befund.region is Region.AUSSERHALB
    assert "ausserhalb Europas" in befund.grund


def test_sie_faellt_nach_c_und_nicht_nach_d() -> None:
    """Sie ist nicht bezuglos - was sie verliert, ist der Vortritt.

    Ein einzelner Beitrag, in dem jemand aus Deutschland schreibt, bleibt
    erreichbar; eine Beitrittsanfrage bekommt sie nicht mehr. Dieselbe
    Zurueckhaltung wie ueberall: "niedrige Prioritaet" ist etwas anderes als
    "geloescht".
    """
    befund = _befund("نقل من لبنان إلى سورية")

    assert befund.bearbeitbar is True
    assert befund.beitritt_wert is False


def test_ohne_genanntes_land_bleibt_es_bei_a() -> None:
    """Die meisten deutschen Gruppen heissen "شحن الى سوريا".

    Sie fuer eine Auslassung herabzustufen hiesse, den halben Zielmarkt an
    eine Namensgewohnheit zu verlieren.
    """
    befund = _befund("أسرع شركة شحن الى سوريا")

    assert befund.prioritaet is Zielprioritaet.A
    assert befund.region is Region.UNBEKANNT


# --- Die Rangfolge ---------------------------------------------------------

def test_der_rang_ist_erst_die_klasse_dann_das_land() -> None:
    """Ein Paar und keine Summe.

    Eine deutsche Gemeinschaftsgruppe steht **nicht** vor einer
    oesterreichischen Versandgruppe, nur weil sie in Deutschland ist. Das
    Land ordnet innerhalb einer Klasse, es hebt keine Klasse an.
    """
    a_eu = _befund("شحن من النمسا الى سوريا")
    b_de = zielgruppe.einstufe(
        Merkmale(name="Syrer in Berlin", kategorie="community",
                 audiences=("syrians",), stadt="Berlin"),
        REGELN,
    )

    assert a_eu.prioritaet is Zielprioritaet.A
    assert b_de.prioritaet is Zielprioritaet.B
    assert a_eu.rang < b_de.rang


def test_unbekannt_steht_vor_ausserhalb() -> None:
    """"Nicht gemessen" ist etwas anderes als "gemessen und schlecht".

    Dieselbe Unterscheidung wie beim Score, bei der Aktivitaet und bei der
    Resonanz - hier trifft sie den halben Zielmarkt.
    """
    assert (
        zielgruppe.RANG_REGION[Region.DE]
        < zielgruppe.RANG_REGION[Region.EU]
        < zielgruppe.RANG_REGION[Region.UNBEKANNT]
        < zielgruppe.RANG_REGION[Region.AUSSERHALB]
    )


def _gruppe(gid: str, prioritaet: Zielprioritaet, region: Region):
    return lauf.Gruppenfortschritt(
        campaign_id="k",
        group_id=gid,
        name=f"Gruppe {gid}",
        veroeffentlicht=0,
        mitglied=True,
        mitgliedschaft_noetig=False,
        zielprioritaet=prioritaet,
        zielregion=region,
        post_status=PostStatus.VEROEFFENTLICHT,
    )


def test_erst_alle_a_in_deutschland_dann_alle_a_in_europa_dann_b() -> None:
    """**Die geforderte Reihenfolge, als einziger Test.**

    "لا تنتقل إلى B لمجرد أن بعض مجموعات A درجاتها منخفضة" - der Score
    entscheidet erst innerhalb einer Klasse **und** eines Landes; er bringt
    nie eine B-Gruppe vor eine A-Gruppe.
    """
    kampagne = lauf.Kampagnenfortschritt(
        campaign_id="k",
        name="k",
        gruppen=[
            # Absichtlich in der falschen Reihenfolge - so kaeme die Liste
            # score-sortiert herein.
            _gruppe("b-de", Zielprioritaet.B, Region.DE),
            _gruppe("a-unbekannt", Zielprioritaet.A, Region.UNBEKANNT),
            _gruppe("a-eu", Zielprioritaet.A, Region.EU),
            _gruppe("c-de", Zielprioritaet.C, Region.DE),
            _gruppe("a-de", Zielprioritaet.A, Region.DE),
        ],
    )

    assert [g.group_id for g in kampagne.arbeitsliste] == [
        "a-de", "a-eu", "a-unbekannt", "b-de", "c-de",
    ]
    assert kampagne.naechste_gruppe is not None
    assert kampagne.naechste_gruppe.group_id == "a-de"


def test_die_sortierung_bleibt_stabil() -> None:
    """Innerhalb einer Klasse und eines Landes gilt weiter der Score.

    Die Liste kommt ``sort_by_rank``-sortiert herein; wer hier neu ordnete,
    haette zwei Rangfolgen - und die Anzeige zeigte eine andere Gruppe als
    die, an der gearbeitet wird.
    """
    kampagne = lauf.Kampagnenfortschritt(
        campaign_id="k",
        name="k",
        gruppen=[
            _gruppe("a-de-1", Zielprioritaet.A, Region.DE),
            _gruppe("a-de-2", Zielprioritaet.A, Region.DE),
            _gruppe("a-de-3", Zielprioritaet.A, Region.DE),
        ],
    )

    assert [g.group_id for g in kampagne.arbeitsliste] == ["a-de-1", "a-de-2", "a-de-3"]


def test_die_beitrittsanfragen_gehen_zuerst_nach_deutschland() -> None:
    """Sie folgen der Arbeitsliste - also derselben Reihenfolge.

    Eine Beitrittsanfrage ist die riskanteste Handlung des Projekts, und die
    Tagesmenge ist klein. Sie an eine oesterreichische Gruppe zu geben,
    solange deutsche warten, waere die teuerste Art, sie zu verbrauchen.
    """
    def offen(gid: str, region: Region):
        g = _gruppe(gid, Zielprioritaet.A, region)
        return lauf.Gruppenfortschritt(**{**g.__dict__, "beitritt_noetig": True})

    kampagne = lauf.Kampagnenfortschritt(
        campaign_id="k",
        name="k",
        gruppen=[offen("a-eu", Region.EU), offen("a-de", Region.DE)],
    )

    assert [g.group_id for g in kampagne.beitritt_kandidaten] == ["a-de", "a-eu"]


# --- Die Vorgabe -----------------------------------------------------------

def test_ohne_angabe_gilt_unbekannt_und_nicht_ausserhalb() -> None:
    """Wer nichts gesagt hat, hat nichts gesagt.

    Daraus eine Herabstufung zu machen waere ein Urteil aus dem Nichts -
    dieselbe Ueberlegung wie bei der Vorgabe ``B`` fuer die Klasse und bei
    ``Erlaubnis`` ohne gelesene Regeln.
    """
    assert lauf.Gruppenfortschritt(
        campaign_id="k", group_id="x", name="x", veroeffentlicht=0
    ).zielregion is Region.UNBEKANNT
    assert zielgruppe.Zielbefund().region is Region.UNBEKANNT


# --- Gegen die echte Konfiguration -----------------------------------------

def test_die_echten_listen_stufen_die_beispiele_des_nutzers_richtig_ein(config) -> None:
    """Die drei Zeilen aus der Anforderung, gegen ``settings.yaml``.

    Der Regelsatz oben ist klein und selbstgebaut; dieser Test prueft, dass
    die **echten** Listen dasselbe leisten. Ohne ihn koennte eine vergessene
    Zeile in der Konfiguration die ganze Achse wirkungslos machen, und alle
    Tests darueber blieben gruen.
    """
    regeln = zielgruppe.regeln_aus_config(config)

    beispiele = {
        # FB-SYR-DE-005: Versand mit Ziel, kein Land genannt -> A, unbekannt.
        "أسرع شركة شحن الى سوريا": (Zielprioritaet.A, Region.UNBEKANNT),
        # FB-SYR-DE-024: die Strecke, um die es geht.
        "نقل من لبنان إلى سورية": (Zielprioritaet.C, Region.AUSSERHALB),
        # Und die, die zuerst drankommen soll.
        "شحن وتوصيل من المانيا الى سوريا": (Zielprioritaet.A, Region.DE),
        "شحن من النمسا الى سوريا": (Zielprioritaet.A, Region.EU),
    }
    for name, (klasse, region) in beispiele.items():
        befund = zielgruppe.einstufe(Merkmale(name=name, kategorie="versand"), regeln)
        assert befund.prioritaet is klasse, f"{name}: {befund.grund}"
        assert befund.region is region, f"{name}: {befund.grund}"
