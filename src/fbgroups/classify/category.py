"""Themenkategorie einer Gruppe (Jobs, Wohnen, Community ...).

Gewinner ist die Kategorie mit dem staerksten Treffer; bei Gleichstand
entscheidet die Trefferzahl, danach die Reihenfolge in
``config/categories.yaml``.

Wie bei Zielgruppe und Stadt zaehlt ein Treffer im Gruppennamen mehr als einer
im Beschreibungstext, und das Ergebnis traegt eine Konfidenz. Beides fehlte
zuvor: Ein einziges Stichwort irgendwo im Text ergab dieselbe Kategorie mit
derselben Verbindlichkeit wie ein Name, der die Kategorie ausdruecklich fuehrt
- und im Scoring dieselben vollen Punkte. Beschreibungstexte stammen bei
Facebook-Gruppen jedoch oft aus einem einzelnen Beitrag und sagen ueber die
Ausrichtung der Gruppe wenig aus.
"""

from __future__ import annotations

from dataclasses import dataclass

from fbgroups.config import AppConfig
from fbgroups.textnorm import contains_term, normalize


@dataclass
class CategoryResult:
    category: str | None = None
    label_de: str | None = None
    hits: int = 0
    confidence: float = 0.0


def classify_category(
    name: str,
    snippet: str | None,
    config: AppConfig,
    city_id: str | None = None,
) -> CategoryResult:
    """Bestimmt die wahrscheinlichste Themenkategorie.

    ``city_id`` ist das Ergebnis der Stadterkennung. Begriffe, die zugleich ein
    Name **dieser** Stadt sind, zaehlen hier nicht mit: Ein Wort darf nur
    einmal Punkte bringen. Der Fall ist real und im Bestand messbar - "Essen"
    ist eine Stadt mit 600.000 Einwohnern *und* der deutsche Kategoriebegriff
    fuer Speisen. Ohne diesen Ausschluss bekam jede Gruppe namens "Syrer in
    Essen" neben den Stadtpunkten auch die vollen Kategoriepunkte, und stand in
    der Uebersicht als Essensgruppe: 8 von 16 Treffern der Kategorie ``essen``
    waren genau das.

    Ausgeschlossen wird nur der Name der **erkannten** Stadt. "Arabisches Essen
    in Berlin" behaelt seine Kategorie - dort ist Berlin die Stadt, und
    "Essen" bleibt ein Speisebegriff.
    """
    name_norm = normalize(name)
    snippet_norm = normalize(snippet or "")
    if not name_norm and not snippet_norm:
        return CategoryResult()

    stadtnamen: set[str] = set()
    if city_id and city_id in config.cities:
        stadtnamen = {normalize(n) for n in config.cities[city_id].all_names() if n}

    w_name = float(config.get("classification", "name_confidence", default=1.0))
    w_snippet = float(config.get("classification", "snippet_confidence", default=0.5))

    best = CategoryResult()
    for category in config.categories:
        terms = [term for term in category.all_terms() if normalize(term) not in stadtnamen]
        name_hits = sum(1 for term in terms if contains_term(name_norm, term))
        snippet_hits = sum(1 for term in terms if contains_term(snippet_norm, term))
        if not name_hits and not snippet_hits:
            continue

        confidence = w_name if name_hits else w_snippet
        hits = name_hits + snippet_hits
        # Ein Namenstreffer schlaegt jede Zahl von Texttreffern.
        if (confidence, hits) > (best.confidence, best.hits):
            best = CategoryResult(
                category=category.id,
                label_de=category.label_de,
                hits=hits,
                confidence=round(min(confidence, 1.0), 2),
            )

    return best
