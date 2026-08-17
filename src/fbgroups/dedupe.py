"""Deduplizierung.

Zwei Stufen mit unterschiedlicher Verbindlichkeit:

1. Exakt ueber ``group_id`` - dieselbe Gruppe, wird automatisch zusammengefuehrt.
2. Unscharf ueber den Namen - nur ein *Verdacht*. Bewusst keine automatische
   Zusammenfuehrung: "Syrer in Berlin" und "Syrer in Berlin 2" sind aehnlich,
   aber verschiedene Gruppen. Die Entscheidung bleibt beim Menschen.
"""

from __future__ import annotations

from rapidfuzz import fuzz

from fbgroups.models import DuplicateSuspect, Group
from fbgroups.textnorm import normalize


def deduplicate_exact(groups: list[Group]) -> tuple[list[Group], int]:
    """Fuehrt Gruppen mit identischer ``group_id`` zusammen.

    Returns:
        (eindeutige Gruppen in Eingabereihenfolge, Anzahl zusammengefuehrter Dubletten)
    """
    merged: dict[str, Group] = {}
    duplicates = 0

    for group in groups:
        existing = merged.get(group.group_id)
        if existing is None:
            merged[group.group_id] = group
        else:
            existing.merge_duplicate(group)
            duplicates += 1

    return list(merged.values()), duplicates


def find_duplicate_suspects(
    groups: list[Group],
    threshold: float = 88.0,
    min_name_length: int = 8,
) -> list[DuplicateSuspect]:
    """Findet Paare mit auffaellig aehnlichen Namen."""
    candidates = [
        (g, normalize(g.name))
        for g in groups
        if g.name and len(normalize(g.name)) >= min_name_length
    ]

    suspects: list[DuplicateSuspect] = []
    for i, (group_a, name_a) in enumerate(candidates):
        for group_b, name_b in candidates[i + 1 :]:
            similarity = fuzz.token_set_ratio(name_a, name_b)
            if similarity >= threshold:
                suspects.append(
                    DuplicateSuspect(
                        group_id_a=group_a.group_id,
                        group_id_b=group_b.group_id,
                        name_a=group_a.name,
                        name_b=group_b.name,
                        similarity=round(float(similarity), 1),
                    )
                )

    return sorted(suspects, key=lambda s: s.similarity, reverse=True)
