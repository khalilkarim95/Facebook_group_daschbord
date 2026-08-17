"""Rendert die Suchanfragen aus ``config/queries.yaml``.

In Phase 1 dient das ausschliesslich der Vorschau und Pruefung - es wird
nichts gesucht. Der Builder ist deterministisch: gleiche Konfiguration
erzeugt immer dieselbe Liste in derselben Reihenfolge.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from fbgroups.config import AppConfig


@dataclass(frozen=True)
class PlannedQuery:
    query_id: str
    text: str
    lang: str
    scope: str                # "nationwide" | "city"
    audiences: tuple[str, ...]
    city_id: str | None = None
    template_id: str = ""

    @property
    def fingerprint(self) -> str:
        """Stabiler Hash des Anfragetexts - zum Wiedererkennen ueber Laeufe."""
        return hashlib.sha1(self.text.encode("utf-8")).hexdigest()[:12]


def build_queries(config: AppConfig, phase: int = 1) -> list[PlannedQuery]:
    """Erzeugt alle geplanten Anfragen fuer die angegebene Phase."""
    queries_cfg = config.queries
    planned: list[PlannedQuery] = []

    for entry in queries_cfg.get("nationwide", []) or []:
        planned.append(
            PlannedQuery(
                query_id=entry["id"],
                text=entry["text"],
                lang=entry.get("lang", "de"),
                scope="nationwide",
                audiences=tuple(entry.get("audiences", [])),
                template_id=entry["id"],
            )
        )

    patterns = queries_cfg.get("city_patterns", []) or []
    for city in config.cities_for_phase(phase):
        for pattern in patterns:
            text = (
                pattern["pattern"]
                .replace("{city_ar}", city.name_ar)
                .replace("{city}", city.name_de)
            )
            planned.append(
                PlannedQuery(
                    query_id=f"{pattern['id']}__{city.id}",
                    text=text,
                    lang=pattern.get("lang", "de"),
                    scope="city",
                    audiences=tuple(pattern.get("audiences", [])),
                    city_id=city.id,
                    template_id=pattern["id"],
                )
            )

    return planned


def max_results_per_query(config: AppConfig) -> int:
    return int((config.queries.get("settings") or {}).get("max_results_per_query", 10))
