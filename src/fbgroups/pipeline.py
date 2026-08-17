"""Verkettung der Phase-1-Schritte.

Import -> Normalisierung -> Dedupe -> Klassifikation -> Scoring.

Bewusst frei von Ein-/Ausgabe: Die CLI entscheidet, was gespeichert und
angezeigt wird, damit die Pipeline unveraendert testbar bleibt.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fbgroups.classify import classify_audience, classify_category, classify_city
from fbgroups.config import AppConfig
from fbgroups.dedupe import deduplicate_exact, find_duplicate_suspects
from fbgroups.importers.manual_seed import import_seeds
from fbgroups.models import Group, ImportRun, RecordStatus, SearchRun, ValidationStatus
from fbgroups.scoring import score_all


def classify_group(group: Group, config: AppConfig, phase: int = 1) -> Group:
    """Setzt Zielgruppen-, Stadt- und Kategoriefelder einer Gruppe."""
    audience = classify_audience(group.name, group.description_snippet, config, phase)
    group.audience_tags = audience.tags
    group.audience_confidence = audience.confidence

    city = classify_city(group.name, group.description_snippet, config, phase)
    group.city = city.city
    group.bundesland = city.bundesland
    group.city_confidence = city.confidence

    category = classify_category(group.name, group.description_snippet, config)
    group.category = category.category
    group.category_confidence = category.confidence

    return group


def prepare_groups(
    groups: list[Group],
    config: AppConfig,
    phase: int = 1,
) -> tuple[list[Group], int, list]:
    """Dedupliziert, klassifiziert, bewertet - ohne Bezug auf eine Laufart.

    Manueller Import und automatische Suche nutzen denselben Weg, damit ein
    Treffer aus der Suche exakt so behandelt wird wie eine manuelle Zeile.

    Returns:
        (bewertete Gruppen, Anzahl exakter Dubletten, Dublettenverdacht)
    """
    unique, duplicates = deduplicate_exact(groups)

    for group in unique:
        classify_group(group, config, phase)

    suspects = find_duplicate_suspects(
        unique,
        threshold=float(config.get("dedupe", "fuzzy_threshold", default=88)),
        min_name_length=int(config.get("dedupe", "fuzzy_min_name_length", default=8)),
    )

    return score_all(unique, config), duplicates, suspects


def process_groups(
    groups: list[Group],
    config: AppConfig,
    run: ImportRun,
    phase: int = 1,
) -> list[Group]:
    """Dedupliziert, klassifiziert und bewertet eine Liste von Gruppen."""
    unique, duplicates = deduplicate_exact(groups)
    run.groups_duplicate = duplicates
    run.groups_new = len(unique)

    for group in unique:
        classify_group(group, config, phase)

    run.duplicate_suspects = find_duplicate_suspects(
        unique,
        threshold=float(config.get("dedupe", "fuzzy_threshold", default=88)),
        min_name_length=int(config.get("dedupe", "fuzzy_min_name_length", default=8)),
    )

    # Scoring setzt zugleich data_quality und status jeder Gruppe.
    scored = score_all(unique, config)

    run.groups_test_data = sum(
        1 for g in scored if g.validation_status is ValidationStatus.TEST_DATA
    )
    run.groups_invalid = sum(1 for g in scored if g.status is RecordStatus.INVALID)
    run.groups_insufficient_data = sum(
        1 for g in scored if g.status is RecordStatus.INSUFFICIENT_DATA
    )
    run.groups_validated = sum(1 for g in scored if g.status is RecordStatus.VALIDATED)
    run.groups_scored = sum(1 for g in scored if g.score is not None)

    return scored


def run_seed_import(
    config: AppConfig,
    paths: list[Path] | None = None,
    phase: int = 1,
    run_id: str | None = None,
) -> tuple[list[Group], ImportRun]:
    """Vollstaendiger Phase-1-Lauf ohne Persistenz."""
    groups, run = import_seeds(config, paths=paths, run_id=run_id)
    processed = process_groups(groups, config, run, phase)
    run.finished_at = datetime.now(UTC)
    return processed, run


def process_search_results(
    groups: list[Group],
    config: AppConfig,
    run: SearchRun,
    phase: int = 1,
) -> list[Group]:
    """Verarbeitet die Treffer eines Suchlaufs und fuellt dessen Kennzahlen."""
    processed, _duplicates, suspects = prepare_groups(groups, config, phase)

    run.groups_unique = len(processed)
    run.errors.extend(
        f"Dublettenverdacht: {s.name_a!r} ~ {s.name_b!r} ({s.similarity:.0f})"
        for s in suspects[:5]
    )
    return processed
