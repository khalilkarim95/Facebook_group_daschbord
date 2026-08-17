"""Provider-Tests.

Der erste Teil ist eine gemeinsame Contract-Suite: Jede Implementierung muss
sie bestehen. Damit verhaelt sich ein spaeter ergaenzter Dienst zwangslaeufig
wie die bereits vorhandenen.

Alle Tests laufen ohne Netzwerk und ohne Schluessel - die HTTP-Aufrufe werden
mit respx abgefangen.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from fbgroups.providers.base import (
    AuthError,
    ProviderCapabilities,
    ProviderState,
    ProviderStatus,
    QuotaExhaustedError,
    RateLimitError,
    SearchProvider,
    SearchQuery,
    SearchResponse,
    TransientError,
    available_providers,
)
from fbgroups.providers.brave import ENDPOINT as BRAVE_ENDPOINT
from fbgroups.providers.brave import BraveProvider
from fbgroups.providers.fixture import FixtureProvider
from fbgroups.providers.serper import ENDPOINT as SERPER_ENDPOINT
from fbgroups.providers.serper import SerperProvider

FIXTURES = Path(__file__).parent / "fixtures" / "search"

BRAVE_OK = {
    "web": {
        "results": [
            {
                "title": "Syrer in Berlin | Facebook",
                "url": "https://www.facebook.com/groups/482910573829104",
                "description": "Öffentliche Gruppe · 12.400 Mitglieder",
            },
            {
                "title": "Irgendein Blog",
                "url": "https://example.com/blog",
                "description": "kein Gruppen-Link",
            },
        ]
    }
}

SERPER_OK = {
    "organic": [
        {
            "title": "Syrer in Berlin | Facebook",
            "link": "https://www.facebook.com/groups/482910573829104",
            "snippet": "Öffentliche Gruppe · 12.400 Mitglieder",
            "position": 1,
        },
        {
            "title": "Irgendein Blog",
            "link": "https://example.com/blog",
            "snippet": "kein Gruppen-Link",
            "position": 2,
        },
    ],
    # Serper meldet hier den Verbrauch DIESER Anfrage, nicht das Restguthaben.
    "credits": 1,
}


# --------------------------------------------------------------------------
# Gemeinsame Contract-Suite
# --------------------------------------------------------------------------

def _make_provider(name: str):
    if name == "fixture":
        return FixtureProvider({"fixtures_dir": FIXTURES}), None
    if name == "brave":
        return BraveProvider({}, api_key="k"), ("GET", BRAVE_ENDPOINT, BRAVE_OK)
    if name == "serper":
        return SerperProvider({}, api_key="k"), ("POST", SERPER_ENDPOINT, SERPER_OK)
    raise AssertionError(name)


ALL_PROVIDERS = ["fixture", "brave", "serper"]


@pytest.mark.parametrize("name", ALL_PROVIDERS)
def test_erfuellt_das_protokoll(name: str) -> None:
    provider, _ = _make_provider(name)
    assert isinstance(provider, SearchProvider)
    assert provider.name == name
    assert isinstance(provider.capabilities, ProviderCapabilities)
    assert isinstance(provider.check_availability(), ProviderStatus)


@pytest.mark.parametrize("name", ALL_PROVIDERS)
def test_liefert_neutrale_antwort(name: str) -> None:
    provider, http = _make_provider(name)
    query = SearchQuery(text="Syrer in Berlin", max_results=10, query_id="cp_02__berlin")

    if http is None:
        response = provider.search(query)
    else:
        method, url, payload = http
        with respx.mock:
            respx.request(method, url).mock(return_value=httpx.Response(200, json=payload))
            response = provider.search(query)

    assert isinstance(response, SearchResponse)
    assert response.provider == name
    assert all(hit.url for hit in response.hits)
    # Die Rohantwort bleibt erhalten - Voraussetzung fuer spaetere Neuauswertung
    assert all(isinstance(hit.raw, dict) for hit in response.hits)


@pytest.mark.parametrize("name", ALL_PROVIDERS)
def test_haelt_max_results_ein(name: str) -> None:
    provider, http = _make_provider(name)
    query = SearchQuery(text="Syrer in Berlin", max_results=1, query_id="cp_02__berlin")

    if http is None:
        response = provider.search(query)
    else:
        method, url, payload = http
        with respx.mock:
            respx.request(method, url).mock(return_value=httpx.Response(200, json=payload))
            response = provider.search(query)

    assert len(response.hits) <= 1


@pytest.mark.parametrize(
    ("name", "status_code", "expected"),
    [
        ("brave", 401, AuthError),
        ("brave", 429, RateLimitError),
        ("brave", 402, QuotaExhaustedError),
        ("brave", 503, TransientError),
        ("serper", 403, AuthError),
        ("serper", 429, RateLimitError),
        ("serper", 402, QuotaExhaustedError),
        ("serper", 500, TransientError),
    ],
)
def test_fehler_werden_neutral_gemappt(name: str, status_code: int, expected: type) -> None:
    """Aufrufer duerfen nie anbieterspezifische Exceptions sehen."""
    provider, http = _make_provider(name)
    method, url, _ = http

    with respx.mock:
        respx.request(method, url).mock(return_value=httpx.Response(status_code, json={}))
        with pytest.raises(expected):
            provider.search(SearchQuery(text="test"))


@pytest.mark.parametrize("name", ["brave", "serper"])
def test_leeres_ergebnis_ist_kein_fehler(name: str) -> None:
    provider, http = _make_provider(name)
    method, url, _ = http

    with respx.mock:
        respx.request(method, url).mock(return_value=httpx.Response(200, json={}))
        response = provider.search(SearchQuery(text="etwas ohne Treffer"))

    assert response.hits == []


@pytest.mark.parametrize("name", ["brave", "serper"])
def test_ohne_schluessel_kein_netzwerkaufruf(name: str) -> None:
    provider = BraveProvider({}) if name == "brave" else SerperProvider({})

    assert provider.check_availability().available is False
    with pytest.raises(AuthError):
        provider.search(SearchQuery(text="test"))


# --------------------------------------------------------------------------
# Anbieterspezifisches
# --------------------------------------------------------------------------

def test_brave_sendet_korrekten_header_und_parameter() -> None:
    provider = BraveProvider({"country": "DE", "language": "de"}, api_key="geheim")

    with respx.mock:
        route = respx.get(BRAVE_ENDPOINT).mock(return_value=httpx.Response(200, json=BRAVE_OK))
        provider.search(SearchQuery(text="Syrer Berlin", max_results=10, language="de"))

    request = route.calls[0].request
    assert request.headers["X-Subscription-Token"] == "geheim"
    assert request.url.params["q"] == "Syrer Berlin"
    assert request.url.params["count"] == "10"
    assert request.url.params["country"] == "DE"


def test_serper_sendet_korrekten_header_und_body() -> None:
    import json

    provider = SerperProvider({"country": "de", "language": "de"}, api_key="geheim")

    with respx.mock:
        route = respx.post(SERPER_ENDPOINT).mock(return_value=httpx.Response(200, json=SERPER_OK))
        response = provider.search(SearchQuery(text="Syrer Berlin", max_results=10))

    request = route.calls[0].request
    assert request.headers["X-API-KEY"] == "geheim"
    body = json.loads(request.content)
    assert body["q"] == "Syrer Berlin"
    assert body["gl"] == "de"
    # Der gemeldete Wert ist der Verbrauch dieser Anfrage. Ihn als Restguthaben
    # zu lesen ergaebe eine grob falsche Anzeige ("Restguthaben 1").
    assert response.credits_used == 1
    assert response.quota_remaining is None


def test_serper_deckelt_num_auf_zehn() -> None:
    """Ab 11 Ergebnissen kostet dieselbe Anfrage zwei Credits."""
    import json

    provider = SerperProvider({"max_results_per_call": 50}, api_key="k")
    assert provider.capabilities.max_results_per_call == 10

    with respx.mock:
        route = respx.post(SERPER_ENDPOINT).mock(return_value=httpx.Response(200, json=SERPER_OK))
        provider.search(SearchQuery(text="test", max_results=50))

    assert json.loads(route.calls[0].request.content)["num"] == 10


def test_fixture_provider_braucht_keinen_schluessel() -> None:
    provider = FixtureProvider({"fixtures_dir": FIXTURES})
    assert provider.capabilities.requires_api_key is False
    assert provider.check_availability().available is True


def test_fixture_unbekannte_anfrage_liefert_leer() -> None:
    provider = FixtureProvider({"fixtures_dir": FIXTURES})
    response = provider.search(SearchQuery(text="voellig unbekannt", query_id="gibt_es_nicht"))
    assert response.hits == []


def test_registry_kennt_alle_provider() -> None:
    assert set(available_providers()) >= {"brave", "fixture", "serper"}


def test_capabilities_melden_lebenszyklus() -> None:
    """Ein abgekuendigter Dienst muss das selbst melden koennen."""
    for name in ALL_PROVIDERS:
        provider, _ = _make_provider(name)
        assert provider.capabilities.state is ProviderState.AVAILABLE
        assert provider.capabilities.sunset_date is None
