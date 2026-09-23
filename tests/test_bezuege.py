"""Die Bezuege als Grundlage - und die Zielklassen, die es nicht mehr gibt.

Anweisung des Nutzers vom 23.09.2026: Eine Gruppe wird nicht mehr nach
Klasse A-D, Region oder Note beurteilt, sondern nach den Bezuegen, die in
ihren Beitraegen tatsaechlich vorkommen. Was eine Gruppe ohne Bezug
erfaehrt, ist **noch nicht festgelegt** - bis dahin werden alle gleich
behandelt.

Die Beispiele sind die des Nutzers, ergaenzt um die Faelle, an denen die
alte Erkennung nachweislich scheiterte ("عندي وزن 80 كيلو وبدي انحف").
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.marketing import lauf
from fbgroups.marketing.bezug import Bezug, Richtung, erkenne, fuer_gruppe
from fbgroups.marketing.store import MarketingStore
from fbgroups.storage import SqliteStore

B = Bezug


@pytest.fixture()
def marketing_store(tmp_path: Path):
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad):
        pass
    with MarketingStore(pfad) as store:
        yield store


# --- 1. Die Beispiele des Nutzers --------------------------------------------


def test_platz_in_der_tasche() -> None:
    assert B.GEPAECK_KAPAZITAET in erkenne("معي مجال بالشنطة").bezuege


def test_reisender_der_etwas_mitnimmt() -> None:
    befund = erkenne("نازل ع دمشق وبقدر آخد معاي غرض")

    assert {B.REISENDER, B.MITNAHME, B.GEGENSTAND, B.SENDUNG_MIT_REISENDEM} <= befund.bezuege
    assert befund.richtung is Richtung.NACH_ZIEL


def test_jemand_soll_aus_aleppo_etwas_mitbringen() -> None:
    befund = erkenne("بدي حدا جاي من حلب ياخدلي زيت")

    assert {
        B.REISENDER, B.GEGENSTAND, B.RICHTUNG, B.MITNAHME, B.SENDUNG_MIT_REISENDEM
    } <= befund.bezuege
    assert befund.richtung is Richtung.AUS_ZIEL


# --- 2. Ein Bezug ist ein Begriff, kein Wort --------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "عندي وزن 80 كيلو وبدي انحف",  # Koerpergewicht, nicht Gepaeck
        "رايح ع الشغل بكرا الصبح",  # Bewegung ohne Reise
        "شحن رصيد سيريتل وام تي ان",  # Guthaben, kein Versand
        "بدي شقة ببرلين للايجار ضروري",
        "عندي دوام بكرا بالمطعم",  # "دوام" enthaelt "دوا"
        "بدي محل بالشارع الرئيسي",  # "محل" aus der Musterliste - zu breit
    ],
)
def test_diese_beitraege_tragen_keinen_bezug(text: str) -> None:
    assert erkenne(text).bezuege == frozenset()


def test_naechste_woche_ist_keine_rueckreise() -> None:
    """"الاسبوع الجاي" enthaelt "جاي" - und heisst "naechste Woche"."""
    befund = erkenne("رايح ع الشام الاسبوع الجاي اذا حدا عنده امانة")

    assert befund.richtung is Richtung.NACH_ZIEL
    assert B.SENDUNG_MIT_REISENDEM in befund.bezuege


def test_ein_reisender_der_versand_anbietet() -> None:
    """Die Faelle, an denen die alte Erkennung "keiner" meldete."""
    befund = erkenne("طالع من الشام ع المانيا بعد 3 ايام اذا حدا بدو يبعت شي")

    assert {B.REISENDER, B.MITNAHME, B.SENDUNG_MIT_REISENDEM, B.TERMIN_DATUM} <= befund.bezuege
    assert befund.richtung is Richtung.AUS_ZIEL


def test_der_termin_zaehlt_nur_neben_einer_reise() -> None:
    assert B.TERMIN_DATUM not in erkenne("بكرا عندي موعد بالبلدية").bezuege
    assert B.TERMIN_DATUM in erkenne("مسافر ع دمشق يوم الجمعة").bezuege


def test_gewicht_nur_mit_gepaeck() -> None:
    assert B.GEPAECK_GEWICHT not in erkenne("كيلو البندورة بـ 2 يورو").bezuege
    assert B.GEPAECK_GEWICHT in erkenne("مسافر ع الشام ومعي 5 كيلو").bezuege


def test_jeder_befund_nennt_seine_belege() -> None:
    befund = erkenne("مين مسافر ع دمشق؟")

    belege = dict(befund.treffer)
    assert set(belege) == set(befund.bezuege)
    assert all(belege[b] for b in befund.bezuege)


# --- 3. Die Gruppe: gesammelt, nicht geraten -------------------------------


def test_die_gruppe_sammelt_die_bezuege_ihrer_beitraege() -> None:
    gruppe = fuer_gruppe(
        [
            erkenne("معي مجال بالشنطة").bezuege,
            erkenne("مين مسافر ع دمشق؟").bezuege,
            erkenne("بدي شقة ببرلين للايجار ضروري").bezuege,
        ]
    )

    assert gruppe.beitraege == 3
    assert B.GEPAECK_KAPAZITAET in gruppe.bezuege
    assert B.REISENDER in gruppe.bezuege
    assert not gruppe.leer


def test_gelesen_und_nichts_gefunden_ist_nicht_ungelesen() -> None:
    nichts = fuer_gruppe([frozenset(), frozenset()])
    ungelesen = fuer_gruppe([])

    assert nichts.leer and nichts.gelesen
    assert ungelesen.leer and not ungelesen.gelesen


def test_ein_unbekannter_name_wird_uebergangen() -> None:
    """Ein Bezug aus einer aelteren Fassung der Liste wird nicht geraten."""
    gruppe = fuer_gruppe([["reisender", "gibt_es_nicht"]])

    assert gruppe.bezuege == (B.REISENDER,)


def test_der_speicher_haelt_je_beitrag_eine_zeile(marketing_store) -> None:
    """Derselbe Beitrag, zweimal gelesen, zaehlt einmal."""
    marketing_store.merke_bezuege("g1", "https://fb/p/1", ["reisender"])
    marketing_store.merke_bezuege("g1", "https://fb/p/1", ["reisender", "mitnahme"])
    marketing_store.merke_bezuege("g1", "https://fb/p/2", [])

    gruppe = marketing_store.gruppenbezuege()["g1"]

    assert gruppe.beitraege == 2
    assert gruppe.anzahl == {B.REISENDER: 1, B.MITNAHME: 1}


def test_im_speicher_steht_kein_text(marketing_store) -> None:
    spalten = {
        r["name"]
        for r in marketing_store.conn.execute("PRAGMA table_info(beitrag_bezuege)")
    }

    assert spalten == {"group_id", "post_url", "bezuege", "gelesen_am"}


# --- 4. Die Zielklassen sind weg ---------------------------------------------


def test_es_gibt_keine_zielklassen_mehr() -> None:
    """Kein Modul, kein Import, kein Aufruf - Kommentare duerfen erzaehlen."""
    assert not Path("src/fbgroups/marketing/zielgruppe.py").exists()
    for datei in Path("src/fbgroups").rglob("*.py"):
        quelltext = datei.read_text(encoding="utf-8")
        for spur in (
            "marketing.zielgruppe",
            "import zielgruppe",
            "Zielprioritaet.",
            "bearbeitbare_klassen(",
            "anspruch_fuer(",
            "notenrang(",
        ):
            assert spur not in quelltext, (datei, spur)


def _gruppe(gid: str, **felder) -> lauf.Gruppenfortschritt:
    return lauf.Gruppenfortschritt(
        campaign_id="k",
        group_id=gid,
        name=gid,
        veroeffentlicht=0,
        ziel=10,
        mitglied=True,
        **felder,
    )


def test_bezuege_aendern_weder_reihenfolge_noch_bearbeitung() -> None:
    """Bis die Behandlung festgelegt ist, entscheiden die Bezuege nichts."""
    ohne = _gruppe("ohne")
    mit = _gruppe("mit", bezuege=("reisender", "mitnahme"))
    kampagne = lauf.Kampagnenfortschritt(
        campaign_id="k", name="k", gruppen=[ohne, mit]
    )

    assert [g.group_id for g in kampagne.arbeitsliste] == ["ohne", "mit"]
    assert ohne.bearbeitbar is mit.bearbeitbar is True
    assert kampagne.gruppen_ohne_bezug == 1


def test_beitrittsanfragen_sind_in_der_konfiguration_abgeschaltet(config) -> None:
    """Bis festgelegt ist, welche Bezuege eine Anfrage rechtfertigen.

    Abgeschaltet in ``settings.yaml`` und nicht im Code: Der Weg selbst gilt
    jetzt fuer jede Gruppe (keine Zielklasse mehr), und das Einschalten ist
    eine Zahl.
    """
    from fbgroups.marketing import grenzen

    gruppe = _gruppe("g", beitritt_noetig=True, regeln_gelesen=True)
    kampagne = lauf.Kampagnenfortschritt(campaign_id="k", name="k", gruppen=[gruppe])

    assert [g.group_id for g in kampagne.beitritt_kandidaten] == ["g"]
    assert grenzen.einstellungen(config).fuer(grenzen.Aktion.BEITRITT).abgeschaltet
