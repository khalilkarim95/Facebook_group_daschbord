"""Tests der Suchpipeline und der Trefferauswertung."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fbgroups.config import load_config
from fbgroups.extract.enrich import (
    clean_group_name,
    drop_shared_snippets,
    hit_to_group,
    parse_member_count_from_text,
    parse_privacy_hint,
    points_to_post,
)
from fbgroups.models import PrivacyHint, QueryStatus
from fbgroups.pipeline import prepare_groups, process_search_results
from fbgroups.providers.base import SearchHit
from fbgroups.providers.fixture import FixtureProvider
from fbgroups.query.builder import build_queries
from fbgroups.search import build_plan, build_query_text, run_search
from fbgroups.storage.query_cache import QueryCache
from fbgroups.utils.rate_limit import RateLimiter

FIXTURES = Path(__file__).parent / "fixtures" / "search"

# Umfang eines Laufs in Phase 1: 7 bundesweite Anfragen + 9 Muster je
# freigeschalteter Stadt. Aus der Konfiguration abgeleitet statt
# festgeschrieben: Eine neue Stadt aendert den Umfang eines Laufs und soll
# keinen Test brechen - geprueft wird das Verhalten des Plans, nicht der
# gerade eingestellte Ausbaustand.
GEPLANT = len(build_queries(load_config(), phase=1))

PROVIDER_CONFIG = {
    "active": "fixture",
    "providers": {"fixture": {"enabled": True, "fixtures_dir": str(FIXTURES)}},
    "limits": {
        "max_queries_per_run": 50,
        "max_results_per_query": 10,
        "cache_enabled": False,
    },
}

MIT_SPEICHER = {
    **PROVIDER_CONFIG,
    "limits": {**PROVIDER_CONFIG["limits"], "cache_enabled": True},
}

# Harte Obergrenze dieser Testkonfiguration - bewusst unabhaengig von der des
# Projekts. Sobald mehr Staedte freigeschaltet sind als sie zulaesst, fuehrt
# ein Lauf nicht mehr alle geplanten Anfragen aus; genau das soll sie.
OBERGRENZE = PROVIDER_CONFIG["limits"]["max_queries_per_run"]


@pytest.fixture
def provider() -> FixtureProvider:
    return FixtureProvider({"fixtures_dir": FIXTURES})


@pytest.fixture
def cache(tmp_path: Path) -> QueryCache:
    """Eigener Anfragespeicher je Test - nie der des Projekts."""
    with QueryCache(tmp_path / "query_cache.sqlite") as store:
        yield store


# --- Titel- und Metadatenauswertung -----------------------------------

@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Syrer in Berlin | Facebook", "Syrer in Berlin"),
        ("Syrer in Berlin - Facebook", "Syrer in Berlin"),
        ("سوريين في برلين | Facebook", "سوريين في برلين"),
        ("Facebook | Araber in Hamburg", "Araber in Hamburg"),
        ("Gruppe ohne Suffix", "Gruppe ohne Suffix"),
        ("", ""),
    ],
)
def test_titel_wird_bereinigt(title: str, expected: str) -> None:
    assert clean_group_name(title) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Öffentliche Gruppe · 12.400 Mitglieder", 12400),
        ("Public group · 8500 members", 8500),
        ("مجموعة عامة · 15200 عضو", 15200),
        ("Mitglieder: 3.200", 3200),
        ("Gruppe seit 2019 in Berlin", None),      # blosse Zahl ist kein Beleg
        ("", None),
        (None, None),
    ],
)
def test_mitgliederzahl_nur_mit_benennung(text, expected) -> None:
    assert parse_member_count_from_text(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Öffentliche Gruppe · 100 Mitglieder", PrivacyHint.PUBLIC),
        ("Public group", PrivacyHint.PUBLIC),
        ("مجموعة عامة", PrivacyHint.PUBLIC),
        ("Private Gruppe · 50 Mitglieder", PrivacyHint.PRIVATE),
        ("Irgendein Text", PrivacyHint.UNKNOWN),
        (None, PrivacyHint.UNKNOWN),
    ],
)
def test_sichtbarkeit_wird_erkannt(text, expected) -> None:
    assert parse_privacy_hint(text) is expected


def test_nicht_gruppen_treffer_wird_verworfen() -> None:
    hit = SearchHit(url="https://example.com/blog", title="Blog", snippet="kein Gruppen-Link")
    assert hit_to_group(hit, "q1", "fixture") is None

    hit = SearchHit(url="https://www.facebook.com/pages/abc", title="Seite")
    assert hit_to_group(hit, "q1", "fixture") is None


def test_treffer_wird_zu_gruppe() -> None:
    hit = SearchHit(
        url="https://m.facebook.com/groups/482910573829104/?ref=share",
        title="Syrer in Berlin | Facebook",
        snippet="Öffentliche Gruppe · 12.400 Mitglieder",
    )
    group = hit_to_group(hit, "cp_02__berlin", "brave")

    assert group is not None
    assert group.group_id == "482910573829104"
    assert group.url_canonical == "https://www.facebook.com/groups/482910573829104"
    assert group.name == "Syrer in Berlin"
    assert group.member_count_hint == 12400
    assert group.privacy_hint is PrivacyHint.PUBLIC
    assert group.sources[0].source_ref == "brave:cp_02__berlin"


def test_geteilter_beschreibungstext_wird_verworfen() -> None:
    """Google liefert oft einen Text zu mehreren Gruppen - er gehoert keiner.

    Beobachtet im ersten Livelauf: eine Anfrage lieferte zehn Gruppen, fuenf
    davon mit dem Beschreibungstext einer sechsten. Bliebe er stehen, wanderten
    Mitgliederzahl, Sichtbarkeit und Stadt in fremde Datensaetze.
    """
    geteilt = "Öffentliche Gruppe · 12.400 Mitglieder · Syrer in Berlin"
    hits = [
        SearchHit(url="https://www.facebook.com/groups/1", title="Gruppe A", snippet=geteilt),
        SearchHit(url="https://www.facebook.com/groups/2", title="Gruppe B", snippet=geteilt),
        SearchHit(url="https://www.facebook.com/groups/3", title="Gruppe C", snippet="eigen"),
    ]

    bereinigt = drop_shared_snippets(hits)

    assert bereinigt[0].snippet is None
    assert bereinigt[1].snippet is None
    assert bereinigt[2].snippet == "eigen"      # nur einmal vorhanden, bleibt
    # Die Treffer selbst bleiben erhalten - nur die fremde Zuschreibung faellt weg.
    assert [h.url for h in bereinigt] == [h.url for h in hits]


def test_gleicher_text_bei_derselben_gruppe_bleibt() -> None:
    """Dieselbe Gruppe aus mehreren Anfragen bringt zu Recht denselben Text mit.

    Entscheidend ist die Zahl verschiedener URLs, nicht die Zahl der Treffer -
    sonst verloere jede mehrfach gefundene Gruppe ihre echten Metadaten.
    """
    text = "Öffentliche Gruppe · 500 Mitglieder"
    hits = [
        SearchHit(url="https://www.facebook.com/groups/1", title="A", snippet=text),
        SearchHit(url="https://www.facebook.com/groups/1", title="A", snippet=text),
    ]
    assert drop_shared_snippets(hits) == hits


def test_eigener_beschreibungstext_bleibt_unangetastet() -> None:
    hits = [
        SearchHit(url="https://www.facebook.com/groups/1", title="A", snippet="Text A"),
        SearchHit(url="https://www.facebook.com/groups/2", title="B", snippet="Text B"),
        SearchHit(url="https://www.facebook.com/groups/3", title="C", snippet=None),
    ]
    assert drop_shared_snippets(hits) == hits


def test_geteilter_text_verhindert_falsche_mitgliederzahl(config, provider, cache) -> None:
    """Die Wirkung im Lauf: keine fremde Mitgliederzahl im Datensatz."""
    groups, _ = run_search(config, provider, PROVIDER_CONFIG, cache=cache)
    berlin = next(g for g in groups if g.group_id == "482910573829104")
    assert berlin.member_count_hint == 12400   # eigener Text, bleibt erhalten


def test_metadaten_werden_nicht_erfunden() -> None:
    """Ohne Angaben im Treffer bleiben die Felder leer."""
    hit = SearchHit(url="https://www.facebook.com/groups/482910573829104", title="", snippet=None)
    group = hit_to_group(hit, "q1", "fixture")

    assert group.name == ""
    assert group.member_count_hint is None
    assert group.privacy_hint is PrivacyHint.UNKNOWN


def test_beitragstitel_wird_nicht_zum_gruppennamen() -> None:
    """Ein Treffer auf einen Beitrag belegt nur, dass es die Gruppe gibt.

    Beobachtet im Livelauf: "Deutschland geht erst unter seit Mutter Merkel den
    Syrern ..." stand als Gruppenname im Export - es war der Titel eines
    Beitrags innerhalb der Gruppe.
    """
    hit = SearchHit(
        url="https://www.facebook.com/groups/1222823522802063/posts/1626558335761911/",
        title="Deutschland geht erst unter seit Mutter Merkel den Syrern ...",
        snippet="Öffentliche Gruppe · 12.400 Mitglieder",
    )
    group = hit_to_group(hit, "q1", "fixture")

    assert group is not None
    assert group.group_id == "1222823522802063"          # die Gruppe bleibt im Bestand
    assert group.name == ""
    assert group.description_snippet is None
    assert group.member_count_hint is None
    assert group.privacy_hint is PrivacyHint.UNKNOWN


@pytest.mark.parametrize(
    "url",
    [
        "https://www.facebook.com/groups/123456789012/posts/98765432/",
        "https://www.facebook.com/groups/meine.gruppe/permalink/987654/",
        "https://m.facebook.com/groups/123456789012/photos/1/",
    ],
)
def test_beitrags_urls_werden_erkannt(url: str) -> None:
    assert points_to_post(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.facebook.com/groups/123456789012",
        "https://www.facebook.com/groups/123456789012/",
        "https://www.facebook.com/groups/meine.gruppe/about",
        "https://www.facebook.com/groups/meine.gruppe/?ref=share",
    ],
)
def test_gruppen_urls_sind_keine_beitrags_urls(url: str) -> None:
    assert not points_to_post(url)


def test_name_aus_der_gruppen_url_ueberlebt_den_beitragstreffer(config) -> None:
    """Findet ein zweiter Treffer dieselbe Gruppe direkt, greift ihr Name."""
    treffer = [
        SearchHit(
            url="https://www.facebook.com/groups/482910573829104/posts/999/",
            title="Wer kennt einen guten Friseur?",
            snippet=None,
        ),
        SearchHit(
            url="https://www.facebook.com/groups/482910573829104",
            title="Syrer in Berlin | Facebook",
            snippet=None,
        ),
    ]
    groups = [hit_to_group(hit, "q1", "fixture") for hit in treffer]
    verarbeitet, _, _ = prepare_groups([g for g in groups if g], config)

    assert len(verarbeitet) == 1
    assert verarbeitet[0].name == "Syrer in Berlin"


# --- Anfragetext ------------------------------------------------------

def test_anfragetext_mit_site_operator_und_anfuehrungszeichen(provider) -> None:
    text = build_query_text("Syrer Deutschland", provider, "facebook.com/groups/")
    assert text == 'site:facebook.com/groups/ "Syrer Deutschland"'


def test_anfragetext_ohne_site_operator(provider) -> None:
    """Ohne site:-Filter bleibt nur der zitierte Suchbegriff."""
    assert build_query_text("Syrer Deutschland", provider, None) == '"Syrer Deutschland"'


def test_geplante_anfragen_tragen_den_site_operator(config, provider, cache) -> None:
    plan = build_plan(config, provider, PROVIDER_CONFIG, cache)
    assert all(r.text.startswith("site:facebook.com/groups/ ") for r in plan.requests)
    assert 'site:facebook.com/groups/ "Syrer in Berlin"' in {r.text for r in plan.requests}


# --- Planung ----------------------------------------------------------

def test_plan_beziffert_den_verbrauch_vorab(config, provider, cache) -> None:
    plan = build_plan(config, provider, MIT_SPEICHER, cache, limit=5)

    assert plan.n_planned == GEPLANT
    assert plan.n_cached == 0
    assert plan.n_new == GEPLANT
    assert plan.n_to_send == 5
    assert plan.n_skipped == GEPLANT - 5
    assert plan.estimated_credits == 5


def test_plan_zaehlt_gespeicherte_anfragen_nicht_als_verbrauch(
    config, provider, cache
) -> None:
    run_search(config, provider, MIT_SPEICHER, limit=3, cache=cache)
    plan = build_plan(config, provider, MIT_SPEICHER, cache, limit=3)

    assert plan.n_cached == 3
    # Die drei bezahlten Anfragen belegen kein Kontingent mehr - der Lauf
    # kommt zu drei WEITEREN neuen Anfragen.
    assert plan.n_to_send == 3
    assert plan.estimated_credits == 3


# --- Suchlauf ---------------------------------------------------------

def test_dry_run_fragt_nichts_ab(config, provider, cache) -> None:
    groups, run = run_search(config, provider, PROVIDER_CONFIG, dry_run=True, cache=cache)

    assert groups == []
    assert run.queries_executed == 0
    assert run.queries_planned == GEPLANT
    assert all(r.status is QueryStatus.SKIPPED for r in run.records)
    # Kein Eintrag im dauerhaften Protokoll: es ging nichts hinaus.
    assert cache.history() == []
    assert cache.count() == 0


def test_suchlauf_filtert_und_zaehlt(config, provider, cache) -> None:
    groups, run = run_search(config, provider, PROVIDER_CONFIG, cache=cache)

    assert run.queries_planned == GEPLANT
    assert run.queries_executed == min(GEPLANT, OBERGRENZE)
    assert run.hits_total > 0
    # Fremde Domains und Nicht-Gruppen sind herausgefiltert
    assert run.group_urls_found < run.hits_total
    assert 0 < run.precision < 100
    assert all(g.url_canonical.startswith("https://www.facebook.com/groups/") for g in groups)


def test_limit_begrenzt_den_verbrauch(config, provider, cache) -> None:
    _, run = run_search(config, provider, PROVIDER_CONFIG, limit=3, cache=cache)

    assert run.queries_executed == 3
    skipped = [r for r in run.records if r.status is QueryStatus.SKIPPED]
    assert len(skipped) == GEPLANT - 3


def test_limit_zaehlt_nur_neue_anfragen(config, provider, cache) -> None:
    """Eine gespeicherte Anfrage kostet nichts und darf kein Kontingent belegen."""
    run_search(config, provider, MIT_SPEICHER, limit=2, cache=cache)
    _, zweiter = run_search(config, provider, MIT_SPEICHER, limit=2, cache=cache)

    assert zweiter.queries_cached == 2      # die beiden von vorhin, gratis
    assert zweiter.queries_ok == 2          # zwei WEITERE neue Anfragen
    assert zweiter.queries_executed == 4


def test_harte_obergrenze_aus_der_konfiguration(config, provider, cache) -> None:
    strenge_konfiguration = {
        **PROVIDER_CONFIG,
        "limits": {**PROVIDER_CONFIG["limits"], "max_queries_per_run": 5},
    }
    _, run = run_search(config, provider, strenge_konfiguration, cache=cache)
    assert run.queries_executed == 5


def test_obergrenze_gewinnt_gegen_zu_grosses_limit(config, provider, cache) -> None:
    strenge_konfiguration = {
        **PROVIDER_CONFIG,
        "limits": {**PROVIDER_CONFIG["limits"], "max_queries_per_run": 5},
    }
    plan = build_plan(config, provider, strenge_konfiguration, cache, limit=99)
    assert plan.limit_effective == 5
    assert plan.n_to_send == 5


def test_ergebnisse_durchlaufen_dieselbe_verarbeitung(config, provider, cache) -> None:
    """Ein Suchtreffer wird genauso behandelt wie eine manuelle Zeile."""
    groups, run = run_search(config, provider, PROVIDER_CONFIG, cache=cache)
    processed = process_search_results(groups, config, run)

    by_id = {g.group_id: g for g in processed}
    berlin = by_id["482910573829104"]

    assert berlin.city == "Berlin"
    assert "syrians" in berlin.audience_tags
    assert berlin.score is not None
    # Dieselbe Gruppe wurde ueber die deutsche und die arabische Anfrage gefunden
    assert berlin.times_seen > 1
    assert run.groups_unique == len(processed)


def test_dubletten_ueber_anfragen_hinweg(config, provider, cache) -> None:
    groups, run = run_search(config, provider, PROVIDER_CONFIG, cache=cache)
    processed = process_search_results(groups, config, run)

    assert len(processed) < len(groups)   # zusammengefuehrt
    assert len({g.group_id for g in processed}) == len(processed)


# --- Anfragespeicher (SQLite) -----------------------------------------

def test_speicher_haelt_und_liefert(cache) -> None:
    key = QueryCache.key("serper", 'site:facebook.com/groups/ "Syrer Berlin"')

    assert cache.get("serper", key) is None
    cache.store_success(
        provider="serper",
        cache_key=key,
        query_text='site:facebook.com/groups/ "Syrer Berlin"',
        query_id="cp_01__berlin",
        n_results=1,
        payload={"hits": [{"url": "u"}]},
        raw_response={"organic": [{"link": "u"}]},
    )

    eintrag = cache.get("serper", key)
    assert eintrag.payload["hits"][0]["url"] == "u"
    assert eintrag.raw_response["organic"][0]["link"] == "u"
    assert eintrag.n_results == 1
    assert cache.has("serper", key)


def test_speicher_ueberdauert_das_programm(tmp_path: Path) -> None:
    """Der Sinn der Sache: die Ersparnis gilt auch nach einem Neustart."""
    pfad = tmp_path / "query_cache.sqlite"
    key = QueryCache.key("serper", "q")

    with QueryCache(pfad) as erste:
        erste.store_success(
            provider="serper", cache_key=key, query_text="q", payload={"hits": []}
        )

    with QueryCache(pfad) as zweite:
        assert zweite.has("serper", key)


def test_schluessel_ist_stabil() -> None:
    a = QueryCache.key("serper", "Syrer Berlin", {"n": 10})
    b = QueryCache.key("serper", "Syrer Berlin", {"n": 10})
    c = QueryCache.key("serper", "Syrer Hamburg", {"n": 10})
    d = QueryCache.key("brave", "Syrer Berlin", {"n": 10})
    assert a == b
    assert a != c
    assert a != d      # anderer Anbieter, andere Antwort


def test_speicher_kann_abgeschaltet_werden(tmp_path: Path) -> None:
    with QueryCache(tmp_path / "c.sqlite", enabled=False) as store:
        store.store_success(provider="serper", cache_key="k", query_text="q", payload={})
        assert store.get("serper", "k") is None
        # Das Protokoll laeuft weiter - es beantwortet, was tatsaechlich hinausging.
        assert len(store.history()) == 1


def test_fehlschlag_wird_protokolliert_aber_nicht_gespeichert(cache) -> None:
    """Sonst waere ein einzelner Netzwerkfehler tagelang bindend."""
    cache.log_failure(
        provider="serper",
        cache_key="k",
        query_text="q",
        query_id="nw_01",
        error_type="TransientError",
        error_message="Zeitueberschreitung",
    )

    assert cache.get("serper", "k") is None
    assert cache.count() == 0
    eintrag = cache.history()[0]
    assert eintrag["success"] == 0
    assert eintrag["error_type"] == "TransientError"


def test_beschaedigter_eintrag_bricht_nicht_ab(cache) -> None:
    cache.store_success(provider="serper", cache_key="k", query_text="q", payload={})
    cache.conn.execute("UPDATE query_cache SET payload = '{kaputt' WHERE cache_key = 'k'")
    assert cache.get("serper", "k") is None


def test_gueltigkeitsdauer_laeuft_ab(tmp_path: Path) -> None:
    pfad = tmp_path / "c.sqlite"
    with QueryCache(pfad) as store:
        store.store_success(provider="serper", cache_key="k", query_text="q", payload={})
        store.conn.execute(
            "UPDATE query_cache SET updated_at = ? WHERE cache_key = 'k'",
            ((datetime.now(UTC) - timedelta(days=30)).isoformat(),),
        )
        store.conn.commit()

    with QueryCache(pfad, ttl_days=14) as abgelaufen:
        assert abgelaufen.get("serper", "k") is None
    with QueryCache(pfad, ttl_days=0) as unbegrenzt:
        assert unbegrenzt.has("serper", "k")   # 0 = unbegrenzt gueltig


def test_speicher_spart_den_zweiten_aufruf(config, provider, cache) -> None:
    konfiguration = {
        **MIT_SPEICHER,
        "limits": {**MIT_SPEICHER["limits"], "max_queries_per_run": 3},
    }
    run_search(config, provider, konfiguration, limit=3, cache=cache)
    _, zweiter_lauf = run_search(config, provider, konfiguration, limit=0, cache=cache)

    assert zweiter_lauf.queries_cached == 3
    assert zweiter_lauf.queries_ok == 0


def test_jede_ausfuehrung_wird_protokolliert(config, provider, cache) -> None:
    """Anfrage, Zeitpunkt, Provider, Rohantwort, Erfolg und Trefferzahl."""
    run_search(config, provider, MIT_SPEICHER, limit=2, cache=cache)
    eintraege = cache.history()

    assert len(eintraege) == 2
    eintrag = eintraege[0]
    assert eintrag["provider"] == "fixture"
    assert eintrag["query_text"].startswith("site:facebook.com/groups/ ")
    assert eintrag["query_id"]
    assert eintrag["executed_at"]
    assert eintrag["success"] == 1
    assert eintrag["from_cache"] == 0
    assert eintrag["n_results"] >= 0
    assert eintrag["raw_response"]

    # Der zweite Lauf holt dieselben Anfragen aus dem Speicher - erkennbar am Protokoll.
    run_search(config, provider, MIT_SPEICHER, limit=0, cache=cache)
    assert cache.history()[0]["from_cache"] == 1


def test_rate_limiter_haelt_abstand() -> None:
    limiter = RateLimiter(0.05)
    limiter.wait()
    gewartet = limiter.wait()
    assert gewartet > 0


def test_rate_limiter_ohne_abstand() -> None:
    limiter = RateLimiter(0.0)
    limiter.wait()
    assert limiter.wait() == 0.0
