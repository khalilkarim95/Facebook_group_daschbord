"""Tests der Validierungsschicht.

Deckt die fuenf geforderten Faelle ab: gueltige URL, ungueltige URL,
offensichtliche Platzhalter-URL, Duplikat, fehlende Metadaten.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.models import DataQuality, Group, PrivacyHint, RecordStatus, ValidationStatus
from fbgroups.pipeline import run_seed_import
from fbgroups.scoring import score_group
from fbgroups.validation import (
    assess_data_quality,
    determine_status,
    has_sufficient_data,
    is_placeholder_identifier,
    validate_group,
)

# Realistisch aussehende Kennungen (zufaellig, keine erkennbaren Muster)
REAL_ID_A = "482910573829104"
REAL_ID_B = "739201847362915"
REAL_ID_C = "615840293748162"


def make_group(group_id: str = REAL_ID_A, **kwargs) -> Group:
    defaults = {
        "group_id": group_id,
        "url_canonical": f"https://www.facebook.com/groups/{group_id}",
    }
    return Group(**{**defaults, **kwargs})


def _write(tmp_path: Path, content: str, name: str = "seeds.csv") -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# --- Fall 1: gueltige Facebook-Gruppen-URL -----------------------------

def test_gueltige_url_wird_als_valid_erkannt() -> None:
    group = make_group()
    assert validate_group(group) is ValidationStatus.VALID


@pytest.mark.parametrize("group_id", [REAL_ID_A, REAL_ID_B, "syrer.berlin.community"])
def test_echte_kennungen_sind_keine_platzhalter(group_id: str) -> None:
    assert not is_placeholder_identifier(group_id)


def test_gueltige_gruppe_mit_daten_wird_validated(config, tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "url;name;member_count\n"
        f"https://www.facebook.com/groups/{REAL_ID_A};Syrer in Berlin;12000\n",
    )
    groups, run = run_seed_import(config, paths=[path])

    assert groups[0].validation_status is ValidationStatus.VALID
    assert groups[0].status is RecordStatus.VALIDATED
    assert groups[0].score is not None
    assert run.groups_validated == 1


# --- Fall 2: ungueltige URL -------------------------------------------

def test_ungueltige_urls_werden_verworfen(config, tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "url;name\n"
        "https://example.com/groups/123;Falsche Domain\n"
        "https://www.facebook.com/pages/abc;Keine Gruppe\n"
        "kein text;Unsinn\n",
    )
    groups, run = run_seed_import(config, paths=[path])

    assert groups == []
    assert run.rows_rejected == 3


def test_leere_kennung_ist_invalid() -> None:
    group = make_group()
    group.group_id = ""
    assert validate_group(group) is ValidationStatus.INVALID


# --- Fall 3: offensichtliche Platzhalter-URL --------------------------

@pytest.mark.parametrize(
    "group_id",
    [
        "123456789012345",   # aufsteigende Ziffernfolge
        "123456789",
        "111111111111",      # identische Ziffern
        "000000000",
        "121212121212",      # kurzer Wiederholungszyklus
        "12345",             # zu kurz fuer eine echte Kennung
        "testgruppe",
        "test.group.berlin",
        "example-group",
        "meine-dummy-gruppe",
        "placeholder",
        "foo.bar",
        "xxx",
    ],
)
def test_platzhalter_werden_erkannt(group_id: str) -> None:
    assert is_placeholder_identifier(group_id), group_id


def test_platzhalter_wird_markiert_nicht_geloescht(config, tmp_path: Path) -> None:
    """Platzhalter bleiben sichtbar im Bestand - markiert, nicht stillschweigend entfernt."""
    path = _write(
        tmp_path,
        "url;name;member_count\n"
        "https://www.facebook.com/groups/123456789012345;Syrer in Berlin;12000\n",
    )
    groups, run = run_seed_import(config, paths=[path])

    assert len(groups) == 1
    assert groups[0].validation_status is ValidationStatus.TEST_DATA
    assert groups[0].status is RecordStatus.INVALID
    assert groups[0].score is None
    assert "test_data" in groups[0].score_reason
    assert run.groups_test_data == 1


def test_platzhalter_erhaelt_keinen_score(config) -> None:
    group = make_group(
        "111111111111",
        name="Syrer in Berlin",
        audience_tags=["syrians"],
        audience_confidence=1.0,
        city="Berlin",
        city_confidence=1.0,
        member_count_hint=50000,
        validation_status=ValidationStatus.TEST_DATA,
    )
    score_group(group, config)
    # Selbst bei vollstaendigen Metadaten: keine Bewertung erfundener Kennungen.
    assert group.score is None
    assert group.status is RecordStatus.INVALID


# --- Fall 4: Duplikat --------------------------------------------------

def test_duplikat_wird_zusammengefuehrt(config, tmp_path: Path) -> None:
    """Dieselbe Gruppe unter zwei URLs ergibt einen Datensatz.

    Vermerkt wird der Zusammenschluss am **Lauf** (``run.groups_duplicate``),
    nicht am Datensatz: Was uebrig bleibt, ist die Gruppe selbst, und die ist
    ``validated``. Frueher trug sie ``duplicate``, abgeleitet aus
    ``times_seen > 1`` - dieselbe Bedingung loest aber auch jeder erneute Fund
    aus einer weiteren Suchanfrage aus. Beide Faelle sind hinterher nicht mehr
    zu unterscheiden, und der zweite ist der weitaus haeufigere.
    """
    path = _write(
        tmp_path,
        "url;name;member_count\n"
        f"https://www.facebook.com/groups/{REAL_ID_A};Syrer in Berlin;12000\n"
        f"https://m.facebook.com/groups/{REAL_ID_A}/?ref=share;Syrer in Berlin;12000\n",
    )
    groups, run = run_seed_import(config, paths=[path])

    assert len(groups) == 1
    assert run.groups_duplicate == 1
    assert groups[0].times_seen == 2
    assert groups[0].status is RecordStatus.VALIDATED
    assert groups[0].score is not None


# --- Fall 5: fehlende Metadaten ---------------------------------------

def test_nur_url_ergibt_insufficient_data(config, tmp_path: Path) -> None:
    """Der Fall aus dem Excel-Export: reine URL-Liste ohne Metadaten."""
    path = tmp_path / "seeds.txt"
    path.write_text(
        f"https://www.facebook.com/groups/{REAL_ID_A}\n"
        f"https://www.facebook.com/groups/{REAL_ID_B}\n"
        f"https://www.facebook.com/groups/{REAL_ID_C}\n",
        encoding="utf-8",
    )
    groups, run = run_seed_import(config, paths=[path])

    assert len(groups) == 3
    for group in groups:
        assert group.status is RecordStatus.INSUFFICIENT_DATA
        assert group.score is None
        assert group.data_quality is DataQuality.NONE
        assert "insufficient_data" in group.score_reason

    assert run.groups_insufficient_data == 3
    assert run.groups_scored == 0


def test_kein_kuenstlicher_einheitsscore(config, tmp_path: Path) -> None:
    """Regressionstest: fruehere Fassung vergab jeder Gruppe denselben Score 8.75."""
    path = tmp_path / "seeds.txt"
    path.write_text(
        "\n".join(f"https://www.facebook.com/groups/{i}" for i in (REAL_ID_A, REAL_ID_B)),
        encoding="utf-8",
    )
    groups, _ = run_seed_import(config, paths=[path])
    assert {g.score for g in groups} == {None}


def test_name_allein_genuegt_nicht(config) -> None:
    group = make_group(name="Irgendeine Gruppe")
    assert not has_sufficient_data(group)
    score_group(group, config)
    assert group.score is None
    assert group.status is RecordStatus.INSUFFICIENT_DATA


def test_name_plus_signal_genuegt(config) -> None:
    group = make_group(name="Syrer in Berlin", audience_tags=["syrians"], audience_confidence=1.0)
    assert has_sufficient_data(group)
    score_group(group, config)
    assert group.score is not None


def test_fehlende_mitgliederzahl_wird_nicht_erfunden(config_mit_mitgliederzahl) -> None:
    """Unbekannte Mitgliederzahl bringt keine Punkte - und keinen Ersatzwert.

    Geprueft mit eingeschalteter Mitgliederzahl: Im Projekt steht das Gewicht
    auf 0, der Ersatzwert-Mechanismus muss aber weiterhin fehlen, falls jemand
    die Zahl von Hand pflegt und den Bestandteil wieder einschaltet.
    """
    config = config_mit_mitgliederzahl
    ohne = score_group(
        make_group(
            name="Syrer in Berlin",
            audience_tags=["syrians"],
            audience_confidence=1.0,
            city="Berlin",
            city_confidence=1.0,
        ),
        config,
    )
    assert ohne.score is not None
    assert ohne.score_breakdown.member_count == 0.0
    assert "Mitgliederzahl unbekannt" in ohne.score_reason

    mit = score_group(
        make_group(
            REAL_ID_B,
            name="Syrer in Berlin",
            audience_tags=["syrians"],
            audience_confidence=1.0,
            city="Berlin",
            city_confidence=1.0,
            member_count_hint=60000,
        ),
        config,
    )
    # Eine sehr grosse Gruppe steht besser da. Die Gruppe ohne Angabe verliert
    # keine Punkte, ihre erreichbare Hoechstpunktzahl sinkt aber entsprechend.
    assert mit.score > ohne.score
    assert mit.score_max == 100.0
    assert ohne.score_max < mit.score_max
    # Alles, was ohne die Mitgliederzahl beurteilbar war, wurde vergeben.
    assert ohne.score == ohne.score_breakdown.total()


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, DataQuality.NONE),
        ({"name": "Test"}, DataQuality.MINIMAL),
        ({"name": "Test", "member_count_hint": 100}, DataQuality.PARTIAL),
        (
            {"name": "Test", "member_count_hint": 100, "description_snippet": "Text"},
            DataQuality.PARTIAL,
        ),
        (
            {
                "name": "Syrer in Berlin",
                "member_count_hint": 100,
                "description_snippet": "Oeffentliche Gruppe",
                "privacy_hint": PrivacyHint.PUBLIC,
            },
            DataQuality.COMPLETE,
        ),
    ],
)
def test_data_quality_stufen(kwargs: dict, expected: DataQuality) -> None:
    assert assess_data_quality(make_group(**kwargs)) is expected


def test_abgeleitete_felder_erhoehen_die_datenqualitaet_nicht() -> None:
    """Zielgruppe, Stadt und Kategorie stammen aus dem Namen - keine neue Information.

    Regressionstest: Sie wurden mitgezaehlt, sodass ein Datensatz mit nichts
    als Name und Beschreibungstext im Export als "complete" auswies, waehrend
    Mitgliederzahl und Sichtbarkeit unbekannt waren.
    """
    group = make_group(
        name="Syrer in Berlin",
        description_snippet="Gruppe fuer Syrer in Berlin",
        audience_tags=["syrians"],
        city="Berlin",
        category="community",
    )
    assert assess_data_quality(group) is DataQuality.PARTIAL


# --- Fall 6: Urteil aus der Pruefliste ---------------------------------

def test_nicht_erreichbar_aus_der_pruefliste(config, tmp_path: Path) -> None:
    """Was der Mensch im Browser sieht, kann das Programm nicht wissen."""
    path = _write(
        tmp_path,
        "url;name;mitglieder;erreichbar\n"
        f"https://www.facebook.com/groups/{REAL_ID_A};Syrer in Berlin;12000;nein\n"
        f"https://www.facebook.com/groups/{REAL_ID_B};Araber in Hamburg;9000;ja\n",
    )
    groups, _ = run_seed_import(config, paths=[path])
    nach_id = {g.group_id: g for g in groups}

    tot = nach_id[REAL_ID_A]
    assert tot.validation_status is ValidationStatus.UNREACHABLE
    assert tot.score is None
    assert tot.status is RecordStatus.INVALID
    assert "unreachable" in tot.score_reason

    lebt = nach_id[REAL_ID_B]
    assert lebt.validation_status is ValidationStatus.VALID
    assert lebt.score is not None


def test_leere_spalte_erreichbar_aendert_nichts(config, tmp_path: Path) -> None:
    """Eine leere Zelle heisst 'nicht geprueft', nicht 'nicht erreichbar'."""
    path = _write(
        tmp_path,
        "url;name;mitglieder;erreichbar\n"
        f"https://www.facebook.com/groups/{REAL_ID_A};Syrer in Berlin;12000;\n",
    )
    groups, _ = run_seed_import(config, paths=[path])
    assert groups[0].validation_status is ValidationStatus.VALID
    assert groups[0].member_count_hint == 12000


def test_manuelles_urteil_ueberlebt_einen_suchtreffer(config, tmp_path: Path) -> None:
    """Ein Suchtreffer belegt nur, dass die URL indexiert wurde - mehr nicht."""
    from fbgroups.storage import SqliteStore

    geprueft = make_group(
        REAL_ID_A,
        name="Syrer in Berlin",
        validation_status=ValidationStatus.UNREACHABLE,
    )
    spaeterer_fund = make_group(REAL_ID_A, name="Syrer in Berlin")

    with SqliteStore(tmp_path / "bestand.sqlite") as store:
        store.upsert_groups([geprueft])
        store.upsert_groups([spaeterer_fund])
        wieder = store.load_groups()[0]

    assert wieder.validation_status is ValidationStatus.UNREACHABLE


def test_metadaten_werden_nie_erfunden(config, tmp_path: Path) -> None:
    """Was nicht in der Datei stand, bleibt leer - kein Ratewert."""
    path = _write(tmp_path, f"url\nhttps://www.facebook.com/groups/{REAL_ID_A}\n")
    groups, _ = run_seed_import(config, paths=[path])

    group = groups[0]
    assert group.name == ""
    assert group.city is None
    assert group.bundesland is None
    assert group.category is None
    assert group.member_count_hint is None
    assert group.audience_tags == []
    assert group.privacy_hint.value == "unknown"


def test_mehrfach_gefundene_gruppe_ist_keine_dublette() -> None:
    """``times_seen`` zaehlt Funde, nicht Datensaetze.

    Nach der Ausweitung der Suchmuster fanden mehrere Anfragen dieselben
    Gruppen - und zwar die einschlaegigsten. Galt das als Dublette, verschwand
    mehr als die Haelfte des Bestands aus jeder Auswertung, die auf
    ``validated`` filterte: 146 von 273, darunter zwei der drei bestbewerteten.

    Echte Dubletten sind hier laengst zusammengefuehrt (``deduplicate_exact``
    laeuft vor der Bewertung), ein ueberlebender Datensatz ist also nie eine
    offene Dublette.
    """
    gruppe = make_group(
        name="Syrer in Berlin",
        city="Berlin",
        audience_tags=["syrians"],
        times_seen=17,
    )

    assert determine_status(gruppe) is RecordStatus.VALIDATED
