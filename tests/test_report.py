from __future__ import annotations

import pytest

from fbgroups.models import Group, RecordStatus
from fbgroups.report import build_score_stats, build_status_counts, passes_min_score

REAL_ID = "482910573829104"


def make_group(score: float | None, group_id: str = REAL_ID) -> Group:
    return Group(
        group_id=group_id,
        url_canonical=f"https://www.facebook.com/groups/{group_id}",
        name="Testgruppe",
        score=score,
        status=RecordStatus.VALIDATED if score is not None else RecordStatus.INSUFFICIENT_DATA,
    )


@pytest.mark.parametrize(
    ("score", "min_score", "expected"),
    [
        (80.0, 0.0, True),
        (80.0, 50.0, True),
        (30.0, 50.0, False),
        (None, 0.0, True),    # ohne Filter bleiben unbewertete Gruppen sichtbar
        (None, 50.0, False),  # mit Schwellwert entfallen sie
    ],
)
def test_min_score_filter(score, min_score, expected) -> None:
    assert passes_min_score(make_group(score), min_score) is expected


def test_statistik_ignoriert_unbewertete(config) -> None:
    """Unbewertete Gruppen duerfen den Mittelwert nicht nach unten ziehen."""
    groups = [
        make_group(80.0, "482910573829104"),
        make_group(60.0, "739201847362915"),
        make_group(None, "615840293748162"),
    ]
    stats = build_score_stats(groups)

    assert stats.count == 2
    assert stats.unscored == 1
    assert stats.average == 70.0     # nicht 46.7
    assert stats.minimum == 60.0


def test_statistik_ohne_bewertete_gruppen() -> None:
    stats = build_score_stats([make_group(None)])
    assert stats.count == 0
    assert stats.unscored == 1
    assert stats.average == 0.0


def test_status_zaehlung() -> None:
    counts = build_status_counts([make_group(80.0), make_group(None, "739201847362915")])
    assert counts["validated"] == 1
    assert counts["insufficient_data"] == 1
