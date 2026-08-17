"""Stadt-Erkennung.

Anders als bei den Zielgruppen wird genau eine Stadt zugeordnet: die mit der
hoechsten Konfidenz. Bei Gleichstand entscheidet die groessere Einwohnerzahl,
weil ein Nebentreffer selten die groessere Stadt meint.
"""

from __future__ import annotations

from dataclasses import dataclass

from fbgroups.config import AppConfig, City
from fbgroups.textnorm import contains_term, normalize


@dataclass
class CityResult:
    city: str | None = None
    city_id: str | None = None
    bundesland: str | None = None
    confidence: float = 0.0
    matched_name: str | None = None


def classify_city(
    name: str,
    snippet: str | None,
    config: AppConfig,
    phase: int = 1,
) -> CityResult:
    """Ordnet der Gruppe hoechstens eine Stadt zu."""
    name_norm = normalize(name)
    snippet_norm = normalize(snippet or "")

    w_name = float(config.get("classification", "name_confidence", default=1.0))
    w_snippet = float(config.get("classification", "snippet_confidence", default=0.5))
    min_conf = float(config.get("classification", "city_min_confidence", default=0.5))

    best: tuple[float, int, City, str] | None = None

    for city in config.cities_for_phase(phase):
        for candidate in city.all_names():
            if not candidate:
                continue
            if contains_term(name_norm, candidate):
                conf = w_name
            elif contains_term(snippet_norm, candidate):
                conf = w_snippet
            else:
                continue

            key = (conf, city.population, city, candidate)
            if best is None or (key[0], key[1]) > (best[0], best[1]):
                best = key

    if best is None or best[0] < min_conf:
        return CityResult()

    conf, _, city, matched = best
    return CityResult(
        city=city.name_de,
        city_id=city.id,
        bundesland=city.bundesland,
        confidence=round(min(conf, 1.0), 2),
        matched_name=matched,
    )
