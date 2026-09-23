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
import secrets
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fbgroups.marketing.kurzcode import kurzcode
from fbgroups.marketing.models import (
    MARKETING_FORTSCHRITT,
    POST_STATUS_ZU_JOB,
    Campaign,
    CampaignGroup,
    CampaignStatus,
    EventType,
    GroupMarketing,
    JobStatus,
    KampagnenLaufStatus,
    LaufStatus,
    MarketingStatus,
    PostStatus,
    PostVersuch,
    QueueZustand,
    Referral,
    ReferralStatus,
    Reward,
    RewardStatus,
    TextQuelle,
    Texttyp,
    Textvorschlag,
    TrackingEvent,
    VorschlagStatus,
)
from fbgroups.marketing.queue import darf_arbeiten, pruefe_uebergang, zustand_schluessel

#: Das Geheimnis der Kurzcodes - neben dem Salt der Besucherpruefsumme im
#: selben Speicher. Beide sind Eigenschaften **dieser** Datenbank: Wer die
#: Datei umzieht, nimmt sie mit, und die Adressen bleiben dieselben.
_KURZCODE_SALT = "kurzcode_salt"


@dataclass(frozen=True)
class Aufloesung:
    """Was hinter einer Weiterleitungsadresse steht.

    Vier Angaben statt eines Datensatzes, weil drei davon aus dem Datensatz
    allein nicht hervorgehen: **welcher** der beiden Codes gemeint war, wie er
    nach aussen heisst und wohin er fuehrt. Ohne sie muesste jeder Aufrufer
    die Zuordnung selbst nachrechnen - und zwei Rechnungen koennen
    auseinanderlaufen, mit Klicks auf der falschen Gruppe.
    """

    link: CampaignGroup
    #: Der Code, unter dem gezaehlt und ausgewertet wird.
    interner_code: str
    #: Der Code, der in einem Beitrag stehen darf.
    oeffentlicher_code: str
    #: ``store`` oder ``browser``.
    ziel: str

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
    -- Wohin ein Klick fuehrt: 'store' | 'landing' | '' (Vorgabe aus der
    -- Konfiguration). Leer ist die Vorgabe und nicht 'landing': Sonst
    -- muesste jede bestehende Kampagne einzeln umgestellt werden.
    ziel             TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'draft',
    starts_on        TEXT,
    ends_on          TEXT,
    -- Auswahlregel: welche Gruppen einen Tracking-Code bekommen.
    -- Leere Liste bzw. NULL heisst: keine Einschraenkung.
    target_audiences        TEXT NOT NULL DEFAULT '[]',
    target_cities           TEXT NOT NULL DEFAULT '[]',
    target_categories       TEXT NOT NULL DEFAULT '[]',
    target_statuses         TEXT NOT NULL DEFAULT '[]',
    -- Die Note der Mitgliederliste ("A++".."B") und die Aktivitaetsstufe.
    -- Leere Liste heisst auch hier: keine Einschraenkung.
    target_prioritaeten     TEXT NOT NULL DEFAULT '[]',
    target_aktivitaet       TEXT NOT NULL DEFAULT '[]',
    target_min_score        REAL,
    target_include_unscored INTEGER NOT NULL DEFAULT 0,
    auto_assign             INTEGER NOT NULL DEFAULT 0,
    -- Trug einmal die Frage "braucht diese Kampagne Kommentartexte?".
    -- Seit dem 28.08.2026 entstehen sie fuer jede Kampagne; die Spalte bleibt,
    -- weil Migrationen hier additiv sind, und wird auf 1 geschrieben, damit
    -- eine aeltere Fassung des Programms dieselbe Datei nicht anders liest.
    kommentare              INTEGER NOT NULL DEFAULT 1,
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
    -- Das Ergebnis der deterministischen Herstellung, unveraendert daneben.
    -- Siehe CampaignGroup: "was hat die Maschine gebaut?" und "was steht
    -- jetzt da?" sind zwei Fragen. Auch diese Spalten stehen hier UND als
    -- Migrationsschritt 13, aus dem oben genannten Grund.
    generated_text  TEXT NOT NULL DEFAULT '',
    vorlage_key     TEXT NOT NULL DEFAULT '',
    text_quelle     TEXT NOT NULL DEFAULT 'vorlage',
    generiert_am    TEXT,
    freigegeben_am  TEXT,
    freigegeben_von TEXT NOT NULL DEFAULT '',
    -- Der Kommentar: dieselben Felder, eigener Einsatzzweck. Er gehoert zum
    -- SELBEN Paar aus Kampagne und Gruppe - gleiche Lebensdauer, gleicher
    -- Schluessel, gleiche Loeschung. Eine zweite Tabelle daneben waere eine
    -- Kopie dieses Schluessels mit dem Risiko, auseinanderzulaufen.
    --
    -- Kein eigener job_status: Der Beitrag traegt den Ablauf (Freigabe,
    -- Warteschlange, Versuche), der Kommentar wird kopiert und eingefuegt.
    -- Auch diese Spalten stehen hier UND als Migrationsschritt 14, aus dem
    -- oben genannten Grund.
    kommentar_text         TEXT NOT NULL DEFAULT '',
    kommentar_generated    TEXT NOT NULL DEFAULT '',
    kommentar_vorlage_key  TEXT NOT NULL DEFAULT '',
    kommentar_quelle       TEXT NOT NULL DEFAULT 'vorlage',
    kommentar_generiert_am TEXT,
    -- Der dritte Ausgang der Kommentarautomatik neben "voll" und "offen":
    -- Die Gruppe gibt nicht mehr her. Zu wenige Beitraege zum Kommentieren,
    -- Kommentare abgeschaltet, kein Zugang mehr - in allen Faellen ist jeder
    -- weitere Anlauf vergeblich, und das ist etwas anderes als ein
    -- Fehlschlag: Ein Fehlschlag laedt zum Wiederholen ein.
    --
    -- Ohne diesen Endstand haengt eine Kampagne fuer immer bei 4 von 5, weil
    -- eine einzige Gruppe nur vier Beitraege hat - und 'completed' waere
    -- unerreichbar. Er ist nicht ableitbar: Er ist das Urteil nach dem
    -- Versuch, nicht eine Eigenschaft der Daten. Auch diese Spalten stehen
    -- hier UND als Migrationsschritt 18, aus dem oben genannten Grund.
    kommentar_erschoepft       INTEGER NOT NULL DEFAULT 0,
    kommentar_erschoepft_grund TEXT NOT NULL DEFAULT '',
    kommentar_erschoepft_am    TEXT,
    -- Der zweite Tracking-Code: derselbe Weg, anderes Ziel.
    --
    -- ``tracking_code`` fuehrt zum Play Store (``marketing.ziel: store``) und
    -- **bleibt dabei**. Er steht in veroeffentlichten Beitraegen; sein Ziel
    -- nachtraeglich umzustellen aenderte, wohin alte Beitraege fuehren, und
    -- das ohne dass jemand sie angefasst haette.
    --
    -- Der neue Code fuehrt in den Browser (auf ``campaign.landing_page``).
    -- Beide werden vollstaendig gezaehlt - der Unterschied ist allein das
    -- Ziel nach der Weiterleitung, und dadurch laesst sich im Trichter
    -- unterscheiden, ob ein Mensch ueber Store oder Browser kam.
    --
    -- Nullable, weil Bestandszeilen ihn nicht haben: Er entsteht beim ersten
    -- Bedarf (``vergib_browsercode``) und nicht durch eine Migration, die
    -- vierhundert Codes auf einmal erfindet.
    tracking_code_browser TEXT UNIQUE,
    tracking_url_browser  TEXT NOT NULL DEFAULT '',
    -- Der oeffentliche Deckname beider Codes - das, was in einem Beitrag
    -- steht. Kein zweites Tracking: Die Weiterleitung loest ihn auf und
    -- zaehlt unter dem inneren Code, der damit in jeder Auswertung
    -- unveraendert bleibt (``marketing/kurzcode.py``).
    --
    -- Nullable aus demselben Grund wie der Browser-Code: Bestandszeilen
    -- haben ihn nicht, und eine Migration, die vierhundert Adressen auf
    -- einmal erfindet, waere der falsche Ort dafuer. Fehlt er, geht die
    -- lange Adresse hinaus - unschoen, aber richtig.
    public_code           TEXT UNIQUE,
    public_url            TEXT NOT NULL DEFAULT '',
    public_code_browser   TEXT UNIQUE,
    public_url_browser    TEXT NOT NULL DEFAULT '',
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
    -- Was die Regeln DIESER Gruppe erlauben. Nur das Ergebnis des Abrufs,
    -- nicht das Urteil: Die Qualifikation wird bei jedem Lesen aus
    -- Mitgliedschaft, Regeln und Versuchsprotokoll gerechnet
    -- (``qualifikation.beurteile``) und steht in keiner Spalte - ein
    -- gespeichertes Urteil neben seinen Grundlagen laeuft von ihnen weg.
    --
    -- ``regeln_gelesen_am`` traegt die Unterscheidung, auf die es ankommt:
    -- NULL heisst "nicht nachgesehen", nicht "nichts verboten". Auch diese
    -- Spalten stehen hier UND als Migrationsschritt 20, aus dem oben
    -- genannten Grund.
    regeln_gelesen_am     TEXT,
    regel_keine_links     INTEGER NOT NULL DEFAULT 0,
    regel_keine_werbung   INTEGER NOT NULL DEFAULT 0,
    regel_freigabe_noetig INTEGER NOT NULL DEFAULT 0,
    regel_neue_ohne_links INTEGER NOT NULL DEFAULT 0,
    updated_at        TEXT NOT NULL,
    FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_group_marketing_status
    ON group_marketing(marketing_status);

-- Ein Lauf der Kommentarautomatik ueber mehrere Kampagnen.
--
-- Warum ueberhaupt eine Tabelle, wo der Fortschritt doch ableitbar ist: Der
-- Fortschritt IST ableitbar - welche Fassung veroeffentlicht wurde, steht in
-- campaign_group_texte, und daraus ergibt sich alles Uebrige. Ein zweiter
-- Zaehler daneben waere eine zweite Wahrheit ueber dieselbe Zahl.
--
-- Was hier steht, ist genau das, was sich NICHT ableiten laesst:
--
--   * dass ueberhaupt gerade ein Lauf im Gange ist (der globale Zustand),
--   * und WELCHE Kampagnen zu ihm gehoeren.
--
-- Das zweite ist der Kern: Ein Lauf arbeitet die Liste ab, die beim Start
-- festgelegt wurde. Wer waehrend des Laufs eine Kampagne auf 'active' setzt,
-- greift damit nicht in einen bereits laufenden Vorgang ein - sie kommt beim
-- naechsten Start dran. Ohne die eingefrorene Liste waere "alle aktiven
-- Kampagnen" bei jedem Schritt eine andere Menge, und ein Lauf koennte nie
-- fertig werden, weil die Bedingung unter ihm wegwandert.
CREATE TABLE IF NOT EXISTS automatik_lauf (
    lauf_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    status            TEXT NOT NULL DEFAULT 'laufend',
    texttyp           TEXT NOT NULL DEFAULT 'kommentar',
    ziel_je_gruppe    INTEGER NOT NULL DEFAULT 5,
    begonnen_am       TEXT NOT NULL,
    beendet_am        TEXT,
    letzter_schritt_am TEXT,
    meldung           TEXT NOT NULL DEFAULT ''
);

-- Die eingefrorene Warteschlange des Laufs. 'position' haelt die Reihenfolge
-- fest: Ohne sie entschiede die Sortierung der Abfrage, welche Kampagne als
-- naechste kommt, und ein Neustart koennte eine andere waehlen als der
-- unterbrochene Lauf.
CREATE TABLE IF NOT EXISTS automatik_lauf_kampagnen (
    lauf_id     INTEGER NOT NULL,
    campaign_id TEXT NOT NULL,
    position    INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'wartet',
    -- Wann die Gruppen dieser Kampagne in diesem Lauf neu bewertet wurden.
    -- Einmal je Kampagne und Lauf: Die Neubewertung laeuft ueber den ganzen
    -- Bestand und kostet Sekunden; bei jedem Schritt erneut waere sie die
    -- teuerste Zeile des Laufs. Leer heisst: steht noch aus - und danach
    -- richtet sich die Reihenfolge, in der gearbeitet wird.
    bewertet_am TEXT,
    PRIMARY KEY (lauf_id, campaign_id),
    FOREIGN KEY (lauf_id) REFERENCES automatik_lauf(lauf_id) ON DELETE CASCADE
);

-- Was in **diesem** Lauf uebersprungen wurde - und warum.
--
-- Die Fehlerisolierung je Gruppe: Scheitert eine Beitrittsanfrage, laesst
-- sich eine Gruppenseite nicht lesen, wirft die Textherstellung, bricht der
-- Browser mitten im Schritt ab - dann wird die Gruppe fuer diesen Lauf
-- beiseitegelegt, statt den Lauf zu beenden. Der naechste Lauf faengt ohne
-- diese Liste an (sie haengt an ``lauf_id``): Ein Fehler von heute ist kein
-- Urteil ueber morgen.
--
-- **Nicht dasselbe wie ``kommentar_erschoepft``.** Jenes sagt "diese Gruppe
-- gibt nichts mehr her" und gilt dauerhaft; hier steht "heute ging es nicht".
-- In eine Spalte gezwungen waere ein abgestuerzter Browser ein Urteil ueber
-- die Gruppe - genau die Verwechslung, an der am 11.09.2026 45 Gruppen als
-- erschoepft galten.
--
-- ``wiederholen_ab`` macht aus dem Beiseitelegen eine **Ruhezeit**
-- (20.09.2026). Vorher galt jeder Uebersprung fuer den ganzen Lauf, und bei
-- zwoelf Gruppen war der Lauf nach zwoelf Schritten zu Ende: "kein passender
-- Beitrag" in jeder von ihnen, 11 von 120 Kommentaren, fertig. "Heute steht
-- hier gerade nichts Passendes" ist aber keine Aussage ueber die naechste
-- Stunde - morgen frueh stehen dort andere Beitraege, und in einer halben
-- Stunde oft auch. Leer (NULL) heisst weiterhin: fuer diesen Lauf erledigt.
CREATE TABLE IF NOT EXISTS automatik_lauf_uebersprungen (
    lauf_id       INTEGER NOT NULL,
    campaign_id   TEXT NOT NULL,
    group_id      TEXT NOT NULL,
    grund         TEXT NOT NULL DEFAULT '',
    zeitpunkt     TEXT NOT NULL,
    wiederholen_ab TEXT,
    PRIMARY KEY (lauf_id, campaign_id, group_id),
    FOREIGN KEY (lauf_id) REFERENCES automatik_lauf(lauf_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_automatik_lauf_status
    ON automatik_lauf(status);

-- Die Bezuege je gelesenem Beitrag (23.09.2026) - die Grundlage, auf der
-- eine Gruppe seither beurteilt wird (``bezug.fuer_gruppe``).
--
-- **Kein Text, kein Autor**: ``bezuege`` haelt Schlagwoerter wie
-- ``reisender,mitnahme`` - das Urteil, nie den Satz. Dieselbe Grenze wie bei
-- ``group_posts``, das keine Textspalte hat.
--
-- Je Beitrag eine Zeile und nicht je Gruppe ein Zaehler: Derselbe Beitrag
-- wird in jedem Durchgang wieder gelesen, und ein Zaehler zaehlte ihn jedes
-- Mal mit. Eine Zeile je Adresse ueberschreibt sich selbst.
CREATE TABLE IF NOT EXISTS beitrag_bezuege (
    group_id    TEXT NOT NULL,
    post_url    TEXT NOT NULL,
    bezuege     TEXT NOT NULL DEFAULT '',
    gelesen_am  TEXT NOT NULL,
    PRIMARY KEY (group_id, post_url)
);
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
# ``post_entwuerfe`` stand hier einmal daneben: Textfassungen, die ein
# Sprachmodell geschrieben hatte. Die KI ist aus dem Projekt entfernt, und
# damit hat die Tabelle keinen Schreiber und keinen Leser mehr. Aus dem
# aktuellen Schema ist sie deshalb weg; in einer **bestehenden** Datei bleibt
# sie unberuehrt stehen. Migrationen sind hier ausschliesslich additiv - ein
# DROP loeschte Zeilen, die einmal Arbeit waren, und gewaenne nichts ausser
# ein paar Kilobyte.
#
# Die Job-Felder selbst liegen NICHT hier, sondern als Spalten an
# ``campaign_groups``: Ein Beitrag gehoert zum Paar aus Kampagne und Gruppe,
# und genau dieses Paar ist diese Tabelle. Eine zweite Tabelle daneben haette
# dieselbe Schluesselkombination und dieselbe Lebensdauer - sie waere eine
# Kopie mit dem Risiko, auseinanderzulaufen.
SCHEMA_POSTING = """
CREATE TABLE IF NOT EXISTS post_versuche (
    versuch_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id    TEXT NOT NULL,
    group_id       TEXT NOT NULL,
    texttyp        TEXT NOT NULL DEFAULT 'post',
    nummer         INTEGER NOT NULL DEFAULT 1,
    -- Mitgeschrieben statt nachgeschlagen: Der Code kann Jahre spaeter noch
    -- gebraucht werden, um einen veroeffentlichten Beitrag zuzuordnen, und
    -- die Zuordnung koennte bis dahin entfernt worden sein.
    tracking_code  TEXT NOT NULL DEFAULT '',
    job_status     TEXT NOT NULL DEFAULT 'processing',
    erfolg         INTEGER NOT NULL DEFAULT 0,
    post_url       TEXT NOT NULL DEFAULT '',
    fehler         TEXT NOT NULL DEFAULT '',
    -- Die Einstufung der Antwort von Facebook (qualifikation.Ablehnungsgrund):
    -- 'link_rejected', 'comment_requires_review', 'group_pending_limit' ...
    -- Sie steht **neben** dem Fehlertext, nicht statt seiner: Der Text ist
    -- der Beleg, die Einstufung die Auskunft. Leer bei Zeilen aus der Zeit
    -- vor Migrationsschritt 22 - nachtraeglich einzustufen hiesse, ein Urteil
    -- ueber Antworten zu faellen, die niemand mehr nachlesen kann.
    grund          TEXT NOT NULL DEFAULT '',
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

# Vierter Teil: die Textvorschlaege - mehrere Fassungen je Gruppe und Zweck.
#
# Bis hierher trug ``campaign_groups`` genau einen Beitrags- und einen
# Kommentartext. Das war die stillschweigende Behauptung, die Wahl der Vorlage
# sei schon getroffen - dabei ist sie das einzige, was ein Mensch beim
# Durchsehen wirklich entscheidet. Wer eine andere Fassung wollte, musste die
# vorhandene ueberschreiben, und die verworfene war weg.
#
# Warum hier doch eine eigene Tabelle, wo der Kommentar bewusst als **Spalten**
# an ``campaign_groups`` haengt: Der Kommentar ist *einer* je Paar, die
# Vorschlaege sind *viele*. Fuenf Fassungen mal zwei Zwecke waeren vierzig
# Spalten, und die sechste Fassung ein Schemawechsel. Die Regel ist nicht
# "keine zweite Tabelle", sondern "keine zweite Tabelle mit demselben
# Schluessel" - dieser Schluessel ist um Zweck und Nummer laenger.
#
# Die Spalten von ``campaign_groups`` bleiben unveraendert und werden
# weitergepflegt: Sie sind das Schaufenster fuer alles, was nach einem Text je
# Paar fragt (``campaign message``, ``queue``, ``beitragstext``, die
# Uebersicht). Was dort steht, ist die zuletzt bearbeitete oder
# veroeffentlichte Fassung - siehe ``spiegle_ins_paar``.
SCHEMA_VORSCHLAEGE = """
CREATE TABLE IF NOT EXISTS campaign_group_texte (
    campaign_id        TEXT NOT NULL,
    group_id           TEXT NOT NULL,
    -- 'post' | 'kommentar'. Beide Zwecke in einer Tabelle, weil sie sich bis
    -- auf den Vorrat, aus dem sie stammen, gleich verhalten.
    texttyp            TEXT NOT NULL,
    -- Position im Vorrat, ab 1. Keine Rangfolge: Fassung 1 ist nicht besser
    -- als Fassung 4, sie steht nur vorn.
    nummer             INTEGER NOT NULL,
    text               TEXT NOT NULL DEFAULT '',
    generated_text     TEXT NOT NULL DEFAULT '',
    vorlage_key        TEXT NOT NULL DEFAULT '',
    quelle             TEXT NOT NULL DEFAULT 'vorlage',
    -- 'entwurf' | 'gespeichert' | 'veroeffentlicht' | 'fehlgeschlagen'.
    -- Je Fassung, nicht je Gruppe - das ist der ganze Zweck der Tabelle.
    status             TEXT NOT NULL DEFAULT 'entwurf',
    generiert_am       TEXT,
    veroeffentlicht_am TEXT,
    versuche           INTEGER NOT NULL DEFAULT 0,
    fehler             TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (campaign_id, group_id, texttyp, nummer),
    FOREIGN KEY (campaign_id, group_id)
        REFERENCES campaign_groups(campaign_id, group_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_texte_paar
    ON campaign_group_texte(campaign_id, group_id);
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
# Staende, in denen ein Beitrag noch **nicht** freigegeben ist. Wer hierhin
# zurueckgeht, verliert seine Freigabe - sie gehoert zu einem Text, und der
# ist dann entweder weg oder ersetzt.
_VOR_DER_FREIGABE: frozenset[JobStatus] = frozenset(
    {JobStatus.DRAFT, JobStatus.AI_GENERATED, JobStatus.PENDING_REVIEW}
)

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


#: Welche Spalten zu welchem Einsatzzweck gehoeren.
#:
#: Eine Tabelle statt zweier Methodensaetze: ``set_post_text`` und
#: ``set_generierten_text`` gaebe es sonst zweimal, und die zweite Kopie waere
#: irgendwann die laxere - genau der Unterschied, der erst in einem
#: veroeffentlichten Beitrag auffaellt. Der Kommentar hat bewusst keine
#: Entsprechung fuer ``job_status``: Der Beitrag traegt den Ablauf.
_TEXTSPALTEN: dict[Texttyp, dict[str, str]] = {
    Texttyp.POST: {
        "text": "post_text",
        "erzeugt": "generated_text",
        "vorlage": "vorlage_key",
        "quelle": "text_quelle",
        "wann": "generiert_am",
    },
    Texttyp.KOMMENTAR: {
        "text": "kommentar_text",
        "erzeugt": "kommentar_generated",
        "vorlage": "kommentar_vorlage_key",
        "quelle": "kommentar_quelle",
        "wann": "kommentar_generiert_am",
    },
}


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
        self.conn.executescript(SCHEMA_VORSCHLAEGE)
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
                message_template, landing_page, ziel, status, starts_on, ends_on,
                target_audiences, target_cities, target_categories,
                target_statuses, target_prioritaeten, target_aktivitaet,
                target_min_score, target_include_unscored,
                auto_assign, kommentare, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(campaign_id) DO UPDATE SET
                name             = excluded.name,
                description      = excluded.description,
                audiences        = excluded.audiences,
                cities           = excluded.cities,
                language         = excluded.language,
                message_template = excluded.message_template,
                landing_page     = excluded.landing_page,
                ziel             = excluded.ziel,
                status           = excluded.status,
                starts_on        = excluded.starts_on,
                ends_on          = excluded.ends_on,
                target_audiences        = excluded.target_audiences,
                target_cities           = excluded.target_cities,
                target_categories       = excluded.target_categories,
                target_statuses         = excluded.target_statuses,
                target_prioritaeten     = excluded.target_prioritaeten,
                target_aktivitaet       = excluded.target_aktivitaet,
                target_min_score        = excluded.target_min_score,
                target_include_unscored = excluded.target_include_unscored,
                auto_assign             = excluded.auto_assign,
                kommentare              = excluded.kommentare,
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
                campaign.ziel,
                campaign.status.value,
                _iso(campaign.starts_on),
                _iso(campaign.ends_on),
                json.dumps(campaign.target_audiences, ensure_ascii=False),
                json.dumps(campaign.target_cities, ensure_ascii=False),
                json.dumps(campaign.target_categories, ensure_ascii=False),
                json.dumps(campaign.target_statuses, ensure_ascii=False),
                json.dumps(campaign.target_prioritaeten, ensure_ascii=False),
                json.dumps(campaign.target_aktivitaet, ensure_ascii=False),
                campaign.target_min_score,
                int(campaign.target_include_unscored),
                int(campaign.auto_assign),
                # Konstante 1: Die Kampagne entscheidet das nicht mehr.
                1,
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

    def was_geht_verloren(self, campaign_id: str) -> dict[str, int]:
        """Was ein Loeschen dieser Kampagne mitnimmt.

        Getrennt vom Loeschen, damit Vorschau und Ernstfall dieselbe Zahl
        nennen - dieselbe Ueberlegung wie bei ``zaehle_zuruecksetzbar``.

        Der wichtigste Wert ist ``veroeffentlichte_codes``: So viele
        Tracking-Codes stehen in Beitraegen, die jemand wirklich abgesetzt hat.
        Nach dem Loeschen antwortet ``/r/{code}`` fuer sie mit 404 - der Link
        im Facebook-Beitrag fuehrt ins Leere, und zurueckholen laesst sich der
        Beitrag nicht.
        """

        def eins(sql: str) -> int:
            row = self.conn.execute(sql, (campaign_id,)).fetchone()
            return int(row[0] or 0)

        return {
            "zuordnungen": eins(
                "SELECT COUNT(*) FROM campaign_groups WHERE campaign_id = ?"
            ),
            "veroeffentlichte_codes": eins(
                "SELECT COUNT(*) FROM campaign_groups "
                "WHERE campaign_id = ? AND posted_at IS NOT NULL"
            ),
            "versuche": eins("SELECT COUNT(*) FROM post_versuche WHERE campaign_id = ?"),
            # Bleiben stehen - sie haengen an keinem Fremdschluessel. Die Zahlen
            # einer Auswertung von gestern aendern sich durch das Loeschen also
            # nicht; nur der Weg vom Code zurueck zur Gruppe ist danach weg.
            "ereignisse_bleiben": eins(
                "SELECT COUNT(*) FROM tracking_events WHERE campaign_id = ?"
            ),
        }

    def delete_campaign(self, campaign_id: str) -> int:
        """Loescht eine Kampagne - **samt ihrer Zuordnungen und Codes**.

        ``ON DELETE CASCADE`` an ``campaign_groups`` nimmt jeden Tracking-Code
        dieser Kampagne mit. Steht einer davon in einem veroeffentlichten
        Beitrag, fuehrt der Link dort danach ins Leere (404). Der Aufrufer soll
        deshalb vorher ``was_geht_verloren`` zeigen; die Zahl der
        veroeffentlichten Codes ist die einzige, die sich nicht
        wiederherstellen laesst.

        Die Ereignisse bleiben: Sie haengen an keinem Fremdschluessel. Eine
        Auswertung von gestern behaelt damit ihre Zahlen - was fehlt, ist der
        Weg vom Code zurueck zur Gruppe.
        """
        # SQLite prueft Fremdschluessel nur mit eingeschaltetem PRAGMA. Ohne
        # das bliebe campaign_groups stehen, und die Codes waeren Waisen: nicht
        # aufloesbar, aber weiterhin vergeben - der naechste Lauf koennte
        # dieselbe Nummer ein zweites Mal ausgeben.
        self.conn.execute("PRAGMA foreign_keys = ON")
        cursor = self.conn.execute("DELETE FROM campaigns WHERE campaign_id = ?", (campaign_id,))
        self.audit("kampagne_geloescht", campaign_id)
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
        # Der oeffentliche Deckname entsteht mit der Zuordnung. Das ist kein
        # Code auf Vorrat: Der Tracking-Code, dessen Deckname er ist, steht in
        # derselben Zeile und ist damit bereits endgueltig. Wer ihn erst beim
        # ersten Beitrag vergaebe, haette dieselbe Adresse nur spaeter - und
        # eine Gruppe, die durch eine vergessene Stelle rutscht, bekaeme
        # wieder die lange.
        self.vergib_kurzcodes(link.campaign_id, link.group_id)
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
        # Die Decknamen zu den eben entstandenen Zuordnungen. Ausserhalb der
        # Transaktion und ueber die Kampagnen, die gerade angefasst wurden:
        # Was schon einen hat, wird uebergangen, also kostet der Aufruf nur
        # fuer die neuen Zeilen etwas.
        for campaign_id in campaign_ids:
            self.kurzcodes_nachtragen(campaign_id)
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
            # Alle vier Adressen desselben Paares ziehen mit - die beiden
            # inneren und die beiden oeffentlichen. Nur die Haelfte
            # umzustellen hiesse, dass ein Beitrag je nach Alter auf zwei
            # verschiedene Dienste zeigt, und gemerkt haette man es an einer
            # Zahl, die nicht mehr steigt. Die **Codes** bleiben, wie sie
            # sind; es wechselt nur ihr Vorspann.
            for spalte, code, alt_url in (
                ("tracking_url", link.tracking_code, link.tracking_url),
                ("tracking_url_browser", link.tracking_code_browser, link.tracking_url_browser),
                ("public_url", link.public_code, link.public_url),
                ("public_url_browser", link.public_code_browser, link.public_url_browser),
            ):
                if not code:
                    continue
                neu = basis_url_bauer(code)
                if neu == alt_url:
                    continue
                self.conn.execute(
                    f"UPDATE campaign_groups SET {spalte} = ? "  # noqa: S608
                    "WHERE campaign_id = ? AND group_id = ?",
                    (neu, link.campaign_id, link.group_id),
                )
                if spalte == "tracking_url":
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

    def links_zum_bearbeiten(self, campaign_id: str) -> list[CampaignGroup]:
        """Alle Zuordnungen, an denen gearbeitet wird - erledigte eingeschlossen.

        Die Liste der **Arbeitsseite**, und der Unterschied zu ``offene_links``
        ist der Kern der gruppenweisen Arbeitsweise: Eine Gruppe verschwindet
        nicht mehr, sobald ihr erster Beitrag draussen steht. Sie hat fuenf
        Fassungen, und die zweite kann Wochen spaeter drankommen; wer sie dann
        sucht, findet sie in einer Liste, die nur Offenes zeigt, nicht mehr
        wieder.

        Ausgeschlossen bleibt genau das, was ein Mensch ausgeschlossen hat
        (``bearbeiten = 0``) - und ``uebersprungen``, denn "passt nicht" ist
        ein Urteil ueber die Gruppe und keine offene Aufgabe. Beide behalten
        ihren Tracking-Code: Er steht moeglicherweise in einem
        veroeffentlichten Beitrag, und ein Klick darauf muss ankommen.

        Sortiert wird hier nach ``tracking_code`` - also stabil, aber
        fachlich beliebig. Die Rangfolge nach Score setzt
        ``arbeit.arbeitsreihenfolge``, weil dafuer der Gruppenbestand
        gebraucht wird und dieser Speicher ihn nicht kennt.
        """
        rows = self.conn.execute(
            """
            SELECT cg.* FROM campaign_groups AS cg
            LEFT JOIN group_marketing AS gm ON gm.group_id = cg.group_id
             WHERE cg.campaign_id = ?
               AND cg.post_status <> 'uebersprungen'
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
        ausgehen als beim ersten, steht aber jedes Mal wieder in der Liste und
        verdeckt die erreichbaren Gruppen. ``0`` schaltet die Grenze ab; die
        Zahl steht in der Konfiguration und nicht hier.

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

    #: Welche Fassung ein ``reset`` anfasst - **eine** Bedingung fuer Zaehlung
    #: und Ausfuehrung.
    #:
    #: Getrennt formuliert liefe es auf dasselbe hinaus wie zwei Zaehlungen:
    #: Die Vorschau naennte eine Zahl, und zurueckgesetzt wuerde eine andere
    #: Menge. Angefasst wird, was einen **Ausgang** hinter sich hat -
    #: veroeffentlicht, fehlgeschlagen oder mit gezaehltem Versuch. Ein
    #: blosser Entwurf und ein von Hand geschriebener, noch nicht abgesetzter
    #: Text stehen nicht darin: Bei ihnen gibt es nichts zurueckzunehmen.
    _AUSGANG_JE_FASSUNG = (
        "(status IN ('veroeffentlicht', 'fehlgeschlagen') OR versuche > 0)"
    )

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
            "fassungen": eins(
                "SELECT COUNT(*) FROM campaign_group_texte WHERE campaign_id = ? "  # noqa: S608
                f"AND {self._AUSGANG_JE_FASSUNG}",
                campaign_id,
            ),
            "erschoepft": eins(
                "SELECT COUNT(*) FROM campaign_groups WHERE campaign_id = ? "
                "AND kommentar_erschoepft = 1",
                campaign_id,
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

        Zurueckgenommen wird der Stand auf **beiden** Ebenen: am Paar
        (``campaign_groups``) und an jeder einzelnen Fassung
        (``campaign_group_texte``). Nur die zweite ist die, die man auf der
        Arbeitsseite sieht - die Haken ueber den fuenf Beitraegen und den zehn
        Kommentaren stehen dort, und der Fortschritt der Kommentarautomatik
        wird aus genau dieser Spalte **gelesen**. Bliebe sie stehen, meldete
        eine zurueckgesetzte Kampagne beim naechsten Lauf weiterhin "erledigt",
        und ein Testlauf waere nicht zu wiederholen.

        **Die Texte selbst bleiben stehen** - auf beiden Ebenen. Ein Reset
        nimmt den Ausgang zurueck, nicht die Arbeit: Was von Hand geschrieben
        wurde, geht auf ``gespeichert`` zurueck und nicht auf ``entwurf``,
        sonst waere die Unterscheidung "durchgelesen und behalten" mit dem
        Stand verloren.

        Ebenfalls zurueckgenommen wird ``kommentar_erschoepft``. Es ist das
        Urteil "diese Gruppe gibt nichts mehr her" **nach** einem Lauf; nach
        einem Reset gibt es diesen Lauf nicht mehr, und die Gruppe waere sonst
        fuer immer uebersprungen, ohne dass etwas gegen sie spraeche.

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
                   post_error      = '',
                   kommentar_erschoepft       = 0,
                   kommentar_erschoepft_grund = '',
                   kommentar_erschoepft_am    = NULL
             WHERE campaign_id = ?
            """,
            (campaign_id,),
        )

        # Jede einzelne Fassung: der Haken faellt, der Text bleibt. ``quelle``
        # entscheidet ueber das Ziel - dieselbe Regel wie in
        # ``setze_vorschlag_text``, nur andersherum gelesen: Was ein Mensch
        # angefasst hat, ist "gespeichert"; was aus der Vorlage kam, ist wieder
        # ein Entwurf.
        self.conn.execute(
            "UPDATE campaign_group_texte "  # noqa: S608
            "   SET status = CASE WHEN quelle = 'hand' THEN 'gespeichert' "
            "                     ELSE 'entwurf' END, "
            "       versuche = 0, "
            "       veroeffentlicht_am = NULL, "
            "       fehler = '' "
            f" WHERE campaign_id = ? AND {self._AUSGANG_JE_FASSUNG}",
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
                last_posted_at, bearbeiten, ausschlussgrund, notes,
                regeln_gelesen_am, regel_keine_links, regel_keine_werbung,
                regel_freigabe_noetig, regel_neue_ohne_links, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                -- Ein gelesener Regelbefund wird von einem Eintrag ohne
                -- Befund nicht geloescht: Wer den Arbeitsstand speichert,
                -- hat die Gruppenseite nicht abgerufen. Dieselbe Regel wie
                -- bei ``upsert_groups`` und COALESCE - eine fehlende Angabe
                -- ist kein Beleg dafuer, dass die vorhandene falsch war.
                regeln_gelesen_am     = COALESCE(
                    excluded.regeln_gelesen_am, group_marketing.regeln_gelesen_am
                ),
                regel_keine_links     = CASE WHEN excluded.regeln_gelesen_am IS NULL
                    THEN group_marketing.regel_keine_links
                    ELSE excluded.regel_keine_links END,
                regel_keine_werbung   = CASE WHEN excluded.regeln_gelesen_am IS NULL
                    THEN group_marketing.regel_keine_werbung
                    ELSE excluded.regel_keine_werbung END,
                regel_freigabe_noetig = CASE WHEN excluded.regeln_gelesen_am IS NULL
                    THEN group_marketing.regel_freigabe_noetig
                    ELSE excluded.regel_freigabe_noetig END,
                regel_neue_ohne_links = CASE WHEN excluded.regeln_gelesen_am IS NULL
                    THEN group_marketing.regel_neue_ohne_links
                    ELSE excluded.regel_neue_ohne_links END,
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
                _iso(eintrag.regeln_gelesen_am),
                int(eintrag.regel_keine_links),
                int(eintrag.regel_keine_werbung),
                int(eintrag.regel_freigabe_noetig),
                int(eintrag.regel_neue_ohne_links),
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
        if neu in _VOR_DER_FREIGABE:
            # Eine zurueckgenommene Freigabe ist keine Freigabe mehr. Bliebe der
            # Zeitpunkt stehen, sagte die Zeile "freigegeben am ...", waehrend
            # sie auf Pruefung wartet.
            #
            # Die Regel gilt fuer **jeden** Weg zurueck, nicht nur fuer
            # ``approved -> pending_review``: Auch ein neu gefuellter oder von
            # Hand ueberschriebener Text schickt einen freigegebenen Job ueber
            # ``draft`` zurueck. Blieb der Zeitpunkt dabei stehen, trug ein
            # Entwurf die Freigabe eines Textes, den es nicht mehr gibt.
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
        texttyp: Texttyp = Texttyp.POST,
    ) -> CampaignGroup:
        """Legt den laufenden Text ab - den, der spaeter wirklich hinausgeht.

        Bewusst ohne Statuswechsel als Vorgabe: Text schreiben und Text
        freigeben sind zwei Handlungen, und die zweite gehoert einem Menschen.

        ``texttyp`` waehlt die Spalten (``_TEXTSPALTEN``). Der Vorgabewert
        haelt jeden bestehenden Aufruf unveraendert - ein Beitrag bleibt ein
        Beitrag, auch wenn es den Kommentar jetzt gibt. Ein ``neuer_status``
        gilt allein fuer den Beitrag: ``JobStatus`` beschreibt dessen
        Vorbereitung, und ein Kommentar wird nicht eingereiht.
        """
        spalten = _TEXTSPALTEN[texttyp]
        if neuer_status is not None and texttyp is not Texttyp.POST:
            raise ValueError("Ein Kommentar hat keinen JobStatus - der Ablauf gehoert dem Beitrag.")
        self.conn.execute(
            f"UPDATE campaign_groups SET {spalten['text']} = ?, "  # noqa: S608
            f"{spalten['quelle']} = ?, {spalten['wann']} = ? "
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

    def set_generierten_text(
        self,
        campaign_id: str,
        group_id: str,
        *,
        text: str,
        vorlage_key: str,
        uebernehmen: bool,
        texttyp: Texttyp = Texttyp.POST,
    ) -> CampaignGroup:
        """Legt den erzeugten Text ab - und auf Wunsch auch als laufenden Text.

        Beides in einer Anweisung, damit die zwei Felder nie halb geschrieben
        nebeneinanderstehen: Ein ``generated_text`` ohne den zugehoerigen
        ``post_text`` saehe aus wie ein Text, den jemand verworfen hat.

        ``uebernehmen=False`` ist der Fall "es steht schon etwas da". Der
        laufende Text kann von Hand ueberarbeitet oder freigegeben sein, und
        ein erneutes Fuellen darf diese Arbeit nicht beilaeufig ueberschreiben -
        dieselbe Regel wie bei ``upsert_groups`` und den Notizen. Der erzeugte
        Text wird trotzdem aufgefrischt: Er ist die Vergleichsgroesse, und eine
        veraltete Vergleichsgroesse ist schlechter als gar keine.
        """
        jetzt = _iso(datetime.now(UTC))
        spalten = _TEXTSPALTEN[texttyp]
        if uebernehmen:
            self.conn.execute(
                f"UPDATE campaign_groups SET {spalten['erzeugt']} = ?, "  # noqa: S608
                f"{spalten['vorlage']} = ?, {spalten['text']} = ?, "
                f"{spalten['quelle']} = ?, {spalten['wann']} = ? "
                "WHERE campaign_id = ? AND group_id = ?",
                (text, vorlage_key, text, TextQuelle.VORLAGE.value, jetzt,
                 campaign_id, group_id),
            )
        else:
            self.conn.execute(
                f"UPDATE campaign_groups SET {spalten['erzeugt']} = ?, "  # noqa: S608
                f"{spalten['vorlage']} = ? "
                "WHERE campaign_id = ? AND group_id = ?",
                (text, vorlage_key, campaign_id, group_id),
            )
        self.conn.commit()
        link = self.link_for(campaign_id, group_id)
        if link is None:
            raise UnknownGroupError(f"{campaign_id}/{group_id}")
        return link

    # -- Die Textvorschlaege ------------------------------------------------
    #
    # Fuenf Fassungen je Gruppe und Zweck, jede mit eigenem Stand. Die
    # Methoden hier sind die einzige Stelle, an der ``campaign_group_texte``
    # geschrieben wird - dieselbe Ueberlegung wie bei ``set_job_status``: eine
    # Regel, die an drei Stellen gepflegt wird, gilt bald an zweien.

    def vorschlaege(
        self,
        campaign_id: str,
        group_id: str,
        texttyp: Texttyp | None = None,
    ) -> list[Textvorschlag]:
        """Alle Fassungen dieses Paares, nach Nummer geordnet.

        Ohne ``texttyp`` kommen beide Zwecke - der Aufrufer trennt sie dann
        selbst. Mit ``texttyp`` genau ein Topf, und das ist der Regelfall:
        Die Arbeitsseite fragt je Spalte einmal.
        """
        if texttyp is None:
            rows = self.conn.execute(
                "SELECT * FROM campaign_group_texte WHERE campaign_id = ? "
                "AND group_id = ? ORDER BY texttyp, nummer",
                (campaign_id, group_id),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM campaign_group_texte WHERE campaign_id = ? "
                "AND group_id = ? AND texttyp = ? ORDER BY nummer",
                (campaign_id, group_id, texttyp.value),
            ).fetchall()
        return [self._row_to_vorschlag(row) for row in rows]

    def vorschlag(
        self, campaign_id: str, group_id: str, texttyp: Texttyp, nummer: int
    ) -> Textvorschlag | None:
        """Genau eine Fassung. ``None``, wenn es sie nicht gibt."""
        row = self.conn.execute(
            "SELECT * FROM campaign_group_texte WHERE campaign_id = ? "
            "AND group_id = ? AND texttyp = ? AND nummer = ?",
            (campaign_id, group_id, texttyp.value, nummer),
        ).fetchone()
        return self._row_to_vorschlag(row) if row else None

    def setze_erzeugten_vorschlag(
        self,
        campaign_id: str,
        group_id: str,
        texttyp: Texttyp,
        nummer: int,
        *,
        text: str,
        vorlage_key: str,
        ueberschreiben: bool = False,
    ) -> Textvorschlag:
        """Legt eine erzeugte Fassung ab - ohne Handarbeit zu ueberschreiben.

        Dieselbe Regel wie bei ``set_generierten_text`` am Paar: Der
        **erzeugte** Text wird immer aufgefrischt (eine veraltete
        Vergleichsgroesse ist schlechter als keine), der **laufende** nur
        dann, wenn dort noch nichts steht oder ``ueberschreiben`` es
        ausdruecklich verlangt.

        Der Stand einer Fassung wird dabei nie zurueckgedreht: Was
        veroeffentlicht ist, steht in der Gruppe, und ein erneutes Fuellen
        macht daraus keinen Entwurf mehr. ``ueberschreiben`` gilt deshalb fuer
        den Text, nicht fuer die Geschichte.
        """
        jetzt = _iso(datetime.now(UTC))
        vorhanden = self.vorschlag(campaign_id, group_id, texttyp, nummer)

        if vorhanden is None:
            self.conn.execute(
                "INSERT INTO campaign_group_texte (campaign_id, group_id, texttyp, "
                "nummer, text, generated_text, vorlage_key, quelle, status, "
                "generiert_am) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (campaign_id, group_id, texttyp.value, nummer, text, text,
                 vorlage_key, TextQuelle.VORLAGE.value,
                 VorschlagStatus.ENTWURF.value, jetzt),
            )
        elif ueberschreiben or not vorhanden.text.strip():
            # Ein veroeffentlichter Vorschlag behaelt seinen Stand - der Text
            # steht in der Gruppe, und "Entwurf" waere eine Falschaussage.
            stand = (
                vorhanden.status
                if vorhanden.status
                in (VorschlagStatus.VEROEFFENTLICHT, VorschlagStatus.FEHLGESCHLAGEN)
                else VorschlagStatus.ENTWURF
            )
            self.conn.execute(
                "UPDATE campaign_group_texte SET text = ?, generated_text = ?, "
                "vorlage_key = ?, quelle = ?, status = ?, generiert_am = ? "
                "WHERE campaign_id = ? AND group_id = ? AND texttyp = ? AND nummer = ?",
                (text, text, vorlage_key, TextQuelle.VORLAGE.value, stand.value,
                 jetzt, campaign_id, group_id, texttyp.value, nummer),
            )
        else:
            self.conn.execute(
                "UPDATE campaign_group_texte SET generated_text = ?, vorlage_key = ? "
                "WHERE campaign_id = ? AND group_id = ? AND texttyp = ? AND nummer = ?",
                (text, vorlage_key, campaign_id, group_id, texttyp.value, nummer),
            )
        self.conn.commit()

        # Das **Paar** wird hier bewusst nicht angefasst. Wer die Fassungen
        # einer Gruppe fuellt, schreibt Platz 1 ausdruecklich ueber
        # ``set_generierten_text`` ans Paar (``arbeit.stelle_texte_bereit``) -
        # dort steht die Regel, wann ein laufender Text uebernommen wird, und
        # sie ein zweites Mal hier zu haben hiesse, sie zweimal zu pflegen.
        neu = self.vorschlag(campaign_id, group_id, texttyp, nummer)
        if neu is None:  # pragma: no cover - gerade geschrieben
            raise UnknownGroupError(f"{campaign_id}/{group_id}")
        return neu

    def setze_vorschlag_text(
        self,
        campaign_id: str,
        group_id: str,
        texttyp: Texttyp,
        nummer: int,
        text: str,
        quelle: TextQuelle = TextQuelle.HAND,
    ) -> Textvorschlag:
        """Speichert **genau diese** Fassung - keine der vier anderen.

        Der Kern der Forderung "beim Speichern darf nur dieser konkrete
        Vorschlag gespeichert werden": Der Schluessel traegt die Nummer, und
        die Anweisung fasst keine Zeile ohne sie an.

        Der Stand geht auf ``gespeichert``, wenn die Fassung noch nirgends
        hingegangen ist. Ein veroeffentlichter Vorschlag behaelt seinen Haken:
        Der Text in der Gruppe aendert sich nicht dadurch, dass jemand hier
        etwas umschreibt - er waere sonst als erledigt markiert und traege
        einen anderen Wortlaut, als wirklich draussen steht.
        """
        vorhanden = self.vorschlag(campaign_id, group_id, texttyp, nummer)
        if vorhanden is None:
            self.conn.execute(
                "INSERT INTO campaign_group_texte (campaign_id, group_id, texttyp, "
                "nummer, text, quelle, status, generiert_am) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (campaign_id, group_id, texttyp.value, nummer, text, quelle.value,
                 VorschlagStatus.GESPEICHERT.value, _iso(datetime.now(UTC))),
            )
        else:
            stand = (
                vorhanden.status
                if vorhanden.status is VorschlagStatus.VEROEFFENTLICHT
                else VorschlagStatus.GESPEICHERT
            )
            self.conn.execute(
                "UPDATE campaign_group_texte SET text = ?, quelle = ?, status = ? "
                "WHERE campaign_id = ? AND group_id = ? AND texttyp = ? AND nummer = ?",
                (text, quelle.value, stand.value,
                 campaign_id, group_id, texttyp.value, nummer),
            )
        self.conn.commit()
        self._spiegle_ins_paar(campaign_id, group_id, texttyp, text, "", quelle)
        neu = self.vorschlag(campaign_id, group_id, texttyp, nummer)
        if neu is None:  # pragma: no cover - gerade geschrieben
            raise UnknownGroupError(f"{campaign_id}/{group_id}")
        return neu

    def vorschlag_zuruecksetzen(
        self, campaign_id: str, group_id: str, texttyp: Texttyp, nummer: int
    ) -> Textvorschlag | None:
        """Holt den erzeugten Text dieser einen Fassung zurueck.

        Der Ausweg aus einer misslungenen Ueberarbeitung - genau dafuer steht
        ``generated_text`` neben ``text``. Ist nichts erzeugt worden, bleibt
        alles stehen: Ein leerer Text waere schlimmer als ein unpassender.
        """
        vorhanden = self.vorschlag(campaign_id, group_id, texttyp, nummer)
        if vorhanden is None or not vorhanden.generated_text.strip():
            return vorhanden
        return self.setze_vorschlag_text(
            campaign_id, group_id, texttyp, nummer,
            vorhanden.generated_text, TextQuelle.VORLAGE,
        )

    def setze_vorschlag_stand(
        self,
        campaign_id: str,
        group_id: str,
        texttyp: Texttyp,
        nummer: int,
        stand: VorschlagStatus,
        *,
        fehler: str = "",
    ) -> Textvorschlag | None:
        """Traegt den Ausgang **einer** Fassung ein. Returns: die neue Fassung.

        ``versuche`` zaehlt jeden gemeldeten Ausgang mit, auch den Erfolg -
        dieselbe Bedeutung wie ``post_attempts`` am Paar.
        ``veroeffentlicht_am`` wird dagegen nur beim **ersten** Erfolg
        gesetzt: Die Klicks gehen auf den Beitrag zurueck, der zuerst stand.

        Ein Erfolg loescht den Fehlergrund. Bliebe er stehen, stuende neben
        einer veroeffentlichten Fassung der Grund, aus dem sie beim vorletzten
        Mal nicht ging.
        """
        vorhanden = self.vorschlag(campaign_id, group_id, texttyp, nummer)
        if vorhanden is None:
            return None
        jetzt = _iso(datetime.now(UTC))
        erfolg = stand is VorschlagStatus.VEROEFFENTLICHT
        zaehlt = stand in (
            VorschlagStatus.VEROEFFENTLICHT,
            VorschlagStatus.FEHLGESCHLAGEN,
        )
        self.conn.execute(
            "UPDATE campaign_group_texte SET status = ?, fehler = ?, "
            "versuche = ?, veroeffentlicht_am = ? "
            "WHERE campaign_id = ? AND group_id = ? AND texttyp = ? AND nummer = ?",
            (
                stand.value,
                "" if erfolg else fehler,
                vorhanden.versuche + (1 if zaehlt else 0),
                jetzt if (erfolg and vorhanden.veroeffentlicht_am is None)
                else _iso(vorhanden.veroeffentlicht_am),
                campaign_id, group_id, texttyp.value, nummer,
            ),
        )
        self.conn.commit()

        # Was veroeffentlicht wurde, ist der Text, der wirklich draussen steht -
        # er gehoert ins Schaufenster des Paares, damit ``campaign message``
        # und die Uebersicht ihn zeigen und nicht eine verworfene Fassung.
        if erfolg and vorhanden.text.strip():
            self._spiegle_ins_paar(
                campaign_id, group_id, texttyp, vorhanden.text,
                vorhanden.vorlage_key, vorhanden.quelle,
            )
        return self.vorschlag(campaign_id, group_id, texttyp, nummer)

    def verwendete_vorlagen(self, group_id: str, texttyp: str) -> set[str]:
        """Welche Vorlagenschluessel in dieser Gruppe schon hinausgingen.

        Ueber **alle** Kampagnen, denn die Leser einer Gruppe unterscheiden
        sie nicht: Zweimal derselbe Satz ist zweimal derselbe Satz, gleich
        unter welcher Kampagne er gebucht wurde.

        Die Duplikatkontrolle auf **Textebene** (Punkt 17 der Anforderung).
        Die auf Beitragsebene steht daneben (``bisherige_post_urls``), und
        die auf Personenebene gibt es nicht: Sie braeuchte Autorennamen, und
        die speichert dieses Projekt nicht. Was an ihrer Stelle wirkt, ist
        ``limits.comments.je_gruppe_taeglich``.
        """
        rows = self.conn.execute(
            "SELECT DISTINCT vorlage_key FROM campaign_group_texte "
            "WHERE group_id = ? AND texttyp = ? AND status = ? AND vorlage_key <> ''",
            (group_id, str(texttyp), VorschlagStatus.VEROEFFENTLICHT.value),
        ).fetchall()
        return {row["vorlage_key"] for row in rows}

    def merke_verwendeten_text(
        self,
        campaign_id: str,
        group_id: str,
        texttyp: str,
        nummer: int,
        text: str,
        vorlage_key: str,
    ) -> None:
        """Haelt fest, welcher Text wirklich hinausging - **vor** der Buchung.

        Der Lauf waehlt seinen Kommentar seit dem 13.09.2026 erst, wenn er
        den Beitrag kennt (``vorlagen.anlasstext``): Was in der Fassung
        vorbereitet stand, ist dann nicht mehr das, was in der Gruppe steht.
        Ohne diese Zeile stuenden zwei verschiedene Texte fuer denselben
        Vorgang im Bestand, und die Uebersicht zeigte den, der nie abgesetzt
        wurde.

        Geschrieben wird nur, wenn die Fassung existiert - angelegt wird hier
        nichts: Eine Zeile ohne Zuordnung waere ein Beitrag ohne Kampagne.
        """
        self.conn.execute(
            "UPDATE campaign_group_texte SET text = ?, vorlage_key = ? "
            "WHERE campaign_id = ? AND group_id = ? AND texttyp = ? AND nummer = ?",
            (text, vorlage_key, campaign_id, group_id, str(texttyp), int(nummer)),
        )
        self.conn.commit()

    def versuche_heute_je_gruppe(self, tag: str, texttyp: str = "") -> dict[str, int]:
        """Je Gruppe: wie viele Versuche an diesem Tag - ueber alle Kampagnen.

        Die Gegenseite kennt kein Kampagnenmodell, und die Leser einer Gruppe
        erst recht nicht: Zwei Kampagnen mit je einem Kommentar sind fuer sie
        zwei Kommentare von demselben Konto am selben Tag. Deshalb wird ueber
        alle Kampagnen gezaehlt, genau wie in ``versuche_heute``.

        **Gezaehlt werden Erfolge, nicht Versuche** (20.09.2026, Regel 3/4 des
        Nutzers). Bis dahin zaehlte jeder Versuch ausser dem technischen
        Fehlschlag, und die Begruendung dafuer war: Ein abgelehnter Kommentar
        sei trotzdem einer gewesen, den die Gruppe gesehen hat. Das trifft auf
        die Moderation zu und auf sonst nichts - ein Kommentar, den Facebook
        gar nicht angenommen hat, stand nie dort. Die Tagesmenge einer Gruppe
        fuer etwas zu verbrauchen, das ihre Leser nie gesehen haben, ist
        dieselbe Art Verwechslung, an der am 14.09.2026 24 Gruppen mit je
        einem Kommentar im Bericht standen.

        Der Schutz geht dadurch nicht verloren, er wandert nur: Was die Gruppe
        wirklich ablehnt, steht in ``qualifikation.Beobachtung`` und
        beschraenkt sie dort; was das Konto bremst, faengt
        ``Ausgangsart.RATE_LIMIT`` mit seinem Backoff ab.

        Zurueck kommt ein Wortverzeichnis statt einer Einzelabfrage je
        Gruppe: Der Lauf fragt den Stand fuer dreihundert Gruppen auf einmal,
        und dreihundert Abfragen waeren derselbe Fehler wie eine Zaehlung je
        Vergleich bei der Codevergabe.
        """
        nur_erfolge = "AND erfolg = 1"
        if texttyp:
            rows = self.conn.execute(
                "SELECT group_id, COUNT(*) AS n FROM post_versuche "  # noqa: S608
                f"WHERE substr(begonnen_am, 1, 10) = ? AND texttyp = ? {nur_erfolge} "
                "GROUP BY group_id",
                (tag, str(texttyp)),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT group_id, COUNT(*) AS n FROM post_versuche "  # noqa: S608
                f"WHERE substr(begonnen_am, 1, 10) = ? {nur_erfolge} GROUP BY group_id",
                (tag,),
            ).fetchall()
        return {row["group_id"]: int(row["n"]) for row in rows}

    def versuche_heute(self, tag: str, texttyp: str = "") -> int:
        """Wie viele Beitraege an diesem Tag hinausgingen - ueber **alle** Kampagnen.

        Ueber alle, weil die Gegenseite kein Kampagnenmodell hat: Gesperrt wird
        das Konto, und dem ist gleich, unter welcher Kampagne ein Beitrag stand.
        Zwei Kampagnen mit je zwanzig Beitraegen sind vierzig Beitraege an einem
        Tag.

        **Gezaehlt werden Erfolge, nicht Versuche** (20.09.2026, Regel 3/4 des
        Nutzers). Bis dahin zaehlte jeder Versuch, und die Begruendung war:
        Ein fehlgeschlagener Beitrag sei trotzdem einer gewesen, den die
        Gegenseite gesehen hat - nach zwanzig Fehlschlaegen mit voller Portion
        weiterzumachen sei leichtsinnig.

        **Der Einwand bleibt richtig und ist woanders aufgehoben.** Genau
        dafuer gibt es ``Ausgangsart.RATE_LIMIT``: Sagt Facebook selbst, dass
        es zu viel wird, pausiert die Aktion mit einem Backoff, der sich
        verdoppelt und den Neustart ueberlebt. Und ``_Technikwaechter``
        beendet den Lauf, wenn der Rechner selbst nicht mehr mitmacht. Was
        hier entfaellt, ist allein das *stille* Verbrauchen der Tagesmenge
        durch etwas, das nie in einer Gruppe stand.

        ``tag`` ist ein ISO-Datum (``2026-08-29``); ``begonnen_am`` ist ein
        ISO-Zeitstempel, dessen erste zehn Zeichen genau das sind. Der Index
        ``idx_versuche_zeit`` traegt die Abfrage.
        """
        if texttyp:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM post_versuche "
                "WHERE substr(begonnen_am, 1, 10) = ? AND texttyp = ? AND erfolg = 1",
                (tag, str(texttyp)),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM post_versuche "
                "WHERE substr(begonnen_am, 1, 10) = ? AND erfolg = 1",
                (tag,),
            ).fetchone()
        return int(row[0]) if row else 0

    def letzter_versuch(self, texttyp: str = "") -> str:
        """Zeitstempel des juengsten **erfolgreichen** Versuchs, oder ''.

        Grundlage des Mindestabstands. Ueber alle Kampagnen: Der Abstand gilt
        dem Konto, nicht der Kampagne.

        **Nur Erfolge** (20.09.2026, Regel 7/8 des Nutzers). Bis dahin setzte
        jeder Versuch die Uhr, auch ein gescheiterter - und das war der
        teuerste Leerlauf des Laufs: Ein Kommentar, den Facebook nicht
        annahm, hielt den naechsten volle zehn bis zwanzig Minuten auf,
        obwohl nichts hinausgegangen war. Bei einer Gruppe, in der gerade
        nichts geht, wartete der Lauf so eine Viertelstunde je Fehlschlag
        und schrieb in einer Stunde keinen einzigen Kommentar.

        Der Takt ist der Abstand zwischen zwei Dingen, die **in einer Gruppe
        stehen**. Wo nichts steht, gibt es nichts abzuwarten; der Lauf geht
        sofort zur naechsten Gruppe weiter.

        ``texttyp`` schraenkt auf eine Aktion ein (seit 12.09.2026). Ein
        gemeinsamer Abstand fuer Beitrag und Kommentar hiesse, dass ein
        gerade abgesetzter Beitrag einen Kommentar zwei Stunden aufhaelt -
        die beiden haben verschiedene Taktungen, weil sie verschieden
        auffaellig sind.
        """
        if texttyp:
            row = self.conn.execute(
                "SELECT begonnen_am FROM post_versuche WHERE texttyp = ? AND erfolg = 1 "
                "ORDER BY begonnen_am DESC LIMIT 1",
                (str(texttyp),),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT begonnen_am FROM post_versuche WHERE erfolg = 1 "
                "ORDER BY begonnen_am DESC LIMIT 1"
            ).fetchone()
        return str(row[0]) if row and row[0] else ""

    # --- Die Bremse der Gegenseite, je Aktion ----------------------------
    #
    # Gespeichert in ``marketing_meta`` und nicht in einer eigenen Tabelle:
    # Es sind zwei Werte je Aktion (bis wann, wie oft schon), sie ueberleben
    # einen Neustart, und ein Schema dafuer waere mehr Aufwand als Auskunft.
    # Dieselbe Stelle haelt auch den Zufallsschluessel des ``visitor_hash``.

    def merke_sperre(self, aktion: str, bis: datetime, stufe: int) -> None:
        """Haelt fest, dass diese Aktion bis ``bis`` ruht - und die wievielte es ist.

        Die Stufe ist der Grund, warum hier ueberhaupt etwas gespeichert wird:
        Ohne sie faenge der Backoff nach jedem Neustart wieder bei einer
        Stunde an, und genau das Muster, das zur Bremsung gefuehrt hat,
        begaenne von vorn.
        """
        self.set_meta(f"sperre:{aktion}:bis", _iso(bis))
        self.set_meta(f"sperre:{aktion}:stufe", str(int(stufe)))

    def sperre(self, aktion: str) -> tuple[datetime | None, int]:
        """``(bis wann, Stufe)`` - oder ``(None, 0)``, wenn nichts vorliegt.

        Eine abgelaufene Sperre wird **nicht** geloescht: Die Stufe ist die
        Erinnerung daran, dass es schon einmal zu schnell ging. Sie sinkt
        erst mit einem Erfolg (``loesche_sperre``).
        """
        roh = self.meta(f"sperre:{aktion}:bis")
        stufe = int(self.meta(f"sperre:{aktion}:stufe", "0") or 0)
        if not roh:
            return None, stufe
        try:
            return datetime.fromisoformat(roh), stufe
        except ValueError:
            return None, stufe

    def loesche_sperre(self, aktion: str) -> None:
        """Nach einem Erfolg: Die Aktion laeuft, die Stufe faellt auf null."""
        self.set_meta(f"sperre:{aktion}:bis", "")
        self.set_meta(f"sperre:{aktion}:stufe", "0")

    def staende_je_gruppe(
        self, campaign_id: str, texttyp: Texttyp = Texttyp.POST
    ) -> dict[str, set[str]]:
        """Welche Staende in welcher Gruppe vorkommen - eine Abfrage fuer alle.

        Die Frage der Gruppenauswahl beim Blaettern durch 300 Gruppen: "wo bin
        ich schon gewesen, wo ist etwas schiefgegangen?". Je Gruppe einzeln
        nachzufragen waeren 300 Abfragen fuer eine Auswahlliste.
        """
        rows = self.conn.execute(
            "SELECT DISTINCT group_id, status FROM campaign_group_texte "
            "WHERE campaign_id = ? AND texttyp = ?",
            (campaign_id, texttyp.value),
        ).fetchall()
        gefunden: dict[str, set[str]] = {}
        for row in rows:
            gefunden.setdefault(str(row["group_id"]), set()).add(str(row["status"]))
        return gefunden

    def bisherige_post_urls(self, group_id: str) -> set[str]:
        """Liefert die URLs aller Beiträge, auf die in dieser Gruppe bereits 
        erfolgreich kommentiert wurde."""
        rows = self.conn.execute(
            "SELECT post_url FROM post_versuche WHERE group_id = ? "
            "AND erfolg = 1 AND post_url != ''",
            (group_id,)
        ).fetchall()
        return {str(r["post_url"]) for r in rows}

    def letzte_post_url(self, campaign_id: str, group_id: str, texttyp: str, nummer: int) -> str:
        """Liefert die letzte erfolgreiche Post-URL für diesen bestimmten Entwurf."""
        row = self.conn.execute(
            "SELECT post_url FROM post_versuche WHERE campaign_id = ? AND group_id = ? "
            "AND texttyp = ? AND nummer = ? AND erfolg = 1 "
            "ORDER BY begonnen_am DESC LIMIT 1",
            (campaign_id, group_id, texttyp, nummer)
        ).fetchone()
        return str(row[0]) if row and row[0] else ""

    def _spiegle_ins_paar(
        self,
        campaign_id: str,
        group_id: str,
        texttyp: Texttyp,
        text: str,
        vorlage_key: str,
        quelle: TextQuelle,
        *,
        nur_wenn_leer: bool = False,
    ) -> None:
        """Schreibt eine Fassung als Text des **Paares** mit.

        Die Bruecke zu allem, was es vor den Vorschlaegen schon gab:
        ``beitrag.beitragstext``, ``campaign message``, ``campaign queue``,
        die Uebersicht und die Zustandsmaschine lesen ``post_text`` bzw.
        ``kommentar_text`` am Paar. Ohne diese Spiegelung stuenden dort die
        Texte von vorgestern, waehrend die Arbeitsseite die von heute zeigt -
        und beim Kopieren aus der Kommandozeile ginge ein anderer Text hinaus
        als der, den ein Mensch freigegeben hat.

        Gespiegelt wird die zuletzt gespeicherte oder veroeffentlichte
        Fassung. Das ist keine Rangaussage ueber die fuenf, sondern die
        einzige Antwort, die auf die Frage "und was ist *der* Text dieser
        Gruppe?" ehrlich moeglich ist, solange es fuenf gibt.
        """
        spalten = _TEXTSPALTEN[texttyp]
        bedingung = ""
        if nur_wenn_leer:
            bedingung = f" AND TRIM({spalten['text']}) = ''"
        werte: list[object] = [text, quelle.value, _iso(datetime.now(UTC))]
        setzt = f"{spalten['text']} = ?, {spalten['quelle']} = ?, {spalten['wann']} = ?"
        if vorlage_key:
            setzt += f", {spalten['vorlage']} = ?"
            werte.append(vorlage_key)
        self.conn.execute(
            f"UPDATE campaign_groups SET {setzt} "  # noqa: S608
            f"WHERE campaign_id = ? AND group_id = ?{bedingung}",
            (*werte, campaign_id, group_id),
        )
        self.conn.commit()

    @staticmethod
    def _row_to_vorschlag(row: sqlite3.Row) -> Textvorschlag:
        return Textvorschlag(
            campaign_id=str(row["campaign_id"]),
            group_id=str(row["group_id"]),
            texttyp=Texttyp(str(row["texttyp"])),
            nummer=int(row["nummer"]),
            text=str(row["text"]),
            generated_text=str(row["generated_text"]),
            vorlage_key=str(row["vorlage_key"]),
            quelle=TextQuelle(str(row["quelle"])),
            status=VorschlagStatus(str(row["status"])),
            generiert_am=_parse_dt(row["generiert_am"]),
            veroeffentlicht_am=_parse_dt(row["veroeffentlicht_am"]),
            versuche=int(row["versuche"]),
            fehler=str(row["fehler"]),
        )

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
                (campaign_id, group_id, texttyp, nummer, tracking_code, job_status, erfolg,
                 post_url, fehler, browser_session, ausgeloest_von, begonnen_am)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                versuch.campaign_id,
                versuch.group_id,
                versuch.texttyp,
                versuch.nummer,
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
        """Schliesst den Versuch ab - Ausgang, Grund, gegebenenfalls die URL.

        Die **Einstufung** der Antwort entsteht hier und nicht beim Aufrufer
        (``qualifikation.grund``): Jeder Weg, der einen Versuch beendet -
        Arbeitsseite, Automatik, Fernbetrieb -, kommt durch diese Funktion,
        und eine Einstufung, die an drei Stellen gerechnet wird, ist drei
        Einstufungen. Gerechnet, nicht uebergeben: So kann kein Aufrufer sie
        vergessen und keiner sie anders vornehmen.
        """
        from fbgroups.marketing.qualifikation import grund as _grund

        self.conn.execute(
            "UPDATE post_versuche SET erfolg = ?, fehler = ?, post_url = ?, "
            "grund = ?, job_status = ?, beendet_am = ? WHERE versuch_id = ?",
            (
                int(erfolg),
                fehler,
                post_url,
                _grund(fehler, erfolg=erfolg).value,
                (JobStatus.PUBLISHED if erfolg else JobStatus.FAILED).value,
                _iso(datetime.now(UTC)),
                versuch_id,
            ),
        )
        self.conn.commit()

    def schliesse_gruppe_aus(self, group_id: str, grund: str) -> bool:
        """Nimmt eine Gruppe dauerhaft aus der Bearbeitung. Returns: ob neu.

        **Die Anweisung vom 20.09.2026**, und sie hebt eine aeltere Regel auf:
        Bis dahin galt "Technik ist kein Urteil" ausnahmslos - ein technischer
        Fehlschlag legte die Gruppe hoechstens fuer *diesen Lauf* beiseite
        (``ueberspringe_gruppe``). Der Grund dafuer war teuer erkauft: Am
        11.09.2026 liess ein geschlossenes Browserfenster 45 Gruppen als
        erschoepft gelten.

        Im Betrieb kippte die Abwaegung trotzdem. Ein Lauf blieb in **einer**
        Gruppe haengen - derselbe Beitrag, dieselbe Fassung 1, Dutzende Male -
        und endete mit "12 technische Fehlschlaege in Folge, ueber
        verschiedene Gruppen hinweg", obwohl es nie eine zweite Gruppe gab.
        "Kein Kommentarfeld" ist dort eben **keine** Eigenschaft des Browsers,
        sondern eine der Gruppe: Wo Facebook das Feld gar nicht anbietet, wird
        es auch beim dreissigsten Anlauf nicht erscheinen.

        **Ausgeschlossen, nicht geloescht** - und das ist der Unterschied, der
        die alte Sorge auffaengt:

        * ``bearbeiten = 0`` ist die Achse "arbeiten wir daran?", nicht
          ``marketing_status``. Dass wir Mitglied sind, bleibt stehen.
        * Der **Tracking-Code bleibt gueltig**. Er steht moeglicherweise in
          einem veroeffentlichten Beitrag, und ein Klick darauf muss ankommen.
        * Der Grund steht dabei und ist in der Uebersicht zu lesen. Wer ihn
          fuer falsch haelt, hakt die Zeile an und nimmt den Ausschluss
          zurueck - ein Klick, kein Befehl.

        **Ein Menschenurteil wird nie ueberschrieben.** Steht die Gruppe schon
        auf ausgeschlossen, bleibt ihr Grund stehen: Was ein Mensch
        hingeschrieben hat, ist die bessere Auskunft als "technisch: ...".
        """
        eintrag = self.load_marketing(group_id)
        if not eintrag.bearbeiten:
            return False
        eintrag.bearbeiten = False
        eintrag.ausschlussgrund = grund[:160]
        self.save_marketing(eintrag)
        return True

    def gruende_je_gruppe(self, campaign_id: str = "") -> dict[str, dict[str, int]]:
        """Je Gruppe: wie oft welche Antwort von Facebook kam.

        Die Grundlage fuer die Spalte "Facebook Response" in der Uebersicht
        (Punkt 18 der Anforderung). Gezaehlt wird **jeder** beendete Versuch,
        der Erfolg eingeschlossen: "8 angenommen, 2 Link abgelehnt" ist eine
        Auskunft, "2 Link abgelehnt" allein waere eine Andeutung.
        """
        wenn = "AND campaign_id = ?" if campaign_id else ""
        rows = self.conn.execute(
            "SELECT group_id, grund, COUNT(*) AS n FROM post_versuche "  # noqa: S608
            f"WHERE beendet_am IS NOT NULL AND grund <> '' {wenn} "
            "GROUP BY group_id, grund",
            (campaign_id,) if campaign_id else (),
        ).fetchall()
        je_gruppe: dict[str, dict[str, int]] = {}
        for row in rows:
            je_gruppe.setdefault(row["group_id"], {})[row["grund"]] = int(row["n"])
        return je_gruppe

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

    # --- Die Kommentarautomatik ------------------------------------------
    #
    # Gefuehrt wird hier nur, was sich nicht ableiten laesst. Wie weit eine
    # Gruppe ist, steht in campaign_group_texte und wird gelesen (siehe
    # ``kommentarstand``); ein Zaehler daneben waere eine zweite Wahrheit.

    def starte_lauf(
        self, campaign_ids: list[str], *, ziel_je_gruppe: int, texttyp: str = "kommentar"
    ) -> int:
        """Friert die Kampagnenliste ein und eroeffnet einen Lauf.

        Die Reihenfolge der Liste wird als ``position`` festgehalten: Ohne sie
        entschiede die Sortierung der Abfrage, welche Kampagne als naechste
        drankommt, und ein fortgesetzter Lauf koennte eine andere waehlen als
        der unterbrochene.
        """
        jetzt = _iso(datetime.now(UTC))
        cursor = self.conn.execute(
            "INSERT INTO automatik_lauf (status, texttyp, ziel_je_gruppe, begonnen_am) "
            "VALUES (?,?,?,?)",
            (LaufStatus.LAEUFT.value, texttyp, int(ziel_je_gruppe), jetzt),
        )
        lauf_id = int(cursor.lastrowid or 0)
        self.conn.executemany(
            "INSERT INTO automatik_lauf_kampagnen (lauf_id, campaign_id, position, status) "
            "VALUES (?,?,?,?)",
            [
                (lauf_id, cid, pos, KampagnenLaufStatus.WARTET.value)
                for pos, cid in enumerate(campaign_ids, start=1)
            ],
        )
        self.conn.commit()
        return lauf_id

    def offener_lauf(self) -> sqlite3.Row | None:
        """Der juengste Lauf, der noch nicht fertig ist - oder ``None``.

        Ein Neustart setzt hier auf: Wer einen offenen Lauf findet, arbeitet
        dessen eingefrorene Liste weiter, statt eine neue einzufrieren. Sonst
        finge Kampagne 1 nach jedem Abbruch von vorn an.
        """
        return self.conn.execute(
            "SELECT * FROM automatik_lauf WHERE status IN (?,?,?) "
            "ORDER BY lauf_id DESC LIMIT 1",
            (
                LaufStatus.LAEUFT.value,
                LaufStatus.ANGEHALTEN.value,
                LaufStatus.GESCHEITERT.value,
            ),
        ).fetchone()

    def lauf(self, lauf_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM automatik_lauf WHERE lauf_id = ?", (lauf_id,)
        ).fetchone()

    def lauf_kampagnen(self, lauf_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM automatik_lauf_kampagnen WHERE lauf_id = ? ORDER BY position",
            (lauf_id,),
        ).fetchall()

    def setze_lauf_status(self, lauf_id: int, status: str, *, meldung: str = "") -> None:
        """Traegt den Gesamtzustand ein; ``fertig`` setzt zusaetzlich das Ende."""
        jetzt = _iso(datetime.now(UTC))
        beendet = jetzt if status in (LaufStatus.FERTIG.value,) else None
        self.conn.execute(
            "UPDATE automatik_lauf SET status = ?, meldung = ?, "
            "letzter_schritt_am = ?, beendet_am = COALESCE(?, beendet_am) "
            "WHERE lauf_id = ?",
            (status, meldung, jetzt, beendet, lauf_id),
        )
        self.conn.commit()

    def setze_lauf_kampagne_status(self, lauf_id: int, campaign_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE automatik_lauf_kampagnen SET status = ? "
            "WHERE lauf_id = ? AND campaign_id = ?",
            (status, lauf_id, campaign_id),
        )
        self.conn.commit()

    def kampagne_bewertet(self, lauf_id: int, campaign_id: str) -> bool:
        """Wurden die Gruppen dieser Kampagne in diesem Lauf schon neu bewertet?

        Die Neubewertung steht zwischen Beitritt und Arbeit: Erst wenn sie
        gelaufen ist, steht die Rangfolge fest, nach der gearbeitet wird.
        Einmal je Kampagne und Lauf - ein zweiter Durchgang faende dieselben
        Zahlen und kostete nur Zeit.
        """
        zeile = self.conn.execute(
            "SELECT bewertet_am FROM automatik_lauf_kampagnen "
            "WHERE lauf_id = ? AND campaign_id = ?",
            (lauf_id, campaign_id),
        ).fetchone()
        return bool(zeile and zeile["bewertet_am"])

    def merke_bewertung(self, lauf_id: int, campaign_id: str) -> None:
        """Traegt ein, dass die Neubewertung dieser Kampagne gelaufen ist.

        Auch nach einer **gescheiterten** Neubewertung gesetzt: Sonst
        versuchte der Lauf sie bei jedem Schritt erneut, und ein Fehler in
        der Bewertung hielte die Kampagne fuer immer vor der Arbeit fest -
        genau der globale Abbruch, den die Fehlerisolierung verhindern soll.
        """
        self.conn.execute(
            "UPDATE automatik_lauf_kampagnen SET bewertet_am = ? "
            "WHERE lauf_id = ? AND campaign_id = ?",
            (_iso(datetime.now(UTC)), lauf_id, campaign_id),
        )
        self.conn.commit()

    def ueberspringe_gruppe(
        self,
        lauf_id: int,
        campaign_id: str,
        group_id: str,
        grund: str,
        *,
        ruhe_minuten: int = 0,
    ) -> None:
        """Legt eine Gruppe beiseite - fuer diesen Lauf oder auf Zeit.

        Der Kern der Fehlerisolierung: Was hier steht, wird nicht mehr
        angefasst, gilt aber ausdruecklich **nicht** als ungeeignet. Die
        Zeile haengt an der ``lauf_id`` und ist mit dem naechsten Lauf
        verschwunden.

        ``ruhe_minuten`` macht daraus eine **Ruhezeit** (20.09.2026): Die
        Gruppe kommt nach Ablauf von selbst zurueck in die Reihenfolge. Das
        ist der Unterschied zwischen zwei Aussagen, die vorher dieselbe
        Zeile schrieben:

        * *"Hier ging es nicht"* - ein Fehlschlag, der der Gruppe anhaengt.
          Ohne Ruhezeit, wie bisher.
        * *"Hier steht gerade nichts Passendes"* - eine Aussage ueber diesen
          Augenblick. In einer halben Stunde stehen andere Beitraege da, und
          ein Lauf, der zwoelf Gruppen einmal ansieht und dann aufhoert,
          schreibt elf Kommentare statt hundertzwanzig.

        Ein bestehender Eintrag behaelt seinen **Grund**: Der erste ist der
        aussagekraeftige, was danach kommt, sind meist Folgen. Die Ruhezeit
        wird dagegen fortgeschrieben - sonst kaeme dieselbe Gruppe sofort
        wieder, scheiterte wieder und liefe im Kreis. Eine Ruhezeit macht
        aus einem Uebersprung **ohne** Zeit nie einen mit: "fuer diesen Lauf
        erledigt" ist die staerkere Aussage.
        """
        jetzt = datetime.now(UTC)
        bis = _iso(jetzt + timedelta(minutes=ruhe_minuten)) if ruhe_minuten > 0 else None
        self.conn.execute(
            "INSERT INTO automatik_lauf_uebersprungen "
            "(lauf_id, campaign_id, group_id, grund, zeitpunkt, wiederholen_ab) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT (lauf_id, campaign_id, group_id) DO UPDATE SET "
            "  zeitpunkt = excluded.zeitpunkt, "
            "  wiederholen_ab = CASE "
            "    WHEN excluded.wiederholen_ab IS NULL THEN NULL "
            "    WHEN automatik_lauf_uebersprungen.wiederholen_ab IS NULL THEN NULL "
            "    ELSE MAX(automatik_lauf_uebersprungen.wiederholen_ab, "
            "             excluded.wiederholen_ab) END",
            (lauf_id, campaign_id, group_id, grund[:200], _iso(jetzt), bis),
        )
        self.conn.commit()

    def uebersprungene_gruppen(self, lauf_id: int) -> dict[tuple[str, str], str]:
        """``(campaign_id, group_id) -> Grund`` - nur die **jetzt** ruhenden.

        Eine abgelaufene Ruhezeit steht weiter in der Tabelle (sie ist das
        Protokoll dieses Laufs), zaehlt hier aber nicht mehr: Die Gruppe ist
        zurueck in der Reihenfolge.
        """
        rows = self.conn.execute(
            "SELECT campaign_id, group_id, grund FROM automatik_lauf_uebersprungen "
            "WHERE lauf_id = ? AND (wiederholen_ab IS NULL OR wiederholen_ab > ?)",
            (lauf_id, _iso(datetime.now(UTC))),
        ).fetchall()
        return {(row["campaign_id"], row["group_id"]): row["grund"] for row in rows}

    def ruhende_gruppen(self, lauf_id: int) -> set[tuple[str, str]]:
        """Welche Paare **auf Zeit** beiseite liegen - nicht fuer den Lauf.

        Der Unterschied zu ``uebersprungene_gruppen`` ist die Frage, die
        daran haengt: Jenes beantwortet "wer faellt gerade aus?", dieses
        "kommt hier noch etwas von selbst?". Nur das Zweite entscheidet, ob
        die Kampagne ihren Platz behaelt - eine Kampagne, deren Gruppen alle
        ruhen, ist nicht fertig, sondern wartet.
        """
        rows = self.conn.execute(
            "SELECT campaign_id, group_id FROM automatik_lauf_uebersprungen "
            "WHERE lauf_id = ? AND wiederholen_ab IS NOT NULL AND wiederholen_ab > ?",
            (lauf_id, _iso(datetime.now(UTC))),
        ).fetchall()
        return {(str(r["campaign_id"]), str(r["group_id"])) for r in rows}

    def merke_bezuege(
        self, group_id: str, post_url: str, bezuege: Iterable[str]
    ) -> None:
        """Die Bezuege **eines** gelesenen Beitrags festhalten (23.09.2026).

        Auch ein Beitrag **ohne** Bezug bekommt seine Zeile: Er ist gelesen
        worden, und "gelesen, nichts gefunden" ist etwas anderes als "nie
        gelesen" - die spaetere Behandlung der Gruppen ohne Bezug wird beides
        unterscheiden muessen.

        Derselbe Beitrag ueberschreibt seine Zeile: Er wird in jedem
        Durchgang wieder gelesen und darf nicht mehrfach zaehlen.
        """
        if not post_url:
            return
        self.conn.execute(
            "INSERT INTO beitrag_bezuege (group_id, post_url, bezuege, gelesen_am) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(group_id, post_url) DO UPDATE SET "
            "bezuege = excluded.bezuege, gelesen_am = excluded.gelesen_am",
            (
                group_id,
                post_url,
                ",".join(sorted({str(b) for b in bezuege})),
                _iso(datetime.now(UTC)),
            ),
        )
        self.conn.commit()

    def gruppenbezuege(self, group_ids: Iterable[str] | None = None) -> dict:
        """``group_id -> bezug.Gruppenbezuege`` - gesammelt aus den Beitraegen.

        Eine Gruppe ohne eine einzige gelesene Zeile fehlt im Ergebnis; der
        Aufrufer behandelt sie wie ``[]`` mit ``gelesen = False``.
        """
        from fbgroups.marketing.bezug import fuer_gruppe

        rows = self.conn.execute(
            "SELECT group_id, bezuege FROM beitrag_bezuege"
        ).fetchall()
        gesucht = set(group_ids) if group_ids is not None else None
        je_gruppe: dict[str, list[list[str]]] = {}
        for r in rows:
            gid = str(r["group_id"])
            if gesucht is not None and gid not in gesucht:
                continue
            je_gruppe.setdefault(gid, []).append(
                [b for b in str(r["bezuege"] or "").split(",") if b]
            )
        return {gid: fuer_gruppe(beitraege) for gid, beitraege in je_gruppe.items()}

    def naechste_rueckkehr(self, lauf_id: int) -> tuple[int, str] | None:
        """Wie viele Gruppen ruhen und wann die erste zurueckkommt.

        Die Antwort auf die Frage, die ueber das Ende eines Laufs
        entscheidet: *Kommt noch etwas?* Ruht auch nur eine Gruppe, ist der
        Lauf nicht durch - er wartet. ``None`` heisst: Hier kommt nichts
        mehr von selbst, aufhoeren ist richtig.

        Returns: ``(Anzahl, Zeitpunkt der naechsten Rueckkehr)`` oder ``None``.
        """
        row = self.conn.execute(
            "SELECT COUNT(*) AS anzahl, MIN(wiederholen_ab) AS naechste "
            "FROM automatik_lauf_uebersprungen "
            "WHERE lauf_id = ? AND wiederholen_ab IS NOT NULL AND wiederholen_ab > ?",
            (lauf_id, _iso(datetime.now(UTC))),
        ).fetchone()
        if row is None or not row["anzahl"]:
            return None
        return int(row["anzahl"]), str(row["naechste"])

    def beitrittskandidaten(self, campaign_id: str) -> list[str]:
        """Gruppen **dieser Kampagne**, an die noch keine Anfrage ging.

        Der Unterschied zu ``gruppen_ohne_anfrage`` ist der ganze Punkt der
        neuen Reihenfolge: Dort wird der beste Bestand ueberhaupt gewaehlt,
        hier die Gruppen der Kampagne, die gerade an der Reihe ist. Sonst
        gingen die fuenfzig Anfragen des Tages an Gruppen, die in keiner
        laufenden Kampagne stehen.

        Ausgeschlossen bleiben Gruppen, an denen nicht gearbeitet wird
        (``bearbeiten = 0``) - eine Anfrage dorthin waere ein Beitritt zu
        einer Gruppe, die jemand ausdruecklich aussortiert hat. Sortiert nach
        Score: Wird die Tagesmenge nie ausgeschoepft, sollen es die richtigen
        gewesen sein.
        """
        rows = self.conn.execute(
            """
            SELECT cg.group_id
            FROM campaign_groups cg
            JOIN groups g ON g.group_id = cg.group_id
            LEFT JOIN group_marketing gm ON gm.group_id = cg.group_id
            WHERE cg.campaign_id = ?
              AND g.url_canonical <> ''
              AND COALESCE(gm.bearbeiten, 1) = 1
              AND gm.join_requested_at IS NULL
              AND COALESCE(gm.marketing_status, 'not_contacted') = 'not_contacted'
            ORDER BY COALESCE(g.score, -1) DESC, cg.group_id
            """,
            (campaign_id,),
        ).fetchall()
        return [row["group_id"] for row in rows]

    def kommentarstand(self, campaign_id: str) -> dict[str, int]:
        """Je Gruppe: wie viele Kommentarfassungen sind veroeffentlicht?

        **Gelesen, nicht gefuehrt.** Der Stand steht dort, wo er entsteht -
        in ``campaign_group_texte``. Ein Abbruch braucht deshalb keine
        Aufraeumarbeit: Was nicht heraus ist, steht auch nicht als heraus da.
        """
        rows = self.conn.execute(
            "SELECT group_id, COUNT(*) AS n FROM campaign_group_texte "
            "WHERE campaign_id = ? AND texttyp = ? AND status = ? "
            "GROUP BY group_id",
            (campaign_id, Texttyp.KOMMENTAR.value, VorschlagStatus.VEROEFFENTLICHT.value),
        ).fetchall()
        return {row["group_id"]: int(row["n"]) for row in rows}

    def gescheiterte_kommentarfassungen(
        self, campaign_id: str, max_versuche: int
    ) -> dict[str, set[int]]:
        """Je Gruppe: welche Fassungen sind zu oft **an der Gruppe** gescheitert?

        Sie werden uebersprungen, statt den Lauf an derselben Stelle
        festzuhalten. ``versuche`` zaehlt in ``campaign_group_texte`` bereits
        mit - es gab keinen Grund, dafuer etwas Neues zu bauen.

        **Technische Fehlschlaege zaehlen nicht** (seit 12.09.2026). Ein
        geschlossenes Browserfenster, ein Zeitablauf, eine Seite, die nicht
        geladen hat: Das sind Aussagen ueber unseren Rechner, nicht ueber die
        Gruppe. Mitgezaehlt fuehrten sie am 11.09.2026 dazu, dass alle zehn
        Fassungen als aufgegeben galten und die Gruppe als erschoepft - und
        am 12.09.2026 ein zweites Mal, diesmal in 78 Gruppen: vier Kampagnen
        standen auf "fertig" mit null Kommentaren.

        Dieselbe Trennung, die ``qualifikation.Beobachtung`` schon macht (sie
        zaehlt nur ``MODERATION``) - sie fehlte nur hier. Im Zweifel gilt
        ``TECHNISCH``: Eine geratene Ablehnung verurteilte eine Gruppe, gegen
        die nichts vorliegt. Dass der Lauf deshalb nicht ewig an derselben
        Fassung haengt, sichert der Schleifenwaechter des Treibers, und dass
        er bei totem Browser aufhoert, der Technikwaechter.
        """
        from fbgroups.marketing.qualifikation import Ausgangsart, klassifiziere

        rows = self.conn.execute(
            "SELECT group_id, nummer, fehler FROM campaign_group_texte "
            "WHERE campaign_id = ? AND texttyp = ? AND status <> ? AND versuche >= ?",
            (
                campaign_id,
                Texttyp.KOMMENTAR.value,
                VorschlagStatus.VEROEFFENTLICHT.value,
                int(max_versuche),
            ),
        ).fetchall()
        heraus: dict[str, set[int]] = {}
        # Technik und Bremse zaehlen beide nicht: Das eine sagt nichts ueber
        # die Gruppe, das andere etwas ueber **unsere** Geschwindigkeit.
        ohne_urteil = (
            Ausgangsart.TECHNISCH,
            Ausgangsart.RATE_LIMIT,
            # "Die Gruppe nimmt gerade nichts mehr an" ist kein Urteil ueber
            # den Text - die Warteschlange der Freigabe ist voll, mehr nicht.
            Ausgangsart.GRUPPENLIMIT,
        )
        for row in rows:
            if klassifiziere(row["fehler"] or "") in ohne_urteil:
                continue
            heraus.setdefault(row["group_id"], set()).add(int(row["nummer"]))
        return heraus

    # --- Qualifikation: die Frage vor dem Text ----------------------------
    def merke_regeln(self, group_id: str, regeln) -> None:  # noqa: ANN001 - Regelbefund
        """Haelt fest, was auf der Gruppenseite an Regeln stand.

        Nur dieses eine Ergebnis wird gespeichert - das Urteil daraus rechnet
        ``qualifikation.beurteile`` bei jedem Lesen neu. Ein ungelesener
        Befund (``gelesen=False``) schreibt **nichts**: Er wuerde einen
        frueher gelesenen ueberschreiben, und "nicht nachgesehen" ist kein
        Beleg dafuer, dass die Regel weg ist. Dieselbe Ueberlegung wie bei
        ``upsert_groups`` und COALESCE.
        """
        if not regeln.gelesen:
            return
        jetzt = _iso(datetime.now(UTC))
        self.conn.execute(
            "INSERT INTO group_marketing (group_id, regeln_gelesen_am, "
            "regel_keine_links, regel_keine_werbung, regel_freigabe_noetig, "
            "regel_neue_ohne_links, updated_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(group_id) DO UPDATE SET "
            "    regeln_gelesen_am     = excluded.regeln_gelesen_am, "
            "    regel_keine_links     = excluded.regel_keine_links, "
            "    regel_keine_werbung   = excluded.regel_keine_werbung, "
            "    regel_freigabe_noetig = excluded.regel_freigabe_noetig, "
            "    regel_neue_ohne_links = excluded.regel_neue_ohne_links, "
            "    updated_at            = excluded.updated_at",
            (
                group_id,
                jetzt,
                int(regeln.keine_links),
                int(regeln.keine_werbung),
                int(regeln.freigabe_noetig),
                int(regeln.neue_ohne_links),
                jetzt,
            ),
        )
        self.conn.commit()

    def beobachtungen(self, campaign_id: str = "") -> dict:
        """Je Gruppe: was bei den bisherigen Versuchen herauskam.

        **Gelesen, nicht gefuehrt** - wie der Fortschritt in ``lauf.py``. Die
        Zahlen stehen bereits in ``post_versuche``; ein Zaehler daneben waere
        eine zweite Wahrheit, die beim ersten Abbruch von der ersten abweicht.

        Ob ein Kommentar einen Link trug, sagt der Text der **Fassung**, an
        der der Versuch hing - deshalb der Verbund ueber
        ``(campaign_id, group_id, texttyp, nummer)``. Das ist der heutige
        Text; wurde er seither neu erzeugt, beschreibt er den damaligen
        Versuch nur noch ungefaehr. Eine eigene Spalte am Versuch waere
        genauer und ginge nur fuer kuenftige Versuche - der Verbund gilt auch
        rueckwirkend, und das ist hier mehr wert.

        Technische Fehlschlaege werden **nicht** gezaehlt. Ein geschlossener
        Browser sagt nichts ueber die Gruppe; mitgezaehlt machte er aus einem
        zugeklappten Fenster ein Urteil - genau der Fehler vom 11.09.2026.

        ``campaign_id`` leer heisst: ueber alle Kampagnen. Das ist der
        Normalfall - gesperrt wird bei Facebook das Konto, und der Gruppe ist
        gleich, unter welcher Kampagne ein Text stand.
        """
        from fbgroups.marketing.qualifikation import (
            Ausgangsart,
            Beobachtung,
            ist_linkablehnung,
            klassifiziere,
        )

        bedingung = "v.beendet_am IS NOT NULL"
        werte: list[object] = []
        if campaign_id:
            bedingung += " AND v.campaign_id = ?"
            werte.append(campaign_id)

        rows = self.conn.execute(
            f"""
            SELECT v.group_id   AS group_id,
                   v.texttyp    AS texttyp,
                   v.erfolg     AS erfolg,
                   v.fehler     AS fehler,
                   COALESCE(t.text, '') AS text
              FROM post_versuche v
              LEFT JOIN campaign_group_texte t
                     ON t.campaign_id = v.campaign_id
                    AND t.group_id    = v.group_id
                    AND t.texttyp     = v.texttyp
                    AND t.nummer      = v.nummer
             WHERE {bedingung}
            """,  # noqa: S608 - die Bedingung ist hier gebaut, die Werte sind gebunden
            werte,
        ).fetchall()

        # Gezaehlt wird in einfachen Zaehlern und erst am Ende zu
        # ``Beobachtung`` gemacht: Der Datentyp ist eingefroren, und
        # zehntausend Kopien beim Hochzaehlen waeren Aufwand ohne Gegenwert.
        zaehler: dict[str, dict[str, int]] = {}
        for row in rows:
            gid = str(row["group_id"])
            k = zaehler.setdefault(
                gid,
                {
                    "beitrag_erfolg": 0, "beitrag_moderation": 0,
                    "mit_link_erfolg": 0, "mit_link_moderation": 0,
                    "ohne_link_erfolg": 0, "ohne_link_moderation": 0,
                    "link_ausdruecklich": 0,
                },
            )
            erfolg = bool(row["erfolg"])
            ist_post = str(row["texttyp"]) == Texttyp.POST.value
            if ist_post:
                if erfolg:
                    k["beitrag_erfolg"] += 1
                elif klassifiziere(str(row["fehler"] or "")) is Ausgangsart.MODERATION:
                    k["beitrag_moderation"] += 1
                continue

            mit_link = "{link}" in str(row["text"] or "")
            vorsilbe = "mit_link" if mit_link else "ohne_link"
            if erfolg:
                k[f"{vorsilbe}_erfolg"] += 1
                continue
            fehler = str(row["fehler"] or "")
            if klassifiziere(fehler) is Ausgangsart.MODERATION:
                k[f"{vorsilbe}_moderation"] += 1
                if ist_linkablehnung(fehler):
                    k["link_ausdruecklich"] += 1

        return {gid: Beobachtung(**werte_) for gid, werte_ in zaehler.items()}

    def gescheiterte_fassungen_zuruecksetzen(
        self, campaign_id: str, *, texttyp: Texttyp = Texttyp.KOMMENTAR
    ) -> tuple[int, int]:
        """Holt die **gescheiterten** Fassungen zurueck. Returns: (Fassungen, Gruppen).

        Das Gegenstueck zu ``fehlgeschlagene_zuruecksetzen``, nur eine Ebene
        tiefer: Jenes stellt den **Beitrag** eines Paares wieder her, dieses
        die einzelnen Fassungen in ``campaign_group_texte``. Gebraucht wird es
        seit dem 11.09.2026, als ein geschlossener Browser mitten im Lauf
        jede der zehn Kommentarfassungen dreimal scheitern liess
        (``BrowserContext.new_page: Target page ... has been closed``). Danach
        galten alle zehn als aufgegeben, die Gruppe als erschoepft - wegen
        eines Fensters, das jemand zugemacht hat.

        **Veroeffentlichte Fassungen bleiben unberuehrt.** Sie stehen in der
        Gruppe; sie zurueckzusetzen hiesse zu behaupten, sie staenden dort
        nicht. Genau darin liegt der Unterschied zu ``campaign reset``, das
        die ganze Kampagne auf Anfang stellt - einschliesslich dessen, was
        bereits hinausgegangen ist.

        **Der Text bleibt ebenfalls stehen**, und ``quelle`` entscheidet ueber
        den Stand: Was ein Mensch angefasst hat, ist wieder "gespeichert", was
        aus der Vorlage kam, ein Entwurf. Dieselbe Regel wie im Reset.

        Mitgeloescht wird die **Erschoepfung** - aber nur bei den Gruppen, die
        wirklich Fassungen zurueckbekommen. "nur 0 von 10 Kommentaren
        moeglich" war ein Urteil ueber diese Versuche; sind die Versuche
        zurueckgenommen, ist es das Urteil auch. Eine Erschoepfung aus einem
        anderen Grund (keine Gruppen-URL, keine Vorlage) bleibt stehen: Sie
        hat mit den Versuchen nichts zu tun.
        """
        betroffen = [
            str(row["group_id"])
            for row in self.conn.execute(
                "SELECT DISTINCT group_id FROM campaign_group_texte "
                "WHERE campaign_id = ? AND texttyp = ? AND status = ?",
                (campaign_id, texttyp.value, VorschlagStatus.FEHLGESCHLAGEN.value),
            ).fetchall()
        ]
        if not betroffen:
            return (0, 0)

        cursor = self.conn.execute(
            "UPDATE campaign_group_texte "
            "   SET status = CASE WHEN quelle = 'hand' THEN 'gespeichert' "
            "                     ELSE 'entwurf' END, "
            "       versuche = 0, "
            "       fehler = '' "
            " WHERE campaign_id = ? AND texttyp = ? AND status = ?",
            (campaign_id, texttyp.value, VorschlagStatus.FEHLGESCHLAGEN.value),
        )
        fassungen = int(cursor.rowcount or 0)

        platzhalter = ",".join("?" * len(betroffen))
        self.conn.execute(
            "UPDATE campaign_groups SET kommentar_erschoepft = 0, "  # noqa: S608
            "kommentar_erschoepft_grund = '', kommentar_erschoepft_am = NULL "
            f"WHERE campaign_id = ? AND group_id IN ({platzhalter}) "
            "AND kommentar_erschoepft = 1",
            (campaign_id, *betroffen),
        )
        self.conn.commit()
        return (fassungen, len(betroffen))

    def fassungen_mit_text(self, campaign_id: str, texttyp: Texttyp) -> dict[str, set[int]]:
        """Je Gruppe: welche Fassungen dieses Zwecks tragen ueberhaupt einen Text?

        Der Lauf braucht das, seit er auch Beitraege absetzt: Nicht jede
        zugeordnete Gruppe hat einen Beitragstext, und ein Schritt fuer eine
        Gruppe ohne Text koennte nur scheitern - der Fehlschlag stuende
        hinterher als Urteil ueber die Gruppe da, obwohl er eines ueber
        unsere Vorbereitung waere.

        Gelesen, nicht gefuehrt - wie ``kommentarstand``: Die Wahrheit steht
        in ``campaign_group_texte``, wo der Text auch entsteht.
        """
        rows = self.conn.execute(
            "SELECT group_id, nummer FROM campaign_group_texte "
            "WHERE campaign_id = ? AND texttyp = ? AND TRIM(text) <> ''",
            (campaign_id, texttyp.value),
        ).fetchall()
        heraus: dict[str, set[int]] = {}
        for row in rows:
            heraus.setdefault(row["group_id"], set()).add(int(row["nummer"]))
        return heraus

    def fassungen_mit_link(self, campaign_id: str, texttyp: Texttyp) -> dict[str, set[int]]:
        """Je Gruppe: welche Fassungen dieses Zwecks einen ``{link}`` tragen.

        Der Unterschied zwischen "diese Gruppe nimmt keine Links" und "diese
        Gruppe nimmt keine Kommentare". Ohne ihn muesste der Lauf in einer
        linkscheuen Gruppe entweder alle Kommentare sperren oder keinen -
        dabei ist derselbe Text ohne Link dort willkommen.

        Gesucht wird im **gespeicherten** Text, in dem der Platzhalter noch
        steht: Der Tracking-Link kommt erst beim Lesen hinein
        (``beitrag.mit_link``), und genau deshalb ist er hier noch als
        Platzhalter zu erkennen.
        """
        rows = self.conn.execute(
            "SELECT group_id, nummer FROM campaign_group_texte "
            "WHERE campaign_id = ? AND texttyp = ? AND text LIKE '%{link}%'",
            (campaign_id, texttyp.value),
        ).fetchall()
        heraus: dict[str, set[int]] = {}
        for row in rows:
            heraus.setdefault(row["group_id"], set()).add(int(row["nummer"]))
        return heraus

    def setze_kommentar_erschoepft(self, campaign_id: str, group_id: str, grund: str) -> None:
        """Haelt fest, dass diese Gruppe nichts mehr hergibt.

        Ein Urteil nach dem Versuch, keine Eigenschaft der Daten - deshalb
        gespeichert und nicht abgeleitet. Es blockiert die Kampagne nicht
        mehr, wird in der Abschlussmeldung aber getrennt ausgewiesen: erledigt
        ist nicht dasselbe wie erfolgreich.
        """
        self.conn.execute(
            "UPDATE campaign_groups SET kommentar_erschoepft = 1, "
            "kommentar_erschoepft_grund = ?, kommentar_erschoepft_am = ? "
            "WHERE campaign_id = ? AND group_id = ?",
            (grund, _iso(datetime.now(UTC)), campaign_id, group_id),
        )
        self.conn.commit()

    def erschoepfte_gruppen(self, campaign_id: str) -> dict[str, str]:
        """Je erschoepfter Gruppe der Grund."""
        rows = self.conn.execute(
            "SELECT group_id, kommentar_erschoepft_grund FROM campaign_groups "
            "WHERE campaign_id = ? AND kommentar_erschoepft = 1",
            (campaign_id,),
        ).fetchall()
        return {row["group_id"]: row["kommentar_erschoepft_grund"] for row in rows}

    # --- Beitrittsanfragen ------------------------------------------------
    #
    # Zwei Staende und nicht mehr: angefragt oder nicht. Gezaehlt wird ueber
    # ``join_requested_at`` - das Feld gibt es seit Migrationsschritt 5, und
    # ein eigener Zaehler daneben waere eine zweite Wahrheit ueber dieselbe
    # Zahl.

    def anfragen_heute(self, tag: str) -> int:
        """Wie viele Beitrittsanfragen heute gestellt wurden.

        Der Vergleich laeuft ueber den Datumsteil des ISO-Zeitpunkts; die
        Tagesgrenze ist damit UTC-Mitternacht, wie ueberall im Projekt.
        """
        return int(
            self.conn.execute(
                "SELECT count(*) FROM group_marketing "
                "WHERE join_requested_at IS NOT NULL AND substr(join_requested_at,1,10) = ?",
                (tag,),
            ).fetchone()[0]
        )

    def letzte_anfrage(self) -> str:
        """Zeitpunkt der juengsten Beitrittsanfrage - fuer den Mindestabstand."""
        zeile = self.conn.execute(
            "SELECT max(join_requested_at) FROM group_marketing "
            "WHERE join_requested_at IS NOT NULL"
        ).fetchone()
        return zeile[0] or ""

    def gruppen_ohne_anfrage(self, grenze: int = 0) -> list[str]:
        """Gruppen, an die noch keine Beitrittsanfrage ging - beste zuerst.

        Sortiert nach Score: Wird die Tagesmenge nie ausgeschoepft, sollen es
        die richtigen fuenfzig gewesen sein. Dieselbe Ueberlegung wie bei der
        Arbeitsliste.

        Ausgeschlossen sind Gruppen, an denen nicht gearbeitet wird
        (``bearbeiten = 0``) - eine Anfrage dorthin waere ein Beitritt zu einer
        Gruppe, die jemand ausdruecklich aussortiert hat.
        """
        sql = """
            SELECT g.group_id
            FROM groups g
            LEFT JOIN group_marketing gm ON gm.group_id = g.group_id
            WHERE g.url_canonical <> ''
              AND COALESCE(gm.bearbeiten, 1) = 1
              AND gm.join_requested_at IS NULL
              AND COALESCE(gm.marketing_status, 'not_contacted') = 'not_contacted'
            ORDER BY COALESCE(g.score, -1) DESC, g.group_id
        """
        if grenze > 0:
            sql += f" LIMIT {int(grenze)}"
        return [row["group_id"] for row in self.conn.execute(sql).fetchall()]

    def merke_anfrage(self, group_id: str, *, mitglied: bool = False) -> None:
        """Traegt ein, dass eine Anfrage gestellt wurde - oder dass wir drin sind.

        ``mitglied=True`` fuer den Fall, den die Gruppenseite ohnehin verraet:
        Wo kein Beitrittsknopf steht, aber das Schreibfeld, sind wir Mitglied.
        Diese Auskunft mitzunehmen kostet keinen zusaetzlichen Abruf und
        schliesst die Kette - sonst wuesste niemand je, dass eine Freigabe
        gekommen ist.

        Ein erreichter Stand wird **nie** zurueckgedreht: Wer schon Mitglied
        ist, wird durch eine Anfrage nicht wieder zum Anfragenden.
        """
        vorhanden = self.load_marketing(group_id)
        jetzt = datetime.now(UTC)

        neuer_stand = MarketingStatus.MEMBER if mitglied else MarketingStatus.JOIN_REQUESTED

        def rang(stand: MarketingStatus) -> int:
            """Stellung in der Fortschrittsfolge - ausserhalb heisst -1.

            Abgelehnt und beendet stehen bewusst nicht in ``MARKETING_FORTSCHRITT``.
            Eine Ablehnung ist ein Ergebnis; sie durch einen Sammellauf zu
            ueberschreiben waere ein Urteil, das niemand gefaellt hat.
            """
            return MARKETING_FORTSCHRITT.index(stand) if stand in MARKETING_FORTSCHRITT else -1

        if vorhanden is not None:
            alt = rang(vorhanden.marketing_status)
            neu = rang(neuer_stand)
            if alt == -1 or alt >= neu:
                # Schon weiter - nur den Zeitpunkt nachtragen, falls er fehlt.
                if vorhanden.join_requested_at is None and not mitglied:
                    vorhanden.join_requested_at = jetzt
                    self.save_marketing(vorhanden)
                return
            vorhanden.marketing_status = neuer_stand
            if not mitglied:
                vorhanden.join_requested_at = jetzt
            self.save_marketing(vorhanden)
            return

        self.save_marketing(
            GroupMarketing(
                group_id=group_id,
                marketing_status=neuer_stand,
                join_requested_at=None if mitglied else jetzt,
            )
        )

    def vergib_browsercode(self, campaign_id: str, group_id: str, basis_url: str) -> str:
        """Legt den Browser-Code dieses Paares an - einmal und endgueltig.

        Er leitet sich vom Store-Code ab (``FB-SYR-BER-010`` →
        ``FB-SYR-BER-010-B``). Zwei Gruende gegen eine eigene Nummernreihe:

        * Man sieht der Kennung an, zu welchem Paar sie gehoert. Bei einem
          Klick in einem Protokoll ist das die erste Frage.
        * Die Reihe des ``CodeAllocator`` bleibt unberuehrt. Zwei Reihen fuer
          dasselbe Paar koennten auseinanderlaufen, und die Nummer ist bereits
          an ``first_seen_at`` gebunden.

        Ein vorhandener Code wird **nie** ersetzt - er steht moeglicherweise
        schon in einem veroeffentlichten Beitrag.
        """
        link = self.link_for(campaign_id, group_id)
        if link is None:
            return ""
        if link.tracking_code_browser:
            return link.tracking_code_browser

        code = f"{link.tracking_code}-B"
        url = f"{basis_url.rstrip('/')}/r/{code}"
        self.conn.execute(
            "UPDATE campaign_groups SET tracking_code_browser = ?, tracking_url_browser = ? "
            "WHERE campaign_id = ? AND group_id = ? AND tracking_code_browser IS NULL",
            (code, url, campaign_id, group_id),
        )
        self.conn.commit()
        # Der Browser-Code entsteht **hier** und damit auch sein Deckname.
        # Getrennt vergeben hiesse: Der erste Beitrag mit Browser-Ziel traegt
        # die lange Adresse, jeder spaetere die kurze - dieselbe Gruppe mit
        # zwei Gesichtern, und niemand koennte sagen, warum.
        self.vergib_kurzcodes(campaign_id, group_id)
        return code

    def aufloesen(self, code: str) -> Aufloesung | None:
        """Loest einen Code auf - den inneren wie den oeffentlichen.

        **Die eine Stelle, an der aus einer Adresse ein Datensatz wird.** Vier
        Spalten kommen in Frage: die beiden Tracking-Codes (Store und Browser)
        und ihre beiden Kurzcodes. Jeder von ihnen kann in einem
        veroeffentlichten Beitrag stehen, und keiner darf ins Leere laufen -
        auch der aelteste nicht, denn zurueckholen laesst sich ein Beitrag
        nicht.

        Zurueck kommt immer der **innere** Code. Das ist der Punkt der
        Uebung: Gezaehlt, gespeichert und ausgewertet wird unter ihm, ganz
        gleich, welcher Deckname in der Adresse stand. Nur so bleiben die
        Auswertungen dieselben wie vor den Kurzcodes.
        """
        row = self.conn.execute(
            "SELECT * FROM campaign_groups WHERE tracking_code = ? "
            "OR tracking_code_browser = ? OR public_code = ? OR public_code_browser = ?",
            (code, code, code, code),
        ).fetchone()
        if row is None:
            return None

        # Die Browser-Spalten zuerst: Ein nicht vergebener Browser-Code ist
        # NULL und trifft keinen Vergleich, ein vergebener trifft genau einen.
        browser = code in (row["tracking_code_browser"], row["public_code_browser"])
        return Aufloesung(
            link=self._row_to_link(row),
            interner_code=(row["tracking_code_browser"] if browser else row["tracking_code"]),
            oeffentlicher_code=(
                (row["public_code_browser"] if browser else row["public_code"])
                or (row["tracking_code_browser"] if browser else row["tracking_code"])
            ),
            ziel="browser" if browser else "store",
        )

    def resolve_code(self, tracking_code: str) -> CampaignGroup | None:
        """Findet Kampagne und Gruppe zu einem Code. Siehe ``aufloesen``."""
        treffer = self.aufloesen(tracking_code)
        return treffer.link if treffer else None

    def interner_code(self, code: str) -> str:
        """Der gespeicherte Tracking-Code zu einem beliebigen Code.

        Der Rueckfall auf die Eingabe ist bewusst: Ein unbekannter Code ist
        keine Umbenennung, und ihn hier still zu leeren verschoebe die
        Entscheidung "unbekannt - was nun?" an eine Stelle, die sie nicht
        trifft. Wer nachschlagen will, ob es ihn gibt, nimmt ``aufloesen``.
        """
        treffer = self.aufloesen(code)
        return treffer.interner_code if treffer else code

    def ziel_des_codes(self, tracking_code: str) -> str:
        """``browser`` oder ``store`` - woran der Code haengt.

        Die Auskunft steht am **Code**, nicht an der Kampagne. Vorher
        entschied ``campaign.ziel`` fuer alle Codes gemeinsam; damit fuehrten
        am 31.08.2026 saemtliche Links zum Play Store, und der Browser kam nie
        vor.
        """
        treffer = self.aufloesen(tracking_code)
        return treffer.ziel if treffer else ""

    def kurzcode_salt(self) -> str:
        """Das Geheimnis, aus dem die Kurzcodes abgeleitet werden.

        Entsteht beim ersten Bedarf und bleibt dann stehen - wie der Salt der
        Besucherpruefsumme. Es ist kein Passwort; es verhindert das Gegenteil
        der Kurzcodes: Ohne Geheimnis koennte jeder, der einen Beitrag sieht,
        die Adressen der Nachbargruppen ausrechnen und damit den Aufbau der
        Kampagne zurueckgewinnen.
        """
        salt = self.meta(_KURZCODE_SALT)
        if not salt:
            salt = secrets.token_hex(16)
            self.set_meta(_KURZCODE_SALT, salt)
        return salt

    def vergib_kurzcodes(self, campaign_id: str, group_id: str) -> CampaignGroup | None:
        """Legt die oeffentlichen Kurzcodes dieses Paares an - einmal, endgueltig.

        Vergeben wird fuer **jeden vorhandenen** Tracking-Code des Paares:
        immer den Store-Code, und den Browser-Code, sobald es ihn gibt. Einer
        auf Vorrat entsteht nicht - dieselbe Regel wie bei
        ``vergib_browsercode``, und aus demselben Grund: Jede vergebene
        Adresse kann ab dem naechsten Beitrag oeffentlich sein.

        Ein vorhandener Kurzcode wird **nie** ersetzt. Er steht
        moeglicherweise schon in einer Gruppe, und ein Klick darauf muss
        ankommen.

        Der Vorspann kommt aus der bereits gespeicherten Tracking-Adresse und
        nicht aus der Konfiguration: Beide Adressen desselben Paares sollen
        auf denselben Dienst zeigen. Stuende hier ``app_base_url``, zeigte die
        kurze Adresse nach einem Domainwechsel woandershin als die lange, und
        gemerkt haette man es an einer Zahl, die nicht mehr steigt.
        """
        link = self.link_for(campaign_id, group_id)
        if link is None:
            return None

        salt = self.kurzcode_salt()
        for spalte, url_spalte, code, alt_url, vorhanden in (
            ("public_code", "public_url", link.tracking_code, link.tracking_url, link.public_code),
            (
                "public_code_browser",
                "public_url_browser",
                link.tracking_code_browser,
                link.tracking_url_browser,
                link.public_code_browser,
            ),
        ):
            if not code or vorhanden:
                continue
            kurz = self._freier_kurzcode(code, salt)
            basis = alt_url.rsplit("/r/", 1)[0] if "/r/" in alt_url else ""
            self.conn.execute(
                f"UPDATE campaign_groups SET {spalte} = ?, {url_spalte} = ? "  # noqa: S608
                f"WHERE campaign_id = ? AND group_id = ? AND {spalte} IS NULL",
                (kurz, f"{basis}/r/{kurz}" if basis else "", campaign_id, group_id),
            )
        self.conn.commit()
        return self.link_for(campaign_id, group_id)

    def _freier_kurzcode(self, tracking_code: str, salt: str) -> str:
        """Der abgeleitete Kurzcode - und bei einem Zusammenstoss der naechste.

        Siebenstellig aus 29 Zeichen: Ein Zusammenstoss ist bei dreihundert
        Gruppen nicht zu erwarten. Behandelt wird er trotzdem, denn zwei
        Gruppen auf derselben Adresse schrieben Klicks der falschen Gruppe
        gut - und das faellt in keiner Auswertung auf.
        """
        for runde in range(50):
            kandidat = kurzcode(tracking_code, salt, runde=runde)
            belegt = self.conn.execute(
                "SELECT 1 FROM campaign_groups "
                "WHERE public_code = ? OR public_code_browser = ? "
                "OR tracking_code = ? OR tracking_code_browser = ?",
                (kandidat, kandidat, kandidat, kandidat),
            ).fetchone()
            if belegt is None:
                return kandidat
        raise RuntimeError(f"Kein freier Kurzcode fuer {tracking_code} gefunden.")

    def kurzcodes_nachtragen(self, campaign_id: str = "") -> int:
        """Traegt fehlende Kurzcodes nach. Returns: fuer wie viele Paare.

        Der Weg fuer den Bestand: Die Migration legt nur die Spalten an, die
        Codes entstehen hier. Wiederholbar und ohne Wirkung auf vorhandene -
        wer ihn zweimal laufen laesst, bekommt beim zweiten Mal eine Null.
        """
        fehlt = (
            "(public_code IS NULL OR (tracking_code_browser IS NOT NULL "
            "AND public_code_browser IS NULL))"
        )
        if campaign_id:
            paare = self.conn.execute(
                "SELECT campaign_id, group_id FROM campaign_groups "  # noqa: S608
                f"WHERE campaign_id = ? AND {fehlt}",
                (campaign_id,),
            ).fetchall()
        else:
            paare = self.conn.execute(
                f"SELECT campaign_id, group_id FROM campaign_groups WHERE {fehlt}"  # noqa: S608
            ).fetchall()
        for zeile in paare:
            self.vergib_kurzcodes(zeile["campaign_id"], zeile["group_id"])
        return len(paare)

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
    def _row_to_versuch(row: sqlite3.Row) -> PostVersuch:
        return PostVersuch(
            versuch_id=row["versuch_id"],
            campaign_id=row["campaign_id"],
            group_id=row["group_id"],
            texttyp=row["texttyp"] if "texttyp" in row.keys() else "post",  # noqa: SIM118
            nummer=row["nummer"] if "nummer" in row.keys() else 1,  # noqa: SIM118
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
            ziel=row["ziel"],
            status=row["status"],
            starts_on=row["starts_on"],
            ends_on=row["ends_on"],
            target_audiences=json.loads(row["target_audiences"]),
            target_cities=json.loads(row["target_cities"]),
            target_categories=json.loads(row["target_categories"]),
            target_statuses=json.loads(row["target_statuses"]),
            target_prioritaeten=json.loads(row["target_prioritaeten"]),
            target_aktivitaet=json.loads(row["target_aktivitaet"]),
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
            # ``or ""`` fuer Bestandszeilen: Die Spalte ist nullable, weil kein
            # Code auf Vorrat entsteht.
            tracking_code_browser=row["tracking_code_browser"] or "",
            tracking_url_browser=row["tracking_url_browser"] or "",
            public_code=row["public_code"] or "",
            public_url=row["public_url"] or "",
            public_code_browser=row["public_code_browser"] or "",
            public_url_browser=row["public_url_browser"] or "",
            added_at=row["added_at"],
            post_status=row["post_status"],
            posted_at=row["posted_at"],
            last_attempt_at=row["last_attempt_at"],
            post_attempts=row["post_attempts"],
            post_error=row["post_error"],
            job_status=row["job_status"],
            post_text=row["post_text"],
            generated_text=row["generated_text"],
            vorlage_key=row["vorlage_key"],
            text_quelle=row["text_quelle"],
            generiert_am=row["generiert_am"],
            freigegeben_am=row["freigegeben_am"],
            freigegeben_von=row["freigegeben_von"],
            kommentar_text=row["kommentar_text"],
            kommentar_generated=row["kommentar_generated"],
            kommentar_vorlage_key=row["kommentar_vorlage_key"],
            kommentar_quelle=row["kommentar_quelle"],
            kommentar_generiert_am=row["kommentar_generiert_am"],
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
            regeln_gelesen_am=row["regeln_gelesen_am"],
            regel_keine_links=bool(row["regel_keine_links"]),
            regel_keine_werbung=bool(row["regel_keine_werbung"]),
            regel_freigabe_noetig=bool(row["regel_freigabe_noetig"]),
            regel_neue_ohne_links=bool(row["regel_neue_ohne_links"]),
            updated_at=row["updated_at"],
        )
