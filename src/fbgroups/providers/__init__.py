"""Search-Provider-Schicht.

Phase 1 enthaelt bewusst nur den Vertrag (``base``), keine Implementierung.
Kein Modul ausserhalb dieses Pakets darf anbieterspezifischen Code importieren.
"""

from fbgroups.providers.base import (
    AuthError,
    ProviderCapabilities,
    ProviderError,
    ProviderState,
    ProviderStatus,
    ProviderUnavailableError,
    QuotaExhaustedError,
    RateLimitError,
    SearchHit,
    SearchProvider,
    SearchQuery,
    SearchResponse,
    TransientError,
)

__all__ = [
    "AuthError",
    "ProviderCapabilities",
    "ProviderError",
    "ProviderState",
    "ProviderStatus",
    "ProviderUnavailableError",
    "QuotaExhaustedError",
    "RateLimitError",
    "SearchHit",
    "SearchProvider",
    "SearchQuery",
    "SearchResponse",
    "TransientError",
]
