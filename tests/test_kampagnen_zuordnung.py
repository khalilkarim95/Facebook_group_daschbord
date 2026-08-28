"""Automatische Zuordnung von Gruppen zu einer Kampagne.

Der Kern dieser Tests ist nicht, dass die Zuordnung funktioniert - das waere
schnell geprueft. Es geht darum, was sie **nicht** tun darf: einen vergebenen
Tracking-Code veraendern, eine Zuordnung entfernen, denselben Code zweimal
ausgeben oder bei jedem Lauf andere Nummern vergeben. Ein Code steht in einem
veroeffentlichten Facebook-Beitrag; er ist ab der Vergabe fremdes Eigentum.

Ausserdem muss die Zuordnung bei 1000 Gruppen dasselbe tun wie bei 8 - deshalb
laufen mehrere Tests gegen einen kuenstlich grossen Bestand.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fbgroups.marketing.models import Campaign, CampaignGroup, CampaignStatus, PostStatus
from fbgroups.marketing.selection import (
    auswahl_der_kampagne,
    baue_plan,
    passt,
    synchronisiere,
    waehle_gruppen,
)
from fbgroups.marketing.store import SCHEMA as MARKETING_SCHEMA
from fbgroups.marketing.store import MarketingStore
from fbgroups.marketing.tracking import CodeAllocator
from fbgroups.models import Group, RecordStatus
from fbgroups.storage import SqliteStore
from fbgroups.storage.sqlite_store import SCHEMA_VERSION

PROJEKT = Path(__file__).resolve().parents[1]
ECHTER_BESTAND = PROJEKT / "data" / "groups.sqlite"

BASIS = datetime(2026, 1, 1, tzinfo=UTC)


def _group(
    group_id: str,
    *,
    name: str = "Syrer in Berlin",
    city: str | None = "Berlin",
    audience_tags: list[str] | None = None,
    category: str | None = None,
    score: float | None = 80.0,
    status: RecordStatus = RecordStatus.VALIDATED,
    minuten: int = 0,
) -> Group:
    return Group(
        group_id=group_id,
        url_canonical=f"https://www.facebook.com/groups/{group_id}",
        name=name,
        city=city,
        audience_tags=audience_tags if audience_tags is not None else ["syrians"],
        category=category,
        score=score,
        status=status,
        first_seen_at=BASIS + timedelta(minutes=minuten),
    )


@pytest.fixture()
def bestand(tmp_path: Path) -> Path:
    """Fuenf Gruppen, bewusst verschieden - Stadt, Zielgruppe, Score, Status."""
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                _group("100000000000001", minuten=0),
                _group(
                    "100000000000002",
                    name="Araber in Hamburg",
                    city="Hamburg",
                    audience_tags=["arabs"],
                    category="jobs",
                    minuten=1,
                ),
                _group(
                    "100000000000003",
                    name="Syrer in Hamburg",
                    city="Hamburg",
                    score=30.0,
                    minuten=2,
                ),
                _group(
                    "100000000000004",
                    name="",
                    city=None,
                    audience_tags=[],
                    score=None,
                    status=RecordStatus.INSUFFICIENT_DATA,
                    minuten=3,
                ),
                _group(
                    "100000000000005",
                    name="Araber in Berlin",
                    audience_tags=["arabs"],
                    status=RecordStatus.DUPLICATE,
                    minuten=4,
                ),
            ]
        )
    return pfad


def _kampagne(store: MarketingStore, **felder) -> Campaign:
    campaign = Campaign(
        campaign_id="batreeq-syrian-germany", name="Batreeq Syrian Germany", **felder
    )
    store.save_campaign(campaign)
    return campaign


def _codes(store: MarketingStore, campaign_id: str = "batreeq-syrian-germany") -> dict[str, str]:
    return {link.group_id: link.tracking_code for link in store.links_for_campaign(campaign_id)}


# --- Die Auswahlregel ---------------------------------------------------

def test_ohne_einschraenkung_sind_alle_gruppen_gemeint(bestand: Path, config) -> None:
    """Leer heisst "alles". Sonst waere "alle Gruppen" ein Sonderfall."""
    with SqliteStore(bestand) as store:
        groups = store.load_groups()
    with MarketingStore(bestand) as store:
        campaign = _kampagne(store, target_include_unscored=True)

    auswahl = auswahl_der_kampagne(campaign, config)
    assert auswahl.ohne_einschraenkung
    assert len(waehle_gruppen(groups, auswahl)) == len(groups) == 5


def test_ohne_unbewertete_faellt_die_gruppe_ohne_score_heraus(bestand: Path, config) -> None:
    with SqliteStore(bestand) as store:
        groups = store.load_groups()
    with MarketingStore(bestand) as store:
        campaign = _kampagne(store)          # target_include_unscored ist False

    gewaehlt = waehle_gruppen(groups, auswahl_der_kampagne(campaign, config))
    assert {g.group_id for g in gewaehlt} == {
        "100000000000001",
        "100000000000002",
        "100000000000003",
        "100000000000005",
    }


@pytest.mark.parametrize(
    ("felder", "erwartet"),
    [
        ({"target_cities": ["berlin"]}, {"100000000000001", "100000000000005"}),
        ({"target_audiences": ["arabs"]}, {"100000000000002", "100000000000005"}),
        ({"target_categories": ["jobs"]}, {"100000000000002"}),
        ({"target_statuses": ["validated"]}, {"100000000000001", "100000000000002",
                                              "100000000000003"}),
        ({"target_min_score": 50.0}, {"100000000000001", "100000000000002",
                                      "100000000000005"}),
    ],
)
def test_jede_einschraenkung_wirkt_einzeln(
    bestand: Path, config, felder: dict, erwartet: set[str]
) -> None:
    with SqliteStore(bestand) as store:
        groups = store.load_groups()
    with MarketingStore(bestand) as store:
        campaign = _kampagne(store, **felder)

    gewaehlt = waehle_gruppen(groups, auswahl_der_kampagne(campaign, config))
    assert {g.group_id for g in gewaehlt} == erwartet


def test_stadtkennung_wird_ueber_die_konfiguration_uebersetzt(bestand: Path, config) -> None:
    """In der Kampagne steht "berlin", im Bestand "Berlin" - keine zweite Liste."""
    with MarketingStore(bestand) as store:
        campaign = _kampagne(store, target_cities=["berlin"])

    auswahl = auswahl_der_kampagne(campaign, config)
    assert "berlin" in auswahl.cities
    assert passt(_group("1", city="Berlin"), auswahl)
    assert not passt(_group("2", city="Hamburg"), auswahl)


def test_unbekannte_stadtkennung_verschwindet_nicht_stillschweigend(
    bestand: Path, config
) -> None:
    """Ein Tippfehler darf die Einschraenkung nicht heimlich aufheben."""
    with MarketingStore(bestand) as store:
        campaign = _kampagne(store, target_cities=["gibtsnicht"])

    auswahl = auswahl_der_kampagne(campaign, config)
    assert not auswahl.ohne_einschraenkung
    assert not passt(_group("1", city="Berlin"), auswahl)


# --- Zuordnung und Codevergabe ------------------------------------------

def test_alle_gruppen_bekommen_genau_einen_code(bestand: Path, config) -> None:
    with SqliteStore(bestand) as gruppen_store:
        groups = gruppen_store.load_groups()
    with MarketingStore(bestand) as store:
        campaign = _kampagne(store, target_include_unscored=True)
        plan = synchronisiere(store, groups, campaign, config)

        assert plan.anzahl_neu == 5
        codes = _codes(store)
        assert len(codes) == 5
        assert len(set(codes.values())) == 5


def test_zweiter_lauf_aendert_nichts(bestand: Path, config) -> None:
    """Wiederholbarkeit ist die Voraussetzung dafuer, dass es automatisch geht."""
    with SqliteStore(bestand) as gruppen_store:
        groups = gruppen_store.load_groups()
    with MarketingStore(bestand) as store:
        campaign = _kampagne(store, target_include_unscored=True)
        synchronisiere(store, groups, campaign, config)
        vorher = _codes(store)

        zweiter = synchronisiere(store, groups, campaign, config)

        assert zweiter.anzahl_neu == 0
        assert zweiter.bereits_zugeordnet == 5
        assert _codes(store) == vorher


def test_vergebener_code_bleibt_auch_bei_geaenderter_regel(bestand: Path, config) -> None:
    """Der Code steht moeglicherweise schon in einem Beitrag."""
    with SqliteStore(bestand) as gruppen_store:
        groups = gruppen_store.load_groups()
    with MarketingStore(bestand) as store:
        campaign = _kampagne(store, target_include_unscored=True)
        synchronisiere(store, groups, campaign, config)
        vorher = _codes(store)

        campaign.target_cities = ["berlin"]
        store.save_campaign(campaign)
        plan = synchronisiere(store, groups, campaign, config)

        assert _codes(store) == vorher                    # nichts entfernt
        assert len(plan.nicht_mehr_passend) == 3          # nur gemeldet
        assert "100000000000002" in plan.nicht_mehr_passend


def test_neue_gruppe_bekommt_die_naechste_freie_nummer(bestand: Path, config) -> None:
    with SqliteStore(bestand) as gruppen_store:
        groups = gruppen_store.load_groups()
        with MarketingStore(bestand) as store:
            campaign = _kampagne(store, target_cities=["berlin"])
            synchronisiere(store, groups, campaign, config)
            assert set(_codes(store).values()) == {"FB-SYR-BER-001", "FB-ARA-BER-001"}

            gruppen_store.upsert_groups(
                [_group("100000000000006", name="Syrer in Berlin 2", minuten=10)]
            )
            plan = synchronisiere(store, campaign=campaign, config=config,
                                  groups=gruppen_store.load_groups())

            assert plan.anzahl_neu == 1
            assert _codes(store)["100000000000006"] == "FB-SYR-BER-002"


def test_codes_sind_ueber_kampagnen_hinweg_eindeutig(bestand: Path, config) -> None:
    with SqliteStore(bestand) as gruppen_store:
        groups = gruppen_store.load_groups()
    with MarketingStore(bestand) as store:
        erste = _kampagne(store, target_include_unscored=True)
        synchronisiere(store, groups, erste, config)

        zweite = Campaign(campaign_id="zweite", name="Zweite", target_include_unscored=True)
        store.save_campaign(zweite)
        synchronisiere(store, groups, zweite, config)

        alle = [row["tracking_code"] for row in store.conn.execute(
            "SELECT tracking_code FROM campaign_groups"
        )]
        assert len(alle) == 10
        assert len(set(alle)) == 10


# --- Determinismus ------------------------------------------------------

def test_gleiche_eingabe_ergibt_gleiche_codes(bestand: Path, tmp_path: Path, config) -> None:
    with SqliteStore(bestand) as gruppen_store:
        groups = gruppen_store.load_groups()

    ergebnisse = []
    for nummer in (1, 2):
        pfad = tmp_path / f"lauf{nummer}.sqlite"
        shutil.copy(bestand, pfad)
        with MarketingStore(pfad) as store:
            campaign = _kampagne(store, target_include_unscored=True)
            synchronisiere(store, groups, campaign, config)
            ergebnisse.append(_codes(store))

    assert ergebnisse[0] == ergebnisse[1]


def test_rescore_aendert_die_codevergabe_nicht(bestand: Path, tmp_path: Path, config) -> None:
    """Die Vergabe haengt an ``first_seen_at``, nicht am Score.

    Frueher lief sie in Score-Reihenfolge. Damit bekam dieselbe Gruppe nach
    jeder Aenderung an den Gewichten eine andere Nummer - "deterministisch"
    war die Vergabe nur, solange niemand neu bewertete.
    """
    with SqliteStore(bestand) as gruppen_store:
        original = gruppen_store.load_groups()

    umgekehrt = []
    for i, group in enumerate(sorted(original, key=lambda g: g.group_id)):
        kopie = group.model_copy()
        kopie.score = float(i)          # Rangfolge auf den Kopf gestellt
        umgekehrt.append(kopie)

    ergebnisse = []
    for nummer, gruppen in ((1, original), (2, umgekehrt)):
        pfad = tmp_path / f"score{nummer}.sqlite"
        shutil.copy(bestand, pfad)
        with MarketingStore(pfad) as store:
            campaign = _kampagne(store, target_include_unscored=True)
            synchronisiere(store, gruppen, campaign, config)
            ergebnisse.append(_codes(store))

    assert ergebnisse[0] == ergebnisse[1]


# --- Codevergabe im Grossen ---------------------------------------------

def test_freigewordene_nummer_wird_nicht_wieder_vergeben(config) -> None:
    """Ein zurueckgegebener Code bleibt verbraucht - er kann veroeffentlicht sein."""
    allocator = CodeAllocator(config, {"FB-SYR-BER-001", "FB-SYR-BER-003"})
    assert allocator.next_for(_group("1")) == "FB-SYR-BER-004"


def test_tausend_gruppen_bekommen_tausend_verschiedene_codes(tmp_path: Path, config) -> None:
    """Der Massstab, auf den es ankommt: dasselbe Verhalten bei 1000 Gruppen."""
    pfad = tmp_path / "gross.sqlite"
    viele = [
        _group(f"9{i:014d}", name=f"Gruppe {i}", minuten=i)
        for i in range(1000)
    ]
    with SqliteStore(pfad) as gruppen_store:
        gruppen_store.upsert_groups(viele)
        groups = gruppen_store.load_groups()

    with MarketingStore(pfad) as store:
        campaign = _kampagne(store, target_include_unscored=True)
        plan = synchronisiere(store, groups, campaign, config)

        assert plan.anzahl_neu == 1000
        codes = _codes(store)
        assert len(set(codes.values())) == 1000
        # Alle in derselben Stadt/Zielgruppe: die Nummern muessen bis 1000 laufen.
        assert "FB-SYR-BER-1000" in codes.values()

        # Und der zweite Lauf legt nichts an.
        assert synchronisiere(store, groups, campaign, config).anzahl_neu == 0


# --- Automatische Uebernahme --------------------------------------------

def test_nur_aktive_kampagnen_mit_auto_assign_werden_gemeldet(bestand: Path) -> None:
    """Zwei Bedingungen, nicht eine: eingeschaltet **und** aktiv.

    Ein Entwurf ist noch nicht entschieden, und eine pausierte Kampagne soll
    gerade nicht weiterwachsen - sonst waere "pausiert" eine Beschriftung ohne
    Wirkung, und ein Suchlauf vergaebe Monate spaeter noch Codes fuer eine
    Kampagne, die niemand mehr betreibt.
    """
    with MarketingStore(bestand) as store:
        _kampagne(store)  # ohne auto_assign
        store.save_campaign(
            Campaign(
                campaign_id="automatisch",
                name="Automatisch",
                auto_assign=True,
                status=CampaignStatus.ACTIVE,
            )
        )
        store.save_campaign(
            Campaign(
                campaign_id="entwurf", name="Entwurf", auto_assign=True
            )  # status: draft
        )
        store.save_campaign(
            Campaign(
                campaign_id="pausiert",
                name="Pausiert",
                auto_assign=True,
                status=CampaignStatus.PAUSED,
            )
        )

        assert [c.campaign_id for c in store.campaigns_mit_auto_assign()] == ["automatisch"]


def test_von_hand_bleibt_jede_kampagne_zuordnbar(bestand: Path, config) -> None:
    """``campaign sync`` fragt nicht nach dem Status - dort steht ein Mensch davor.

    Der Statusfilter schuetzt vor dem *beilaeufigen* Wachsen im Suchlauf, nicht
    vor einer ausdruecklichen Entscheidung.
    """
    with SqliteStore(bestand) as gruppen_store, MarketingStore(bestand) as store:
        campaign = _kampagne(
            store, target_include_unscored=True, status=CampaignStatus.PAUSED
        )
        plan = synchronisiere(store, gruppen_store.load_groups(), campaign, config)

    assert plan.anzahl_neu > 0


def test_auto_assign_uebernimmt_spaeter_importierte_gruppen(bestand: Path, config) -> None:
    with SqliteStore(bestand) as gruppen_store, MarketingStore(bestand) as store:
        campaign = _kampagne(
            store,
            target_include_unscored=True,
            auto_assign=True,
            status=CampaignStatus.ACTIVE,
        )
        synchronisiere(store, gruppen_store.load_groups(), campaign, config)
        assert len(_codes(store)) == 5

        gruppen_store.upsert_groups(
            [_group("100000000000007", name="Neu gefunden", minuten=20)]
        )
        for kampagne in store.campaigns_mit_auto_assign():
            synchronisiere(store, gruppen_store.load_groups(), kampagne, config)

        assert len(_codes(store)) == 6
        assert "100000000000007" in _codes(store)


# --- Migration ----------------------------------------------------------

def _alte_datenbank(pfad: Path) -> None:
    """Eine Datei im Stand von Schema-Version 6 - ohne die target_*-Spalten.

    Die Marketing-Tabellen gehoeren dazu: Sie entstehen in Schritt 3, eine
    Datei auf Stand 6 hat sie also zwangslaeufig. Fehlten sie hier, pruefte der
    Test einen Zustand, den es nicht geben kann - und spaetere Schritte, die
    auf ihnen aufbauen, schluegen an der Fixture fehl statt an der Migration.
    """
    conn = sqlite3.connect(pfad)
    conn.executescript(
        """
        CREATE TABLE groups (
            group_id TEXT PRIMARY KEY, url_canonical TEXT NOT NULL,
            url_variants TEXT NOT NULL DEFAULT '[]', name TEXT NOT NULL DEFAULT '',
            description_snippet TEXT, member_count INTEGER,
            privacy_hint TEXT NOT NULL DEFAULT 'unknown', language_hint TEXT,
            audience_tags TEXT NOT NULL DEFAULT '[]',
            audience_confidence REAL NOT NULL DEFAULT 0, city TEXT, bundesland TEXT,
            city_confidence REAL NOT NULL DEFAULT 0, category TEXT,
            category_confidence REAL NOT NULL DEFAULT 0, score REAL, score_max REAL,
            score_reason TEXT NOT NULL DEFAULT '',
            score_breakdown TEXT NOT NULL DEFAULT '{}',
            validation_status TEXT NOT NULL DEFAULT 'valid',
            data_quality TEXT NOT NULL DEFAULT 'none',
            status TEXT NOT NULL DEFAULT 'new', notes TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
            times_seen INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE campaigns (
            campaign_id TEXT PRIMARY KEY, name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '', audiences TEXT NOT NULL DEFAULT '[]',
            cities TEXT NOT NULL DEFAULT '[]', language TEXT NOT NULL DEFAULT '',
            message_template TEXT NOT NULL DEFAULT '',
            landing_page TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'draft',
            starts_on TEXT, ends_on TEXT, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO campaigns VALUES
            ('alt', 'Alte Kampagne', '', '["syrians"]', '["berlin"]', '', '', '',
             'active', NULL, NULL, '2026-01-01T00:00:00+00:00',
             '2026-01-01T00:00:00+00:00');
        PRAGMA user_version = 6;
        """
    )
    # Aus dem aktuellen Schema, wie es Schritt 3 auch taete. Die bereits
    # angelegte alte 'campaigns' bleibt dank IF NOT EXISTS unangetastet -
    # genau darum geht es in diesem Test.
    conn.executescript(MARKETING_SCHEMA)
    conn.commit()
    conn.close()


def test_migration_uebernimmt_die_bisherige_reichweite(tmp_path: Path) -> None:
    """Eine Migration darf eine Kampagne nicht heimlich vergroessern."""
    pfad = tmp_path / "alt.sqlite"
    _alte_datenbank(pfad)

    with SqliteStore(pfad) as store:
        assert int(store.conn.execute("PRAGMA user_version").fetchone()[0]) == SCHEMA_VERSION

    with MarketingStore(pfad) as store:
        campaign = store.load_campaign("alt")

    assert campaign is not None
    assert campaign.target_audiences == ["syrians"]
    assert campaign.target_cities == ["berlin"]
    assert campaign.target_include_unscored is False
    assert campaign.auto_assign is False


def test_marketing_store_holt_die_migration_selbst_nach(tmp_path: Path) -> None:
    """``/r/{code}`` und ``/events`` oeffnen nur diesen Speicher.

    Ohne diesen Schritt fehlte auf einem Server, dessen Datei aus einer
    aelteren Fassung stammt, genau die neue Spalte - und die Weiterleitung
    stuerbe an einer Stelle, an der niemand eine Migration vermutet.
    """
    pfad = tmp_path / "alt.sqlite"
    _alte_datenbank(pfad)

    with MarketingStore(pfad) as store:
        assert store.load_campaign("alt") is not None
        assert int(store.conn.execute("PRAGMA user_version").fetchone()[0]) == SCHEMA_VERSION


def test_frisch_angelegte_datei_traegt_ihre_versionsnummer(tmp_path: Path) -> None:
    """Sonst haelt der naechste SqliteStore sie fuer eine Datei aus grauer Vorzeit."""
    pfad = tmp_path / "neu.sqlite"
    with MarketingStore(pfad) as store:
        store.save_campaign(Campaign(campaign_id="x", name="X"))

    with SqliteStore(pfad) as store:      # wirft SchemaVersionError, wenn 0
        assert store.count_groups() == 0


# --- Der echte Bestand --------------------------------------------------

@pytest.mark.skipif(not ECHTER_BESTAND.exists(), reason="Kein Bestand vorhanden")
def test_echter_bestand_behaelt_gruppen_und_die_acht_codes(tmp_path: Path, config) -> None:
    """Der Wirklichkeitstest: die echte Datei, nicht ein nachgebautes Beispiel.

    Die acht Codes stehen in veroeffentlichten Beitraegen. Nichts an der
    Zuordnung darf sie beruehren - auch nicht, wenn 300 weitere dazukommen.

    Geprueft wird **jede** vorgefundene Zuordnung, nicht nur die acht: Der Test
    soll auch dann noch die richtige Frage stellen, wenn der Bestand laengst
    weitergewachsen ist. Die acht sind zusaetzlich namentlich aufgefuehrt, weil
    sie den Anlass bilden und ihre Zuordnung zur Gruppe feststeht.
    """
    pfad = tmp_path / "echt.sqlite"
    shutil.copy(ECHTER_BESTAND, pfad)

    ACHT = {
        "FB-ARA-BER-001": "arabinberlin",
        "FB-ARA-BER-002": "arab.in.deutschland",
        "FB-ARA-BER-003": "266394850499868",
        "FB-ARA-BER-004": "998502627289006",
        "FB-ARA-BER-005": "araberlin",
        "FB-SYR-BER-001": "1477938102509363",
        "FB-SYR-BER-002": "1060505847347081",
        "FB-SYR-BER-003": "888294561239687",
    }

    with SqliteStore(pfad) as gruppen_store:
        vorher_gruppen = gruppen_store.count_groups()
        vorher_scores = {
            row["group_id"]: row["score"]
            for row in gruppen_store.conn.execute("SELECT group_id, score FROM groups")
        }
        groups = gruppen_store.load_groups()

    with MarketingStore(pfad) as store:
        campaign = store.load_campaign("batreeq-syrian-germany")
        if campaign is None:
            # Die Datei allein ist kein Bestand: Ein Befehl legt sie leer an,
            # sobald er einmal laeuft. Wer den Bestand bewusst neu aufsetzt,
            # hat danach eine gueltige, leere Datenbank - und dieser Test
            # haette nichts mehr zu pruefen. Ein Fehlschlag waere dann eine
            # Aussage ueber die Datenlage, nicht ueber den Code.
            pytest.skip("Kampagne nicht im Bestand - nichts zu pruefen")
        vorher_links = {
            link.group_id: (link.tracking_code, link.tracking_url)
            for link in store.links_for_campaign(campaign.campaign_id)
        }
        for code, group_id in ACHT.items():
            assert vorher_links[group_id][0] == code

        campaign.target_audiences = []
        campaign.target_cities = []
        campaign.target_include_unscored = True
        campaign.status = CampaignStatus.ACTIVE
        store.save_campaign(campaign)

        synchronisiere(store, groups, campaign, config)
        nachher = {
            link.group_id: (link.tracking_code, link.tracking_url)
            for link in store.links_for_campaign(campaign.campaign_id)
        }

    assert len(nachher) == vorher_gruppen
    for group_id, wert in vorher_links.items():
        assert nachher[group_id] == wert          # zeichengleich, samt URL
    for code, group_id in ACHT.items():
        assert nachher[group_id][0] == code

    codes = [code for code, _url in nachher.values()]
    assert len(set(codes)) == len(codes)

    with SqliteStore(pfad) as gruppen_store:
        assert gruppen_store.count_groups() == vorher_gruppen
        assert {
            row["group_id"]: row["score"]
            for row in gruppen_store.conn.execute("SELECT group_id, score FROM groups")
        } == vorher_scores


def test_plan_ist_ohne_speichern_dieselbe_rechnung(bestand: Path, config) -> None:
    """--dry-run und Ernstfall lesen denselben Plan - sonst luegt die Vorschau."""
    with SqliteStore(bestand) as gruppen_store:
        groups = gruppen_store.load_groups()
    with MarketingStore(bestand) as store:
        campaign = _kampagne(store, target_include_unscored=True)

        vorschau = baue_plan(
            groups, campaign, config,
            vorhandene_gruppen=store.assigned_group_ids(campaign.campaign_id),
            vergebene_codes=store.assigned_codes(),
        )
        assert store.links_for_campaign(campaign.campaign_id) == []

        echt = synchronisiere(store, groups, campaign, config)

        assert [link.tracking_code for _g, link in vorschau.neu] == [
            link.tracking_code for _g, link in echt.neu
        ]


def test_top_begrenzt_die_neuen_nicht_die_passenden(bestand: Path, config) -> None:
    """Sonst kaeme ein zweiter Lauf nie ueber die bereits zugeordneten hinaus."""
    with SqliteStore(bestand) as gruppen_store:
        groups = gruppen_store.load_groups()
    with MarketingStore(bestand) as store:
        campaign = _kampagne(store, target_include_unscored=True)

        erst = baue_plan(
            groups, campaign, config,
            vorhandene_gruppen=set(), vergebene_codes=set(), top=2,
        )
        store.add_links([link for _g, link in erst.neu])
        assert erst.anzahl_neu == 2

        zweit = baue_plan(
            groups, campaign, config,
            vorhandene_gruppen=store.assigned_group_ids(campaign.campaign_id),
            vergebene_codes=store.assigned_codes(),
            top=2,
        )
        assert zweit.anzahl_neu == 2
        assert zweit.bereits_zugeordnet == 2


def test_zuordnung_nur_zu_bekannten_gruppen(bestand: Path, config) -> None:
    with MarketingStore(bestand) as store:
        _kampagne(store)
        with pytest.raises(KeyError):
            store.add_links(
                [
                    CampaignGroup(
                        campaign_id="batreeq-syrian-germany",
                        group_id="gibt-es-nicht",
                        tracking_code="FB-SYR-BER-999",
                    )
                ]
            )


def test_migration_schliesst_gruppen_ohne_daten_aus(tmp_path: Path) -> None:
    """Datensaetze ohne verwertbare Daten starten ausgeschlossen.

    Von ihnen ist nichts als eine URL bekannt - meist ein Treffer auf einen
    Beitrag statt auf ein Gruppenprofil. Arbeiten laesst sich daran nicht, und
    in der Arbeitsliste verdecken sie die brauchbaren Gruppen: Beim ersten Lauf
    waren es 144 von 413.

    Der Ausschluss ist eine Voreinstellung, kein Urteil - ein Klick nimmt ihn
    zurueck. Und er ueberschreibt nichts: Eine bereits von Hand gepflegte Zeile
    bleibt, wie sie ist.
    """
    pfad = tmp_path / "alt.sqlite"
    _alte_datenbank(pfad)

    conn = sqlite3.connect(pfad)
    conn.executescript(
        """
        INSERT INTO groups (group_id, url_canonical, name, status,
                            first_seen_at, last_seen_at)
        VALUES ('ohne', 'https://www.facebook.com/groups/ohne', '',
                'insufficient_data', '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:00:00+00:00'),
               ('mit', 'https://www.facebook.com/groups/mit', 'Syrer Berlin',
                'validated', '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:00:00+00:00'),
               ('haende', 'https://www.facebook.com/groups/haende', '',
                'insufficient_data', '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:00:00+00:00');
        INSERT INTO group_marketing (group_id, bearbeiten, ausschlussgrund, updated_at)
        VALUES ('haende', 1, 'von Hand geprueft', '2026-01-01T00:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()

    with SqliteStore(pfad):
        pass

    with MarketingStore(pfad) as store:
        ohne = store.load_marketing("ohne")
        mit = store.load_marketing("mit")
        haende = store.load_marketing("haende")

    assert ohne.bearbeiten is False
    assert ohne.ausschlussgrund == "keine Daten"
    assert mit.bearbeiten is True          # bewertbare Gruppe bleibt in der Liste
    assert haende.bearbeiten is True       # Menschenurteil wird nicht ueberschrieben
    assert haende.ausschlussgrund == "von Hand geprueft"


@pytest.fixture()
def mit_zuordnungen(bestand: Path, config) -> Path:
    """Bestand plus eine Kampagne, der alle Gruppen zugeordnet sind."""
    with SqliteStore(bestand) as gruppen_store:
        gruppen = gruppen_store.load_groups()
    with MarketingStore(bestand) as store:
        synchronisiere(store, gruppen, _kampagne(store), config)
    return bestand


# --- Kampagne loeschen ----------------------------------------------------

def test_die_vorschau_aendert_nichts(mit_zuordnungen: Path, config) -> None:
    """Ohne Bestaetigung wird nur gezeigt, was verlorenginge.

    Ein Loeschen nimmt die Tracking-Codes mit, und die stehen moeglicherweise
    in veroeffentlichten Beitraegen. Dieselbe Vorsicht wie bei ``sync``:
    zeigen, dann handeln.
    """
    pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    client = TestClient(create_app(config=config, db_path=mit_zuordnungen))

    antwort = client.post("/kampagnen/batreeq-syrian-germany/loeschen", json={"bestaetigt": False})

    assert antwort.status_code == 200
    assert antwort.json()["geloescht"] is False
    with MarketingStore(mit_zuordnungen) as store:
        assert store.load_campaign("batreeq-syrian-germany") is not None


def test_die_vorschau_nennt_die_veroeffentlichten_codes(mit_zuordnungen: Path) -> None:
    """Die einzige Zahl, die sich nicht wiederherstellen laesst."""
    with MarketingStore(mit_zuordnungen) as store:
        links = store.links_for_campaign("batreeq-syrian-germany")
        assert links
        store.set_post_status(
            "batreeq-syrian-germany", links[0].group_id, PostStatus.VEROEFFENTLICHT
        )

        verlust = store.was_geht_verloren("batreeq-syrian-germany")

    assert verlust["veroeffentlichte_codes"] == 1
    assert verlust["zuordnungen"] == len(links)


def test_loeschen_nimmt_die_zuordnungen_mit(mit_zuordnungen: Path) -> None:
    """``ON DELETE CASCADE`` - und das PRAGMA, ohne das es wirkungslos waere.

    Ohne eingeschaltete Fremdschluessel bliebe ``campaign_groups`` stehen: Die
    Codes waeren nicht mehr aufloesbar, aber weiterhin vergeben - der naechste
    Lauf koennte dieselbe Nummer ein zweites Mal ausgeben.
    """
    with MarketingStore(mit_zuordnungen) as store:
        assert store.links_for_campaign("batreeq-syrian-germany")

        store.delete_campaign("batreeq-syrian-germany")

        assert store.load_campaign("batreeq-syrian-germany") is None
        assert store.links_for_campaign("batreeq-syrian-germany") == []


def test_die_ereignisse_ueberleben_das_loeschen(mit_zuordnungen: Path) -> None:
    """Eine Auswertung von gestern behaelt ihre Zahlen.

    Sie haengen an keinem Fremdschluessel. Was danach fehlt, ist allein der Weg
    vom Code zurueck zur Gruppe.
    """
    from fbgroups.marketing.models import EventType, TrackingEvent

    with MarketingStore(mit_zuordnungen) as store:
        links = store.links_for_campaign("batreeq-syrian-germany")
        store.record_event(
            TrackingEvent(
                tracking_code=links[0].tracking_code,
                campaign_id="batreeq-syrian-germany",
                group_id=links[0].group_id,
                event_type=EventType.CLICK,
            )
        )

        store.delete_campaign("batreeq-syrian-germany")

        assert store.event_counts().get(EventType.CLICK.value) == 1
        assert store.resolve_code(links[0].tracking_code) is None


def test_loeschen_ist_von_aussen_nicht_moeglich(mit_zuordnungen: Path, config) -> None:
    pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    fremd = TestClient(
        create_app(config=config, db_path=mit_zuordnungen), client=("203.0.113.7", 44321)
    )

    assert fremd.post(
        "/kampagnen/batreeq-syrian-germany/loeschen", json={"bestaetigt": True}
    ).status_code == 404
