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
    Campaign,
    CampaignGroup,
    CampaignStatus,
    GroupMarketing,
    Referral,
    ReferralStatus,
    Reward,
    RewardStatus,
    TrackingEvent,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id      TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    audiences        TEXT NOT NULL DEFAULT '[]',
    cities           TEXT NOT NULL DEFAULT '[]',
    language         TEXT NOT NULL DEFAULT '',
    message_template TEXT NOT NULL DEFAULT '',
    landing_page     TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'draft',
    starts_on        TEXT,
    ends_on          TEXT,
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
    PRIMARY KEY (campaign_id, group_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_campaign_groups_group ON campaign_groups(group_id);

CREATE TABLE IF NOT EXISTS group_marketing (
    group_id          TEXT PRIMARY KEY,
    marketing_status  TEXT NOT NULL DEFAULT 'not_contacted',
    contact_status    TEXT NOT NULL DEFAULT 'none',
    permission_status TEXT NOT NULL DEFAULT 'unknown',
    campaign_status   TEXT NOT NULL DEFAULT 'none',
    last_contacted_at TEXT,
    last_posted_at    TEXT,
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


class UnknownGroupError(KeyError):
    """Die Gruppe steht nicht im Bestand - ohne sie gibt es nichts zu bewerben."""


class UnknownCampaignError(KeyError):
    """Die Kampagne gibt es nicht."""


def _iso(wert: datetime | date | None) -> str | None:
    return wert.isoformat() if wert is not None else None


class MarketingStore:
    """Zugriff auf die Marketing-Tabellen derselben SQLite-Datei."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.executescript(SCHEMA_TRACKING)
        self.conn.commit()

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
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                campaign_status, last_contacted_at, last_posted_at, notes, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(group_id) DO UPDATE SET
                marketing_status  = excluded.marketing_status,
                contact_status    = excluded.contact_status,
                permission_status = excluded.permission_status,
                campaign_status   = excluded.campaign_status,
                last_contacted_at = excluded.last_contacted_at,
                last_posted_at    = excluded.last_posted_at,
                notes             = excluded.notes,
                updated_at        = excluded.updated_at
            """,
            (
                eintrag.group_id,
                eintrag.marketing_status.value,
                eintrag.contact_status.value,
                eintrag.permission_status.value,
                eintrag.campaign_status.value,
                _iso(eintrag.last_contacted_at),
                _iso(eintrag.last_posted_at),
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

    def erste_zuordnung(self, user_ref: str) -> tuple[str, str, str]:
        """Kampagne, Gruppe und Code, ueber die dieser Benutzer erstmals kam.

        Spaetere Meldungen ("qualified", "conversion") tragen den Tracking-Code
        meist nicht mehr mit - die Zielanwendung kennt zu dem Zeitpunkt nur
        noch ihren Benutzer. Ohne diese Erbschaft blieben genau die Stufen
        ohne Gruppe, um die es geht: Welche Gruppe bringt *qualifizierte*
        Benutzer? Massgeblich ist der **erste** Fund - er hat den Menschen
        gebracht, nicht ein spaeterer Link.
        """
        row = self.conn.execute(
            "SELECT campaign_id, group_id, tracking_code FROM tracking_events "
            "WHERE user_ref = ? AND tracking_code <> '' "
            "ORDER BY occurred_at, event_id LIMIT 1",
            (user_ref,),
        ).fetchone()
        if row is None:
            return "", "", ""
        return row["campaign_id"], row["group_id"], row["tracking_code"]

    def events_for_user(self, user_ref: str) -> list[TrackingEvent]:
        rows = self.conn.execute(
            "SELECT * FROM tracking_events WHERE user_ref = ? ORDER BY occurred_at",
            (user_ref,),
        ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def event_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT event_type, COUNT(*) AS anzahl FROM tracking_events GROUP BY event_type"
        ).fetchall()
        return {row["event_type"]: int(row["anzahl"]) for row in rows}

    def counts_by(self, spalte: str) -> dict[tuple[str, str], int]:
        """Ereigniszahlen je Gruppe bzw. Kampagne.

        ``spalte`` ist ``group_id`` oder ``campaign_id`` - beides sind eigene
        Spaltennamen dieser Tabelle, keine Eingabe von aussen.
        """
        if spalte not in {"group_id", "campaign_id"}:
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
        )

    @staticmethod
    def _row_to_marketing(row: sqlite3.Row) -> GroupMarketing:
        return GroupMarketing(
            group_id=row["group_id"],
            marketing_status=row["marketing_status"],
            contact_status=row["contact_status"],
            permission_status=row["permission_status"],
            campaign_status=row["campaign_status"],
            last_contacted_at=row["last_contacted_at"],
            last_posted_at=row["last_posted_at"],
            notes=row["notes"],
            updated_at=row["updated_at"],
        )
