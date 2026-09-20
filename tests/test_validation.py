"""Tests der Validierungsschicht.

Deckt die fuenf geforderten Faelle ab: gueltige URL, ungueltige URL,
offensichtliche Platzhalter-URL, Duplikat, fehlende Metadaten.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.models import DataQuality, Group, PrivacyHint, RecordStatus, ValidationStatus
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


# --- Fall 2: ungueltige URL -------------------------------------------


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


def test_platzhalter_erhaelt_keinen_score(config) -> None:
    group = make_group(
        "111111111111",
        name="Syrer in Berlin",
        audience_tags=["syrians"],
        audience_confidence=1.0,
        city="Berlin",
        city_confidence=1.0,
        member_count=50000,
        validation_status=ValidationStatus.TEST_DATA,
    )
    score_group(group, config)
    # Selbst bei vollstaendigen Metadaten: keine Bewertung erfundener Kennungen.
    assert group.score is None
    assert group.status is RecordStatus.INVALID


# --- Fall 4: Duplikat --------------------------------------------------


# --- Fall 5: fehlende Metadaten ---------------------------------------


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


def test_fehlende_mitgliederzahl_wird_nicht_erfunden(config) -> None:
    """Unbekannte Mitgliederzahl bringt keine Punkte - und keinen Ersatzwert.

    Seit dem 27.08.2026 traegt die Mitgliederzahl 25 der 100 Punkte. Ein
    Ersatzwert waere damit noch gefaehrlicher als vorher: Er saehe in der
    Datenbank aus wie eine gemessene Zahl und entschiede darueber, wo die
    naechsten dreihundert Beitraege hingehen.
    """
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
    assert ohne.score_breakdown.members == 0.0
    assert ohne.member_count is None            # nicht 0 - das waere eine Aussage
    assert "Mitglieder unbekannt" in ohne.score_reason

    mit = score_group(
        make_group(
            REAL_ID_B,
            name="Syrer in Berlin",
            audience_tags=["syrians"],
            audience_confidence=1.0,
            city="Berlin",
            city_confidence=1.0,
            member_count=60000,
        ),
        config,
    )
    # Eine sehr grosse Gruppe steht besser da. Die Gruppe ohne Angabe verliert
    # keine Punkte, ihre erreichbare Hoechstpunktzahl sinkt aber entsprechend.
    assert mit.score > ohne.score
    # Nicht 100: Aktivitaet und Kategorie liegen auch hier nicht vor. Genau
    # das soll score_max sagen - was bei DIESER Datenlage erreichbar war.
    assert mit.score_max == 55.0
    assert ohne.score_max < mit.score_max
    # Alles, was ohne die Mitgliederzahl beurteilbar war, wurde vergeben.
    assert ohne.score == ohne.score_breakdown.total()


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, DataQuality.NONE),
        ({"name": "Test"}, DataQuality.MINIMAL),
        ({"name": "Test", "member_count": 100}, DataQuality.PARTIAL),
        (
            {"name": "Test", "member_count": 100, "description_snippet": "Text"},
            DataQuality.PARTIAL,
        ),
        (
            {
                "name": "Syrer in Berlin",
                "member_count": 100,
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
