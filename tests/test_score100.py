"""Der 100-Punkte-Score als Ganzes - Registry, Anzeige, Export.

Die Einzelregeln stehen in ``test_scoring.py``. Hier steht, was das Ganze
zusammenhaelt: dass die Registry die einzige Quelle der Bestandteile ist,
dass die Anzeige die Bewertung belegen kann statt sie zu behaupten, und dass
ein unbekannter Wert nirgends unterwegs zu einer Null wird.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.export.columns import COLUMNS, headers, row_values
from fbgroups.models import (
    ActivitySource,
    Group,
    MemberCountSource,
    ScoreBreakdown,
)
from fbgroups.scoring import BESTANDTEILE, Befund, Lage, bestandteil, gewichte, score_group

REAL_ID = "482910573829104"


@pytest.fixture()
def bestand_leer(tmp_path: Path) -> Path:
    """Eine frisch angelegte Bestandsdatei - mit dem aktuellen Schema."""
    return tmp_path / "groups.sqlite"


def _gruppe(**kwargs) -> Group:
    daten = {
        "group_id": REAL_ID,
        "url_canonical": f"https://www.facebook.com/groups/{REAL_ID}",
        "name": "Syrer in Bonn",
        "audience_tags": ["syrians"],
        "audience_confidence": 1.0,
        "city": "Bonn",
        "city_confidence": 1.0,
        "category": "community",
        "category_confidence": 1.0,
    }
    return Group(**{**daten, **kwargs})


# --- Die Registry ist die einzige Quelle ------------------------------------


def test_die_gewichte_ergeben_hundert(config) -> None:
    """Die fachliche Vorgabe, als Zusicherung."""
    assert sum(gewichte(config).values()) == 100


def test_reichweite_und_betrieb_tragen_die_haelfte(config) -> None:
    """Der Score darf nicht ueberwiegend aus Kategorie und Zielgruppe entstehen."""
    aktiv = gewichte(config)

    assert aktiv["members"] + aktiv["activity"] == 50
    assert aktiv["members"] == aktiv["activity"]   # keiner ersetzt den anderen


def test_die_aufschluesselung_kennt_jeden_bestandteil() -> None:
    """Ein Bestandteil ohne Feld in ScoreBreakdown wuerde still verschwinden.

    ``score_group`` baut die Aufschluesselung aus den Namen der Registry.
    Fehlte dort ein Feld, wuerfe Pydantic - dieser Test macht daraus einen
    verstaendlichen Fehler statt eines Absturzes im Betrieb.
    """
    felder = set(ScoreBreakdown().model_dump())

    assert set(BESTANDTEILE) <= felder


def test_ein_unbekanntes_gewicht_wird_nicht_still_geschluckt(config) -> None:
    """Ein Tippfehler in settings.yaml darf nicht als Erweiterung durchgehen."""
    from dataclasses import replace

    verdreht = replace(config, settings={
        **config.settings,
        "scoring": {**config.settings["scoring"], "weights": {"mitglieder": 25}},
    })

    # "mitglieder" gibt es nicht - "members" schon. Der unbekannte Name wird
    # ignoriert, der echte behaelt seine Vorgabe; config-check meldet es.
    assert "mitglieder" not in gewichte(verdreht)
    assert gewichte(verdreht)["members"] == 25.0


def test_ein_neuer_bestandteil_braucht_nur_funktion_und_gewicht(config) -> None:
    """Die Zusicherung der Registry: erweitern heisst eintragen, nicht umbauen."""
    from dataclasses import replace

    @bestandteil("probe", "Probe", 10.0)
    def _probe(lage: Lage) -> Befund | None:
        return Befund(faktor=1.0, konfidenz=1.0, quelle="Test")

    try:
        mit = replace(config, settings={
            **config.settings,
            "scoring": {
                **config.settings["scoring"],
                "weights": {**config.settings["scoring"]["weights"], "probe": 10},
            },
        })
        ergebnis = score_group(_gruppe(), mit)

        assert "Probe 10 (Test)" in ergebnis.score_reason
        assert ergebnis.score_max == 60.0    # 50 Passung + 10 Probe
    finally:
        del BESTANDTEILE["probe"]


# --- Unbekannt bleibt unbekannt ---------------------------------------------


@pytest.mark.parametrize(
    "feld", ["member_count", "posts_per_day", "activity_factor", "last_post_at"]
)
def test_kein_feld_wird_unterwegs_zu_null(config, feld: str) -> None:
    """``None`` heisst unbekannt und darf nirgends zu 0 werden.

    "0 Mitglieder" bedeutet etwas anderes als "Mitgliederzahl unbekannt" -
    und der Unterschied entscheidet, wo die naechsten Beitraege hingehen.
    """
    ergebnis = score_group(_gruppe(), config)

    assert getattr(ergebnis, feld) is None


def test_die_herkunft_wandert_mit_der_zahl(bestand_leer: Path, config) -> None:
    """Eine Zahl ohne Quellenangabe liesse sich spaeter nicht mehr einordnen."""
    from fbgroups.storage import SqliteStore

    gruppe = _gruppe(
        member_count=42_300, member_count_source=MemberCountSource.FACEBOOK,
        posts_per_day=18.0, activity_factor=0.9,
        activity_source=ActivitySource.FACEBOOK, activity_confidence=1.0,
    )
    with SqliteStore(bestand_leer) as store:
        store.upsert_groups([gruppe])
        zurueck = store.load_groups()[0]

    assert zurueck.member_count == 42_300
    assert zurueck.member_count_source is MemberCountSource.FACEBOOK
    assert zurueck.activity_source is ActivitySource.FACEBOOK
    assert zurueck.posts_per_day == 18.0


def test_ein_suchlauf_loescht_erhobene_zahlen_nicht(bestand_leer: Path) -> None:
    """Der haeufigste Weg, gute Daten zu verlieren.

    ``fbgroups search`` schreibt jeden gefundenen Datensatz neu und bringt
    weder Mitgliederzahl noch Aktivitaet mit. Ohne COALESCE im Upsert loeschte
    jeder Suchlauf, was ``fbgroups enrich`` erhoben hat - und niemand merkte
    es, weil der Score einfach wieder sank.
    """
    from fbgroups.storage import SqliteStore

    with SqliteStore(bestand_leer) as store:
        store.upsert_groups([
            _gruppe(
                member_count=42_300, member_count_source=MemberCountSource.FACEBOOK,
                posts_per_day=18.0, activity_factor=0.9,
                activity_source=ActivitySource.FACEBOOK,
            )
        ])
        # Derselbe Fund noch einmal - wie ihn ein Suchtreffer liefert: ohne Zahlen.
        store.upsert_groups([_gruppe()])
        zurueck = store.load_groups()[0]

    assert zurueck.member_count == 42_300
    assert zurueck.posts_per_day == 18.0
    assert zurueck.activity_factor == 0.9


# --- Export -----------------------------------------------------------------


def test_der_export_zeigt_die_bestandteile_einzeln(config) -> None:
    """Eine Zahl, die sich nicht nachrechnen laesst, ist keine Auskunft."""
    zeile = dict(zip(headers(), row_values(score_group(_gruppe(), config)), strict=True))

    assert zeile["Punkte Ort"] == 15.0
    assert zeile["Punkte Kategorie"] == 20.0
    assert zeile["Punkte Zielgruppe"] == 15.0
    # Unbekannt heisst 0 Punkte - und die Begruendung nennt den Grund.
    assert zeile["Punkte Mitglieder"] == 0.0
    assert "Mitglieder unbekannt" in zeile["Score Reason"]


def test_unbewertete_gruppen_bekommen_keine_nullen_im_export(config) -> None:
    """Eine 0 neben einem leeren Score waere eine Aussage ueber eine Gruppe,
    ueber die keine gemacht wurde."""
    ohne = score_group(Group(group_id="1", url_canonical="u"), config)
    zeile = dict(zip(headers(), row_values(ohne), strict=True))

    assert ohne.score is None
    assert zeile["Punkte Mitglieder"] == ""
    assert zeile["Punkte Aktivitaet"] == ""


def test_die_spaltennamen_sind_eindeutig() -> None:
    namen = [name for name, _ in COLUMNS]
    beschriftungen = [label for _, label in COLUMNS]

    assert len(namen) == len(set(namen))
    assert len(beschriftungen) == len(set(beschriftungen))
