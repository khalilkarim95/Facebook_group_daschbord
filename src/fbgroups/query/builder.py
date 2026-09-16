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

    # Zwei bundesweite Bloecke, und die Reihenfolge ist die Rangfolge der
    # Kampagne: ``reise_versand`` steht vorn, weil dort der Zielmarkt liegt
    # (Prioritaet A in ``marketing/zielgruppe.py``). Wer mit ``--limit`` nur
    # einen Teil abfragt, soll den wichtigeren Teil bekommen.
    #
    # Ein eigener Block und nicht angehaengt an ``nationwide``: Die Kosten
    # sollen sich einzeln ablesen lassen, und wer ihn nicht will, kommentiert
    # ihn aus, statt 46 Zeilen zwischen 18 anderen zu suchen.
    # **Die Reihenfolge ist die Rangfolge der Kampagne** (13.09.2026): erst
    # alles, was den Zielmarkt sucht (Reise und Versand nach Syrien -
    # Prioritaet A in ``marketing/zielgruppe.py``), dann die
    # Gemeinschaftsgruppen. Sie wird erst sichtbar, wenn das Guthaben nicht
    # fuer alle Anfragen reicht: Wer mit ``--limit 106`` sucht, bekommt genau
    # die neuen Anfragen und keine einzige alte.
    #
    # Warum vier Bloecke und nicht zwei: Die Stadtmuster werden je Stadt
    # ausgefaltet. Lagen die Reise-Muster mit in ``city_patterns``, standen
    # sie **je Stadt** hinter deren sechzehn Gemeinschaftsanfragen - erst
    # Berlin-Gemeinschaft, dann Berlin-Reise, dann Hamburg-Gemeinschaft. Ein
    # begrenzter Lauf bezahlte damit fast nur Gemeinschaftsgruppen, also
    # genau die dreihundert, die ohnehin schon im Bestand standen.
    for block in ("reise_versand", "nationwide"):
        for entry in queries_cfg.get(block, []) or []:
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
        # Die Stadtmuster desselben Zwecks direkt dahinter: erst alle
        # Reise-/Versandanfragen (bundesweit und je Stadt), dann alle
        # Gemeinschaftsanfragen.
        stadtblock = (
            "city_patterns_reise_versand" if block == "reise_versand" else "city_patterns"
        )
        planned.extend(_stadtanfragen(config, queries_cfg.get(stadtblock, []) or [], phase))

    return planned


def _stadtanfragen(config: AppConfig, patterns: list, phase: int) -> list[PlannedQuery]:
    """Ein Muster mal jede freigeschaltete Stadt.

    Herausgeloest, weil es jetzt zweimal gebraucht wird - einmal fuer die
    Reise- und Versandmuster, einmal fuer die Gemeinschaftsmuster. Zwei
    Kopien dieser Schleife koennten auseinanderlaufen, und der Unterschied
    fiele erst an einer Anfrage auf, die nie gestellt wurde.
    """
    gebaut: list[PlannedQuery] = []
    for city in config.cities_for_phase(phase):
        for pattern in patterns:
            text = (
                pattern["pattern"]
                .replace("{city_ar}", city.name_ar)
                .replace("{city}", city.name_de)
            )
            gebaut.append(
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
    return gebaut


def max_results_per_query(config: AppConfig) -> int:
    return int((config.queries.get("settings") or {}).get("max_results_per_query", 10))
