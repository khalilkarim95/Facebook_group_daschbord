"""Automatischer Suchlauf (Phase 2).

Ablauf je Anfrage: Anfragespeicher pruefen -> ggf. Dienst befragen -> Treffer
auf Facebook-Gruppen-URLs filtern -> Gruppen aufbauen. Danach laeuft alles
durch dieselbe Verarbeitung wie beim manuellen Import: Dedupe, Klassifikation,
Validierung, Scoring.

Sparsamkeit ist eingebaut:

* Jede erfolgreiche Antwort steht dauerhaft in SQLite; dieselbe Anfrage geht
  nie zweimal an den Dienst.
* ``build_plan`` beantwortet **vor** jedem Zugriff, wie viele Anfragen neu
  waeren und was der Lauf hoechstens kostet. ``--dry-run`` zeigt genau diesen
  Plan und ruft nichts ab.
* Die Obergrenze zaehlt ausschliesslich **neue** Anfragen. Eine bereits
  gespeicherte Anfrage verbraucht kein Guthaben und darf deshalb auch kein
  Kontingent des Laufs belegen.
* Ein aufgebrauchtes Kontingent beendet den Lauf geordnet, statt ihn abbrechen
  zu lassen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fbgroups.config import AppConfig
from fbgroups.extract.enrich import drop_shared_snippets, hit_to_group
from fbgroups.models import Group, QueryRecord, QueryStatus, SearchRun
from fbgroups.providers.base import (
    ProviderError,
    QuotaExhaustedError,
    RateLimitError,
    SearchHit,
    SearchProvider,
    SearchQuery,
)
from fbgroups.query.builder import PlannedQuery, build_queries
from fbgroups.storage.query_cache import QueryCache
from fbgroups.utils.rate_limit import RateLimiter

# --------------------------------------------------------------------------
# Anfragetext
# --------------------------------------------------------------------------

def build_query_text(
    text: str,
    provider: SearchProvider,
    site_filter: str | None,
    quote_phrase: bool = True,
) -> str:
    """Setzt den tatsaechlich abgeschickten Anfragetext zusammen.

    Der ``site:``-Operator wird nur angehaengt, wenn der Dienst ihn laut
    ``capabilities`` unterstuetzt. Die Anfuehrungszeichen um den Suchbegriff
    sind kein Beiwerk: ohne sie liefert die Suche auch Treffer, die die Worte
    nur einzeln und verstreut enthalten - und jeder solche Treffer kostet
    Guthaben, ohne etwas beizutragen.
    """
    phrase = f'"{text}"' if quote_phrase and text else text
    if site_filter and provider.capabilities.supports_site_operator:
        return f"site:{site_filter} {phrase}".strip()
    return phrase


# --------------------------------------------------------------------------
# Planung - beantwortet den Verbrauch, bevor irgendetwas abgefragt wird
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class PlannedRequest:
    """Eine geplante Anfrage samt Einschaetzung, ob sie Guthaben kosten wuerde."""

    query: PlannedQuery
    text: str                     # inkl. site:-Operator und Anfuehrungszeichen
    cache_key: str
    cached: bool                  # liegt bereits erfolgreich im Speicher
    will_send: bool               # neu UND innerhalb der Obergrenze
    skip_reason: str = ""

    @property
    def costs_credits(self) -> bool:
        return self.will_send


@dataclass(frozen=True)
class SearchPlan:
    """Was ein Lauf tun wuerde - Grundlage von ``--dry-run`` und Ausfuehrung."""

    provider: str
    requests: list[PlannedRequest] = field(default_factory=list)
    max_results_per_query: int = 10
    limit_effective: int = 0
    credits_per_query: int = 1
    limit_source: str = ""

    @property
    def n_planned(self) -> int:
        return len(self.requests)

    @property
    def n_cached(self) -> int:
        return sum(1 for r in self.requests if r.cached)

    @property
    def n_new(self) -> int:
        """Anfragen, die noch nie erfolgreich beantwortet wurden."""
        return sum(1 for r in self.requests if not r.cached)

    @property
    def n_to_send(self) -> int:
        """Neue Anfragen, die dieser Lauf tatsaechlich abschicken wuerde."""
        return sum(1 for r in self.requests if r.will_send)

    @property
    def n_skipped(self) -> int:
        return sum(1 for r in self.requests if not r.cached and not r.will_send)

    @property
    def estimated_credits(self) -> int:
        """Hoechstverbrauch dieses Laufs. Gespeicherte Anfragen kosten nichts."""
        return self.n_to_send * self.credits_per_query


def _limits(provider: SearchProvider, provider_config: dict[str, Any]) -> dict[str, Any]:
    limits = provider_config.get("limits", {}) or {}
    settings = (provider_config.get("providers", {}) or {}).get(provider.name, {}) or {}
    return {
        "max_queries_per_run": int(limits.get("max_queries_per_run", 50)),
        "max_results_per_query": min(
            int(limits.get("max_results_per_query", 10)),
            provider.capabilities.max_results_per_call,
        ),
        "cache_enabled": bool(limits.get("cache_enabled", True)),
        "cache_ttl_days": int(limits.get("cache_ttl_days", 0)),
        "credits_per_query": int(settings.get("credits_per_query", 1)),
        "min_interval_seconds": float(settings.get("min_interval_seconds", 0.0)),
    }


def open_query_cache(
    config: AppConfig,
    provider_config: dict[str, Any] | None = None,
) -> QueryCache:
    """Oeffnet den dauerhaften Anfragespeicher an der konfigurierten Stelle."""
    limits = (provider_config or {}).get("limits", {}) or {}
    try:
        path = config.path("query_cache_path")
    except KeyError:
        path = config.path("data_dir") / "query_cache.sqlite"
    return QueryCache(
        path=path,
        ttl_days=int(limits.get("cache_ttl_days", 0)),
        enabled=bool(limits.get("cache_enabled", True)),
    )


def build_plan(
    config: AppConfig,
    provider: SearchProvider,
    provider_config: dict[str, Any],
    cache: QueryCache | None = None,
    phase: int = 1,
    limit: int | None = None,
) -> SearchPlan:
    """Stellt den Lauf zusammen, ohne eine einzige Anfrage abzuschicken."""
    limits = _limits(provider, provider_config)
    query_settings = config.queries.get("settings") or {}
    site_filter = query_settings.get("site_filter")
    quote_phrase = bool(query_settings.get("quote_phrase", True))

    max_queries = limits["max_queries_per_run"]
    if limit is None:
        effective_limit, source = max_queries, "max_queries_per_run"
    elif limit <= max_queries:
        effective_limit, source = limit, "--limit"
    else:
        effective_limit, source = max_queries, "max_queries_per_run"

    requests: list[PlannedRequest] = []
    sends_left = effective_limit

    for planned_query in build_queries(config, phase):
        text = build_query_text(planned_query.text, provider, site_filter, quote_phrase)
        cache_key = QueryCache.key(
            provider.name, text, {"n": limits["max_results_per_query"]}
        )
        cached = cache.has(provider.name, cache_key) if cache is not None else False

        will_send = False
        skip_reason = ""
        if cached:
            skip_reason = "bereits im Anfragespeicher - kostet kein Guthaben"
        elif sends_left > 0:
            will_send = True
            sends_left -= 1
        else:
            skip_reason = f"Obergrenze von {effective_limit} neuen Anfragen erreicht"

        requests.append(
            PlannedRequest(
                query=planned_query,
                text=text,
                cache_key=cache_key,
                cached=cached,
                will_send=will_send,
                skip_reason=skip_reason,
            )
        )

    return SearchPlan(
        provider=provider.name,
        requests=requests,
        max_results_per_query=limits["max_results_per_query"],
        limit_effective=effective_limit,
        credits_per_query=limits["credits_per_query"],
        limit_source=source,
    )


# --------------------------------------------------------------------------
# Speicherform der Treffer
# --------------------------------------------------------------------------

def _hits_from_payload(payload: dict[str, Any]) -> list[SearchHit]:
    """Stellt Treffer aus einer gespeicherten Antwort wieder her."""
    return [
        SearchHit(
            url=item.get("url", ""),
            title=item.get("title", ""),
            snippet=item.get("snippet"),
            rank=item.get("rank", index),
            raw=item.get("raw", {}),
        )
        for index, item in enumerate(payload.get("hits", []), start=1)
    ]


def _payload_from_hits(hits: list[SearchHit], provider_name: str, query_text: str) -> dict:
    return {
        "provider": provider_name,
        "query": query_text,
        "hits": [
            {
                "url": hit.url,
                "title": hit.title,
                "snippet": hit.snippet,
                "rank": hit.rank,
                "raw": hit.raw,
            }
            for hit in hits
        ],
    }


def _record_for(planned_query: PlannedQuery, provider_name: str) -> QueryRecord:
    return QueryRecord(
        query_id=planned_query.query_id,
        text=planned_query.text,
        lang=planned_query.lang,
        scope=planned_query.scope,
        template_id=planned_query.template_id,
        city_id=planned_query.city_id,
        audiences=list(planned_query.audiences),
        provider=provider_name,
    )


# --------------------------------------------------------------------------
# Ausfuehrung
# --------------------------------------------------------------------------

def run_search(
    config: AppConfig,
    provider: SearchProvider,
    provider_config: dict[str, Any],
    phase: int = 1,
    limit: int | None = None,
    dry_run: bool = False,
    run_id: str | None = None,
    cache: QueryCache | None = None,
) -> tuple[list[Group], SearchRun]:
    """Fuehrt die geplanten Suchanfragen aus und liefert die gefundenen Gruppen.

    Bei ``dry_run`` wird nichts abgefragt - der Lauf zeigt nur, was er tun
    wuerde. Wird kein ``cache`` uebergeben, oeffnet der Lauf den dauerhaften
    Speicher an der konfigurierten Stelle und schliesst ihn wieder.
    """
    owns_cache = cache is None
    if cache is None:
        cache = open_query_cache(config, provider_config)

    try:
        return _run_search(
            config, provider, provider_config, cache, phase, limit, dry_run, run_id
        )
    finally:
        if owns_cache:
            cache.close()


def _run_search(
    config: AppConfig,
    provider: SearchProvider,
    provider_config: dict[str, Any],
    cache: QueryCache,
    phase: int,
    limit: int | None,
    dry_run: bool,
    run_id: str | None,
) -> tuple[list[Group], SearchRun]:
    limits = _limits(provider, provider_config)
    plan = build_plan(config, provider, provider_config, cache, phase, limit)
    limiter = RateLimiter(limits["min_interval_seconds"])

    run = SearchRun(
        run_id=run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        provider=provider.name,
        dry_run=dry_run,
        queries_planned=plan.n_planned,
    )

    # Treffer werden gesammelt und erst nach dem letzten Lauf ausgewertet -
    # siehe _auswerten.
    ausbeute: list[tuple[QueryRecord, PlannedRequest, list[SearchHit]]] = []

    for request in plan.requests:
        record = _record_for(request.query, provider.name)

        # --- dry-run: nichts abfragen, nur den Plan protokollieren ---
        if dry_run:
            record.status = QueryStatus.SKIPPED
            record.from_cache = request.cached
            record.error_message = (
                "dry-run: im Anfragespeicher"
                if request.cached
                else ("dry-run: neu" if request.will_send else f"dry-run: {request.skip_reason}")
            )
            run.records.append(record)
            continue

        cached = cache.get(provider.name, request.cache_key)

        if cached is not None:
            hits = _hits_from_payload(cached.payload)
            record.status = QueryStatus.CACHED
            record.from_cache = True
            run.queries_cached += 1
            cache.log_cache_hit(
                provider=provider.name,
                cache_key=request.cache_key,
                query_text=request.text,
                query_id=request.query.query_id,
                n_results=len(hits),
            )
        elif not request.will_send:
            record.status = QueryStatus.SKIPPED
            record.error_message = request.skip_reason
            run.records.append(record)
            continue
        else:
            query = SearchQuery(
                text=request.text,
                max_results=plan.max_results_per_query,
                language=request.query.lang if request.query.lang != "translit" else None,
                region=(provider_config.get("providers", {}) or {})
                .get(provider.name, {})
                .get("country"),
                site_filter=(config.queries.get("settings") or {}).get("site_filter"),
                query_id=request.query.query_id,
            )

            limiter.wait()
            try:
                response = provider.search(query)
            except QuotaExhaustedError as exc:
                _fail(cache, run, record, request, exc, QueryStatus.QUOTA_EXHAUSTED)
                run.errors.append(f"Lauf vorzeitig beendet: {exc}")
                break
            except RateLimitError as exc:
                _fail(cache, run, record, request, exc, QueryStatus.RATE_LIMITED)
                continue
            except ProviderError as exc:
                _fail(cache, run, record, request, exc, QueryStatus.ERROR)
                continue

            hits = response.hits
            record.duration_ms = response.duration_ms
            record.status = QueryStatus.OK if hits else QueryStatus.EMPTY
            if response.credits_used is not None:
                run.credits_used += response.credits_used
            if response.quota_remaining is not None:
                run.quota_remaining = response.quota_remaining

            cache.store_success(
                provider=provider.name,
                cache_key=request.cache_key,
                query_text=request.text,
                query_id=request.query.query_id,
                params={"n": plan.max_results_per_query},
                n_results=len(hits),
                payload=_payload_from_hits(hits, provider.name, request.text),
                raw_response={"hits": [hit.raw for hit in hits]},
                duration_ms=response.duration_ms,
            )
            run.queries_ok += 1

        run.queries_executed += 1
        record.n_results = len(hits)
        run.hits_total += len(hits)
        run.records.append(record)
        ausbeute.append((record, request, hits))

    groups = _auswerten(ausbeute, run, provider.name)
    run.finished_at = datetime.now(UTC)
    return groups, run


def _auswerten(
    ausbeute: list[tuple[QueryRecord, PlannedRequest, list[SearchHit]]],
    run: SearchRun,
    provider_name: str,
) -> list[Group]:
    """Wertet die Treffer des gesamten Laufs gemeinsam aus.

    Die Auswertung erfolgt bewusst erst am Ende und ueber alle Anfragen
    hinweg: Ein von Google faelschlich mehrfach vergebener Beschreibungstext
    verteilt sich ueber verschiedene Anfragen, und erst wenn alle vorliegen,
    ist erkennbar, dass er an mehreren Gruppen haengt und damit keine belegt.
    """
    alle_treffer = [hit for _record, _request, hits in ausbeute for hit in hits]
    # Gleiche Reihenfolge und gleiche Anzahl - nur der Beschreibungstext faellt
    # gegebenenfalls weg. Deshalb laesst sich hier positionsweise zuordnen.
    bereinigt = iter(drop_shared_snippets(alle_treffer))

    groups: list[Group] = []
    seen_ids: set[str] = set()

    for record, request, hits in ausbeute:
        for _ in hits:
            group = hit_to_group(next(bereinigt), request.query.query_id, provider_name)
            if group is None:
                continue
            record.n_group_urls += 1
            run.group_urls_found += 1
            if group.group_id not in seen_ids:
                seen_ids.add(group.group_id)
                record.n_groups_new += 1
            groups.append(group)

    return groups


def _fail(
    cache: QueryCache,
    run: SearchRun,
    record: QueryRecord,
    request: PlannedRequest,
    exc: Exception,
    status: QueryStatus,
) -> None:
    """Protokolliert einen Fehlschlag - im Lauf und dauerhaft im Anfragelog.

    Ein Fehlschlag wird bewusst nicht zwischengespeichert: sonst waere ein
    einzelner Netzwerkfehler tagelang bindend.
    """
    record.status = status
    record.error_type = type(exc).__name__
    record.error_message = str(exc)
    run.records.append(record)
    run.queries_failed += 1
    cache.log_failure(
        provider=record.provider,
        cache_key=request.cache_key,
        query_text=request.text,
        query_id=request.query.query_id,
        error_type=type(exc).__name__,
        error_message=str(exc),
    )
