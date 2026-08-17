"""Offline-Provider aus gespeicherten Antworten.

Erfuellt denselben Vertrag wie die echten Dienste, ruft aber nichts ab. Damit
laeuft die gesamte Suchpipeline in Tests und in der Entwicklung ohne Konto,
ohne Schluessel und ohne Kontingentverbrauch.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fbgroups.providers.base import (
    ProviderCapabilities,
    ProviderState,
    ProviderStatus,
    ProviderUnavailableError,
    SearchHit,
    SearchQuery,
    SearchResponse,
    register_provider,
)
from fbgroups.utils.keys import query_key


@register_provider("fixture")
class FixtureProvider:
    name = "fixture"

    def __init__(self, settings: dict[str, Any], api_key: str | None = None) -> None:
        self.fixtures_dir = Path(settings.get("fixtures_dir", "tests/fixtures/search"))
        self.strict = bool(settings.get("strict", False))
        self.capabilities = ProviderCapabilities(
            max_results_per_call=int(settings.get("max_results_per_call", 10)),
            supports_pagination=False,
            supports_site_operator=True,
            supports_language_hint=True,
            requires_api_key=False,
            state=ProviderState.AVAILABLE,
            notes="Offline-Provider - liest gespeicherte Antworten, ruft nichts ab.",
        )

    def check_availability(self) -> ProviderStatus:
        if not self.fixtures_dir.exists():
            return ProviderStatus(
                available=False,
                state=ProviderState.UNAVAILABLE,
                message=f"Fixture-Verzeichnis fehlt: {self.fixtures_dir}",
            )
        count = len(list(self.fixtures_dir.glob("*.json")))
        return ProviderStatus(
            available=True,
            state=ProviderState.AVAILABLE,
            message=f"{count} gespeicherte Antworten in {self.fixtures_dir}",
        )

    def search(self, query: SearchQuery) -> SearchResponse:
        started = time.monotonic()
        # Erster Weg: Datei nach der query_id benannt - leichter zu pflegen und
        # unabhaengig davon, wie der Anfragetext gerade zusammengesetzt wird.
        path = self.fixtures_dir / f"{query.query_id}.json"

        if not path.exists():
            path = self.fixtures_dir / f"{query_key('fixture', query.text)}.json"

        if not path.exists():
            if self.strict:
                raise ProviderUnavailableError(f"Keine Fixture fuer Anfrage: {query.text!r}")
            return SearchResponse(query=query, hits=[], provider=self.name, duration_ms=0)

        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)

        hits = [
            SearchHit(
                url=item.get("url", ""),
                title=item.get("title", ""),
                snippet=item.get("snippet"),
                rank=index,
                raw=item,
            )
            for index, item in enumerate(payload.get("results", [])[: query.max_results], start=1)
        ]

        return SearchResponse(
            query=query,
            hits=hits,
            provider=self.name,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
