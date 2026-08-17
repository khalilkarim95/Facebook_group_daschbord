"""Brave Search API - eigener Index.

Vertrag laut Anbieterdokumentation:
    GET https://api.search.brave.com/res/v1/web/search
    Header: X-Subscription-Token, Accept: application/json
    Parameter: q, count (max 20), offset (max 9), country, search_lang
    Antwort: {"web": {"results": [{"title", "url", "description"}]}}

Der kostenlose Tarif erlaubt rund eine Anfrage pro Sekunde; die Taktbremse in
``search.py`` haelt diesen Abstand ein, statt in ein 429 zu laufen.
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

ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
MAX_COUNT = 20


@register_provider("brave")
class BraveProvider:
    name = "brave"

    def __init__(self, settings: dict[str, Any], api_key: str | None = None) -> None:
        self.api_key = api_key or ""
        self.endpoint = settings.get("endpoint", ENDPOINT)
        self.country = settings.get("country", "DE")
        self.language = settings.get("language", "de")
        self.timeout = float(settings.get("timeout_seconds", 20))
        self._max_results = min(int(settings.get("max_results_per_call", 10)), MAX_COUNT)

        self.capabilities = ProviderCapabilities(
            max_results_per_call=self._max_results,
            supports_pagination=True,
            supports_site_operator=True,
            supports_language_hint=True,
            requires_api_key=True,
            state=ProviderState.AVAILABLE,
            notes="5 $ Guthaben monatlich, erneuert sich (~1.000 Anfragen). Ca. 1 Anfrage/Sekunde.",
        )

    def check_availability(self) -> ProviderStatus:
        if not self.api_key:
            return ProviderStatus(
                available=False,
                state=ProviderState.UNAVAILABLE,
                message=(
                    "BRAVE_API_KEY fehlt - Schluessel unter "
                    "api-dashboard.search.brave.com anlegen und in .env eintragen."
                ),
            )
        return ProviderStatus(
            available=True,
            state=ProviderState.AVAILABLE,
            message="Schluessel vorhanden (Kontingent wird erst beim Suchen sichtbar).",
        )

    def _build_params(self, query: SearchQuery) -> dict[str, Any]:
        params: dict[str, Any] = {
            "q": query.text,
            "count": min(query.max_results, self._max_results),
        }
        if self.country:
            params["country"] = self.country
        if query.language or self.language:
            params["search_lang"] = query.language or self.language
        return params

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        code = response.status_code
        if code in (401, 403):
            raise AuthError(f"Brave lehnt den Schluessel ab (HTTP {code}).")
        if code == 429:
            retry_after = response.headers.get("Retry-After")
            raise RateLimitError(
                "Brave: Taktgrenze erreicht (kostenloser Tarif ~1 Anfrage/Sekunde).",
                retry_after=float(retry_after) if retry_after else None,
            )
        if code == 402:
            raise QuotaExhaustedError("Brave: Monatsguthaben aufgebraucht.")
        if 500 <= code < 600:
            raise TransientError(f"Brave: Serverfehler (HTTP {code}).")
        if code >= 400:
            raise ProviderUnavailableError(f"Brave: unerwarteter Status {code}.")

    def search(self, query: SearchQuery) -> SearchResponse:
        if not self.api_key:
            raise AuthError("BRAVE_API_KEY fehlt.")

        started = time.monotonic()
        try:
            response = httpx.get(
                self.endpoint,
                params=self._build_params(query),
                headers={
                    "X-Subscription-Token": self.api_key,
                    "Accept": "application/json",
                },
                timeout=self.timeout,
            )
        except httpx.TimeoutException as exc:
            raise TransientError(f"Brave: Zeitueberschreitung nach {self.timeout}s.") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"Brave nicht erreichbar: {exc}") from exc

        self._raise_for_status(response)
        payload = response.json()

        results = (payload.get("web") or {}).get("results") or []
        hits = [
            SearchHit(
                url=item.get("url", ""),
                title=item.get("title", ""),
                snippet=item.get("description"),
                rank=index,
                raw=item,
            )
            for index, item in enumerate(results, start=1)
            if item.get("url")
        ]

        return SearchResponse(
            query=query,
            hits=hits[: query.max_results],
            provider=self.name,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
