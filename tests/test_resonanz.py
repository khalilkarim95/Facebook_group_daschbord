"""Tests fuer die gemessene Resonanz im Score.

Die entscheidende Unterscheidung, die hier abgesichert wird: **nicht gemessen**
ist etwas anderes als **wirkungslos**. Eine Gruppe, in der noch nie ein Beitrag
stand, darf nicht neben einer stehen, deren Beitrag niemand angeklickt hat -
die eine ist unbearbeitet, die andere beantwortet.

Die Resonanz ist seit dem 27.08.2026 kein eigener Bewertungsblock mehr,
sondern eine **Quelle des Bestandteils ``activity``** - neben der
Beitragsliste der Gruppenseite und den Datumsangaben der Suchtreffer. Zwei
Bloecke waeren zweimal dieselbe Frage gewesen. Die Rechnung darin ist
unveraendert; was sich geaendert hat, ist, wohin ihr Ergebnis fliesst.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    EventType,
    PostStatus,
    TrackingEvent,
)
from fbgroups.marketing.resonanz import resonanz_je_gruppe
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.scoring import Resonanz, score_group
from fbgroups.storage import SqliteStore

REAL_ID_A = "482910573829104"
REAL_ID_B = "739201847362915"


def _gruppe(group_id: str = REAL_ID_A) -> Group:
    """Eine vollstaendig eingeordnete Gruppe - die Passung ist damit konstant."""
    return Group(
        group_id=group_id,
        url_canonical=f"https://www.facebook.com/groups/{group_id}",
        name="Syrer in Berlin",
        description_snippet="Gruppe fuer Syrerinnen und Syrer in Berlin",
        audience_tags=["syrians"],
        audience_confidence=1.0,
        city="Berlin",
        city_confidence=1.0,
        category="community",
        category_confidence=1.0,
    )


@pytest.fixture()
def resonanz_an(config):
    """Konfiguration mit eingeschalteter Resonanz.

    Nicht die des Projekts: Die Tests duerfen nicht davon abhaengen, wie die
    Gewichte gerade stehen - sonst pruefen sie die Konfiguration statt die
    Rechnung.
    """
    settings = {
        **config.settings,
        "scoring": {
            **config.settings.get("scoring", {}),
            "weights": {
                # Die Mitgliederzahl bleibt hier aus: Diese Datei prueft die
                # Resonanzrechnung, nicht die Groessenklassen. Waere sie an,
                # senkte ihr Fehlen score_max in jedem Test um 25.
                "members": 0,
                "activity": 25,
                "category": 20,
                "location": 15,
                "target_audience": 15,
                "name_quality": 0,
            },
            "resonanz": {
                "ziel_quote": 0.15,
                "mindest_klicks": 20,
                "ziel_klicks_je_beitrag": 25,
                "aktualitaet_tage": 30,
                "schonfrist_tage": 3,
                "anteile": {
                    "engagement": 0.60,
                    "reichweite": 0.25,
                    "aktualitaet": 0.15,
                },
            },
        },
    }
    return replace(config, settings=settings)


def _vor(tage: float) -> datetime:
    return datetime.now(UTC) - timedelta(days=tage)


# --- Die Kernunterscheidung -------------------------------------------------

def test_ohne_beitrag_bleibt_die_resonanz_unbekannt(resonanz_an) -> None:
    """Null Klicks ohne Beitrag sagen etwas ueber uns, nicht ueber die Gruppe."""
    ohne = score_group(_gruppe(), resonanz_an, Resonanz(beitraege=0))

    assert ohne.score_max == 50.0          # nur die Passung ist erreichbar
    assert "Aktivitaet unbekannt" in ohne.score_reason
    assert ohne.score_breakdown.activity == 0.0


def test_beitrag_ohne_klicks_ist_ein_ergebnis(resonanz_an) -> None:
    """Hier wurde gemessen - und es kam nichts dabei heraus.

    Der Unterschied zum Fall oben steht in score_max: 75 statt 50. Die
    Gruppe hatte ihre Gelegenheit.
    """
    gemessen = score_group(
        _gruppe(),
        resonanz_an,
        Resonanz(beitraege=1, klicks=0, erster_beitrag_am=_vor(20)),
    )

    assert gemessen.score_max == 75.0
    # Die Passung ist voll, die Aktivitaet steuert nichts bei - aber sie
    # wurde gemessen, und genau das steht in score_max.
    assert gemessen.score == 50.0
    assert gemessen.score_breakdown.activity == 0.0
    assert "Aktivitaet unbekannt" not in gemessen.score_reason


def test_frischer_beitrag_zaehlt_noch_nicht(resonanz_an) -> None:
    """Wer vor zwei Stunden gepostet hat, hat noch keine Klicks.

    Eine Null waere hier eine Behauptung ueber die Zukunft.
    """
    frisch = score_group(
        _gruppe(),
        resonanz_an,
        Resonanz(beitraege=1, klicks=0, erster_beitrag_am=_vor(0.1)),
    )

    assert frisch.score_max == 50.0
    assert "Aktivitaet unbekannt" in frisch.score_reason


# --- Der Score bewegt sich mit den Daten ------------------------------------

def test_mehr_registrierungen_heben_den_score(resonanz_an) -> None:
    """Die Anforderung in einem Satz: Aendert sich die Aktivitaet, aendert
    sich der Score."""
    basis = dict(beitraege=1, klicks=100, erster_beitrag_am=_vor(10), letzte_regung=_vor(1))

    schwach = score_group(_gruppe(), resonanz_an, Resonanz(**basis, registrierungen=1))
    mittel = score_group(_gruppe(), resonanz_an, Resonanz(**basis, registrierungen=8))
    stark = score_group(_gruppe(), resonanz_an, Resonanz(**basis, registrierungen=20))

    assert schwach.score < mittel.score < stark.score
    # Die Passung ist in allen drei Faellen dieselbe - der Unterschied kommt
    # ausschliesslich aus der gemessenen Resonanz.
    assert schwach.score_breakdown.target_audience == stark.score_breakdown.target_audience


def test_kleine_aktive_gruppe_schlaegt_grosse_stille(resonanz_an) -> None:
    """Das Beispiel aus der Anforderung - mit den Zahlen, die wir haben.

    Gruppe A hat viel Reichweite, aber niemand bleibt. Gruppe B hat weniger
    Klicks, aber die Menschen registrieren sich. B muss gewinnen.
    """
    gross_still = score_group(
        _gruppe(REAL_ID_A),
        resonanz_an,
        Resonanz(
            beitraege=1, klicks=200, registrierungen=2,
            erster_beitrag_am=_vor(20), letzte_regung=_vor(1),
        ),
    )
    klein_aktiv = score_group(
        _gruppe(REAL_ID_B),
        resonanz_an,
        Resonanz(
            beitraege=1, klicks=60, registrierungen=18,
            erster_beitrag_am=_vor(20), letzte_regung=_vor(1),
        ),
    )

    assert klein_aktiv.score > gross_still.score


def test_alte_regung_senkt_die_aktualitaet(resonanz_an) -> None:
    """Eine Gruppe, die seit Monaten nicht reagiert, ist heute keine gute Wahl."""
    basis = dict(beitraege=1, klicks=100, registrierungen=15, erster_beitrag_am=_vor(200))

    frisch = score_group(_gruppe(), resonanz_an, Resonanz(**basis, letzte_regung=_vor(1)))
    alt = score_group(_gruppe(), resonanz_an, Resonanz(**basis, letzte_regung=_vor(200)))

    assert frisch.score > alt.score
    # Die Aktualitaet steckt jetzt mit im EINEN Aktivitaetsfaktor. Sichtbar
    # bleibt sie an der Differenz: dieselben Klicks, dieselbe Quote, nur
    # aelter - und damit weniger Punkte, aber nicht null.
    assert alt.score_breakdown.activity < frisch.score_breakdown.activity
    assert alt.score_breakdown.activity > 0.0
    # Die Passung bleibt unberuehrt.
    assert frisch.score_breakdown.category == alt.score_breakdown.category


def test_ein_klick_mit_einer_registrierung_ist_nicht_die_beste_gruppe(resonanz_an) -> None:
    """100 % aus einem einzigen Klick beweisen nichts.

    Ohne die Belastbarkeitsschranke stuende jede Gruppe mit genau einem Klick
    und einer Registrierung an der Spitze der Rangliste.
    """
    zufall = score_group(
        _gruppe(),
        resonanz_an,
        Resonanz(beitraege=1, klicks=1, registrierungen=1,
                 erster_beitrag_am=_vor(20), letzte_regung=_vor(1)),
    )
    belegt = score_group(
        _gruppe(),
        resonanz_an,
        Resonanz(beitraege=1, klicks=100, registrierungen=15,
                 erster_beitrag_am=_vor(20), letzte_regung=_vor(1)),
    )

    assert belegt.score > zufall.score


def test_reichweite_zaehlt_je_beitrag_nicht_absolut(resonanz_an) -> None:
    """Sonst gewaenne die Gruppe, in der wir am oeftesten gepostet haben."""
    einmal = score_group(
        _gruppe(),
        resonanz_an,
        Resonanz(beitraege=1, klicks=50, registrierungen=8,
                 erster_beitrag_am=_vor(20), letzte_regung=_vor(1)),
    )
    fuenfmal = score_group(
        _gruppe(),
        resonanz_an,
        Resonanz(beitraege=5, klicks=50, registrierungen=8,
                 erster_beitrag_am=_vor(20), letzte_regung=_vor(1)),
    )

    assert einmal.score_breakdown.activity > fuenfmal.score_breakdown.activity


def test_score_bleibt_in_der_spanne(resonanz_an) -> None:
    """Auch bei absurden Zahlen kein Ueberlauf ueber score_max hinaus."""
    extrem = score_group(
        _gruppe(),
        resonanz_an,
        Resonanz(beitraege=1, klicks=100000, registrierungen=100000,
                 erster_beitrag_am=_vor(5), letzte_regung=datetime.now(UTC)),
    )

    assert 0 <= extrem.score <= extrem.score_max == 75.0


# --- Abschaltbar wie jedes andere Gewicht -----------------------------------

def test_gewicht_null_schaltet_die_resonanz_ganz_ab(config) -> None:
    """Ohne Gewichte bleibt die Bewertung exakt die bisherige.

    Dieselbe Regel wie bei der Mitgliederzahl: 0 nimmt den Bestandteil aus der
    Bewertung, er erscheint nicht einmal als "unbekannt".
    """
    aus = replace(config, settings={
        **config.settings,
        "scoring": {
            **config.settings.get("scoring", {}),
            "weights": {
                "members": 0, "activity": 0, "name_quality": 0,
                "category": 20, "location": 15, "target_audience": 15,
            },
        },
    })

    ergebnis = score_group(
        _gruppe(), aus,
        Resonanz(beitraege=1, klicks=100, registrierungen=50, letzte_regung=_vor(1)),
    )

    assert ergebnis.score_max == 50.0
    assert "Aktivitaet" not in ergebnis.score_reason


# --- Die Zahlen kommen aus der Datenbank ------------------------------------

@pytest.fixture()
def bestand(tmp_path: Path) -> Path:
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups([_gruppe(REAL_ID_A), _gruppe(REAL_ID_B)])
    with MarketingStore(pfad) as store:
        store.save_campaign(Campaign(campaign_id="batreeq", name="Batreeq"))
        for gid, code in ((REAL_ID_A, "FB-SYR-BER-001"), (REAL_ID_B, "FB-SYR-BER-002")):
            store.add_link(
                CampaignGroup(campaign_id="batreeq", group_id=gid, tracking_code=code)
            )
    return pfad


def test_nur_gruppen_mit_veroeffentlichtem_beitrag_erscheinen(bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        assert resonanz_je_gruppe(store) == {}

        store.set_post_status("batreeq", REAL_ID_A, PostStatus.VEROEFFENTLICHT)
        gemessen = resonanz_je_gruppe(store)

    assert set(gemessen) == {REAL_ID_A}
    assert gemessen[REAL_ID_A].beitraege == 1


def test_klicks_und_registrierungen_werden_der_gruppe_zugeordnet(bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        store.set_post_status("batreeq", REAL_ID_A, PostStatus.VEROEFFENTLICHT)
        for _ in range(4):
            store.record_event(TrackingEvent(
                tracking_code="FB-SYR-BER-001", campaign_id="batreeq",
                group_id=REAL_ID_A, event_type=EventType.CLICK,
            ))
        store.record_event(TrackingEvent(
            tracking_code="FB-SYR-BER-001", campaign_id="batreeq",
            group_id=REAL_ID_A, event_type=EventType.REGISTRATION, user_ref="u1",
        ))
        gemessen = resonanz_je_gruppe(store)[REAL_ID_A]

    assert gemessen.klicks == 4
    assert gemessen.registrierungen == 1
    assert gemessen.letzte_regung is not None
    assert gemessen.erster_beitrag_am is not None


def test_der_score_folgt_der_datenbank(bestand: Path, resonanz_an) -> None:
    """Der Durchstich: Ereignis eintragen -> neu bewerten -> Score steigt.

    Genau die Anforderung "der Score aktualisiert sich, wenn die Aktivitaet
    steigt" - hier ueber denselben Weg, den 'fbgroups rescore' geht.
    """
    from fbgroups.scoring import score_all

    with SqliteStore(bestand) as gstore:
        gruppen = gstore.load_groups()

    with MarketingStore(bestand) as store:
        store.set_post_status("batreeq", REAL_ID_A, PostStatus.VEROEFFENTLICHT)
        # Kuenstlich in die Vergangenheit, sonst greift die Schonfrist.
        store.conn.execute(
            "UPDATE campaign_groups SET posted_at = ? WHERE group_id = ?",
            (_vor(20).isoformat(), REAL_ID_A),
        )
        store.conn.commit()

        vorher = {g.group_id: g.score for g in score_all(
            gruppen, resonanz_an, resonanz_je_gruppe(store)
        )}

        for _ in range(40):
            store.record_event(TrackingEvent(
                tracking_code="FB-SYR-BER-001", campaign_id="batreeq",
                group_id=REAL_ID_A, event_type=EventType.CLICK,
            ))
        for i in range(10):
            store.record_event(TrackingEvent(
                tracking_code="FB-SYR-BER-001", campaign_id="batreeq",
                group_id=REAL_ID_A, event_type=EventType.REGISTRATION, user_ref=f"u{i}",
            ))

        with SqliteStore(bestand) as gstore:
            gruppen = gstore.load_groups()
        nachher = {g.group_id: g.score for g in score_all(
            gruppen, resonanz_an, resonanz_je_gruppe(store)
        )}

    assert nachher[REAL_ID_A] > vorher[REAL_ID_A]
    # Die Gruppe ohne Beitrag bleibt unberuehrt.
    assert nachher[REAL_ID_B] == vorher[REAL_ID_B]


# --- Uebersicht -------------------------------------------------------------

def test_uebersicht_belegt_den_score_mit_zahlen(bestand: Path, config) -> None:
    """Die Zeile soll die Bewertung belegen, nicht nur behaupten."""
    from fbgroups.marketing.dashboard import sammle_daten

    with MarketingStore(bestand) as store:
        store.set_post_status("batreeq", REAL_ID_A, PostStatus.VEROEFFENTLICHT)
        store.conn.execute(
            "UPDATE campaign_groups SET posted_at = ? WHERE group_id = ?",
            (_vor(20).isoformat(), REAL_ID_A),
        )
        store.conn.commit()
        for _ in range(10):
            store.record_event(TrackingEvent(
                campaign_id="batreeq", group_id=REAL_ID_A, event_type=EventType.CLICK))
        store.record_event(TrackingEvent(
            campaign_id="batreeq", group_id=REAL_ID_A,
            event_type=EventType.REGISTRATION, user_ref="u1"))

    daten = sammle_daten(config, bestand)
    gemessen = next(z for z in daten["gruppen"] if z["id"] == REAL_ID_A)
    ohne = next(z for z in daten["gruppen"] if z["id"] == REAL_ID_B)

    assert gemessen["resonanz"]["klicks"] == 10
    assert gemessen["resonanz"]["registrierungen"] == 1
    assert gemessen["resonanz"]["quote"] == 10.0
    assert gemessen["resonanz"]["beitraege"] == 1
    # Die Einzelteile des Scores stehen als Zahlen daneben.
    assert "target_audience" in gemessen["punkte"]

    # Ohne veroeffentlichten Beitrag gibt es nichts zu zeigen - und ausdruecklich
    # keine Null, die als "geprueft und wertlos" gelesen werden koennte.
    assert ohne["resonanz"] is None


def test_die_resonanz_hat_keine_eigene_spalte_mehr(bestand: Path, config) -> None:
    """Die Spalte ist weg, die gemessene Resonanz ist es nicht.

    Sie stand mit Quote, Verhaeltnis, Beitragszahl und letzter Regung in jeder
    der 300 Zeilen und war damit die breiteste Spalte der Tabelle - fuer eine
    Angabe, die man je Gruppe einmal nachschlaegt. Ihre Zahlen stehen weiter
    daneben: Klicks und Registrierungen haben eigene Spalten, und ihr Anteil
    am Score steht in der Aufschluesselung am Score selbst.

    Der Unterschied, auf den es ankommt: Sie ist aus der *Anzeige* verschwunden,
    nicht aus der Bewertung. Waere sie aus `punkte` verschwunden, hiesse das,
    dass sie nicht mehr zaehlt - und das ist etwas voellig anderes. Dass die
    Zahlen weiterhin in den Daten der Seite stehen, prueft der Test darueber.
    """
    from fbgroups.marketing.dashboard import render, sammle_daten

    seite = render(sammle_daten(config, bestand))

    assert "resonanzZelle" not in seite
    assert ">Resonanz</th>" not in seite

    # Die Aufschluesselung des Scores steht im Tooltip - und nennt die
    # Resonanzbestandteile weiterhin beim Namen.
    assert "punkteText" in seite
    assert "activity" in seite
