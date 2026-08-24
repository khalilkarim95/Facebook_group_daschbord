"""Speicherung der Marketing-Daten.

Bewusst dieselbe Datei und dasselbe Migrationsverfahren wie der Bestand:
``data/groups.sqlite`` mit ``PRAGMA user_version`` und der additiven Liste in
``storage/sqlite_store.py``. Ein zweites Datenbanksystem daneben waere eine
zweite Wahrheit ueber dieselben Gruppen.

Drei neue Tabellen, keine Aenderung an ``groups``:

``campaigns``        die Kampagnen
``campaign_groups``  Zuordnung Kampagne <-> Gruppe samt Tracking-Code
``group_marketing``  Arbeitsstand je Gruppe

Der Arbeitsstand steht absichtlich **nicht** in ``groups``: Ein Suchlauf
schreibt dort jeden gefundenen Datensatz neu. Von Hand gepflegte Angaben
haetten dort keinen sicheren Platz.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

from fbgroups.marketing.models import (
    POST_STATUS_ZU_JOB,
    Campaign,
    CampaignGroup,
    CampaignStatus,
    EventType,
    GroupMarketing,
    JobStatus,
    PostEntwurf,
    PostStatus,
    PostVersuch,
    QueueZustand,
    Referral,
    ReferralStatus,
    Reward,
    RewardStatus,
    TextQuelle,
    TrackingEvent,
)
from fbgroups.marketing.queue import darf_arbeiten, pruefe_uebergang, zustand_schluessel

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id      TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    -- Beschreibung: wen bewirbt die Kampagne. NICHT der Zuordnungsfilter -
    -- der steht in den target_*-Spalten.
    audiences        TEXT NOT NULL DEFAULT '[]',
    cities           TEXT NOT NULL DEFAULT '[]',
    language         TEXT NOT NULL DEFAULT '',
    message_template TEXT NOT NULL DEFAULT '',
    landing_page     TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'draft',
    starts_on        TEXT,
    ends_on          TEXT,
    -- Auswahlregel: welche Gruppen einen Tracking-Code bekommen.
    -- Leere Liste bzw. NULL heisst: keine Einschraenkung.
    target_audiences        TEXT NOT NULL DEFAULT '[]',
    target_cities           TEXT NOT NULL DEFAULT '[]',
    target_categories       TEXT NOT NULL DEFAULT '[]',
    target_statuses         TEXT NOT NULL DEFAULT '[]',
    target_min_score        REAL,
    target_include_unscored INTEGER NOT NULL DEFAULT 0,
    auto_assign             INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaign_groups (
    campaign_id   TEXT NOT NULL,
    group_id      TEXT NOT NULL,
    -- Eindeutig ueber alle Kampagnen: Ein Code identifiziert genau ein Paar
    -- aus Kampagne und Gruppe, sonst waere die spaetere Zuordnung mehrdeutig.
    tracking_code TEXT NOT NULL UNIQUE,
    tracking_url  TEXT NOT NULL DEFAULT '',
    added_at      TEXT NOT NULL,
    -- Protokoll des Beitrags. Am Paar, nicht an der Gruppe: Dieselbe Gruppe
    -- kann in zwei Kampagnen stehen und traegt dann zwei Beitraege.
    post_status     TEXT NOT NULL DEFAULT 'offen',
    posted_at       TEXT,
    last_attempt_at TEXT,
    post_attempts   INTEGER NOT NULL DEFAULT 0,
    post_error      TEXT NOT NULL DEFAULT '',
    -- Der Beitrag selbst und sein Stand in der Vorbereitung. Frueher entstand
    -- der Text bei jedem Aufruf neu aus der Vorlage der Kampagne; sobald ein
    -- Mensch ihn ueberarbeitet oder Claude ihn je Gruppe verschieden
    -- schreibt, ist das nicht mehr haltbar: Freigegeben wird ein bestimmter
    -- Text, und veroeffentlicht muss genau dieser werden.
    --
    -- Diese Spalten stehen hier UND als Migrationsschritt 10. Beides ist
    -- noetig: Eine frische Datei entsteht aus diesem Schema, eine
    -- vorhandene aus dem Schritt. Fehlten sie hier, bekaeme eine frisch
    -- angelegte Datei die aktuelle Versionsnummer ohne die zugehoerigen
    -- Spalten - und der Migrationsschritt holte sie nie nach, weil die
    -- Nummer ja schon stimmt.
    job_status      TEXT NOT NULL DEFAULT 'draft',
    post_text       TEXT NOT NULL DEFAULT '',
    text_quelle     TEXT NOT NULL DEFAULT 'vorlage',
    generiert_am    TEXT,
    freigegeben_am  TEXT,
    freigegeben_von TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (campaign_id, group_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_campaign_groups_group ON campaign_groups(group_id);

-- Die Arbeitsliste fragt immer nach einer Kampagne und einem Stand.
CREATE INDEX IF NOT EXISTS idx_campaign_groups_post
    ON campaign_groups(campaign_id, post_status);

CREATE TABLE IF NOT EXISTS group_marketing (
    group_id          TEXT PRIMARY KEY,
    marketing_status  TEXT NOT NULL DEFAULT 'not_contacted',
    contact_status    TEXT NOT NULL DEFAULT 'none',
    permission_status TEXT NOT NULL DEFAULT 'unknown',
    campaign_status   TEXT NOT NULL DEFAULT 'none',
    join_requested_at TEXT,
    last_contacted_at TEXT,
    last_posted_at    TEXT,
    bearbeiten        INTEGER NOT NULL DEFAULT 1,
    ausschlussgrund   TEXT NOT NULL DEFAULT '',
    notes             TEXT NOT NULL DEFAULT '',
    updated_at        TEXT NOT NULL,
    FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_group_marketing_status
    ON group_marketing(marketing_status);
"""

# Zweiter Teil: Ereignisse, Empfehlungen, Praemien. Getrennt gehalten, weil er
# spaeter dazukam - die Migrationsliste in storage/sqlite_store.py fuehrt beide
# Schritte einzeln auf, damit eine aeltere Datei genau das Fehlende nachholt.
SCHEMA_TRACKING = """
CREATE TABLE IF NOT EXISTS tracking_events (
    event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    tracking_code TEXT NOT NULL DEFAULT '',
    campaign_id   TEXT NOT NULL DEFAULT '',
    group_id      TEXT NOT NULL DEFAULT '',
    -- Undurchsichtige Kennung aus der Zielanwendung. Nie ein Name, nie eine
    -- Adresse. Ein Klick hat keine - dann bleibt das Feld leer.
    user_ref      TEXT NOT NULL DEFAULT '',
    event_type    TEXT NOT NULL,
    occurred_at   TEXT NOT NULL,
    -- Taeglich wechselnder Pruefwert gegen doppelte Klicks. Nicht
    -- zurueckrechenbar, wird nie ausgegeben, ersetzt das Speichern der
    -- IP-Adresse.
    visitor_hash  TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_events_code  ON tracking_events(tracking_code);
CREATE INDEX IF NOT EXISTS idx_events_type  ON tracking_events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_group ON tracking_events(group_id);
CREATE INDEX IF NOT EXISTS idx_events_user  ON tracking_events(user_ref);

CREATE TABLE IF NOT EXISTS referral_codes (
    referral_code TEXT PRIMARY KEY,
    user_ref      TEXT NOT NULL UNIQUE,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS referrals (
    referral_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    referral_code     TEXT NOT NULL,
    referrer_user_ref TEXT NOT NULL,
    -- Eindeutig: Ein Benutzer kann nur einmal geworben worden sein. Das ist
    -- der Schutz gegen mehrfaches Ausloesen desselben Referrers.
    referred_user_ref TEXT NOT NULL UNIQUE,
    status            TEXT NOT NULL DEFAULT 'pending',
    campaign_id       TEXT NOT NULL DEFAULT '',
    group_id          TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    note              TEXT NOT NULL DEFAULT '',
    CHECK (referrer_user_ref <> referred_user_ref)
);

CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_user_ref);
CREATE INDEX IF NOT EXISTS idx_referrals_status   ON referrals(status);

CREATE TABLE IF NOT EXISTS rewards (
    reward_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_ref    TEXT NOT NULL,
    rule_id     TEXT NOT NULL,
    reward_type TEXT NOT NULL DEFAULT 'custom',
    value       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'earned',
    earned_at   TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    -- Jede Regel wird je Benutzer hoechstens einmal vergeben.
    UNIQUE (user_ref, rule_id)
);

CREATE TABLE IF NOT EXISTS marketing_audit (
    audit_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    action     TEXT NOT NULL,
    subject    TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS marketing_meta (
    schluessel TEXT PRIMARY KEY,
    wert       TEXT NOT NULL
);
"""

# Vierter Teil: die Beitrags-Warteschlange. Kommt spaeter dazu als die
# Zuordnung und steht deshalb als eigener Migrationsschritt in
# storage/sqlite_store.py.
#
# Die Job-Felder selbst liegen NICHT hier, sondern als Spalten an
# ``campaign_groups``: Ein Beitrag gehoert zum Paar aus Kampagne und Gruppe,
# und genau dieses Paar ist diese Tabelle. Eine zweite Tabelle daneben haette
# dieselbe Schluesselkombination und dieselbe Lebensdauer - sie waere eine
# Kopie mit dem Risiko, auseinanderzulaufen.
SCHEMA_POSTING = """
CREATE TABLE IF NOT EXISTS post_entwuerfe (
    entwurf_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL,
    group_id    TEXT NOT NULL,
    -- Laufende Nummer je Paar. Mehrere Fassungen zur Auswahl: Wer nur den
    -- ersten Vorschlag bekommt, nimmt ihn - und alle 310 Beitraege klingen
    -- gleich, was genau das ist, was Facebook als Spam erkennt.
    variante    INTEGER NOT NULL DEFAULT 1,
    text        TEXT NOT NULL DEFAULT '',
    quelle      TEXT NOT NULL DEFAULT 'ki',
    modell      TEXT NOT NULL DEFAULT '',
    erzeugt_am  TEXT NOT NULL,
    gewaehlt    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (campaign_id, group_id, variante)
);

CREATE INDEX IF NOT EXISTS idx_entwuerfe_paar
    ON post_entwuerfe(campaign_id, group_id);

CREATE TABLE IF NOT EXISTS post_versuche (
    versuch_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id    TEXT NOT NULL,
    group_id       TEXT NOT NULL,
    -- Mitgeschrieben statt nachgeschlagen: Der Code kann Jahre spaeter noch
    -- gebraucht werden, um einen veroeffentlichten Beitrag zuzuordnen, und
    -- die Zuordnung koennte bis dahin entfernt worden sein.
    tracking_code  TEXT NOT NULL DEFAULT '',
    job_status     TEXT NOT NULL DEFAULT 'processing',
    erfolg         INTEGER NOT NULL DEFAULT 0,
    post_url       TEXT NOT NULL DEFAULT '',
    fehler         TEXT NOT NULL DEFAULT '',
    -- Nur ein Name wie 'standard'. Nie ein Passwort, nie ein Cookie, nie ein
    -- Token - dieses Modell hat dafuer kein Feld.
    browser_session TEXT NOT NULL DEFAULT '',
    ausgeloest_von TEXT NOT NULL DEFAULT '',
    begonnen_am    TEXT NOT NULL,
    beendet_am     TEXT
);

CREATE INDEX IF NOT EXISTS idx_versuche_paar ON post_versuche(campaign_id, group_id);
CREATE INDEX IF NOT EXISTS idx_versuche_zeit ON post_versuche(begonnen_am);
"""

# Dritter Teil: der Uebergang vom anonymen Besucher zum angemeldeten Benutzer.
#
# Ein Mensch traegt auf dem Weg durch den Trichter nacheinander verschiedene
# Kennungen: erst die des Browsers, den die Web-App sich selbst vergibt
# ("anon-..."), spaeter die Benutzerkennung der Anwendung ("user-8472").
# Ohne diese Tabelle sind das fuer die Auswertung zwei verschiedene Menschen -
# und die Zuordnung zur Facebook-Gruppe, die allein am ersten Besuch haengt,
# geht beim Registrieren verloren. Genau dort ist sie verloren gegangen.
#
# Gespeichert wird nur, dass zwei undurchsichtige Kennungen zusammengehoeren.
# Es kommt kein Feld hinzu, das einen Menschen benennt: ``user_ref`` ist und
# bleibt eine Kennung aus der Zielanwendung.
SCHEMA_IDENTITAETEN = """
CREATE TABLE IF NOT EXISTS user_identities (
    -- Eine der Kennungen, unter denen derselbe Mensch aufgetreten ist.
    user_ref   TEXT PRIMARY KEY,
    -- Die gemeinsame Kennung der Gruppe. Ohne Zeile gilt jede Kennung als
    -- ihre eigene Identitaet - die Tabelle bleibt leer, solange niemand
    -- zweimal auftritt.
    identity   TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_identities ON user_identities(identity);
"""


# Welcher Vorbereitungsstand zu einem eingetragenen Ergebnis gehoert.
#
# Die Gegenrichtung zu POST_STATUS_ZU_JOB, und sie ist nur deshalb eindeutig,
# weil sie ausschliesslich fuer den aelteren Weg gebraucht wird: Dort traegt
# ein Mensch ein Ergebnis ein, und ein Ergebnis hat immer genau einen
# passenden Vorbereitungsstand. ``offen`` heisst "wieder aufgenommen" - der
# Beitrag geht in die Warteschlange zurueck, wo ``campaign retry`` ihn findet.
_JOB_ZU_POST_STATUS: dict[PostStatus, JobStatus] = {
    PostStatus.OFFEN: JobStatus.QUEUED,
    PostStatus.VEROEFFENTLICHT: JobStatus.PUBLISHED,
    PostStatus.FEHLGESCHLAGEN: JobStatus.FAILED,
    PostStatus.UEBERSPRUNGEN: JobStatus.CANCELLED,
}


class UnknownGroupError(KeyError):
    """Die Gruppe steht nicht im Bestand - ohne sie gibt es nichts zu bewerben."""


class UnknownCampaignError(KeyError):
    """Die Kampagne gibt es nicht."""


def _iso(wert: datetime | date | None) -> str | None:
    return wert.isoformat() if wert is not None else None


def _parse_dt(wert: str | None) -> datetime | None:
    """ISO-Zeitstempel aus der Datenbank zurueck in ein ``datetime``.

    Sonst uebernimmt das Pydantic beim Bauen eines Modells; hier wird ein
    einzelner Wert gebraucht, ohne dass ein Modell entsteht. Ein Wert ohne
    Zeitzone gilt als UTC - so wurde er geschrieben, und ein naiver Wert
    liesse sich sonst nicht mit ``datetime.now(UTC)`` vergleichen.
    """
    if not wert:
        return None
    try:
        gelesen = datetime.fromisoformat(wert)
    except ValueError:
        return None
    return gelesen if gelesen.tzinfo else gelesen.replace(tzinfo=UTC)


def _schema_version() -> int:
    """Aktuelle Schema-Version. Import in der Funktion - sonst Ringschluss."""
    from fbgroups.storage.sqlite_store import SCHEMA_VERSION

    return SCHEMA_VERSION


class MarketingStore:
    """Zugriff auf die Marketing-Tabellen derselben SQLite-Datei."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        vorhanden = self.path.exists()

        # Erst migrieren, dann das eigene Schema anlegen: Ein fehlender
        # Migrationsschritt liesse sonst eine Tabelle ohne die neue Spalte
        # zurueck, und "CREATE TABLE IF NOT EXISTS" ergaenzt keine Spalte.
        if vorhanden:
            self._auf_stand_bringen()

        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.executescript(SCHEMA_TRACKING)
        self.conn.executescript(SCHEMA_IDENTITAETEN)
        self.conn.executescript(SCHEMA_POSTING)
        if not vorhanden:
            # Eine hier neu entstandene Datei traegt das aktuelle Schema und
            # muss das auch sagen. Ohne die Versionsnummer hielte der naechste
            # SqliteStore sie fuer eine Datei aus grauer Vorzeit und
            # verweigerte den Dienst.
            self.conn.execute(f"PRAGMA user_version = {_schema_version()}")
        self.conn.commit()

    def _auf_stand_bringen(self) -> None:
        """Holt fehlende Migrationsschritte nach, bevor gelesen wird.

        Notwendig, weil ``GET /r/{code}`` und ``POST /events`` **nur** diesen
        Speicher oeffnen. Auf einem Server, dessen Datenbankdatei aus einer
        aelteren Fassung stammt, fehlte sonst genau die Spalte, die eine
        Erweiterung gerade hinzugefuegt hat - und die Weiterleitung stuerbe an
        einer Stelle, an der niemand nach einer Migration sucht.

        Der Import steht in der Funktion, nicht oben in der Datei:
        ``sqlite_store`` liest von hier das Schema, ein Import auf Modulebene
        waere ein Ringschluss.
        """
        from fbgroups.storage.sqlite_store import SCHEMA_VERSION, SqliteStore

        pruef = sqlite3.connect(self.path)
        try:
            version = int(pruef.execute("PRAGMA user_version").fetchone()[0])
        finally:
            pruef.close()

        if version != SCHEMA_VERSION:
            # SqliteStore fuehrt die Schritte aus und meldet einen Stand, der
            # sich nicht nachholen laesst, als Fehler - genau richtig, das ist
            # nichts, was ein Redirect stillschweigend uebergehen darf.
            SqliteStore(self.path).close()

    def __enter__(self) -> MarketingStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    # -- Kampagnen ------------------------------------------------------
    def save_campaign(self, campaign: Campaign) -> None:
        self.conn.execute(
            """
            INSERT INTO campaigns (
                campaign_id, name, description, audiences, cities, language,
                message_template, landing_page, status, starts_on, ends_on,
                target_audiences, target_cities, target_categories,
                target_statuses, target_min_score, target_include_unscored,
                auto_assign, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(campaign_id) DO UPDATE SET
                name             = excluded.name,
                description      = excluded.description,
                audiences        = excluded.audiences,
                cities           = excluded.cities,
                language         = excluded.language,
                message_template = excluded.message_template,
                landing_page     = excluded.landing_page,
                status           = excluded.status,
                starts_on        = excluded.starts_on,
                ends_on          = excluded.ends_on,
                target_audiences        = excluded.target_audiences,
                target_cities           = excluded.target_cities,
                target_categories       = excluded.target_categories,
                target_statuses         = excluded.target_statuses,
                target_min_score        = excluded.target_min_score,
                target_include_unscored = excluded.target_include_unscored,
                auto_assign             = excluded.auto_assign,
                updated_at       = excluded.updated_at
            """,
            (
                campaign.campaign_id,
                campaign.name,
                campaign.description,
                json.dumps(campaign.audiences, ensure_ascii=False),
                json.dumps(campaign.cities, ensure_ascii=False),
                campaign.language,
                campaign.message_template,
                campaign.landing_page,
                campaign.status.value,
                _iso(campaign.starts_on),
                _iso(campaign.ends_on),
                json.dumps(campaign.target_audiences, ensure_ascii=False),
                json.dumps(campaign.target_cities, ensure_ascii=False),
                json.dumps(campaign.target_categories, ensure_ascii=False),
                json.dumps(campaign.target_statuses, ensure_ascii=False),
                campaign.target_min_score,
                int(campaign.target_include_unscored),
                int(campaign.auto_assign),
                _iso(campaign.created_at),
                _iso(campaign.updated_at),
            ),
        )
        self.conn.commit()

    def load_campaign(self, campaign_id: str) -> Campaign | None:
        row = self.conn.execute(
            "SELECT * FROM campaigns WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()
        return self._row_to_campaign(row) if row else None

    def load_campaigns(self, status: CampaignStatus | None = None) -> list[Campaign]:
        if status is None:
            rows = self.conn.execute("SELECT * FROM campaigns ORDER BY created_at DESC").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM campaigns WHERE status = ? ORDER BY created_at DESC",
                (status.value,),
            ).fetchall()
        return [self._row_to_campaign(row) for row in rows]

    def delete_campaign(self, campaign_id: str) -> int:
        cursor = self.conn.execute("DELETE FROM campaigns WHERE campaign_id = ?", (campaign_id,))
        self.conn.commit()
        return cursor.rowcount

    # -- Zuordnung und Tracking-Codes -----------------------------------
    def assigned_codes(self, campaign_id: str | None = None) -> set[str]:
        """Bereits vergebene Codes - Grundlage fuer den naechsten freien."""
        if campaign_id is None:
            rows = self.conn.execute("SELECT tracking_code FROM campaign_groups").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT tracking_code FROM campaign_groups WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchall()
        return {row["tracking_code"] for row in rows}

    def link_for(self, campaign_id: str, group_id: str) -> CampaignGroup | None:
        row = self.conn.execute(
            "SELECT * FROM campaign_groups WHERE campaign_id = ? AND group_id = ?",
            (campaign_id, group_id),
        ).fetchone()
        return self._row_to_link(row) if row else None

    def add_link(self, link: CampaignGroup) -> bool:
        """Legt die Zuordnung an. Returns: True, wenn sie neu war.

        Eine bestehende Zuordnung wird nicht angefasst - ihr Tracking-Code ist
        moeglicherweise schon veroeffentlicht.
        """
        if self.link_for(link.campaign_id, link.group_id) is not None:
            return False

        if self.conn.execute(
            "SELECT 1 FROM groups WHERE group_id = ?", (link.group_id,)
        ).fetchone() is None:
            raise UnknownGroupError(link.group_id)
        if self.conn.execute(
            "SELECT 1 FROM campaigns WHERE campaign_id = ?", (link.campaign_id,)
        ).fetchone() is None:
            raise UnknownCampaignError(link.campaign_id)

        self.conn.execute(
            """
            INSERT INTO campaign_groups
                (campaign_id, group_id, tracking_code, tracking_url, added_at)
            VALUES (?,?,?,?,?)
            """,
            (
                link.campaign_id,
                link.group_id,
                link.tracking_code,
                link.tracking_url,
                _iso(link.added_at),
            ),
        )
        self.conn.commit()
        return True

    def assigned_group_ids(self, campaign_id: str) -> set[str]:
        """Welche Gruppen dieser Kampagne schon zugeordnet sind.

        Eine Abfrage statt einer je Gruppe: Bei 1000 Gruppen waeren das sonst
        1000 Einzelabfragen, nur um festzustellen, dass fast alle schon da sind.
        """
        return {
            row["group_id"]
            for row in self.conn.execute(
                "SELECT group_id FROM campaign_groups WHERE campaign_id = ?", (campaign_id,)
            )
        }

    def add_links(self, links: list[CampaignGroup]) -> int:
        """Legt viele Zuordnungen in einem Vorgang an. Returns: wie viele neu waren.

        Ein einzelner ``add_link``-Aufruf schreibt und bestaetigt fuer sich; bei
        302 Zuordnungen sind das 302 Schreibvorgaenge auf die Platte. Hier
        entsteht **eine** Transaktion: entweder stehen alle Zuordnungen oder
        keine. Ein Abbruch mittendrin wuerde sonst eine halb zugeordnete
        Kampagne hinterlassen, deren Codes bereits vergeben sind.

        ``INSERT OR IGNORE`` statt einer Vorabpruefung: Eine bereits vorhandene
        Zuordnung bleibt unangetastet - ihr Code kann veroeffentlicht sein.
        """
        if not links:
            return 0

        unbekannt = [
            link.group_id
            for link in links
            if self.conn.execute(
                "SELECT 1 FROM groups WHERE group_id = ?", (link.group_id,)
            ).fetchone()
            is None
        ]
        if unbekannt:
            raise UnknownGroupError(", ".join(sorted(unbekannt)[:5]))

        campaign_ids = {link.campaign_id for link in links}
        for campaign_id in campaign_ids:
            if self.conn.execute(
                "SELECT 1 FROM campaigns WHERE campaign_id = ?", (campaign_id,)
            ).fetchone() is None:
                raise UnknownCampaignError(campaign_id)

        with self.conn:
            cursor = self.conn.executemany(
                """
                INSERT OR IGNORE INTO campaign_groups
                    (campaign_id, group_id, tracking_code, tracking_url, added_at)
                VALUES (?,?,?,?,?)
                """,
                [
                    (
                        link.campaign_id,
                        link.group_id,
                        link.tracking_code,
                        link.tracking_url,
                        _iso(link.added_at),
                    )
                    for link in links
                ],
            )
        return int(cursor.rowcount or 0)

    def campaigns_mit_auto_assign(self) -> list[Campaign]:
        """Kampagnen, die neu gefundene Gruppen von selbst uebernehmen.

        Nur **aktive**. Ein Entwurf ist noch nicht entschieden, und eine
        pausierte oder beendete Kampagne soll gerade nicht weiterwachsen -
        sonst waere "pausiert" eine Beschriftung ohne Wirkung, und ein
        Suchlauf vergaebe Monate spaeter noch Codes fuer eine Kampagne, die
        niemand mehr betreibt. Von Hand bleibt jede Kampagne zuordnbar:
        ``campaign sync`` fragt nicht nach dem Status, denn dort steht ein
        Mensch davor.
        """
        rows = self.conn.execute(
            "SELECT * FROM campaigns WHERE auto_assign = 1 AND status = 'active' "
            "ORDER BY created_at"
        ).fetchall()
        return [self._row_to_campaign(row) for row in rows]

    def refresh_tracking_urls(self, campaign_id: str, basis_url_bauer) -> int:
        """Schreibt die Links neu, ohne die Codes anzufassen.

        Noetig nach einem Wechsel der Basis-URL (localhost -> echte Domain).
        Der Code bleibt, nur sein Vorspann aendert sich.
        """
        geaendert = 0
        for link in self.links_for_campaign(campaign_id):
            neu = basis_url_bauer(link.tracking_code)
            if neu != link.tracking_url:
                self.conn.execute(
                    "UPDATE campaign_groups SET tracking_url = ? "
                    "WHERE campaign_id = ? AND group_id = ?",
                    (neu, link.campaign_id, link.group_id),
                )
                geaendert += 1
        self.conn.commit()
        return geaendert

    def links_for_campaign(self, campaign_id: str) -> list[CampaignGroup]:
        rows = self.conn.execute(
            "SELECT * FROM campaign_groups WHERE campaign_id = ? ORDER BY tracking_code",
            (campaign_id,),
        ).fetchall()
        return [self._row_to_link(row) for row in rows]

    def links_for_group(self, group_id: str) -> list[CampaignGroup]:
        rows = self.conn.execute(
            "SELECT * FROM campaign_groups WHERE group_id = ? ORDER BY tracking_code",
            (group_id,),
        ).fetchall()
        return [self._row_to_link(row) for row in rows]

    # -- Protokoll des Beitrags ------------------------------------------
    def set_post_status(
        self,
        campaign_id: str,
        group_id: str,
        status: PostStatus,
        fehler: str = "",
    ) -> CampaignGroup | None:
        """Haelt fest, was beim Beitrag herauskam. Returns: der neue Stand.

        ``posted_at`` wird nur beim **ersten** Erfolg gesetzt und danach nie
        ueberschrieben: Die Klicks eines Tracking-Codes gehen auf den Beitrag
        zurueck, der zuerst stand. Ein spaeteres erneutes Posten aendert daran
        nichts, und ein Datum, das mitwandert, machte die Frage "seit wann
        laeuft dieser Link?" unbeantwortbar.

        ``post_attempts`` zaehlt jeden Ausgang mit, auch den Erfolg - die Zahl
        beantwortet "wie oft haben wir es angefasst?", nicht "wie oft ist es
        schiefgegangen?". Der Grund des letzten Fehlschlags steht daneben.

        Der Fehlertext wird beim Erfolg geleert. Bliebe er stehen, zeigte die
        Uebersicht neben einem veroeffentlichten Beitrag den Grund, aus dem er
        beim vorletzten Mal nicht ging.

        **Der Vorbereitungsstand wird mitgezogen.** Dieser Weg ist der
        aeltere - ``campaign posted`` und der Knopf in der Uebersicht rufen
        ihn - und er traegt ein Ergebnis ein, ohne die Warteschlange zu
        kennen. Schriebe er nur ``post_status``, stuende danach
        "fehlgeschlagen" neben einem ``job_status`` von "draft", und
        ``campaign retry`` faende den Beitrag nicht mehr: Fuer die eine Liste
        waere er gescheitert, fuer die andere nie begonnen. ``set_job_status``
        macht dasselbe in der Gegenrichtung; zusammen halten die beiden
        Methoden die zwei Achsen deckungsgleich.
        """
        jetzt = datetime.now(UTC)
        job_status = _JOB_ZU_POST_STATUS[status]
        cursor = self.conn.execute(
            """
            UPDATE campaign_groups
               SET post_status     = ?,
                   job_status      = ?,
                   post_error      = ?,
                   last_attempt_at = ?,
                   post_attempts   = post_attempts + 1,
                   posted_at       = CASE
                       WHEN ? = 'veroeffentlicht' AND posted_at IS NULL THEN ?
                       ELSE posted_at
                   END
             WHERE campaign_id = ? AND group_id = ?
            """,
            (
                status.value,
                job_status.value,
                "" if status is PostStatus.VEROEFFENTLICHT else fehler,
                _iso(jetzt),
                status.value,
                _iso(jetzt),
                campaign_id,
                group_id,
            ),
        )
        self.conn.commit()
        if cursor.rowcount == 0:
            return None
        return self.link_for(campaign_id, group_id)

    def offene_links(self, campaign_id: str) -> list[CampaignGroup]:
        """Die noch zu erledigenden Beitraege einer Kampagne.

        Ausgeschlossene Gruppen (``group_marketing.bearbeiten = 0``) bleiben
        draussen: Sie sind bereits als "daran arbeiten wir nicht" beurteilt,
        und in der Arbeitsliste verdeckten sie die brauchbaren. Ihr
        Tracking-Code bleibt davon unberuehrt gueltig.

        ``uebersprungen`` erscheint ebenfalls nicht - es ist ein Urteil ueber
        diese Gruppe, kein offener Posten.
        """
        rows = self.conn.execute(
            """
            SELECT cg.* FROM campaign_groups AS cg
            LEFT JOIN group_marketing AS gm ON gm.group_id = cg.group_id
             WHERE cg.campaign_id = ?
               AND cg.post_status IN ('offen', 'fehlgeschlagen')
               AND COALESCE(gm.bearbeiten, 1) = 1
             ORDER BY cg.tracking_code
            """,
            (campaign_id,),
        ).fetchall()
        return [self._row_to_link(row) for row in rows]

    def post_counts(self, campaign_id: str) -> dict[str, int]:
        """Wie viele Beitraege je Stand - fuer die Fortschrittsanzeige."""
        zaehler = {status.value: 0 for status in PostStatus}
        for row in self.conn.execute(
            "SELECT post_status, COUNT(*) AS n FROM campaign_groups "
            "WHERE campaign_id = ? GROUP BY post_status",
            (campaign_id,),
        ):
            zaehler[row["post_status"]] = row["n"]
        return zaehler

    def fehlgeschlagene_zuruecksetzen(
        self, campaign_id: str, *, max_versuche: int = 0
    ) -> int:
        """Stellt die fehlgeschlagenen Beitraege wieder in die Arbeitsliste.

        Nur ``fehlgeschlagen``. ``uebersprungen`` bleibt stehen: Dort hat ein
        Mensch entschieden, dass diese Gruppe nicht passt - ein Sammelbefehl
        macht diese Entscheidung nicht rueckgaengig. ``post_attempts`` und der
        Fehlertext bleiben erhalten; sie sind das Gedaechtnis darueber, was
        beim letzten Mal schiefging.

        ``max_versuche`` (aus ``marketing.posting.max_versuche``) laesst Jobs
        stehen, die es schon so oft versucht haben. Ohne diese Grenze holte
        jeder Aufruf dieselbe Gruppe zurueck, die aus einem *dauerhaften* Grund
        scheitert - "erlaubt keine Links" wird beim vierten Mal nicht anders
        ausgehen als beim ersten, aber es kostet jedes Mal einen Platz im
        Tageslimit, den eine erreichbare Gruppe gebraucht haette. ``0`` schaltet
        die Grenze ab; die Zahl steht in der Konfiguration und nicht hier.

        **Beide Achsen werden gesetzt.** Ein Job mit ``post_status: offen``
        neben ``job_status: failed`` waere fuer die eine Liste erledigt und
        fuer die andere gescheitert. Wohin er zurueckgeht, haengt am Text:
        Wer einen freigegebenen Text hat, geht in die Warteschlange
        (``queued``); wer keinen hat, faengt beim Entwurf an - er koennte
        sonst nirgends wieder aufgegriffen werden.
        """
        bedingung = "campaign_id = ? AND job_status = 'failed'"
        werte: list[object] = [campaign_id]
        if max_versuche > 0:
            bedingung += " AND post_attempts < ?"
            werte.append(max_versuche)

        cursor = self.conn.execute(
            f"""
            UPDATE campaign_groups
               SET job_status  = CASE WHEN TRIM(post_text) <> '' THEN 'queued' ELSE 'draft' END,
                   post_status = 'offen'
             WHERE {bedingung}
            """,  # noqa: S608 - die Bedingung ist hier gebaut, die Werte sind gebunden
            werte,
        )
        self.conn.commit()
        return cursor.rowcount

    def aufgegeben(self, campaign_id: str, max_versuche: int) -> list[CampaignGroup]:
        """Fehlgeschlagene Jobs, die ihre Versuche aufgebraucht haben.

        Sie verschwinden nicht - sie warten auf einen Menschen. Ein Job, der
        dreimal an "erlaubt keine Links" gescheitert ist, braucht eine
        Entscheidung (anderer Text, Gruppe ausschliessen), keinen vierten
        gleichen Versuch. Ohne diese Liste faenden sie sich nie wieder:
        ``retry`` uebergeht sie, und in der Warteschlange stehen sie nicht.
        """
        if max_versuche <= 0:
            return []
        rows = self.conn.execute(
            "SELECT * FROM campaign_groups WHERE campaign_id = ? AND job_status = 'failed' "
            "AND post_attempts >= ? ORDER BY post_attempts DESC, group_id",
            (campaign_id, max_versuche),
        ).fetchall()
        return [self._row_to_link(row) for row in rows]

    def zaehle_zuruecksetzbar(self, campaign_id: str) -> dict[str, int]:
        """Was ein ``reset`` dieser Kampagne loeschen bzw. zuruecksetzen wuerde.

        Getrennt vom Loeschen, damit ``--dry-run`` und Ernstfall dieselbe Zahl
        nennen - dieselbe Ueberlegung wie bei ``search.build_plan``. Eine
        zweite Zaehlung koennte abweichen, und der Mensch bestaetigte dann eine
        Zahl und bekaeme eine andere.
        """
        def eins(sql: str, *werte: object) -> int:
            row = self.conn.execute(sql, werte).fetchone()
            return int(row[0] or 0)

        return {
            "zuordnungen": eins(
                "SELECT COUNT(*) FROM campaign_groups WHERE campaign_id = ?", campaign_id
            ),
            "veroeffentlicht": eins(
                "SELECT COUNT(*) FROM campaign_groups WHERE campaign_id = ? "
                "AND posted_at IS NOT NULL",
                campaign_id,
            ),
            "versuche": eins(
                "SELECT COUNT(*) FROM post_versuche WHERE campaign_id = ?", campaign_id
            ),
            "ereignisse": eins(
                "SELECT COUNT(*) FROM tracking_events WHERE campaign_id = ?", campaign_id
            ),
            "entwuerfe": eins(
                "SELECT COUNT(*) FROM post_entwuerfe WHERE campaign_id = ?", campaign_id
            ),
        }

    def setze_kampagne_zurueck(
        self, campaign_id: str, *, auch_ereignisse: bool = False
    ) -> dict[str, int]:
        """Setzt den Beitragsstand einer Kampagne auf Anfang. Fuer Testlaeufe.

        **Tracking-Code und Tracking-URL bleiben unangetastet.** Das ist die
        wichtigste Zusage dieser Methode: Ein vergebener Code steht
        moeglicherweise in einem veroeffentlichten Beitrag, und ein Klick
        darauf muss weiterhin ankommen. Zurueckgesetzt wird der *Stand*, nie
        die Zuordnung.

        Ebenso unberuehrt bleiben ``groups`` und ``group_marketing``: Die
        Gruppen und der Kooperationsstand ("wir sind dort Mitglied") sind
        Handarbeit und haben mit einem Testlauf nichts zu tun.

        ``auch_ereignisse`` loescht zusaetzlich die gemessene Resonanz dieser
        Kampagne - Klicks, Registrierungen, Downloads. Das ist die einzige
        Angabe, die sich **nicht** wiederherstellen laesst: Sie ist von aussen
        entstanden und kommt nicht noch einmal. Deshalb ein eigener Schalter
        und nicht Teil des Normalfalls.
        """
        zahlen = self.zaehle_zuruecksetzbar(campaign_id)

        # Der Stand geht auf ``approved``, wenn ein freigegebener Text da ist -
        # sonst auf ``draft``. Dieselbe Regel wie in
        # ``fehlgeschlagene_zuruecksetzen``: Wer keinen Text hat, koennte in
        # der Warteschlange nirgends wieder aufgegriffen werden.
        self.conn.execute(
            """
            UPDATE campaign_groups
               SET job_status      = CASE WHEN TRIM(post_text) <> ''
                                          THEN 'approved' ELSE 'draft' END,
                   post_status     = 'offen',
                   posted_at       = NULL,
                   last_attempt_at = NULL,
                   post_attempts   = 0,
                   post_error      = ''
             WHERE campaign_id = ?
            """,
            (campaign_id,),
        )
        self.conn.execute("DELETE FROM post_versuche WHERE campaign_id = ?", (campaign_id,))
        self.set_queue_zustand(campaign_id, QueueZustand.LAUFEND)

        if auch_ereignisse:
            self.conn.execute(
                "DELETE FROM tracking_events WHERE campaign_id = ?", (campaign_id,)
            )
        else:
            zahlen["ereignisse"] = 0

        self.audit(
            "kampagne_zurueckgesetzt",
            campaign_id,
            f"ereignisse={'ja' if auch_ereignisse else 'nein'}",
        )
        self.conn.commit()
        return zahlen

    def remove_link(self, campaign_id: str, group_id: str) -> int:
        cursor = self.conn.execute(
            "DELETE FROM campaign_groups WHERE campaign_id = ? AND group_id = ?",
            (campaign_id, group_id),
        )
        self.conn.commit()
        return cursor.rowcount

    # -- Arbeitsstand je Gruppe -----------------------------------------
    def save_marketing(self, eintrag: GroupMarketing) -> None:
        self.conn.execute(
            """
            INSERT INTO group_marketing (
                group_id, marketing_status, contact_status, permission_status,
                campaign_status, join_requested_at, last_contacted_at,
                last_posted_at, bearbeiten, ausschlussgrund, notes, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(group_id) DO UPDATE SET
                marketing_status  = excluded.marketing_status,
                contact_status    = excluded.contact_status,
                permission_status = excluded.permission_status,
                campaign_status   = excluded.campaign_status,
                join_requested_at = excluded.join_requested_at,
                last_contacted_at = excluded.last_contacted_at,
                last_posted_at    = excluded.last_posted_at,
                bearbeiten        = excluded.bearbeiten,
                ausschlussgrund   = excluded.ausschlussgrund,
                notes             = excluded.notes,
                updated_at        = excluded.updated_at
            """,
            (
                eintrag.group_id,
                eintrag.marketing_status.value,
                eintrag.contact_status.value,
                eintrag.permission_status.value,
                eintrag.campaign_status.value,
                _iso(eintrag.join_requested_at),
                _iso(eintrag.last_contacted_at),
                _iso(eintrag.last_posted_at),
                int(eintrag.bearbeiten),
                eintrag.ausschlussgrund,
                eintrag.notes,
                _iso(eintrag.updated_at),
            ),
        )
        self.conn.commit()

    def load_marketing(self, group_id: str) -> GroupMarketing:
        """Arbeitsstand einer Gruppe - fehlt er, gilt der Anfangszustand."""
        row = self.conn.execute(
            "SELECT * FROM group_marketing WHERE group_id = ?", (group_id,)
        ).fetchone()
        return self._row_to_marketing(row) if row else GroupMarketing(group_id=group_id)

    def load_all_marketing(self) -> dict[str, GroupMarketing]:
        rows = self.conn.execute("SELECT * FROM group_marketing").fetchall()
        return {row["group_id"]: self._row_to_marketing(row) for row in rows}

    # -- Ereignisse -----------------------------------------------------
    # -- Beitrags-Warteschlange ------------------------------------------
    def set_job_status(
        self,
        campaign_id: str,
        group_id: str,
        neu: JobStatus,
        *,
        akteur: str = "",
        fehler: str | None = None,
        erzwingen: bool = False,
    ) -> CampaignGroup:
        """Setzt den Vorbereitungsstand - und nur ueber die erlaubten Wege.

        Die Pruefung liegt hier und nicht beim Aufrufer: Kommandozeile,
        Uebersicht und Arbeiter setzen denselben Stand, und eine Regel, die an
        drei Stellen gepflegt wird, gilt bald an zweien. ``erzwingen`` ist fuer
        den einen Fall gedacht, in dem ein Mensch einen verwaisten
        ``processing`` aufloest.

        ``post_status`` wird hier mitgeschrieben, aus ``POST_STATUS_ZU_JOB``.
        Das ist der ganze Grund, warum es diese eine Methode gibt: Solange
        beide Felder nur hier gesetzt werden, koennen sie nicht auseinander-
        laufen, und jeder aeltere Leser (``campaign queue``, ``retry``,
        ``post_counts``, die Uebersicht) sieht weiter, was er immer sah.
        """
        link = self.link_for(campaign_id, group_id)
        if link is None:
            raise UnknownGroupError(f"{campaign_id}/{group_id}")

        if not erzwingen:
            pruefe_uebergang(link.job_status, neu, hat_text=bool(link.post_text.strip()))

        jetzt = datetime.now(UTC)
        felder: dict[str, object] = {
            "job_status": neu.value,
            "post_status": POST_STATUS_ZU_JOB[neu].value,
        }

        if neu is JobStatus.APPROVED:
            felder["freigegeben_am"] = _iso(jetzt)
            felder["freigegeben_von"] = akteur
        if neu is JobStatus.PENDING_REVIEW and link.job_status is JobStatus.APPROVED:
            # Eine zurueckgenommene Freigabe ist keine Freigabe mehr. Bliebe der
            # Zeitpunkt stehen, sagte die Zeile "freigegeben am ...", waehrend
            # sie auf Pruefung wartet.
            felder["freigegeben_am"] = None
            felder["freigegeben_von"] = ""
        if neu is JobStatus.PUBLISHED and link.posted_at is None:
            # Nur beim ersten Erfolg - die Klicks gehen auf den Beitrag zurueck,
            # der zuerst stand.
            felder["posted_at"] = _iso(jetzt)
        if neu in (JobStatus.PUBLISHED, JobStatus.FAILED):
            felder["last_attempt_at"] = _iso(jetzt)
            felder["post_attempts"] = link.post_attempts + 1
        if fehler is not None:
            felder["post_error"] = fehler
        elif neu is JobStatus.PUBLISHED:
            # Ein Erfolg loescht den Grund, aus dem es beim letzten Mal nicht
            # ging - sonst stuende er neben einem veroeffentlichten Beitrag.
            felder["post_error"] = ""

        zuweisung = ", ".join(f"{name} = ?" for name in felder)
        self.conn.execute(
            f"UPDATE campaign_groups SET {zuweisung} "  # noqa: S608
            "WHERE campaign_id = ? AND group_id = ?",
            (*felder.values(), campaign_id, group_id),
        )
        self.conn.commit()
        neuer_stand = self.link_for(campaign_id, group_id)
        if neuer_stand is None:
            raise UnknownGroupError(f"{campaign_id}/{group_id}")
        return neuer_stand

    def set_post_text(
        self,
        campaign_id: str,
        group_id: str,
        text: str,
        quelle: TextQuelle,
        *,
        neuer_status: JobStatus | None = None,
    ) -> CampaignGroup:
        """Legt den Beitragstext ab - den, der spaeter wirklich gepostet wird.

        Bewusst ohne Statuswechsel als Vorgabe: Text schreiben und Text
        freigeben sind zwei Handlungen, und die zweite gehoert einem Menschen.
        """
        self.conn.execute(
            "UPDATE campaign_groups SET post_text = ?, text_quelle = ?, generiert_am = ? "
            "WHERE campaign_id = ? AND group_id = ?",
            (text, quelle.value, _iso(datetime.now(UTC)), campaign_id, group_id),
        )
        self.conn.commit()
        if neuer_status is not None:
            return self.set_job_status(campaign_id, group_id, neuer_status)
        link = self.link_for(campaign_id, group_id)
        if link is None:
            raise UnknownGroupError(f"{campaign_id}/{group_id}")
        return link

    def jobs_mit_status(
        self, campaign_id: str, status: JobStatus | None = None
    ) -> list[CampaignGroup]:
        """Alle Jobs einer Kampagne, wahlweise nur die in einem Stand."""
        if status is None:
            rows = self.conn.execute(
                "SELECT * FROM campaign_groups WHERE campaign_id = ? ORDER BY added_at",
                (campaign_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM campaign_groups WHERE campaign_id = ? AND job_status = ? "
                "ORDER BY added_at",
                (campaign_id, status.value),
            ).fetchall()
        return [self._row_to_link(row) for row in rows]

    def job_counts(self, campaign_id: str) -> dict[str, int]:
        """Zaehler je Vorbereitungsstand - die Kacheln der Uebersicht.

        Auch die leeren Staende erscheinen mit 0: "keine Entwuerfe" ist eine
        Aussage, eine fehlende Kachel ist ein Raetsel.
        """
        rows = self.conn.execute(
            "SELECT job_status, COUNT(*) AS anzahl FROM campaign_groups "
            "WHERE campaign_id = ? GROUP BY job_status",
            (campaign_id,),
        ).fetchall()
        zaehler = {stand.value: 0 for stand in JobStatus}
        for row in rows:
            zaehler[str(row["job_status"])] = int(row["anzahl"])
        return zaehler

    def queue_zustand(self, campaign_id: str) -> QueueZustand:
        """Laeuft die Warteschlange dieser Kampagne gerade?

        Vorgabe ``laufend``: Eine Kampagne, die noch nie angehalten wurde, ist
        nicht angehalten. Ein unbekannter gespeicherter Wert gilt dagegen als
        ``gestoppt`` - im Zweifel wird nicht gepostet.
        """
        wert = self.meta(zustand_schluessel(campaign_id), QueueZustand.LAUFEND.value)
        try:
            return QueueZustand(wert)
        except ValueError:
            return QueueZustand.GESTOPPT

    def set_queue_zustand(self, campaign_id: str, zustand: QueueZustand) -> int:
        """Haelt die Warteschlange an oder laesst sie weiterlaufen.

        Bei ``gestoppt`` gehen alle eingereihten Jobs auf ``approved`` zurueck.
        Returns: wie viele das waren. ``processing`` wird **nicht** angefasst -
        dort ist moeglicherweise gerade ein Beitrag unterwegs, und ihn aus der
        Buchfuehrung zu nehmen, waehrend er in der Gruppe landet, waere
        schlimmer als ein Job zu viel in der Liste.
        """
        self.set_meta(zustand_schluessel(campaign_id), zustand.value)
        if zustand is not QueueZustand.GESTOPPT:
            return 0

        cursor = self.conn.execute(
            "UPDATE campaign_groups SET job_status = ?, post_status = ? "
            "WHERE campaign_id = ? AND job_status = ?",
            (
                JobStatus.APPROVED.value,
                POST_STATUS_ZU_JOB[JobStatus.APPROVED].value,
                campaign_id,
                JobStatus.QUEUED.value,
            ),
        )
        self.conn.commit()
        return int(cursor.rowcount or 0)

    def naechster_job(self, campaign_id: str) -> CampaignGroup | None:
        """Der naechste Job aus der Warteschlange - oder nichts.

        Liefert nur bei ``laufend`` etwas. Die Reihenfolge kommt aus
        ``added_at``; die fachliche Rangfolge nach Score setzt der Aufrufer
        beim **Einreihen**, nicht hier - sonst entschiede eine Neubewertung
        mitten im Lauf, welcher Beitrag als naechstes hinausgeht.
        """
        if not darf_arbeiten(self.queue_zustand(campaign_id)):
            return None
        row = self.conn.execute(
            "SELECT * FROM campaign_groups WHERE campaign_id = ? AND job_status = ? "
            "ORDER BY added_at, group_id LIMIT 1",
            (campaign_id, JobStatus.QUEUED.value),
        ).fetchone()
        return self._row_to_link(row) if row else None

    # -- Entwuerfe --------------------------------------------------------
    def add_entwurf(self, entwurf: PostEntwurf) -> int:
        """Legt eine Textfassung ab. Die Variantennummer zaehlt je Paar hoch."""
        if entwurf.variante <= 0:
            row = self.conn.execute(
                "SELECT COALESCE(MAX(variante), 0) AS hoechste FROM post_entwuerfe "
                "WHERE campaign_id = ? AND group_id = ?",
                (entwurf.campaign_id, entwurf.group_id),
            ).fetchone()
            entwurf.variante = int(row["hoechste"]) + 1

        cursor = self.conn.execute(
            """
            INSERT INTO post_entwuerfe
                (campaign_id, group_id, variante, text, quelle, modell, erzeugt_am, gewaehlt)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                entwurf.campaign_id,
                entwurf.group_id,
                entwurf.variante,
                entwurf.text,
                entwurf.quelle.value,
                entwurf.modell,
                _iso(entwurf.erzeugt_am),
                int(entwurf.gewaehlt),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid or 0)

    def entwuerfe_for(self, campaign_id: str, group_id: str) -> list[PostEntwurf]:
        rows = self.conn.execute(
            "SELECT * FROM post_entwuerfe WHERE campaign_id = ? AND group_id = ? "
            "ORDER BY variante",
            (campaign_id, group_id),
        ).fetchall()
        return [self._row_to_entwurf(row) for row in rows]

    def waehle_entwurf(self, entwurf_id: int) -> PostEntwurf | None:
        """Macht eine Fassung zur gewaehlten und uebernimmt ihren Text.

        Die verworfenen Fassungen bleiben stehen. Sie kosten nichts und
        beantworten spaeter die Frage, wogegen entschieden wurde.
        """
        row = self.conn.execute(
            "SELECT * FROM post_entwuerfe WHERE entwurf_id = ?", (entwurf_id,)
        ).fetchone()
        if row is None:
            return None
        entwurf = self._row_to_entwurf(row)

        self.conn.execute(
            "UPDATE post_entwuerfe SET gewaehlt = 0 WHERE campaign_id = ? AND group_id = ?",
            (entwurf.campaign_id, entwurf.group_id),
        )
        self.conn.execute(
            "UPDATE post_entwuerfe SET gewaehlt = 1 WHERE entwurf_id = ?", (entwurf_id,)
        )
        self.conn.commit()
        self.set_post_text(entwurf.campaign_id, entwurf.group_id, entwurf.text, entwurf.quelle)
        return entwurf

    # -- Versuchsprotokoll ------------------------------------------------
    def beginne_versuch(self, versuch: PostVersuch) -> int:
        """Traegt einen begonnenen Versuch ein. Returns: seine Kennung.

        Geschrieben wird **vor** dem Versuch, nicht danach: Ein Arbeiter, der
        mitten im Absetzen abstuerzt, hinterliesse sonst keine Spur - und
        niemand wuesste, ob in der Gruppe nun ein Beitrag steht oder nicht.
        """
        cursor = self.conn.execute(
            """
            INSERT INTO post_versuche
                (campaign_id, group_id, tracking_code, job_status, erfolg,
                 post_url, fehler, browser_session, ausgeloest_von, begonnen_am)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                versuch.campaign_id,
                versuch.group_id,
                versuch.tracking_code,
                versuch.job_status.value,
                int(versuch.erfolg),
                versuch.post_url,
                versuch.fehler,
                versuch.browser_session,
                versuch.ausgeloest_von,
                _iso(versuch.begonnen_am),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid or 0)

    def beende_versuch(
        self, versuch_id: int, *, erfolg: bool, fehler: str = "", post_url: str = ""
    ) -> None:
        """Schliesst den Versuch ab - Ausgang, Grund, gegebenenfalls die URL."""
        self.conn.execute(
            "UPDATE post_versuche SET erfolg = ?, fehler = ?, post_url = ?, "
            "job_status = ?, beendet_am = ? WHERE versuch_id = ?",
            (
                int(erfolg),
                fehler,
                post_url,
                (JobStatus.PUBLISHED if erfolg else JobStatus.FAILED).value,
                _iso(datetime.now(UTC)),
                versuch_id,
            ),
        )
        self.conn.commit()

    def versuche_heute(
        self, campaign_id: str | None = None, *, jetzt: datetime | None = None
    ) -> int:
        """Wie viele Versuche heute schon unternommen wurden.

        Grundlage des Tageslimits, und sie steht mit Absicht in der Datenbank
        statt in einem Zaehler im Arbeiter: Wer um 08:00 zwanzig Beitraege
        setzt, abstuerzt und um 14:00 neu startet, saehe sonst einen leeren
        Zaehler und setzte zwanzig weitere.

        **Ohne ``campaign_id`` ueber alle Kampagnen** - und so ruft der Arbeiter
        es auch. Die knappe Ressource ist das Facebook-Konto, nicht die
        Kampagne: Zwei Kampagnen mit je zwanzig Beitraegen sind vierzig
        Beitraege aus demselben Konto, und das Limit waere eine Beschriftung
        ohne Wirkung. Je Kampagne zaehlen laesst sich weiterhin - fuer die
        Anzeige, nicht fuer die Grenze.

        Gezaehlt wird ab **oertlicher** Mitternacht, nicht ab UTC: "20 pro Tag"
        meint den Tag des Menschen, der davorsitzt. Gespeichert ist in UTC, die
        Grenze wird deshalb umgerechnet.

        Gezaehlt wird **jeder** Versuch, auch der fehlgeschlagene. Das Limit
        schuetzt nicht vor zu vielen Beitraegen, sondern vor zu viel Betrieb -
        zehn Fehlschlaege hintereinander sind ein Grund, den Tag zu beenden,
        kein Grund, es zwanzig weitere Male zu versuchen.
        """
        jetzt = jetzt or datetime.now()
        mitternacht = jetzt.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        seit = _iso(mitternacht.astimezone(UTC))

        if campaign_id is None:
            row = self.conn.execute(
                "SELECT COUNT(*) AS anzahl FROM post_versuche WHERE begonnen_am >= ?",
                (seit,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) AS anzahl FROM post_versuche "
                "WHERE campaign_id = ? AND begonnen_am >= ?",
                (campaign_id, seit),
            ).fetchone()
        return int(row["anzahl"] or 0)

    def letzter_versuch(self, *, campaign_id: str | None = None) -> datetime | None:
        """Wann zuletzt ein Beitrag begonnen wurde - oder ``None``.

        Grundlage der Wartezeit zwischen zwei Beitraegen, wenn der Ablauf
        **nicht** in einer Schleife laeuft: Bei der Arbeit ueber die Uebersicht
        steht zwischen zwei Beitraegen keine Schleife, die schlafen koennte,
        sondern ein Mensch, der eine Seite neu laedt. Die Pause muss deshalb
        aus dem Bestand kommen und nicht aus dem Ablauf - sonst umginge man sie
        durch Neuladen.

        Wie beim Tageslimit ueber **alle** Kampagnen: Der Takt gilt fuer das
        Konto, nicht fuer eine einzelne Kampagne.
        """
        if campaign_id is None:
            row = self.conn.execute(
                "SELECT MAX(begonnen_am) AS letzte FROM post_versuche"
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT MAX(begonnen_am) AS letzte FROM post_versuche WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
        return _parse_dt(row["letzte"]) if row and row["letzte"] else None

    def versuche_for(self, campaign_id: str, group_id: str) -> list[PostVersuch]:
        rows = self.conn.execute(
            "SELECT * FROM post_versuche WHERE campaign_id = ? AND group_id = ? "
            "ORDER BY begonnen_am, versuch_id",
            (campaign_id, group_id),
        ).fetchall()
        return [self._row_to_versuch(row) for row in rows]

    def offene_versuche(self, campaign_id: str) -> list[PostVersuch]:
        """Versuche ohne Abschluss - die Kandidaten fuer verwaiste Jobs."""
        rows = self.conn.execute(
            "SELECT * FROM post_versuche WHERE campaign_id = ? AND beendet_am IS NULL "
            "ORDER BY begonnen_am",
            (campaign_id,),
        ).fetchall()
        return [self._row_to_versuch(row) for row in rows]

    def resolve_code(self, tracking_code: str) -> CampaignGroup | None:
        """Findet Kampagne und Gruppe zu einem Tracking-Code."""
        row = self.conn.execute(
            "SELECT * FROM campaign_groups WHERE tracking_code = ?", (tracking_code,)
        ).fetchone()
        return self._row_to_link(row) if row else None

    def record_event(self, event: TrackingEvent) -> int:
        """Schreibt ein Ereignis und liefert seine Kennung."""
        cursor = self.conn.execute(
            """
            INSERT INTO tracking_events (
                tracking_code, campaign_id, group_id, user_ref,
                event_type, occurred_at, visitor_hash, source
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                event.tracking_code,
                event.campaign_id,
                event.group_id,
                event.user_ref,
                event.event_type.value,
                _iso(event.occurred_at),
                event.visitor_hash,
                event.source,
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid or 0)

    def klick_bereits_gezaehlt(self, tracking_code: str, visitor_hash: str) -> bool:
        """Gab es diesen Klick heute schon?

        ``visitor_hash`` wechselt taeglich; ein erneuter Aufruf desselben Links
        am selben Tag zaehlt deshalb nicht doppelt, ein Aufruf am naechsten Tag
        schon. Ohne Pruefwert (z. B. bei fehlenden Kopfzeilen) wird gezaehlt -
        lieber ein Klick zu viel als ein stillschweigend verworfener.
        """
        if not visitor_hash:
            return False
        row = self.conn.execute(
            "SELECT 1 FROM tracking_events WHERE tracking_code = ? AND visitor_hash = ? "
            "AND event_type = 'click' LIMIT 1",
            (tracking_code, visitor_hash),
        ).fetchone()
        return row is not None

    # -- Identitaeten ---------------------------------------------------
    def identitaet(self, user_ref: str) -> str:
        """Die gemeinsame Kennung aller Auftritte dieses Menschen.

        Ohne Eintrag ist eine Kennung ihre eigene Identitaet. Die Tabelle
        bleibt damit leer, solange niemand unter zwei Kennungen auftritt -
        gespeichert wird nur, was tatsaechlich zusammengehoert.
        """
        if not user_ref:
            return ""
        row = self.conn.execute(
            "SELECT identity FROM user_identities WHERE user_ref = ?", (user_ref,)
        ).fetchone()
        return str(row["identity"]) if row else user_ref

    def kennungen(self, user_ref: str) -> list[str]:
        """Alle Kennungen, unter denen dieser Mensch aufgetreten ist."""
        if not user_ref:
            return []
        ident = self.identitaet(user_ref)
        rows = self.conn.execute(
            "SELECT user_ref FROM user_identities WHERE identity = ?", (ident,)
        ).fetchall()
        return sorted({user_ref, ident, *(str(row["user_ref"]) for row in rows)})

    def verknuepfe_kennung(self, alias_ref: str, user_ref: str) -> bool:
        """Haelt fest, dass ``alias_ref`` und ``user_ref`` derselbe Mensch sind.

        Gerufen wird das an genau einer Stelle: wenn eine Meldung beide
        Kennungen mitbringt - der anonyme Besucher, der sich gerade
        registriert hat. Die **Benutzerkennung gewinnt** als gemeinsame
        Identitaet: Sie ist die bestaendige; die anonyme verschwindet mit dem
        Browserspeicher.

        Nichts wird dabei ueberschrieben. Die alten Ereignisse behalten die
        Kennung, unter der sie gemeldet wurden - erst beim Lesen werden sie
        zusammengefuehrt. Eine Zuordnung, die einmal in der Datenbank steht,
        darf sich nicht nachtraeglich aendern; sie ist die Grundlage von
        Zahlen, die jemand schon gesehen hat.

        Liefert ``True``, wenn dadurch eine neue Verbindung entstanden ist.
        """
        if not alias_ref or not user_ref or alias_ref == user_ref:
            return False

        ziel = self.identitaet(user_ref)
        quelle = self.identitaet(alias_ref)
        if ziel == quelle:
            return False

        jetzt = _iso(datetime.now(UTC))
        # Erst die bereits verbundenen Kennungen der anonymen Seite
        # umhaengen, dann beide Enden selbst eintragen. Andernfalls verloere
        # eine dritte Kennung, die frueher schon an ``alias_ref`` haengt,
        # ihren Anschluss.
        self.conn.execute(
            "UPDATE user_identities SET identity = ? WHERE identity = ?", (ziel, quelle)
        )
        for ref in (user_ref, alias_ref):
            self.conn.execute(
                "INSERT INTO user_identities (user_ref, identity, created_at) VALUES (?,?,?) "
                "ON CONFLICT(user_ref) DO UPDATE SET identity = excluded.identity",
                (ref, ziel, jetzt),
            )
        self.conn.commit()
        return True

    def erste_zuordnung(self, user_ref: str) -> tuple[str, str, str]:
        """Kampagne, Gruppe und Code, ueber die dieser Mensch erstmals kam.

        Spaetere Meldungen ("download", "qualified", "conversion") tragen den
        Tracking-Code meist nicht mehr mit - die Zielanwendung kennt zu dem
        Zeitpunkt nur noch ihren Benutzer. Ohne diese Erbschaft blieben genau
        die Stufen ohne Gruppe, um die es geht: Welche Gruppe bringt Benutzer,
        die die App wirklich holen? Massgeblich ist der **erste** Fund - er hat
        den Menschen gebracht, nicht ein spaeterer Link.

        Gesucht wird ueber **alle** Kennungen desselben Menschen. Der erste
        Besuch traegt die anonyme Kennung des Browsers, die Registrierung die
        Benutzerkennung der Anwendung; nur ueber die eigene Kennung gesucht,
        endete die Zuordnung genau an diesem Uebergang - und jeder Download
        danach stand ohne Gruppe da.
        """
        refs = self.kennungen(user_ref)
        if not refs:
            return "", "", ""
        platzhalter = ",".join("?" * len(refs))
        row = self.conn.execute(
            "SELECT campaign_id, group_id, tracking_code FROM tracking_events "  # noqa: S608
            f"WHERE user_ref IN ({platzhalter}) AND tracking_code <> '' "  # noqa: S608
            "ORDER BY occurred_at, event_id LIMIT 1",
            tuple(refs),
        ).fetchone()
        if row is None:
            return "", "", ""
        return row["campaign_id"], row["group_id"], row["tracking_code"]

    def ereignis_bereits_gezaehlt(self, event_type: EventType, user_ref: str) -> bool:
        """Gab es dieses Ereignis fuer diesen Menschen schon?

        Nur fuer die Stufen aus ``EINMAL_JE_MENSCH``. Geprueft wird ueber alle
        Kennungen desselben Menschen - sonst zaehlte derselbe Download vor und
        nach dem Registrieren zweimal.
        """
        refs = self.kennungen(user_ref)
        if not refs:
            return False
        platzhalter = ",".join("?" * len(refs))
        row = self.conn.execute(
            "SELECT 1 FROM tracking_events "  # noqa: S608
            f"WHERE event_type = ? AND user_ref IN ({platzhalter}) LIMIT 1",  # noqa: S608
            (event_type.value, *refs),
        ).fetchone()
        return row is not None

    def events_for_user(self, user_ref: str) -> list[TrackingEvent]:
        rows = self.conn.execute(
            "SELECT * FROM tracking_events WHERE user_ref = ? ORDER BY occurred_at",
            (user_ref,),
        ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def events_for_code(self, tracking_code: str) -> list[TrackingEvent]:
        """Alle Ereignisse, die diesem Tracking-Code zugeordnet sind."""
        rows = self.conn.execute(
            "SELECT * FROM tracking_events WHERE tracking_code = ? "
            "ORDER BY occurred_at, event_id",
            (tracking_code,),
        ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def event_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT event_type, COUNT(*) AS anzahl FROM tracking_events GROUP BY event_type"
        ).fetchall()
        return {row["event_type"]: int(row["anzahl"]) for row in rows}

    def counts_by(self, spalte: str) -> dict[tuple[str, str], int]:
        """Ereigniszahlen je Gruppe bzw. Kampagne.

        ``spalte`` ist ``group_id``, ``campaign_id`` oder ``tracking_code`` -
        alle drei sind eigene Spaltennamen dieser Tabelle, keine Eingabe von
        aussen.
        """
        if spalte not in {"group_id", "campaign_id", "tracking_code"}:
            raise ValueError(f"Unzulaessige Spalte: {spalte}")
        rows = self.conn.execute(
            f"SELECT {spalte} AS schluessel, event_type, COUNT(*) AS anzahl "  # noqa: S608
            f"FROM tracking_events WHERE {spalte} <> '' "  # noqa: S608
            "GROUP BY schluessel, event_type"
        ).fetchall()
        return {(row["schluessel"], row["event_type"]): int(row["anzahl"]) for row in rows}

    # -- Empfehlungen ---------------------------------------------------
    def referral_code_for(self, user_ref: str) -> str | None:
        row = self.conn.execute(
            "SELECT referral_code FROM referral_codes WHERE user_ref = ?", (user_ref,)
        ).fetchone()
        return row["referral_code"] if row else None

    def user_for_referral_code(self, referral_code: str) -> str | None:
        row = self.conn.execute(
            "SELECT user_ref FROM referral_codes WHERE referral_code = ?", (referral_code,)
        ).fetchone()
        return row["user_ref"] if row else None

    def save_referral_code(self, referral_code: str, user_ref: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO referral_codes (referral_code, user_ref, created_at) "
            "VALUES (?,?,?)",
            (referral_code, user_ref, _iso(datetime.now(UTC))),
        )
        self.conn.commit()

    def referral_codes_vergeben(self) -> set[str]:
        return {row["referral_code"] for row in self.conn.execute(
            "SELECT referral_code FROM referral_codes"
        )}

    def save_referral(self, referral: Referral) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO referrals (
                referral_code, referrer_user_ref, referred_user_ref, status,
                campaign_id, group_id, created_at, updated_at, note
            ) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(referred_user_ref) DO UPDATE SET
                status     = excluded.status,
                updated_at = excluded.updated_at,
                note       = excluded.note
            """,
            (
                referral.referral_code,
                referral.referrer_user_ref,
                referral.referred_user_ref,
                referral.status.value,
                referral.campaign_id,
                referral.group_id,
                _iso(referral.created_at),
                _iso(referral.updated_at),
                referral.note,
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid or 0)

    def referral_for_referred(self, referred_user_ref: str) -> Referral | None:
        row = self.conn.execute(
            "SELECT * FROM referrals WHERE referred_user_ref = ?", (referred_user_ref,)
        ).fetchone()
        return self._row_to_referral(row) if row else None

    def referrals_of(self, referrer_user_ref: str) -> list[Referral]:
        rows = self.conn.execute(
            "SELECT * FROM referrals WHERE referrer_user_ref = ? ORDER BY created_at",
            (referrer_user_ref,),
        ).fetchall()
        return [self._row_to_referral(row) for row in rows]

    def all_referrals(self, status: ReferralStatus | None = None) -> list[Referral]:
        if status is None:
            rows = self.conn.execute("SELECT * FROM referrals ORDER BY created_at DESC").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM referrals WHERE status = ? ORDER BY created_at DESC",
                (status.value,),
            ).fetchall()
        return [self._row_to_referral(row) for row in rows]

    def referral_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS anzahl FROM referrals GROUP BY status"
        ).fetchall()
        return {row["status"]: int(row["anzahl"]) for row in rows}

    # -- Praemien -------------------------------------------------------
    def save_reward(self, reward: Reward) -> bool:
        """Legt eine Praemie an. Returns: True, wenn sie neu war.

        Eine bereits vergebene Regel wird nicht erneut vergeben - sonst
        entstuenden aus einem einzigen erreichten Schwellenwert beliebig viele
        Praemien.
        """
        cursor = self.conn.execute(
            """
            INSERT OR IGNORE INTO rewards (
                user_ref, rule_id, reward_type, value, status,
                earned_at, updated_at, note
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                reward.user_ref,
                reward.rule_id,
                reward.reward_type.value,
                reward.value,
                reward.status.value,
                _iso(reward.earned_at),
                _iso(reward.updated_at),
                reward.note,
            ),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def set_reward_status(self, user_ref: str, rule_id: str, status: RewardStatus) -> int:
        cursor = self.conn.execute(
            "UPDATE rewards SET status = ?, updated_at = ? WHERE user_ref = ? AND rule_id = ?",
            (status.value, _iso(datetime.now(UTC)), user_ref, rule_id),
        )
        self.conn.commit()
        return cursor.rowcount

    def rewards_of(self, user_ref: str) -> list[Reward]:
        rows = self.conn.execute(
            "SELECT * FROM rewards WHERE user_ref = ? ORDER BY earned_at", (user_ref,)
        ).fetchall()
        return [self._row_to_reward(row) for row in rows]

    def all_rewards(self) -> list[Reward]:
        rows = self.conn.execute("SELECT * FROM rewards ORDER BY earned_at DESC").fetchall()
        return [self._row_to_reward(row) for row in rows]

    # -- Audit und Merkposten -------------------------------------------
    def audit(self, action: str, subject: str = "", detail: str = "") -> None:
        """Haelt jede Entscheidung fest - auch die abgelehnten.

        Ohne dieses Protokoll waere spaeter nicht mehr nachvollziehbar, warum
        eine Empfehlung abgelehnt oder zur Pruefung gestellt wurde.
        """
        self.conn.execute(
            "INSERT INTO marketing_audit (occurred_at, action, subject, detail) VALUES (?,?,?,?)",
            (_iso(datetime.now(UTC)), action, subject, detail),
        )
        self.conn.commit()

    def audit_log(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM marketing_audit ORDER BY audit_id DESC LIMIT ?", (limit,)
        ).fetchall()

    def meta(self, schluessel: str, standard: str = "") -> str:
        row = self.conn.execute(
            "SELECT wert FROM marketing_meta WHERE schluessel = ?", (schluessel,)
        ).fetchone()
        return row["wert"] if row else standard

    def set_meta(self, schluessel: str, wert: str) -> None:
        self.conn.execute(
            "INSERT INTO marketing_meta (schluessel, wert) VALUES (?,?) "
            "ON CONFLICT(schluessel) DO UPDATE SET wert = excluded.wert",
            (schluessel, wert),
        )
        self.conn.commit()

    # -- Umwandlung -----------------------------------------------------
    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> TrackingEvent:
        return TrackingEvent(
            event_id=row["event_id"],
            tracking_code=row["tracking_code"],
            campaign_id=row["campaign_id"],
            group_id=row["group_id"],
            user_ref=row["user_ref"],
            event_type=row["event_type"],
            occurred_at=row["occurred_at"],
            visitor_hash=row["visitor_hash"],
            source=row["source"],
        )

    @staticmethod
    def _row_to_entwurf(row: sqlite3.Row) -> PostEntwurf:
        return PostEntwurf(
            entwurf_id=row["entwurf_id"],
            campaign_id=row["campaign_id"],
            group_id=row["group_id"],
            variante=row["variante"],
            text=row["text"],
            quelle=row["quelle"],
            modell=row["modell"],
            erzeugt_am=row["erzeugt_am"],
            gewaehlt=bool(row["gewaehlt"]),
        )

    @staticmethod
    def _row_to_versuch(row: sqlite3.Row) -> PostVersuch:
        return PostVersuch(
            versuch_id=row["versuch_id"],
            campaign_id=row["campaign_id"],
            group_id=row["group_id"],
            tracking_code=row["tracking_code"],
            job_status=row["job_status"],
            erfolg=bool(row["erfolg"]),
            post_url=row["post_url"],
            fehler=row["fehler"],
            browser_session=row["browser_session"],
            ausgeloest_von=row["ausgeloest_von"],
            begonnen_am=row["begonnen_am"],
            beendet_am=row["beendet_am"],
        )

    @staticmethod
    def _row_to_referral(row: sqlite3.Row) -> Referral:
        return Referral(
            referral_id=row["referral_id"],
            referral_code=row["referral_code"],
            referrer_user_ref=row["referrer_user_ref"],
            referred_user_ref=row["referred_user_ref"],
            status=row["status"],
            campaign_id=row["campaign_id"],
            group_id=row["group_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            note=row["note"],
        )

    @staticmethod
    def _row_to_reward(row: sqlite3.Row) -> Reward:
        return Reward(
            reward_id=row["reward_id"],
            user_ref=row["user_ref"],
            rule_id=row["rule_id"],
            reward_type=row["reward_type"],
            value=row["value"],
            status=row["status"],
            earned_at=row["earned_at"],
            updated_at=row["updated_at"],
            note=row["note"],
        )

    @staticmethod
    def _row_to_campaign(row: sqlite3.Row) -> Campaign:
        return Campaign(
            campaign_id=row["campaign_id"],
            name=row["name"],
            description=row["description"],
            audiences=json.loads(row["audiences"]),
            cities=json.loads(row["cities"]),
            language=row["language"],
            message_template=row["message_template"],
            landing_page=row["landing_page"],
            status=row["status"],
            starts_on=row["starts_on"],
            ends_on=row["ends_on"],
            target_audiences=json.loads(row["target_audiences"]),
            target_cities=json.loads(row["target_cities"]),
            target_categories=json.loads(row["target_categories"]),
            target_statuses=json.loads(row["target_statuses"]),
            target_min_score=row["target_min_score"],
            target_include_unscored=bool(row["target_include_unscored"]),
            auto_assign=bool(row["auto_assign"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_link(row: sqlite3.Row) -> CampaignGroup:
        return CampaignGroup(
            campaign_id=row["campaign_id"],
            group_id=row["group_id"],
            tracking_code=row["tracking_code"],
            tracking_url=row["tracking_url"],
            added_at=row["added_at"],
            post_status=row["post_status"],
            posted_at=row["posted_at"],
            last_attempt_at=row["last_attempt_at"],
            post_attempts=row["post_attempts"],
            post_error=row["post_error"],
            job_status=row["job_status"],
            post_text=row["post_text"],
            text_quelle=row["text_quelle"],
            generiert_am=row["generiert_am"],
            freigegeben_am=row["freigegeben_am"],
            freigegeben_von=row["freigegeben_von"],
        )

    @staticmethod
    def _row_to_marketing(row: sqlite3.Row) -> GroupMarketing:
        return GroupMarketing(
            group_id=row["group_id"],
            marketing_status=row["marketing_status"],
            contact_status=row["contact_status"],
            permission_status=row["permission_status"],
            campaign_status=row["campaign_status"],
            join_requested_at=row["join_requested_at"],
            last_contacted_at=row["last_contacted_at"],
            last_posted_at=row["last_posted_at"],
            bearbeiten=bool(row["bearbeiten"]),
            ausschlussgrund=row["ausschlussgrund"],
            notes=row["notes"],
            updated_at=row["updated_at"],
        )
