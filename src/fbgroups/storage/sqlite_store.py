"""SQLite-Bestand ueber alle Laeufe hinweg.

Bewusst mit der Standardbibliothek umgesetzt - das Schema ist klein und
stabil, ein ORM waere hier zusaetzliches Gewicht ohne Nutzen.

``upsert_groups`` unterscheidet zwischen neuen und bereits bekannten Gruppen.
Genau diese Zahl beantwortet die Frage, ob eine Suchstrategie noch etwas
beitraegt oder nur Bekanntes wiederholt.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fbgroups.marketing.store import SCHEMA as MARKETING_SCHEMA
from fbgroups.marketing.store import SCHEMA_TRACKING as MARKETING_TRACKING_SCHEMA
from fbgroups.models import Group, ImportRun, ScoreBreakdown, ValidationStatus

SCHEMA_VERSION = 5

# Schritte, die eine aeltere Datei nachholt, ohne dass der Bestand neu
# aufgebaut werden muss. Handgepflegte Notizen bleiben so erhalten.
# Ausschliesslich additiv: neue Spalten, neue Tabellen - nie ein Umbau.
_MIGRATIONS: dict[int, tuple[str, ...]] = {
    2: (
        "ALTER TABLE groups ADD COLUMN category_confidence REAL NOT NULL DEFAULT 0",
        "ALTER TABLE groups ADD COLUMN score_max REAL",
    ),
    # Die Marketing-Erweiterung bringt eigene Tabellen mit und fasst 'groups'
    # nicht an. Die Beschreibung steht in marketing/store.py - eine zweite
    # Kopie hier koennte auseinanderlaufen.
    3: (MARKETING_SCHEMA,),
    # Ereignisse, Empfehlungen, Praemien und das Pruefprotokoll.
    4: (MARKETING_TRACKING_SCHEMA,),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    group_id            TEXT PRIMARY KEY,
    url_canonical       TEXT NOT NULL,
    url_variants        TEXT NOT NULL DEFAULT '[]',
    name                TEXT NOT NULL DEFAULT '',
    description_snippet TEXT,
    member_count_hint   INTEGER,
    privacy_hint        TEXT NOT NULL DEFAULT 'unknown',
    language_hint       TEXT,
    audience_tags       TEXT NOT NULL DEFAULT '[]',
    audience_confidence REAL NOT NULL DEFAULT 0,
    city                TEXT,
    bundesland          TEXT,
    city_confidence     REAL NOT NULL DEFAULT 0,
    category            TEXT,
    category_confidence REAL NOT NULL DEFAULT 0,
    -- NULL ist ein gueltiger Wert: nicht bewertbar. Kein DEFAULT 0.
    score               REAL,
    -- Hoechstpunktzahl bei der jeweiligen Datenlage, ebenfalls NULL-faehig.
    score_max           REAL,
    score_reason        TEXT NOT NULL DEFAULT '',
    score_breakdown     TEXT NOT NULL DEFAULT '{}',
    validation_status   TEXT NOT NULL DEFAULT 'valid',
    data_quality        TEXT NOT NULL DEFAULT 'none',
    status              TEXT NOT NULL DEFAULT 'new',
    notes               TEXT NOT NULL DEFAULT '',
    first_seen_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL,
    times_seen          INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_groups_status ON groups(status);

CREATE INDEX IF NOT EXISTS idx_groups_score ON groups(score DESC);
CREATE INDEX IF NOT EXISTS idx_groups_city  ON groups(city);

CREATE TABLE IF NOT EXISTS group_sources (
    group_id      TEXT NOT NULL,
    source_type   TEXT NOT NULL,
    source_ref    TEXT NOT NULL DEFAULT '',
    source_line   INTEGER,
    discovered_at TEXT NOT NULL,
    PRIMARY KEY (group_id, source_type, source_ref, source_line),
    FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS runs (
    run_id           TEXT PRIMARY KEY,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    source_type      TEXT NOT NULL,
    source_files     TEXT NOT NULL DEFAULT '[]',
    rows_total       INTEGER NOT NULL DEFAULT 0,
    rows_valid       INTEGER NOT NULL DEFAULT 0,
    rows_rejected    INTEGER NOT NULL DEFAULT 0,
    groups_new       INTEGER NOT NULL DEFAULT 0,
    groups_duplicate INTEGER NOT NULL DEFAULT 0,
    groups_validated INTEGER NOT NULL DEFAULT 0,
    groups_test_data INTEGER NOT NULL DEFAULT 0,
    groups_invalid   INTEGER NOT NULL DEFAULT 0,
    groups_insufficient_data INTEGER NOT NULL DEFAULT 0,
    groups_scored    INTEGER NOT NULL DEFAULT 0,
    errors           TEXT NOT NULL DEFAULT '[]'
);
"""


class SchemaVersionError(RuntimeError):
    """Die vorhandene Datenbank stammt aus einer aelteren Fassung."""


class SqliteStore:
    """Schmaler Zugriff auf die lokale SQLite-Datenbank."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        existed = self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

        # Version 0 bedeutet: aus einer Fassung vor der Versionierung. Solche
        # Dateien haben ein anderes Spaltenlayout und wuerden beim Schreiben
        # scheitern - deshalb hier ebenfalls ablehnen.
        version = int(self.conn.execute("PRAGMA user_version").fetchone()[0])
        if existed and version != SCHEMA_VERSION:
            version = self._migrate(version)
        if existed and version != SCHEMA_VERSION:
            self.conn.close()
            raise SchemaVersionError(
                f"{self.path} hat Schema-Version {version}, erwartet {SCHEMA_VERSION}. "
                f"Die Datei stammt aus einer aelteren Fassung - bitte loeschen "
                f"und den Import wiederholen."
            )

        self.conn.executescript(SCHEMA)
        # Auch die Tabellen der Marketing-Erweiterung anlegen: Eine frische
        # Datei bekommt sonst ein anderes Layout als eine migrierte, je
        # nachdem, welcher Speicher sie zuerst oeffnet.
        self.conn.executescript(MARKETING_SCHEMA)
        self.conn.executescript(MARKETING_TRACKING_SCHEMA)
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    def _migrate(self, version: int) -> int:
        """Ergaenzt fehlende Spalten Schritt fuer Schritt.

        Nur additive Schritte: Was nicht in ``_MIGRATIONS`` steht, bleibt ein
        Fall fuer die Fehlermeldung. Ein stillschweigender Umbau des Bestands
        waere gefaehrlicher als ein klarer Hinweis.
        """
        while version in _MIGRATIONS:
            for statement in _MIGRATIONS[version]:
                # executescript, weil ein Schritt mehrere Anweisungen umfassen
                # darf (die Marketing-Tabellen sind ein solcher Schritt).
                self.conn.executescript(statement)
            version += 1
            self.conn.execute(f"PRAGMA user_version = {version}")
            self.conn.commit()
        return version

    def __enter__(self) -> SqliteStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    # -- Schreiben ------------------------------------------------------
    def upsert_groups(self, groups: list[Group]) -> tuple[int, int]:
        """Speichert Gruppen. Returns: (neu, bereits bekannt)."""
        new_count = 0
        known_count = 0

        for group in groups:
            existing = self.conn.execute(
                "SELECT group_id, times_seen, notes, validation_status "
                "FROM groups WHERE group_id = ?",
                (group.group_id,),
            ).fetchone()

            if existing is None:
                new_count += 1
                times_seen = group.times_seen
                notes = group.notes
                validation_status = group.validation_status.value
            else:
                known_count += 1
                times_seen = int(existing["times_seen"]) + group.times_seen
                # Manuell gepflegte Notizen niemals durch einen Import verlieren.
                notes = existing["notes"] or group.notes
                # "Nicht erreichbar" hat ein Mensch im Browser festgestellt.
                # Ein Suchtreffer belegt nur, dass die URL einmal indexiert
                # wurde - er darf dieses Urteil nicht zuruecknehmen.
                validation_status = (
                    ValidationStatus.UNREACHABLE.value
                    if existing["validation_status"] == ValidationStatus.UNREACHABLE.value
                    and group.validation_status is not ValidationStatus.UNREACHABLE
                    else group.validation_status.value
                )

            self.conn.execute(
                """
                INSERT INTO groups (
                    group_id, url_canonical, url_variants, name, description_snippet,
                    member_count_hint, privacy_hint, language_hint, audience_tags,
                    audience_confidence, city, bundesland, city_confidence, category,
                    category_confidence, score, score_max, score_reason, score_breakdown,
                    validation_status, data_quality, status, notes,
                    first_seen_at, last_seen_at, times_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(group_id) DO UPDATE SET
                    url_canonical       = excluded.url_canonical,
                    url_variants        = excluded.url_variants,
                    name                = CASE WHEN excluded.name <> '' THEN excluded.name
                                               ELSE groups.name END,
                    description_snippet = COALESCE(excluded.description_snippet,
                                                   groups.description_snippet),
                    member_count_hint   = COALESCE(excluded.member_count_hint,
                                                   groups.member_count_hint),
                    privacy_hint        = excluded.privacy_hint,
                    audience_tags       = excluded.audience_tags,
                    audience_confidence = excluded.audience_confidence,
                    city                = excluded.city,
                    bundesland          = excluded.bundesland,
                    city_confidence     = excluded.city_confidence,
                    category            = excluded.category,
                    category_confidence = excluded.category_confidence,
                    score               = excluded.score,
                    score_max           = excluded.score_max,
                    score_reason        = excluded.score_reason,
                    score_breakdown     = excluded.score_breakdown,
                    validation_status   = excluded.validation_status,
                    data_quality        = excluded.data_quality,
                    status              = excluded.status,
                    notes               = excluded.notes,
                    last_seen_at        = excluded.last_seen_at,
                    times_seen          = excluded.times_seen
                """,
                (
                    group.group_id,
                    group.url_canonical,
                    json.dumps(group.url_variants, ensure_ascii=False),
                    group.name,
                    group.description_snippet,
                    group.member_count_hint,
                    group.privacy_hint.value,
                    group.language_hint,
                    json.dumps(group.audience_tags, ensure_ascii=False),
                    group.audience_confidence,
                    group.city,
                    group.bundesland,
                    group.city_confidence,
                    group.category,
                    group.category_confidence,
                    group.score,
                    group.score_max,
                    group.score_reason,
                    group.score_breakdown.model_dump_json(),
                    validation_status,
                    group.data_quality.value,
                    group.status.value,
                    notes,
                    group.first_seen_at.isoformat(),
                    group.last_seen_at.isoformat(),
                    times_seen,
                ),
            )

            for source in group.sources:
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO group_sources
                        (group_id, source_type, source_ref, source_line, discovered_at)
                    VALUES (?,?,?,?,?)
                    """,
                    (
                        group.group_id,
                        source.source_type.value,
                        source.source_ref,
                        source.source_line,
                        source.discovered_at.isoformat(),
                    ),
                )

        self.conn.commit()
        return new_count, known_count

    def update_scores(self, groups: list[Group]) -> int:
        """Schreibt Klassifikation und Bewertung zurueck - sonst nichts.

        Bewusst nicht ueber ``upsert_groups``: Das waere ein erneuter *Fund*
        und wuerde ``times_seen`` hochzaehlen. Eine Neubewertung ist aber kein
        Fund, sondern dieselbe Gruppe mit geaenderten Regeln. Herkunft,
        Zeitstempel und Notizen bleiben deshalb unberuehrt.
        """
        updated = 0
        for group in groups:
            cursor = self.conn.execute(
                """
                UPDATE groups SET
                    name                = ?,
                    description_snippet = ?,
                    audience_tags       = ?,
                    audience_confidence = ?,
                    city                = ?,
                    bundesland          = ?,
                    city_confidence     = ?,
                    category            = ?,
                    category_confidence = ?,
                    score               = ?,
                    score_max           = ?,
                    score_reason        = ?,
                    score_breakdown     = ?,
                    data_quality        = ?,
                    status              = ?
                WHERE group_id = ?
                """,
                (
                    group.name,
                    group.description_snippet,
                    json.dumps(group.audience_tags, ensure_ascii=False),
                    group.audience_confidence,
                    group.city,
                    group.bundesland,
                    group.city_confidence,
                    group.category,
                    group.category_confidence,
                    group.score,
                    group.score_max,
                    group.score_reason,
                    group.score_breakdown.model_dump_json(),
                    group.data_quality.value,
                    group.status.value,
                    group.group_id,
                ),
            )
            updated += cursor.rowcount
        self.conn.commit()
        return updated

    def source_types(self, group_id: str) -> set[str]:
        """Herkunftsarten eines Datensatzes (``manual_seed``, ``search``)."""
        rows = self.conn.execute(
            "SELECT DISTINCT source_type FROM group_sources WHERE group_id = ?",
            (group_id,),
        ).fetchall()
        return {row["source_type"] for row in rows}

    def save_run(self, run: ImportRun) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO runs (
                run_id, started_at, finished_at, source_type, source_files,
                rows_total, rows_valid, rows_rejected, groups_new, groups_duplicate,
                groups_validated, groups_test_data, groups_invalid,
                groups_insufficient_data, groups_scored, errors
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run.run_id,
                run.started_at.isoformat(),
                run.finished_at.isoformat() if run.finished_at else None,
                run.source_type.value,
                json.dumps(run.source_files, ensure_ascii=False),
                run.rows_total,
                run.rows_valid,
                run.rows_rejected,
                run.groups_new,
                run.groups_duplicate,
                run.groups_validated,
                run.groups_test_data,
                run.groups_invalid,
                run.groups_insufficient_data,
                run.groups_scored,
                json.dumps(run.errors, ensure_ascii=False),
            ),
        )
        self.conn.commit()

    # -- Lesen ----------------------------------------------------------
    def load_groups(self, order_by_score: bool = True) -> list[Group]:
        # SQLite sortiert NULL bei DESC ans Ende - nicht bewertbare Datensaetze
        # stehen damit hinten, ohne dass ein Ersatzwert noetig waere.
        order = "ORDER BY score DESC, name COLLATE NOCASE" if order_by_score else ""
        rows = self.conn.execute(f"SELECT * FROM groups {order}").fetchall()  # noqa: S608
        return [self._row_to_group(row) for row in rows]

    def count_groups(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0])

    @staticmethod
    def _row_to_group(row: sqlite3.Row) -> Group:
        return Group(
            group_id=row["group_id"],
            url_canonical=row["url_canonical"],
            url_variants=json.loads(row["url_variants"]),
            name=row["name"],
            description_snippet=row["description_snippet"],
            member_count_hint=row["member_count_hint"],
            privacy_hint=row["privacy_hint"],
            language_hint=row["language_hint"],
            audience_tags=json.loads(row["audience_tags"]),
            audience_confidence=row["audience_confidence"],
            city=row["city"],
            bundesland=row["bundesland"],
            city_confidence=row["city_confidence"],
            category=row["category"],
            category_confidence=row["category_confidence"],
            score=row["score"],
            score_max=row["score_max"],
            score_reason=row["score_reason"],
            score_breakdown=ScoreBreakdown.model_validate_json(row["score_breakdown"]),
            validation_status=row["validation_status"],
            data_quality=row["data_quality"],
            status=row["status"],
            notes=row["notes"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            times_seen=row["times_seen"],
        )
