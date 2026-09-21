"""Priorität und Aktivität als Auswahlregel einer Kampagne (21.09.2026).

Zwei Angaben, die kein Programm errechnet hat: Die Mitgliederliste traegt je
Gruppe eine Note ("A++" bis "B") und eine Aktivitaetsstufe ("Sehr Aktiv
(نشط جداً)"). Bis hierhin landete die Note als Freitext in ``notes`` und die
Stufe gar nicht im Bestand - filtern liess sich nach keiner von beiden.

Geprueft wird die ganze Strecke, weil jeder Abschnitt fuer sich lautlos
scheitern wuerde: Einlesen (die Stufe steht in derselben Spalte wie der Kopf
der Gruppenseite), Speichern (ein Schreiblauf ohne die Einstufung darf sie
nicht loeschen) und die Regel (leer heisst keine Einschraenkung, eine genannte
Note schliesst die Unbeurteilten aus).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.config import AppConfig
from fbgroups.marketing.models import Campaign
from fbgroups.marketing.selection import auswahl_der_kampagne, passt, waehle_gruppen
from fbgroups.marketing.store import MarketingStore
from fbgroups.mitglieder import (
    Einlesebericht,
    note_aus_rating,
    stufe_aus_aktivitaet,
    zeile_zu_gruppe,
)
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

URL_A = "https://www.facebook.com/groups/739201847362915/"


def _zeile(**felder: str) -> dict[str, str]:
    grund = dict.fromkeys(
        ("url", "name", "category", "activity", "rating", "city", "country", "notes"), ""
    )
    grund["url"] = URL_A
    grund.update(felder)
    return grund


def _gruppe(gid: str, note: str | None = None, stufe: str | None = None) -> Group:
    return Group(
        group_id=gid,
        url_canonical=f"https://www.facebook.com/groups/{gid}/",
        name=f"Gruppe {gid}",
        listenprioritaet=note,
        aktivitaetsstufe=stufe,
    )


def _kampagne(**regel: object) -> Campaign:
    return Campaign(campaign_id="k1", name="K1", target_include_unscored=True, **regel)


# --- Einlesen --------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "erwartet"),
    [
        ("Sehr Aktiv (نشط جداً)", "sehr_aktiv"),
        ("نشط جداً", "sehr_aktiv"),
        ("Aktiv (نشط)", "aktiv"),
        ("Normal (عادي)", "normal"),
        ("", None),
        # Der Kopf der Gruppenseite steht in derselben Spalte und ist keine
        # Stufe. Eine geratene waere die teuerste Zeile dieser Datei: Sie
        # entschiede spaeter, welche Gruppen eine Kampagne erfasst.
        ("Öffentlich · 5.366 Mitglieder · 50+ Beiträge pro Tag", None),
    ],
)
def test_die_aktivitaetsstufe_wird_gelesen_oder_gar_nicht(text: str, erwartet: str | None) -> None:
    assert stufe_aus_aktivitaet(text) == erwartet


def test_sehr_aktiv_wird_nicht_zu_aktiv() -> None:
    """Sehr Aktiv enthaelt Aktiv - die Reihenfolge der Tabelle entscheidet."""
    assert stufe_aus_aktivitaet("sehr aktiv") == "sehr_aktiv"
    assert stufe_aus_aktivitaet("Aktiv") == "aktiv"


@pytest.mark.parametrize("note", ["A++", "A+", "A", "B+", "B"])
def test_jede_note_der_liste_wird_uebernommen(note: str) -> None:
    assert note_aus_rating(f" {note.lower()} ") == (note, "")


def test_eine_unbekannte_note_wird_gemeldet_und_nicht_geraten() -> None:
    """Aus "A-" wird kein "A" - das waere ein erfundenes Urteil."""
    assert note_aus_rating("A-") == (None, "A-")


def test_note_und_stufe_stehen_danach_am_datensatz(config: AppConfig) -> None:
    bericht = Einlesebericht()
    gruppe = zeile_zu_gruppe(
        _zeile(rating="A++", activity="Sehr Aktiv (نشط جداً)"), 2, "t.csv", config, bericht
    )
    assert gruppe is not None
    assert gruppe.listenprioritaet == "A++"
    assert gruppe.aktivitaetsstufe == "sehr_aktiv"
    # Die Note hat ein Feld und gehoert nicht zusaetzlich in den Freitext:
    # zweimal gespeichert waeren es zwei Wahrheiten ueber dieselbe Einstufung.
    assert "A++" not in gruppe.notes
    assert bericht.unbekannte_noten == []
    assert bericht.unbekannte_aktivitaet == []


def test_der_kopf_der_gruppenseite_wird_nicht_als_stufe_gemeldet(config: AppConfig) -> None:
    """Sonst stuende halb so viel Laerm im Bericht wie Zeilen in der Datei."""
    bericht = Einlesebericht()
    gruppe = zeile_zu_gruppe(
        _zeile(activity="Öffentlich · 942 Mitglieder"), 2, "t.csv", config, bericht
    )
    assert gruppe is not None
    assert gruppe.member_count == 942
    assert gruppe.aktivitaetsstufe is None
    assert bericht.unbekannte_aktivitaet == []


# --- Die Regel -------------------------------------------------------------


def test_leer_heisst_weiterhin_keine_einschraenkung() -> None:
    auswahl = auswahl_der_kampagne(_kampagne())
    assert auswahl.ohne_einschraenkung
    assert passt(_gruppe("1"), auswahl)
    assert passt(_gruppe("2", note="B", stufe="normal"), auswahl)


def test_die_regel_waehlt_nach_prioritaet() -> None:
    auswahl = auswahl_der_kampagne(_kampagne(target_prioritaeten=["A++", "A+"]))
    assert passt(_gruppe("1", note="A++"), auswahl)
    assert passt(_gruppe("2", note="A+"), auswahl)
    assert not passt(_gruppe("3", note="A"), auswahl)
    assert not passt(_gruppe("4", note="B"), auswahl)


def test_die_regel_waehlt_nach_aktivitaetsstufe() -> None:
    auswahl = auswahl_der_kampagne(_kampagne(target_aktivitaet=["sehr_aktiv"]))
    assert passt(_gruppe("1", stufe="sehr_aktiv"), auswahl)
    assert not passt(_gruppe("2", stufe="aktiv"), auswahl)


def test_beide_filter_wirken_zusammen_und_nicht_nacheinander() -> None:
    """Eine Regel mit zwei Angaben meint beide - nicht die eine oder die andere."""
    auswahl = auswahl_der_kampagne(
        _kampagne(target_prioritaeten=["A++"], target_aktivitaet=["sehr_aktiv"])
    )
    gruppen = [
        _gruppe("1", note="A++", stufe="sehr_aktiv"),
        _gruppe("2", note="A++", stufe="normal"),
        _gruppe("3", note="B", stufe="sehr_aktiv"),
    ]
    assert [g.group_id for g in waehle_gruppen(gruppen, auswahl)] == ["1"]


def test_eine_nicht_eingestufte_gruppe_faellt_aus_einer_regel_mit_note() -> None:
    """Wer "A++" waehlt, meint nicht "A++ und alles Unbeurteilte".

    Der Unterschied zu ``include_unscored`` ist der Gegenstand: Dort ist
    "kein Score" eine Aussage ueber unsere Datenlage, hier ist "keine Note"
    eine darueber, dass niemand die Gruppe angesehen hat.
    """
    auswahl = auswahl_der_kampagne(_kampagne(target_prioritaeten=["A++"]))
    assert not passt(_gruppe("1"), auswahl)


def test_die_schreibweise_entscheidet_nicht() -> None:
    """Eine Kampagne von der Kommandozeile traegt die Note vielleicht klein."""
    auswahl = auswahl_der_kampagne(
        _kampagne(target_prioritaeten=["a++"], target_aktivitaet=["SEHR_AKTIV"])
    )
    assert passt(_gruppe("1", note="A++", stufe="sehr_aktiv"), auswahl)


def test_die_regel_steht_in_der_beschreibung() -> None:
    auswahl = auswahl_der_kampagne(
        _kampagne(target_prioritaeten=["A+", "A++"], target_aktivitaet=["aktiv"])
    )
    text = auswahl.beschreibung()
    # Von der besten Note nach unten, nicht alphabetisch: Sonst stuende "A+"
    # vor "A++", und eine Regel liest man von oben.
    assert "Priorität: A++, A+" in text
    assert "Aktivität: aktiv" in text


# --- Speichern -------------------------------------------------------------


def test_die_einstufung_ueberlebt_einen_schreiblauf_ohne_sie(tmp_path: Path) -> None:
    """Dieselbe Regel wie bei den erhobenen Zahlen (``COALESCE``).

    Ein Lauf, der die Einstufung nicht mitbringt, darf sie nicht loeschen.
    Sie ist Handarbeit, und niemand merkte ihr Verschwinden - der
    Kampagnenfilter traefe einfach weniger Gruppen.
    """
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups([_gruppe("1", note="A++", stufe="sehr_aktiv")])
        store.upsert_groups([_gruppe("1")])
        geladen = store.get_group("1")

    assert geladen is not None
    assert geladen.listenprioritaet == "A++"
    assert geladen.aktivitaetsstufe == "sehr_aktiv"


def test_eine_neue_einstufung_ersetzt_die_alte(tmp_path: Path) -> None:
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups([_gruppe("1", note="B", stufe="normal")])
        store.upsert_groups([_gruppe("1", note="A+", stufe="aktiv")])
        geladen = store.get_group("1")

    assert geladen is not None
    assert geladen.listenprioritaet == "A+"
    assert geladen.aktivitaetsstufe == "aktiv"


def test_die_regel_ueberlebt_speichern_und_laden(tmp_path: Path) -> None:
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups([_gruppe("1", note="A++", stufe="sehr_aktiv")])

    with MarketingStore(pfad) as store:
        store.save_campaign(
            _kampagne(target_prioritaeten=["A++"], target_aktivitaet=["sehr_aktiv"])
        )
        geladen = store.load_campaign("k1")

    assert geladen is not None
    assert geladen.target_prioritaeten == ["A++"]
    assert geladen.target_aktivitaet == ["sehr_aktiv"]
