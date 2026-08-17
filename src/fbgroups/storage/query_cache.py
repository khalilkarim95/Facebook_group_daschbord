"""Dauerhafter Anfragespeicher in SQLite.

Zweck ist nicht Geschwindigkeit, sondern Sparsamkeit: Das Gratis-Kontingent
ist die knappe Ressource, und dieselbe Anfrage darf hoechstens einmal Guthaben
kosten - auch ueber Programmlaeufe, Tage und Rechnerneustarts hinweg.

Zwei Tabellen mit klar getrennter Aufgabe:

``query_cache``
    Der juengste **erfolgreiche** Lauf je Anfrage. Nur hier wird nachgesehen,
    bevor eine Anfrage abgeschickt wird. Ein Fehlschlag landet bewusst nicht
    hier: sonst waere ein einzelner Netzwerkfehler tagelang bindend.

``query_log``
    Lueckenlose Historie jeder Ausfuehrung - auch der fehlgeschlagenen und der
    aus dem Zwischenspeicher bedienten. Diese Tabelle beantwortet, was wann an
    wen ging, mit welchem Ergebnis. Sie wird nie zur Trefferwiederverwendung
    gelesen.

Die Rohantwort wird im Original abgelegt. Damit lassen sich alte Laeufe nach
einem Providerwechsel neu auswerten, ohne erneut zu suchen.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fbgroups.utils.keys import query_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS query_cache (
    cache_key    TEXT PRIMARY KEY,
    provider     TEXT NOT NULL,
    query_id     TEXT NOT NULL DEFAULT '',
    query_text   TEXT NOT NULL,
    params       TEXT NOT NULL DEFAULT '{}',
    n_results    INTEGER NOT NULL DEFAULT 0,
    payload      TEXT NOT NULL DEFAULT '{}',
    raw_response TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_query_cache_provider ON query_cache(provider);

CREATE TABLE IF NOT EXISTS query_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key     TEXT NOT NULL,
    provider      TEXT NOT NULL,
    query_id      TEXT NOT NULL DEFAULT '',
    query_text    TEXT NOT NULL,
    executed_at   TEXT NOT NULL,
    success       INTEGER NOT NULL,
    from_cache    INTEGER NOT NULL DEFAULT 0,
    n_results     INTEGER NOT NULL DEFAULT 0,
    error_type    TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    duration_ms   INTEGER NOT NULL DEFAULT 0,
    raw_response  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_query_log_key ON query_log(cache_key);
"""


@dataclass(frozen=True)
class CachedQuery:
    """Ein gespeicherter, erfolgreicher Lauf einer Anfrage."""

    cache_key: str
    provider: str
    query_id: str
    query_text: str
    n_results: int
    payload: dict[str, Any]
    raw_response: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    def age_days(self, now: datetime | None = None) -> float:
        reference = now or datetime.now(UTC)
        return (reference - self.updated_at).total_seconds() / 86400


class QueryCache:
    """Anfragespeicher und Anfrageprotokoll in einer SQLite-Datei."""

    def __init__(self, path: Path, ttl_days: int = 0, enabled: bool = True) -> None:
        self.path = Path(path)
        self.ttl_days = max(int(ttl_days), 0)
        self.enabled = enabled

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # -- Lebenszyklus ---------------------------------------------------
    def __enter__(self) -> QueryCache:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    # -- Schluessel -----------------------------------------------------
    @staticmethod
    def key(provider: str, query_text: str, params: dict[str, Any] | None = None) -> str:
        return query_key(provider, query_text, params)

    # -- Lesen ----------------------------------------------------------
    def get(self, provider: str, cache_key: str) -> CachedQuery | None:
        """Liefert den gespeicherten Erfolgslauf - oder None (fehlt/veraltet)."""
        if not self.enabled:
            return None

        row = self.conn.execute(
            "SELECT * FROM query_cache WHERE cache_key = ? AND provider = ?",
            (cache_key, provider),
        ).fetchone()
        if row is None:
            return None

        try:
            entry = self._row_to_entry(row)
        except (json.JSONDecodeError, ValueError, TypeError):
            # Ein beschaedigter Eintrag darf keinen Lauf abbrechen - er gilt
            # als nicht vorhanden und wird beim naechsten Erfolg ueberschrieben.
            return None

        if self.ttl_days and entry.age_days() > self.ttl_days:
            return None
        return entry

    def has(self, provider: str, cache_key: str) -> bool:
        """Beantwortet die Planungsfrage, ob eine Anfrage Guthaben kosten wuerde."""
        return self.get(provider, cache_key) is not None

    def count(self, provider: str | None = None) -> int:
        if provider is None:
            return int(self.conn.execute("SELECT COUNT(*) FROM query_cache").fetchone()[0])
        return int(
            self.conn.execute(
                "SELECT COUNT(*) FROM query_cache WHERE provider = ?", (provider,)
            ).fetchone()[0]
        )

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Juengste Ausfuehrungen - fuer Nachschau und Fehlersuche."""
        rows = self.conn.execute(
            "SELECT * FROM query_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    # -- Schreiben ------------------------------------------------------
    def store_success(
        self,
        *,
        provider: str,
        cache_key: str,
        query_text: str,
        query_id: str = "",
        params: dict[str, Any] | None = None,
        n_results: int = 0,
        payload: dict[str, Any] | None = None,
        raw_response: dict[str, Any] | None = None,
        duration_ms: int = 0,
    ) -> None:
        """Speichert eine erfolgreiche Antwort und protokolliert den Lauf."""
        now = datetime.now(UTC).isoformat()
        payload_json = json.dumps(payload or {}, ensure_ascii=False)
        raw_json = json.dumps(raw_response or {}, ensure_ascii=False)

        if self.enabled:
            self.conn.execute(
                """
                INSERT INTO query_cache (
                    cache_key, provider, query_id, query_text, params,
                    n_results, payload, raw_response, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    query_id     = excluded.query_id,
                    query_text   = excluded.query_text,
                    params       = excluded.params,
                    n_results    = excluded.n_results,
                    payload      = excluded.payload,
                    raw_response = excluded.raw_response,
                    updated_at   = excluded.updated_at
                """,
                (
                    cache_key,
                    provider,
                    query_id,
                    query_text,
                    json.dumps(params or {}, ensure_ascii=False),
                    str(n_results),
                    payload_json,
                    raw_json,
                    now,
                    now,
                ),
            )

        # Das Protokoll wird immer geschrieben, auch bei abgeschaltetem Speicher:
        # es beantwortet, was tatsaechlich an den Dienst ging.
        self._log(
            cache_key=cache_key,
            provider=provider,
            query_id=query_id,
            query_text=query_text,
            success=True,
            from_cache=False,
            n_results=n_results,
            duration_ms=duration_ms,
            raw_response=raw_json,
        )
        self.conn.commit()

    def log_failure(
        self,
        *,
        provider: str,
        cache_key: str,
        query_text: str,
        query_id: str = "",
        error_type: str = "",
        error_message: str = "",
        duration_ms: int = 0,
    ) -> None:
        """Protokolliert einen Fehlschlag - ohne ihn zwischenzuspeichern."""
        self._log(
            cache_key=cache_key,
            provider=provider,
            query_id=query_id,
            query_text=query_text,
            success=False,
            from_cache=False,
            n_results=0,
            error_type=error_type,
            error_message=error_message,
            duration_ms=duration_ms,
        )
        self.conn.commit()

    def log_cache_hit(
        self,
        *,
        provider: str,
        cache_key: str,
        query_text: str,
        query_id: str = "",
        n_results: int = 0,
    ) -> None:
        """Haelt fest, dass eine Anfrage aus dem Speicher bedient wurde."""
        self._log(
            cache_key=cache_key,
            provider=provider,
            query_id=query_id,
            query_text=query_text,
            success=True,
            from_cache=True,
            n_results=n_results,
        )
        self.conn.commit()

    # -- Intern ---------------------------------------------------------
    def _log(
        self,
        *,
        cache_key: str,
        provider: str,
        query_id: str,
        query_text: str,
        success: bool,
        from_cache: bool,
        n_results: int = 0,
        error_type: str = "",
        error_message: str = "",
        duration_ms: int = 0,
        raw_response: str = "",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO query_log (
                cache_key, provider, query_id, query_text, executed_at,
                success, from_cache, n_results, error_type, error_message,
                duration_ms, raw_response
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                cache_key,
                provider,
                query_id,
                query_text,
                datetime.now(UTC).isoformat(),
                int(success),
                int(from_cache),
                n_results,
                error_type,
                error_message,
                duration_ms,
                raw_response,
            ),
        )

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> CachedQuery:
        return CachedQuery(
            cache_key=row["cache_key"],
            provider=row["provider"],
            query_id=row["query_id"],
            query_text=row["query_text"],
            n_results=int(row["n_results"]),
            payload=json.loads(row["payload"] or "{}"),
            raw_response=json.loads(row["raw_response"] or "{}"),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
