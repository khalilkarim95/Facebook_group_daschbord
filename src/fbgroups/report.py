"""Qualitaetsreport eines Laufs und des Gesamtbestands.

Der Report ist das eigentliche Ergebnis von Phase 1: Er zeigt, wie sauber die
Eingabe verarbeitet wurde und wie sich der Bestand auf Zielgruppen, Staedte
und Kategorien verteilt.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from fbgroups.models import Group, ImportRun


@dataclass
class Distribution:
    by_audience: Counter = field(default_factory=Counter)
    by_city: Counter = field(default_factory=Counter)
    by_category: Counter = field(default_factory=Counter)
    by_bundesland: Counter = field(default_factory=Counter)
    unclassified_audience: int = 0
    unclassified_city: int = 0
    unclassified_category: int = 0


@dataclass
class ScoreStats:
    count: int = 0          # Anzahl bewerteter Gruppen
    unscored: int = 0       # nicht bewertbar (Score None)
    minimum: float = 0.0
    maximum: float = 0.0
    average: float = 0.0
    top: list[tuple[str, float]] = field(default_factory=list)


def build_distribution(groups: list[Group]) -> Distribution:
    dist = Distribution()
    for group in groups:
        if group.audience_tags:
            for tag in group.audience_tags:
                dist.by_audience[tag] += 1
        else:
            dist.unclassified_audience += 1

        if group.city:
            dist.by_city[group.city] += 1
            if group.bundesland:
                dist.by_bundesland[group.bundesland] += 1
        else:
            dist.unclassified_city += 1

        if group.category:
            dist.by_category[group.category] += 1
        else:
            dist.unclassified_category += 1

    return dist


def build_score_stats(groups: list[Group], top_n: int = 5) -> ScoreStats:
    """Kennzahlen ausschliesslich ueber die tatsaechlich bewerteten Gruppen.

    Nicht bewertbare Datensaetze fliessen nicht als Null in den Mittelwert ein -
    das wuerde den Durchschnitt verfaelschen.
    """
    scored = [g for g in groups if g.score is not None]
    if not scored:
        return ScoreStats(unscored=len(groups))

    scores = [g.score for g in scored]
    ranked = sorted(scored, key=lambda g: -g.score)[:top_n]

    return ScoreStats(
        count=len(scored),
        unscored=len(groups) - len(scored),
        minimum=round(min(scores), 1),
        maximum=round(max(scores), 1),
        average=round(sum(scores) / len(scores), 1),
        top=[(g.name or g.group_id, g.score) for g in ranked],
    )


def build_status_counts(groups: list[Group]) -> Counter:
    return Counter(g.status.value for g in groups)


def build_validation_counts(groups: list[Group]) -> Counter:
    return Counter(g.validation_status.value for g in groups)


def build_quality_counts(groups: list[Group]) -> Counter:
    return Counter(g.data_quality.value for g in groups)


def passes_min_score(group: Group, min_score: float) -> bool:
    """Filterregel fuer den Export.

    Nicht bewertete Gruppen sind nicht "schlechter" als der Schwellwert - sie
    sind unbewertet. Sie bleiben deshalb enthalten, solange kein Schwellwert
    gefordert ist, und entfallen, sobald ausdruecklich nach Score gefiltert wird.
    """
    if group.score is None:
        return min_score <= 0
    return group.score >= min_score


def rejection_reasons(run: ImportRun) -> Counter:
    return Counter(row.reason for row in run.rejected)


def coverage_percent(run: ImportRun) -> float:
    """Anteil verwertbarer Eingabezeilen - Qualitaetsmass der Seed-Liste."""
    if run.rows_total == 0:
        return 0.0
    return round(run.rows_valid / run.rows_total * 100, 1)
