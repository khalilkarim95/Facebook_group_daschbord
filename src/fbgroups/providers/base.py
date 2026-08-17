"""Vertrag der Search-Provider-Schicht.

In Phase 1 wird hier bewusst nichts implementiert. Die Typen stehen bereits
fest, damit die uebrige Anwendung von Anfang an gegen diese Abstraktion
entwickelt wird und kein Anbieter zur tragenden Saeule werden kann.

``ProviderCapabilities.state`` und ``sunset_date`` machen den Lebenszyklus
eines Dienstes explizit: Ein abgekuendigter oder fuer Neukunden geschlossener
Provider meldet das selbst und wird vor jedem Lauf sichtbar bemaengelt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class ProviderState(StrEnum):
    AVAILABLE = "available"
    CLOSED_TO_NEW = "closed_to_new"   # nur Bestandskunden
    DEPRECATED = "deprecated"         # laeuft, Enddatum bekannt
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ProviderCapabilities:
    max_results_per_call: int
    supports_pagination: bool
    supports_site_operator: bool
    supports_language_hint: bool
    requires_api_key: bool
    state: ProviderState
    sunset_date: date | None = None
    notes: str = ""


@dataclass(frozen=True)
class ProviderStatus:
    available: bool
    state: ProviderState
    message: str = ""
    quota_remaining: int | None = None


@dataclass(frozen=True)
class SearchQuery:
    text: str
    max_results: int = 10
    language: str | None = None
    region: str | None = None
    site_filter: str | None = None
    query_id: str = ""


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    snippet: str | None = None
    rank: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResponse:
    query: SearchQuery
    hits: list[SearchHit]
    provider: str
    # Zwei verschiedene Aussagen, die nicht verwechselt werden duerfen:
    # was diese eine Anfrage gekostet hat, und was insgesamt noch uebrig ist.
    # Die meisten Dienste melden nur das Erste. Wer nichts weiss, laesst None.
    credits_used: int | None = None
    quota_remaining: int | None = None
    duration_ms: int = 0


# --- Neutrale Fehler: Aufrufer fangen nie anbieterspezifische Exceptions ---

class ProviderError(Exception):
    """Basisklasse aller Providerfehler."""


class AuthError(ProviderError):
    """Zugangsdaten fehlen oder sind ungueltig."""


class RateLimitError(ProviderError):
    def __init__(self, message: str = "", retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class QuotaExhaustedError(ProviderError):
    """Kontingent aufgebraucht - der Lauf ist kontrolliert zu beenden."""


class ProviderUnavailableError(ProviderError):
    """Dienst nicht erreichbar oder nicht mehr nutzbar."""


class TransientError(ProviderError):
    """Voruebergehender Fehler, ein Wiederholungsversuch ist sinnvoll."""


@runtime_checkable
class SearchProvider(Protocol):
    """Schnittstelle, die jeder Suchdienst erfuellen muss."""

    name: str
    capabilities: ProviderCapabilities

    def check_availability(self) -> ProviderStatus:
        """Preflight vor jedem Lauf - ohne Kontingentverbrauch, wenn moeglich."""
        ...

    def search(self, query: SearchQuery) -> SearchResponse:
        """Fuehrt eine Suche aus und liefert provider-neutrale Treffer."""
        ...


# --- Registry: Aufloesung ueber den Namen aus der Konfiguration ---

_REGISTRY: dict[str, type] = {}


def register_provider(name: str):
    """Dekorator zur Anmeldung einer Provider-Implementierung."""

    def decorator(cls: type) -> type:
        _REGISTRY[name] = cls
        return cls

    return decorator


def get_provider_class(name: str) -> type:
    if name not in _REGISTRY:
        raise KeyError(
            f"Unbekannter Search-Provider: {name!r}. Registriert: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]


def available_providers() -> list[str]:
    return sorted(_REGISTRY)
