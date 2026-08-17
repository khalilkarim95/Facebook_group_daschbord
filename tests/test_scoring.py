from __future__ import annotations

from fbgroups.models import Group, RecordStatus, ValidationStatus
from fbgroups.scoring import score_all, score_group, sort_by_rank

REAL_ID = "482910573829104"


def make_group(**kwargs) -> Group:
    defaults = {
        "group_id": REAL_ID,
        "url_canonical": f"https://www.facebook.com/groups/{REAL_ID}",
        "name": "Testgruppe Berlin",
    }
    return Group(**{**defaults, **kwargs})


def _full_group(**kwargs) -> Group:
    """Gruppe mit allen Bewertungsbestandteilen."""
    defaults = {
        "name": "Syrer in Berlin Community",
        "audience_tags": ["syrians"],
        "audience_confidence": 1.0,
        "city": "Berlin",
        "city_confidence": 1.0,
        "category": "community",
        "category_confidence": 1.0,
        "member_count_hint": 25000,
    }
    return make_group(**{**defaults, **kwargs})


def test_score_bleibt_in_0_bis_100(config) -> None:
    best = _full_group(
        name="Syrer und Araber in Berlin Community",
        audience_tags=["syrians", "arabs"],
        member_count_hint=90000,
    )
    schwach = make_group(name="Irgendein Treffpunkt", member_count_hint=50)

    for group in (best, schwach):
        score_group(group, config)
        assert group.score is not None
        assert 0.0 <= group.score <= 100.0

    assert best.score > schwach.score


def test_ohne_ausreichende_daten_kein_score(config) -> None:
    group = score_group(make_group(name=""), config)
    assert group.score is None
    assert group.status is RecordStatus.INSUFFICIENT_DATA
    assert group.score_reason.startswith("insufficient_data")


def test_breakdown_summiert_sich_zum_score(config) -> None:
    """Der Score ist die Summe seiner Bestandteile - keine Hochrechnung."""
    group = score_group(_full_group(), config)
    # Der Score ist auf eine Nachkommastelle gerundet, die Summe nicht.
    assert abs(group.score_breakdown.total() - group.score) < 0.1
    assert group.score_max == 100.0


def test_ohne_mitgliederzahl_sinkt_die_erreichbare_punktzahl(
    config_mit_mitgliederzahl,
) -> None:
    """Fehlt ein eingeschalteter Bestandteil, sinkt die erreichbare Punktzahl.

    Regressionstest: Frueher wurde auf 100 hochgerechnet - eine Gruppe, von der
    nur der Name bekannt war, erhielt damit denselben Hoechstwert wie eine
    Gruppe mit belegten 50.000 Mitgliedern.
    """
    config = config_mit_mitgliederzahl
    gewicht = float(config.get("scoring", "weights", "member_count", default=45))
    ohne = score_group(_full_group(member_count_hint=None), config)

    # Bewusst aus der Konfiguration abgeleitet: Der Test soll eine geaenderte
    # Gewichtung ueberstehen, nicht an einer festen Zahl scheitern.
    assert ohne.score_max == 100.0 - gewicht
    assert ohne.score <= ohne.score_max
    assert ohne.score_breakdown.member_count == 0.0
    assert "Mitgliederzahl unbekannt" in ohne.score_reason

    mit = score_group(_full_group(member_count_hint=50000), config)
    assert mit.score > ohne.score


def test_gewicht_null_schaltet_den_bestandteil_ganz_ab(config) -> None:
    """Abgeschaltet ist etwas anderes als unbekannt.

    Bei ``member_count: 0`` darf die Mitgliederzahl weder Punkte bringen noch
    die erreichbare Punktzahl senken noch als "unbekannt" in der Begruendung
    stehen - sonst traegt jede Zeile des Exports einen Mangel vor sich her, den
    das Projekt gar nicht beheben kann. Der Test haengt am Projektstand: Wird
    die Mitgliederzahl wieder eingeschaltet, prueft er nichts mehr.
    """
    gewicht = float(config.get("scoring", "weights", "member_count", default=45))
    if gewicht > 0:
        return

    mit_zahl = score_group(_full_group(member_count_hint=50000), config)
    ohne_zahl = score_group(_full_group(member_count_hint=None), config)

    assert mit_zahl.score == ohne_zahl.score
    assert mit_zahl.score_max == ohne_zahl.score_max == 100.0
    assert mit_zahl.score_breakdown.member_count == 0.0
    assert "Mitglieder" not in mit_zahl.score_reason


def test_mehr_mitglieder_ergibt_hoeheren_score(config_mit_mitgliederzahl) -> None:
    klein = score_group(_full_group(member_count_hint=300), config_mit_mitgliederzahl)
    gross = score_group(_full_group(member_count_hint=60000), config_mit_mitgliederzahl)
    assert gross.score > klein.score


def test_mitgliederklassen_unabhaengig_von_der_reihenfolge(config) -> None:
    """Die Groessenklassen werden absteigend ausgewertet, nicht in Dateireihenfolge."""
    from fbgroups.scoring import _member_count_factor

    assert _member_count_factor(60000, config) > _member_count_factor(300, config)
    assert _member_count_factor(0, config) < _member_count_factor(1000, config)


def test_zielgruppe_und_stadt_zaehlen_nur_einmal(config) -> None:
    """Regressionstest gegen die Doppelzaehlung in name_quality.

    Zielgruppe und Stadt haben eigene Gewichte. Zaehlten sie zusaetzlich in
    name_quality, erreichten beide Bestandteile stets gemeinsam ihr Maximum -
    27 Gruppen standen dadurch auf exakt 100.
    """
    mit_bezug = score_group(_full_group(name="Syrer in Berlin Community"), config)
    ohne_bezug = _full_group(
        name="Syrer in Berlin Community",
        audience_tags=[],
        audience_confidence=0.0,
        city=None,
        city_confidence=0.0,
    )
    score_group(ohne_bezug, config)

    assert mit_bezug.score_breakdown.name_quality == ohne_bezug.score_breakdown.name_quality


def test_beitragstitel_bekommt_weniger_namenspunkte_als_ein_gruppenname(config) -> None:
    gruppe = score_group(_full_group(name="Syrer in Berlin Community"), config)
    beitrag = score_group(
        _full_group(name="Deutschland geht erst unter seit Mutter Merkel den Syrern ..."),
        config,
    )
    frage = score_group(
        _full_group(name="وين اطيب شاورما وأطيب حلو بشارع العرب برلين؟ شكرا سلفا"),
        config,
    )

    assert beitrag.score_breakdown.name_quality < gruppe.score_breakdown.name_quality
    # Arabisches Fragezeichen: ohne eigene Pruefung bliebe der Fall unerkannt.
    assert frage.score_breakdown.name_quality < gruppe.score_breakdown.name_quality


def test_kategorie_aus_dem_beschreibungstext_zaehlt_schwaecher(config) -> None:
    im_namen = score_group(_full_group(category_confidence=1.0), config)
    im_text = score_group(_full_group(category_confidence=0.5), config)
    assert im_text.score < im_namen.score


def test_score_streut_statt_zu_verklumpen(config) -> None:
    """Verschiedene Datenlagen muessen verschiedene Scores ergeben.

    Bewusst ohne Mitgliederzahl: Sie liegt im Betrieb keiner Gruppe vor, die
    Streuung muss also aus den uebrigen Bestandteilen entstehen.
    """
    varianten = [
        _full_group(member_count_hint=None),
        _full_group(member_count_hint=None, category=None, category_confidence=0.0),
        _full_group(member_count_hint=None, city=None, city_confidence=0.0),
        _full_group(member_count_hint=None, city_confidence=0.5),
        _full_group(member_count_hint=None, category_confidence=0.5),
        _full_group(member_count_hint=None, name="Syrer in Berlin ..."),
    ]
    scores = {score_group(g, config).score for g in varianten}
    assert len(scores) == len(varianten)


def test_mitgliederzahl_ist_das_schwerste_kriterium(config_mit_mitgliederzahl) -> None:
    """Eine grosse Gruppe schlaegt eine perfekt passende ohne belegte Groesse."""
    config = config_mit_mitgliederzahl
    gross = _full_group(
        group_id="739201847362915",
        name="Gruppe",              # kein Zielgruppen-, Stadt- oder Kategoriebezug
        audience_tags=["syrians"],
        audience_confidence=0.5,
        city=None,
        city_confidence=0.0,
        category=None,
        category_confidence=0.0,
        member_count_hint=90000,
    )
    passend_ohne_zahl = _full_group(member_count_hint=None)

    assert score_group(gross, config).score > score_group(passend_ohne_zahl, config).score


def test_bei_gleichstand_gewinnt_der_hoehere_anteil() -> None:
    """Gleiche Punktzahl, unterschiedliche Datenlage: der bessere Anteil zuerst.

    55 von 55 heisst "alles belegt, was vorlag"; 55 von 100 heisst "die Groesse
    ist bekannt und klein". Fuer eine Ansprache ist das erste der bessere Fund.
    """
    voll_ausgeschoepft = make_group(name="A", score=55.0, score_max=55.0)
    kleine_gruppe = make_group(group_id="739201847362915", name="B", score=55.0, score_max=100.0)

    ranked = sort_by_rank([kleine_gruppe, voll_ausgeschoepft])
    assert [g.name for g in ranked] == ["A", "B"]


def test_geprueft_nicht_erreichbar_wird_nicht_bewertet(config) -> None:
    group = _full_group(validation_status=ValidationStatus.UNREACHABLE)
    score_group(group, config)

    assert group.score is None
    assert group.status is RecordStatus.INVALID
    assert group.score_reason.startswith("unreachable")


def test_score_all_sortiert_bewertete_nach_vorne(config) -> None:
    groups = [
        make_group(group_id="739201847362915", name="Ohne weitere Angaben"),
        _full_group(member_count_hint=50000),
    ]
    ranked = score_all(groups, config)

    assert ranked[0].score is not None
    assert ranked[-1].score is None       # nicht bewertbar steht hinten


def test_ungueltige_gruppe_wird_nicht_bewertet(config) -> None:
    group = _full_group(validation_status=ValidationStatus.INVALID)
    score_group(group, config)
    assert group.score is None
    assert group.status is RecordStatus.INVALID
    assert group.score_reason.startswith("invalid")


def test_status_wird_beim_scoring_gesetzt(config) -> None:
    assert score_group(_full_group(), config).status is RecordStatus.VALIDATED
