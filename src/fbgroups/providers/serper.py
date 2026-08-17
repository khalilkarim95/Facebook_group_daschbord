"""Serper.dev - Google-Ergebnisse als JSON.

Vertrag laut Anbieterdokumentation:
    POST https://google.serper.dev/search
    Header: X-API-KEY, Content-Type: application/json
    Body:   {"q": ..., "gl": ..., "hl": ..., "num": ...}
    Antwort: {"organic": [{"title", "link", "snippet", "position"}], "credits": n}

Ein Credit deckt eine Anfrage mit bis zu 10 Ergebnissen ab; ab 11 Ergebnissen
kostet dieselbe Anfrage zwei Credits. Deshalb ist ``num`` hier auf 10 gedeckelt.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from fbgroups.providers.base import (
    AuthError,
    ProviderCapabilities,
    ProviderState,
    ProviderStatus,
    ProviderUnavailableError,
    QuotaExhaustedError,
    RateLimitError,
    SearchHit,
    SearchQuery,
    SearchResponse,
    TransientError,
    register_provider,
)

ENDPOINT = "https://google.serper.dev/search"


@register_provider("serper")
class SerperProvider:
    name = "serper"

    def __init__(self, settings: dict[str, Any], api_key: str | None = None) -> None:
        self.api_key = api_key or ""
        self.endpoint = settings.get("endpoint", ENDPOINT)
        self.country = settings.get("country", "de")
        self.language = settings.get("language", "de")
        self.timeout = float(settings.get("timeout_seconds", 20))
        self._max_results = min(int(settings.get("max_results_per_call", 10)), 10)

        self.capabilities = ProviderCapabilities(
            max_results_per_call=self._max_results,
            supports_pagination=True,
            supports_site_operator=True,
            supports_language_hint=True,
            requires_api_key=True,
            state=ProviderState.AVAILABLE,
            notes="2.500 Gratis-Credits, 6 Monate gueltig. 1 Credit = 1 Anfrage bis 10 Treffer.",
        )

    def check_availability(self) -> ProviderStatus:
        if not self.api_key:
            return ProviderStatus(
                available=False,
                state=ProviderState.UNAVAILABLE,
                message=(
                    "SERPER_API_KEY fehlt - Schluessel unter serper.dev anlegen "
                    "und in .env eintragen."
                ),
            )
        return ProviderStatus(
            available=True,
            state=ProviderState.AVAILABLE,
            message="Schluessel vorhanden (Kontingent wird erst beim Suchen sichtbar).",
        )

    def _build_body(self, query: SearchQuery) -> dict[str, Any]:
        body: dict[str, Any] = {
            "q": query.text,
            "num": min(query.max_results, self._max_results),
        }
        if self.country:
            body["gl"] = self.country
        if query.language or self.language:
            body["hl"] = query.language or self.language
        return body

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        code = response.status_code
        if code == 401 or code == 403:
            raise AuthError(f"Serper lehnt den Schluessel ab (HTTP {code}).")
        if code == 429:
            retry_after = response.headers.get("Retry-After")
            raise RateLimitError(
                "Serper: zu viele Anfragen.",
                retry_after=float(retry_after) if retry_after else None,
            )
        if code == 402:
            raise QuotaExhaustedError("Serper: Guthaben aufgebraucht.")
        if 500 <= code < 600:
            raise TransientError(f"Serper: Serverfehler (HTTP {code}).")
        if code >= 400:
            raise ProviderUnavailableError(f"Serper: unerwarteter Status {code}.")

    def search(self, query: SearchQuery) -> SearchResponse:
        if not self.api_key:
            raise AuthError("SERPER_API_KEY fehlt.")

        started = time.monotonic()
        try:
            response = httpx.post(
                self.endpoint,
                json=self._build_body(query),
                headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
                timeout=self.timeout,
            )
        except httpx.TimeoutException as exc:
            raise TransientError(f"Serper: Zeitueberschreitung nach {self.timeout}s.") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"Serper nicht erreichbar: {exc}") from exc

        self._raise_for_status(response)
        payload = response.json()

        hits = [
            SearchHit(
                url=item.get("link", ""),
                title=item.get("title", ""),
                snippet=item.get("snippet"),
                rank=int(item.get("position", index)),
                raw=item,
            )
            for index, item in enumerate(payload.get("organic", []), start=1)
            if item.get("link")
        ]

        return SearchResponse(
            query=query,
            hits=hits[: query.max_results],
            provider=self.name,
            # "credits" ist der Verbrauch DIESER Anfrage (in der Regel 1), nicht
            # das Restguthaben. Serper meldet den Kontostand nicht in der
            # Antwort - er steht allein im Konto unter serper.dev/dashboard.
            # Als Restguthaben gelesen ergaebe der Wert eine grob falsche und
            # beunruhigende Anzeige ("Restguthaben 1").
            credits_used=payload.get("credits"),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
