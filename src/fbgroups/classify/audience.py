"""Zielgruppen-Erkennung.

Eine Gruppe kann mehrere Tags tragen (z. B. ``syrians`` und ``arabs``).
Treffer im Gruppennamen zaehlen staerker als Treffer im Beschreibungstext.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fbgroups.config import AppConfig
from fbgroups.textnorm import contains_term, normalize


@dataclass
class AudienceResult:
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.0
    matched_terms: dict[str, list[str]] = field(default_factory=dict)


def classify_audience(
    name: str,
    snippet: str | None,
    config: AppConfig,
    phase: int = 1,
) -> AudienceResult:
    """Ermittelt alle passenden Zielgruppen-Tags."""
    name_norm = normalize(name)
    snippet_norm = normalize(snippet or "")

    w_name = float(config.get("classification", "name_confidence", default=1.0))
    w_snippet = float(config.get("classification", "snippet_confidence", default=0.5))

    result = AudienceResult()
    best = 0.0

    for audience in config.audiences_for_phase(phase):
        hits: list[str] = []
        strength = 0.0

        for term in audience.all_terms():
            if contains_term(name_norm, term):
                hits.append(term)
                strength = max(strength, w_name)
            elif contains_term(snippet_norm, term):
                hits.append(term)
                strength = max(strength, w_snippet)

        if hits:
            result.tags.append(audience.id)
            result.matched_terms[audience.id] = hits
            best = max(best, strength)

    result.confidence = round(min(best, 1.0), 2)
    return result
