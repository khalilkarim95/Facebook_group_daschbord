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
from datetime import datetime
from pathlib import Path

from fbgroups.marketing.store import SCHEMA as MARKETING_SCHEMA
from fbgroups.marketing.store import SCHEMA_IDENTITAETEN as MARKETING_IDENTITAETEN_SCHEMA
from fbgroups.marketing.store import SCHEMA_POSTING as MARKETING_POSTING_SCHEMA
from fbgroups.marketing.store import SCHEMA_TRACKING as MARKETING_TRACKING_SCHEMA
from fbgroups.marketing.store import SCHEMA_VORSCHLAEGE as MARKETING_VORSCHLAEGE_SCHEMA
from fbgroups.models import Group, GroupPost, ImportRun, ScoreBreakdown, ValidationStatus

SCHEMA_VERSION = 18


def _iso_oder_none(zeitpunkt: datetime | None) -> str | None:
    """Zeitpunkt als ISO-Text - ``None`` bleibt ``None``.

    Ohne die Unterscheidung stuende in einer Spalte, die "noch nie geprueft"
    bedeutet, der Text "None" - und die Frage "welche Gruppen fehlen noch?"
    haette keine Antwort mehr.
    """
    return zeitpunkt.isoformat() if zeitpunkt is not None else None


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
    # Zeitpunkt der Beitrittsanfrage. Die neuen Zustaende der
    # MarketingStatus-Aufzaehlung brauchen keinen Schritt: Die Spalte ist TEXT
    # und nimmt sie ohne Aenderung auf.
    5: ("ALTER TABLE group_marketing ADD COLUMN join_requested_at TEXT",),
    # Die Auswahlregel einer Kampagne. Bisher war der Zuordnungsfilter
    # dasselbe Feld wie die Beschreibung ('audiences'/'cities'), und angewandt
    # wurde er genau einmal - beim Aufruf von 'campaign add-groups'. Ab hier
    # steht die Regel bei der Kampagne und laesst sich beliebig oft anwenden.
    #
    # Die Uebernahme aus 'audiences'/'cities' ist Absicht: Eine bestehende
    # Kampagne behaelt damit exakt ihren bisherigen Umfang. Wer sie weiten
    # will, tut das ausdruecklich ueber 'campaign target' - eine Migration
    # darf keine Kampagne heimlich vergroessern.
    6: (
        "ALTER TABLE campaigns ADD COLUMN target_audiences TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE campaigns ADD COLUMN target_cities TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE campaigns ADD COLUMN target_categories TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE campaigns ADD COLUMN target_statuses TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE campaigns ADD COLUMN target_min_score REAL",
        "ALTER TABLE campaigns ADD COLUMN target_include_unscored INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE campaigns ADD COLUMN auto_assign INTEGER NOT NULL DEFAULT 0",
        "UPDATE campaigns SET target_audiences = audiences, target_cities = cities",
    ),
    # Eigene Achse fuer die Frage "arbeiten wir an dieser Gruppe ueberhaupt?".
    # Sie steht neben dem Kooperationsweg, nicht darin - siehe GroupMarketing.
    #
    # Der abschliessende INSERT schliesst die Gruppen ohne verwertbare Daten
    # einmalig aus. Es sind ausschliesslich Datensaetze, von denen nichts als
    # eine URL bekannt ist (meist Treffer auf einen Beitrag statt auf ein
    # Gruppenprofil); an ihnen laesst sich nicht arbeiten, und in der
    # Arbeitsliste verdecken sie die brauchbaren. ``DO NOTHING`` schuetzt jede
    # bereits von Hand gepflegte Zeile - eine Migration ueberstimmt kein
    # Menschenurteil. Rueckgaengig mit einem Klick je Gruppe.
    7: (
        "ALTER TABLE group_marketing ADD COLUMN bearbeiten INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE group_marketing ADD COLUMN ausschlussgrund TEXT NOT NULL DEFAULT ''",
        """
        INSERT INTO group_marketing (group_id, bearbeiten, ausschlussgrund, updated_at)
        SELECT group_id, 0, 'keine Daten',
               strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')
        FROM groups WHERE status = 'insufficient_data'
        ON CONFLICT(group_id) DO NOTHING
        """,
    ),
    # Protokoll des Beitrags am Paar aus Kampagne und Gruppe.
    #
    # Bewusst KEINE Uebernahme aus group_marketing.last_posted_at: Das Feld
    # gilt fuer die Gruppe, der neue Stand fuer das Paar. Eine Gruppe in zwei
    # Kampagnen bekaeme sonst beide Beitraege als erledigt gemeldet, obwohl
    # nur einer geschrieben wurde - und die Arbeitsliste verschwiege eine
    # offene Aufgabe. Bestehende Zuordnungen starten deshalb auf 'offen'.
    # Das ist die vorsichtige Richtung: Eine Gruppe zu viel in der Liste
    # kostet einen Blick, eine zu wenig kostet einen Beitrag.
    #
    # Nichts an den Tracking-Daten wird angefasst - weder Codes noch URLs
    # noch Ereignisse. Die Spalten kommen additiv dazu.
    8: (
        "ALTER TABLE campaign_groups ADD COLUMN post_status TEXT NOT NULL DEFAULT 'offen'",
        "ALTER TABLE campaign_groups ADD COLUMN posted_at TEXT",
        "ALTER TABLE campaign_groups ADD COLUMN last_attempt_at TEXT",
        "ALTER TABLE campaign_groups ADD COLUMN post_attempts INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE campaign_groups ADD COLUMN post_error TEXT NOT NULL DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS idx_campaign_groups_post "
        "ON campaign_groups(campaign_id, post_status)",
    ),
    # Der Uebergang vom anonymen Besucher zum angemeldeten Benutzer.
    #
    # Rein additiv: eine neue Tabelle, kein Feld an den Ereignissen, keine
    # Zeile veraendert. Bestehende Ereignisse behalten ihre Kennung und ihren
    # Tracking-Code; die Tabelle wirkt erst auf Meldungen, die *beide*
    # Kennungen mitbringen. Ein Bestand, der bis hierher gelaufen ist,
    # rechnet danach exakt dieselben Zahlen aus wie vorher.
    9: (MARKETING_IDENTITAETEN_SCHEMA,),
    # Die Beitrags-Warteschlange: der Text selbst, sein Stand in der
    # Vorbereitung, die Entwuerfe und das Versuchsprotokoll.
    #
    # Rein additiv. ``post_status`` wird NICHT angefasst: Jede bestehende
    # Zuordnung behaelt ihren Ergebnisstand, und ``campaign queue``,
    # ``retry``, ``post_counts`` und die Uebersicht lesen unveraendert
    # weiter. Der neue ``job_status`` wird aus dem vorhandenen
    # ``post_status`` einmalig abgeleitet - eine veroeffentlichte Zuordnung
    # startet als 'published', nicht als 'draft'. Andersherum entstuende der
    # Eindruck, 210 fertige Beitraege stuenden noch aus.
    10: (
        "ALTER TABLE campaign_groups ADD COLUMN job_status TEXT NOT NULL DEFAULT 'draft'",
        "ALTER TABLE campaign_groups ADD COLUMN post_text TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE campaign_groups ADD COLUMN text_quelle TEXT NOT NULL DEFAULT 'vorlage'",
        "ALTER TABLE campaign_groups ADD COLUMN generiert_am TEXT",
        "ALTER TABLE campaign_groups ADD COLUMN freigegeben_am TEXT",
        "ALTER TABLE campaign_groups ADD COLUMN freigegeben_von TEXT NOT NULL DEFAULT ''",
        """
        UPDATE campaign_groups SET job_status = CASE post_status
            WHEN 'veroeffentlicht'  THEN 'published'
            WHEN 'fehlgeschlagen'   THEN 'failed'
            WHEN 'uebersprungen'    THEN 'cancelled'
            ELSE 'draft' END
        """,
        MARKETING_POSTING_SCHEMA,
        "CREATE INDEX IF NOT EXISTS idx_campaign_groups_job "
        "ON campaign_groups(campaign_id, job_status)",
    ),
    # Wohin ein Klick fuehrt: die eigene Seite oder der Play Store.
    #
    # Die Spalte startet **leer** und nicht auf 'landing'. Leer heisst "die
    # Vorgabe aus der Konfiguration": Eine bestehende Kampagne folgt damit dem
    # neuen Standard, ohne dass jemand 400 Zeilen von Hand umstellt - und wer
    # eine Kampagne ausdruecklich auf der Landingpage halten will, traegt das
    # ein. Andersherum waere die Konfigurationsvorgabe fuer den ganzen Bestand
    # wirkungslos, und das faellt erst auf, wenn keine Installation mehr
    # zugeordnet wird.
    11: ("ALTER TABLE campaigns ADD COLUMN ziel TEXT NOT NULL DEFAULT ''",),
    # Der erzeugte Text und die Vorlage, aus der er stammt - neben dem Text,
    # der wirklich hinausgeht. Bis hierher gab es nur ``post_text``, und damit
    # war nach einer Ueberarbeitung nicht mehr feststellbar, was die Vorlage
    # eigentlich ergeben hatte; ein Weg zurueck bestand nicht.
    #
    # Rein additiv. Bestehende Zuordnungen bekommen einen leeren
    # ``generated_text`` und KEINE Abschrift aus ``post_text``: Was dort steht,
    # ist moeglicherweise von Hand geschrieben oder von einem Modell, und es
    # als "erzeugt" auszugeben waere eine Behauptung ueber seine Herkunft.
    # Beim naechsten Fuellen entsteht der Wert ohnehin - und dann stimmt er.
    12: (
        "ALTER TABLE campaign_groups ADD COLUMN generated_text TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE campaign_groups ADD COLUMN vorlage_key TEXT NOT NULL DEFAULT ''",
    ),
    # Der Kommentar als zweiter Einsatzzweck neben dem Beitrag. Ein Post und
    # ein Kommentar haben verschiedene sprachliche Anforderungen und deshalb
    # getrennte Vorlagen, getrennte Texte und getrennte Prompts.
    #
    # Rein additiv, und die Vorgabe ist ueberall die leere: Eine bestehende
    # Zuordnung bekommt KEINEN Kommentartext und keine Abschrift des
    # Beitrags. Ein Beitrag, als Kommentar unter einen fremden Beitrag
    # gesetzt, ist genau das, was hier vermieden werden soll - ihn dorthin zu
    # kopieren waere eine Behauptung, er tauge dafuer.
    #
    # ``campaigns.kommentare`` startete auf 0: Eine laufende Kampagne sollte
    # ihre Arbeitsweise nicht dadurch aendern, dass jemand eine neue Fassung
    # ausrollt. Seit dem 28.08.2026 entstehen Kommentare fuer jede Kampagne,
    # und die Spalte wird nicht mehr gelesen. Der Schritt bleibt unveraendert
    # stehen - eine Datei, die ihn schon ausgefuehrt hat, fuehrt ihn nie
    # wieder aus, und ein nachtraeglich geaenderter Schritt wirkte deshalb
    # nur auf Dateien, die noch gar nicht so weit sind.
    13: (
        "ALTER TABLE campaign_groups ADD COLUMN kommentar_text TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE campaign_groups ADD COLUMN kommentar_generated TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE campaign_groups ADD COLUMN kommentar_vorlage_key TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE campaign_groups ADD COLUMN kommentar_quelle TEXT NOT NULL DEFAULT 'vorlage'",
        "ALTER TABLE campaign_groups ADD COLUMN kommentar_generiert_am TEXT",
        "ALTER TABLE campaigns ADD COLUMN kommentare INTEGER NOT NULL DEFAULT 0",
    ),
    # Mehrere Fassungen je Gruppe und Zweck statt genau einer.
    #
    # Bis hierher trug das Paar aus Kampagne und Gruppe einen Beitragstext und
    # einen Kommentartext. Wer eine andere Fassung wollte, musste die
    # vorhandene ueberschreiben - die verworfene war weg, und "welche der
    # fuenf?" war gar keine Frage, die sich stellen liess.
    #
    # Rein additiv: eine neue Tabelle, keine Spalte an ``campaign_groups``
    # geaendert, keine geloescht. Die vorhandenen Texte werden als **Fassung 1**
    # uebernommen, nicht kopiert-und-vergessen: Ein Bestand, der bis hierher
    # gelaufen ist, findet nach dem Schritt genau seine Texte an Platz 1
    # wieder - mit ihrer Vorlage, ihrer Quelle und ihrem Zeitpunkt. Ohne die
    # Uebernahme stuenden 310 von Hand ueberarbeitete Texte in der alten
    # Spalte und die Arbeitsseite zeigte fuenf leere Felder daneben.
    #
    # Der Stand der uebernommenen Fassung wird aus ``post_status``
    # abgeleitet - dieselbe Richtung wie in Schritt 10: Was veroeffentlicht
    # ist, war veroeffentlicht; was schiefging, ging schief; alles Uebrige
    # gilt als gespeichert, denn ein Text steht ja da. Ein Kommentar hat
    # keinen eigenen Ergebnisstand und startet deshalb als 'gespeichert'.
    #
    # Die Fassungen 2 bis 5 entstehen NICHT hier, sondern beim naechsten
    # Fuellen aus ``config/textvorlagen.yaml``. Eine Migration, die Texte
    # erfindet, waere eine Migration, die Inhalte schreibt.
    14: (
        MARKETING_VORSCHLAEGE_SCHEMA,
        """
        INSERT INTO campaign_group_texte (
            campaign_id, group_id, texttyp, nummer, text, generated_text,
            vorlage_key, quelle, status, generiert_am, veroeffentlicht_am,
            versuche, fehler)
        SELECT campaign_id, group_id, 'post', 1, post_text, generated_text,
               vorlage_key, text_quelle,
               CASE post_status
                   WHEN 'veroeffentlicht' THEN 'veroeffentlicht'
                   WHEN 'fehlgeschlagen'  THEN 'fehlgeschlagen'
                   ELSE 'gespeichert' END,
               generiert_am,
               CASE WHEN post_status = 'veroeffentlicht' THEN posted_at END,
               post_attempts, post_error
        FROM campaign_groups WHERE TRIM(post_text) <> ''
        ON CONFLICT DO NOTHING
        """,
        """
        INSERT INTO campaign_group_texte (
            campaign_id, group_id, texttyp, nummer, text, generated_text,
            vorlage_key, quelle, status, generiert_am)
        SELECT campaign_id, group_id, 'kommentar', 1, kommentar_text,
               kommentar_generated, kommentar_vorlage_key, kommentar_quelle,
               'gespeichert', kommentar_generiert_am
        FROM campaign_groups WHERE TRIM(kommentar_text) <> ''
        ON CONFLICT DO NOTHING
        """,
    ),
    # Der Score wird auf 100 Punkte umgestellt: Groesse und Betrieb tragen
    # zusammen die Haelfte, statt dass die Mitgliederzahl abgeschaltet ist und
    # die Aktivitaet als drei getrennte Resonanzbloecke daneben steht.
    #
    # Rein additiv - dreizehn Spalten, keine geaendert, keine geloescht. Die
    # vorhandenen Werte werden ausdruecklich NICHT umgerechnet: Ein alter
    # Score aus den alten Gewichten ist keine Zahl, die sich in die neuen
    # uebersetzen laesst, und ein geratener Umrechnungsfaktor stuende
    # hinterher in der Rangliste, nach der entschieden wird, wo die naechsten
    # dreihundert Beitraege hingehen. Neu bewertet wird mit "fbgroups rescore" -
    # das ist ein Befehl und damit eine Entscheidung.
    #
    # ``member_count_source`` bleibt fuer Bestandszahlen leer statt auf
    # 'search' gesetzt zu werden: Die vorhandenen Zahlen stammen zwar aus
    # Suchtreffern, aber das WEISS diese Migration nicht - sie liest eine
    # Spalte, in der auch eine von Hand gepflegte Zahl stehen koennte. Eine
    # Migration, die Herkunft behauptet, ist eine Migration, die Daten erfindet.
    15: (
        "ALTER TABLE groups ADD COLUMN member_count_source TEXT",
        "ALTER TABLE groups ADD COLUMN member_count_checked_at TEXT",
        "ALTER TABLE groups ADD COLUMN posts_per_day REAL",
        "ALTER TABLE groups ADD COLUMN last_post_at TEXT",
        "ALTER TABLE groups ADD COLUMN activity_factor REAL",
        "ALTER TABLE groups ADD COLUMN activity_confidence REAL NOT NULL DEFAULT 0",
        "ALTER TABLE groups ADD COLUMN activity_source TEXT",
        "ALTER TABLE groups ADD COLUMN activity_checked_at TEXT",
        "ALTER TABLE groups ADD COLUMN country TEXT",
        "ALTER TABLE groups ADD COLUMN secondary_categories TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE groups ADD COLUMN data_confidence REAL NOT NULL DEFAULT 0",
        "ALTER TABLE groups ADD COLUMN last_checked_at TEXT",
    ),
    # Beitrags-Metriken (URL, Interaktionen, Kommentare) zum Finden des
    # besten Beitrags fuer einen automatisierten Kommentar.
    # Wichtig: Keine Beitragsinhalte, keine Autorennamen (harte Grenze).
    16: (
        """
        CREATE TABLE IF NOT EXISTS group_posts (
            group_id         TEXT NOT NULL,
            post_url         TEXT NOT NULL,
            interactions     INTEGER NOT NULL DEFAULT 0,
            comments         INTEGER NOT NULL DEFAULT 0,
            published_at     TEXT,
            fetched_at       TEXT NOT NULL,
            PRIMARY KEY (group_id, post_url),
            FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
        )
        """,
    ),
    17: (
        "ALTER TABLE post_versuche ADD COLUMN texttyp TEXT NOT NULL DEFAULT 'post'",
        "ALTER TABLE post_versuche ADD COLUMN nummer INTEGER NOT NULL DEFAULT 1",
    ),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    group_id            TEXT PRIMARY KEY,
    url_canonical       TEXT NOT NULL,
    url_variants        TEXT NOT NULL DEFAULT '[]',
    name                TEXT NOT NULL DEFAULT '',
    description_snippet TEXT,
    -- Der Spaltenname traegt noch das alte "hint"; das Modellfeld heisst
    -- member_count. Umbenannt wird die Spalte nicht: Migrationen sind hier
    -- ausschliesslich additiv, und ein RENAME ist keine additive Aenderung.
    member_count_hint   INTEGER,
    -- Herkunft und Zeitpunkt gehoeren zur Zahl. Dieselbe Zahl bedeutet etwas
    -- anderes, je nachdem ob sie auf der Gruppenseite stand oder aus einem
    -- Suchtreffer stammt; checked_at wird auch bei einem erfolglosen Versuch
    -- gesetzt, sonst liefe der Abruf jedes Mal erneut.
    member_count_source     TEXT,
    member_count_checked_at TEXT,
    -- Aktivitaet. posts_per_day bleibt NULL, solange keine Zahl belegt ist;
    -- activity_factor (0-1) ist die daraus abgeleitete Bewertung und steht
    -- getrennt, weil es Quellen gibt, die einen Betriebseindruck liefern,
    -- ohne eine Beitragszahl zu nennen.
    posts_per_day       REAL,
    last_post_at        TEXT,
    activity_factor     REAL,
    activity_confidence REAL NOT NULL DEFAULT 0,
    activity_source     TEXT,
    activity_checked_at TEXT,
    privacy_hint        TEXT NOT NULL DEFAULT 'unknown',
    language_hint       TEXT,
    audience_tags       TEXT NOT NULL DEFAULT '[]',
    audience_confidence REAL NOT NULL DEFAULT 0,
    city                TEXT,
    bundesland          TEXT,
    country             TEXT,
    city_confidence     REAL NOT NULL DEFAULT 0,
    category            TEXT,
    secondary_categories TEXT NOT NULL DEFAULT '[]',
    category_confidence REAL NOT NULL DEFAULT 0,
    -- NULL ist ein gueltiger Wert: nicht bewertbar. Kein DEFAULT 0.
    score               REAL,
    -- Hoechstpunktzahl bei der jeweiligen Datenlage, ebenfalls NULL-faehig.
    score_max           REAL,
    score_reason        TEXT NOT NULL DEFAULT '',
    score_breakdown     TEXT NOT NULL DEFAULT '{}',
    -- Neben dem Score, nie darin: "wie sicher?" und "wie gut?" sind zwei
    -- Fragen, und sie zu verrechnen machte beide Antworten unlesbar.
    data_confidence     REAL NOT NULL DEFAULT 0,
    last_checked_at     TEXT,
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

CREATE TABLE IF NOT EXISTS group_posts (
    group_id         TEXT NOT NULL,
    post_url         TEXT NOT NULL,
    interactions     INTEGER NOT NULL DEFAULT 0,
    comments         INTEGER NOT NULL DEFAULT 0,
    published_at     TEXT,
    fetched_at       TEXT NOT NULL,
    PRIMARY KEY (group_id, post_url),
    FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
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
        self.conn.executescript(MARKETING_IDENTITAETEN_SCHEMA)
        self.conn.executescript(MARKETING_POSTING_SCHEMA)
        self.conn.executescript(MARKETING_VORSCHLAEGE_SCHEMA)
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
                try:
                    self.conn.executescript(statement)
                except sqlite3.OperationalError as exc:
                    # Die Marketing-Tabellen entstehen selbst in einem
                    # Migrationsschritt, und zwar aus dem AKTUELLEN Schema in
                    # marketing/store.py. Eine Datei, die diesen Schritt gerade
                    # erst nachholt, bekommt sie deshalb bereits mit allen
                    # spaeter ergaenzten Spalten - der zugehoerige
                    # ALTER-Schritt liefe dann ins Leere. Genau dieser Fall
                    # wird uebergangen; jeder andere Fehler bleibt stehen.
                    #
                    # Die Alternative waere, das Schema jedes Schrittes hier
                    # einzufrieren. Das hiesse eine zweite Kopie der
                    # Tabellendefinition zu pflegen, die auseinanderlaufen kann.
                    if "duplicate column name" not in str(exc):
                        raise
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
                    member_count_hint, member_count_source, member_count_checked_at,
                    posts_per_day, last_post_at, activity_factor, activity_confidence,
                    activity_source, activity_checked_at,
                    privacy_hint, language_hint, audience_tags,
                    audience_confidence, city, bundesland, country, city_confidence,
                    category, secondary_categories, category_confidence,
                    score, score_max, score_reason, score_breakdown, data_confidence,
                    last_checked_at, validation_status, data_quality, status, notes,
                    first_seen_at, last_seen_at, times_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                          ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(group_id) DO UPDATE SET
                    url_canonical       = excluded.url_canonical,
                    url_variants        = excluded.url_variants,
                    name                = CASE WHEN excluded.name <> '' THEN excluded.name
                                               ELSE groups.name END,
                    description_snippet = COALESCE(excluded.description_snippet,
                                                   groups.description_snippet),
                    -- Eine erhobene Zahl geht einer fehlenden vor, und die
                    -- Herkunft wandert mit: Eine Zahl ohne Quellenangabe
                    -- liesse sich spaeter nicht mehr einordnen.
                    member_count_hint   = COALESCE(excluded.member_count_hint,
                                                   groups.member_count_hint),
                    member_count_source = COALESCE(excluded.member_count_source,
                                                   groups.member_count_source),
                    member_count_checked_at = COALESCE(excluded.member_count_checked_at,
                                                       groups.member_count_checked_at),
                    -- Dasselbe fuer die Aktivitaet. Ein Suchlauf bringt sie
                    -- nicht mit; ohne COALESCE loeschte er, was
                    -- "fbgroups enrich" erhoben hat.
                    posts_per_day       = COALESCE(excluded.posts_per_day,
                                                   groups.posts_per_day),
                    last_post_at        = COALESCE(excluded.last_post_at,
                                                   groups.last_post_at),
                    activity_factor     = COALESCE(excluded.activity_factor,
                                                   groups.activity_factor),
                    activity_confidence = CASE WHEN excluded.activity_factor IS NOT NULL
                                               THEN excluded.activity_confidence
                                               ELSE groups.activity_confidence END,
                    activity_source     = COALESCE(excluded.activity_source,
                                                   groups.activity_source),
                    activity_checked_at = COALESCE(excluded.activity_checked_at,
                                                   groups.activity_checked_at),
                    privacy_hint        = excluded.privacy_hint,
                    audience_tags       = excluded.audience_tags,
                    audience_confidence = excluded.audience_confidence,
                    city                = excluded.city,
                    bundesland          = excluded.bundesland,
                    country             = excluded.country,
                    city_confidence     = excluded.city_confidence,
                    category            = excluded.category,
                    secondary_categories = excluded.secondary_categories,
                    category_confidence = excluded.category_confidence,
                    score               = excluded.score,
                    score_max           = excluded.score_max,
                    score_reason        = excluded.score_reason,
                    score_breakdown     = excluded.score_breakdown,
                    data_confidence     = excluded.data_confidence,
                    last_checked_at     = COALESCE(excluded.last_checked_at,
                                                   groups.last_checked_at),
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
                    group.member_count,
                    group.member_count_source.value if group.member_count_source else None,
                    _iso_oder_none(group.member_count_checked_at),
                    group.posts_per_day,
                    _iso_oder_none(group.last_post_at),
                    group.activity_factor,
                    group.activity_confidence,
                    group.activity_source.value if group.activity_source else None,
                    _iso_oder_none(group.activity_checked_at),
                    group.privacy_hint.value,
                    group.language_hint,
                    json.dumps(group.audience_tags, ensure_ascii=False),
                    group.audience_confidence,
                    group.city,
                    group.bundesland,
                    group.country,
                    group.city_confidence,
                    group.category,
                    json.dumps(group.secondary_categories, ensure_ascii=False),
                    group.category_confidence,
                    group.score,
                    group.score_max,
                    group.score_reason,
                    group.score_breakdown.model_dump_json(),
                    group.data_confidence,
                    _iso_oder_none(group.last_checked_at),
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

    def upsert_group_posts(self, group_id: str, posts: list[GroupPost]) -> None:
        """Speichert Beitragsmetriken. Keine Inhalte, keine Autoren."""
        for post in posts:
            self.conn.execute(
                """
                INSERT INTO group_posts (
                    group_id, post_url, interactions, comments, published_at, fetched_at
                ) VALUES (?,?,?,?,?,?)
                ON CONFLICT(group_id, post_url) DO UPDATE SET
                    interactions = excluded.interactions,
                    comments     = excluded.comments,
                    published_at = COALESCE(excluded.published_at, group_posts.published_at),
                    fetched_at   = excluded.fetched_at
                """,
                (
                    group_id,
                    post.post_url,
                    post.interactions,
                    post.comments,
                    _iso_oder_none(post.published_at),
                    post.fetched_at.isoformat(),
                ),
            )
        self.conn.commit()

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

    def load_group_posts(self, group_id: str) -> list[GroupPost]:
        """Lädt die Metriken der gespeicherten Beiträge einer Gruppe."""
        from fbgroups.models import GroupPost

        rows = self.conn.execute(
            "SELECT * FROM group_posts WHERE group_id = ? ORDER BY fetched_at DESC", (group_id,)
        ).fetchall()

        return [
            GroupPost(
                group_id=r["group_id"],
                post_url=r["post_url"],
                interactions=r["interactions"],
                comments=r["comments"],
                published_at=datetime.fromisoformat(r["published_at"])
                if r["published_at"]
                else None,
                fetched_at=datetime.fromisoformat(r["fetched_at"]),
            )
            for r in rows
        ]

    def count_groups(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0])

    def count_distinct_sources(self) -> dict[str, int]:
        """Je Gruppe: von wie vielen **verschiedenen** Quellen sie stammt.

        Gemeint sind verschiedene Suchanfragen bzw. Seed-Dateien, nicht Funde.
        ``times_seen`` taugt dafuer nicht: Es zaehlt jeden Fund, und ein
        erneuter Lauf zaehlt dieselbe Gruppe wieder mit - auch wenn er
        vollstaendig aus dem Anfragespeicher kommt und nichts kostet. Eine
        Gruppe aus der ersten Ausbaustufe stuende damit bei 99 Funden aus zwei
        Anfragen, eine gleich gute aus einer neuen Stadt bei 1 aus einer.
        Verglichen wuerde dann das Alter des Datensatzes, nicht die Gruppe.

        Die Obergrenze ist fuer jede Stadt dieselbe (9 Stadtmuster + 7
        bundesweite Anfragen), die Werte sind also untereinander vergleichbar.
        """
        rows = self.conn.execute(
            "SELECT group_id, COUNT(DISTINCT source_ref) AS anzahl "
            "FROM group_sources GROUP BY group_id"
        ).fetchall()
        return {row["group_id"]: int(row["anzahl"]) for row in rows}

    @staticmethod
    def _row_to_group(row: sqlite3.Row) -> Group:
        return Group(
            group_id=row["group_id"],
            url_canonical=row["url_canonical"],
            url_variants=json.loads(row["url_variants"]),
            name=row["name"],
            description_snippet=row["description_snippet"],
            member_count=row["member_count_hint"],
            member_count_source=row["member_count_source"],
            member_count_checked_at=row["member_count_checked_at"],
            posts_per_day=row["posts_per_day"],
            last_post_at=row["last_post_at"],
            activity_factor=row["activity_factor"],
            activity_confidence=row["activity_confidence"],
            activity_source=row["activity_source"],
            activity_checked_at=row["activity_checked_at"],
            privacy_hint=row["privacy_hint"],
            language_hint=row["language_hint"],
            audience_tags=json.loads(row["audience_tags"]),
            audience_confidence=row["audience_confidence"],
            city=row["city"],
            bundesland=row["bundesland"],
            country=row["country"],
            city_confidence=row["city_confidence"],
            category=row["category"],
            secondary_categories=json.loads(row["secondary_categories"] or "[]"),
            category_confidence=row["category_confidence"],
            score=row["score"],
            score_max=row["score_max"],
            score_reason=row["score_reason"],
            score_breakdown=ScoreBreakdown.model_validate_json(row["score_breakdown"]),
            data_confidence=row["data_confidence"],
            last_checked_at=row["last_checked_at"],
            validation_status=row["validation_status"],
            data_quality=row["data_quality"],
            status=row["status"],
            notes=row["notes"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            times_seen=row["times_seen"],
        )
