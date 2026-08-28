from __future__ import annotations

from fbgroups.models import ActivitySource, Group, RecordStatus, ValidationStatus
from fbgroups.scoring import _namensform, gewichte, score_all, score_group, sort_by_rank

REAL_ID = "482910573829104"

#: Gewicht der Aktivitaet - sie fehlt jeder Gruppe ohne erhobene Zahlen und
#: senkt damit score_max. Aus der Registry statt als feste Zahl, damit eine
#: geaenderte Gewichtung die Tests nicht reihenweise umwirft.
_AKTIVITAET = 25.0


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
        "member_count": 25000,
    }
    return make_group(**{**defaults, **kwargs})


def test_score_bleibt_in_0_bis_100(config) -> None:
    best = _full_group(
        name="Syrer und Araber in Berlin Community",
        audience_tags=["syrians", "arabs"],
        member_count=90000,
    )
    schwach = make_group(name="Irgendein Treffpunkt", member_count=50)

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
    # 100 minus der Aktivitaet: Sie ist eingeschaltet, liegt fuer diese Gruppe
    # aber nicht vor - genau der Fall, den score_max sichtbar machen soll.
    assert group.score_max == 100.0 - _AKTIVITAET


def test_ohne_mitgliederzahl_sinkt_die_erreichbare_punktzahl(config) -> None:
    """Fehlt ein eingeschalteter Bestandteil, sinkt die erreichbare Punktzahl.

    Regressionstest: Frueher wurde auf 100 hochgerechnet - eine Gruppe, von der
    nur der Name bekannt war, erhielt damit denselben Hoechstwert wie eine
    Gruppe mit belegten 50.000 Mitgliedern.
    """
    gewicht = float(config.get("scoring", "weights", "members", default=25))
    ohne = score_group(_full_group(member_count=None), config)

    # Bewusst aus der Konfiguration abgeleitet: Der Test soll eine geaenderte
    # Gewichtung ueberstehen, nicht an einer festen Zahl scheitern.
    assert ohne.score_max == 100.0 - gewicht - _AKTIVITAET
    assert ohne.score <= ohne.score_max
    assert ohne.score_breakdown.members == 0.0
    assert "Mitglieder unbekannt" in ohne.score_reason

    mit = score_group(_full_group(member_count=50000), config)
    assert mit.score > ohne.score


def test_gewicht_null_schaltet_den_bestandteil_ganz_ab(config) -> None:
    """Abgeschaltet ist etwas anderes als unbekannt.

    Ein Bestandteil mit Gewicht 0 darf weder Punkte bringen noch die
    erreichbare Punktzahl senken noch als "unbekannt" in der Begruendung
    stehen - sonst traegt jede Zeile des Exports einen Mangel vor sich her,
    den niemand beheben will. Geprueft an ``name_quality``: Es steht im
    Projekt auf 0, weil die Form des Namens etwas ueber unsere Daten sagt und
    nichts ueber die Gruppe.
    """
    assert float(config.get("scoring", "weights", "name_quality", default=0)) == 0

    mit_form = score_group(_full_group(name="Syrer in Berlin"), config)
    schlecht = score_group(_full_group(name="Ist das noch normal?!"), config)

    assert mit_form.score == schlecht.score
    assert mit_form.score_breakdown.name_quality == 0.0
    assert "Name" not in mit_form.score_reason


def test_mehr_mitglieder_ergibt_hoeheren_score(config) -> None:
    klein = score_group(_full_group(member_count=300), config)
    gross = score_group(_full_group(member_count=60000), config)
    assert gross.score > klein.score


def test_mitgliederklassen_unabhaengig_von_der_reihenfolge(config) -> None:
    """Die Groessenklassen werden absteigend ausgewertet, nicht in Dateireihenfolge."""
    gross = score_group(_full_group(member_count=60000), config).score_breakdown.members
    klein = score_group(_full_group(member_count=300), config).score_breakdown.members
    winzig = score_group(_full_group(member_count=0), config).score_breakdown.members
    mittel = score_group(_full_group(member_count=1000), config).score_breakdown.members

    assert gross > klein
    assert winzig < mittel


def test_die_mitgliederzahl_waechst_logarithmisch(config) -> None:
    """Zehnmal so viele Mitglieder sind nicht zehnmal so viele Punkte.

    Oberhalb einiger tausend entscheidet nicht mehr die Groesse, sondern ob
    dort etwas geschieht - dafuer gibt es "activity". Eine lineare Skala gaebe
    der groessten Gruppe alles und machte den Rest ununterscheidbar.
    """
    punkte = {
        n: score_group(_full_group(member_count=n), config).score_breakdown.members
        for n in (1_000, 10_000, 100_000)
    }

    assert punkte[1_000] < punkte[10_000] < punkte[100_000]
    # Zehnfache Groesse, aber weit weniger als zehnfache Punkte.
    assert punkte[10_000] < punkte[1_000] * 3
    assert punkte[100_000] < punkte[10_000] * 2


def test_reichweite_und_betrieb_tragen_die_haelfte(config) -> None:
    """Die fachliche Vorgabe: 50 von 100 Punkten, mathematisch nachweisbar.

    Der Score darf nicht ueberwiegend aus Kategorie und Zielgruppe entstehen -
    eine thematisch perfekte Gruppe, in der nichts geschieht, ist kein guter
    Platz fuer einen Beitrag.
    """
    gewichtung = gewichte(config)

    assert gewichtung["members"] + gewichtung["activity"] == 50
    assert sum(gewichtung.values()) == 100


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

    # Der Bestandteil ist abgeschaltet (Gewicht 0), die Regel dahinter gilt
    # weiter und wird direkt geprueft: Sie speist jetzt data_confidence.
    assert beitrag.score == gruppe.score
    assert _namensform(beitrag.name) < _namensform(gruppe.name)
    # Arabisches Fragezeichen: ohne eigene Pruefung bliebe der Fall unerkannt.
    assert _namensform(frage.name) < _namensform(gruppe.name)


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
        _full_group(member_count=None),
        _full_group(member_count=None, category=None, category_confidence=0.0),
        _full_group(member_count=None, city=None, city_confidence=0.0),
        _full_group(member_count=None, city_confidence=0.5),
        _full_group(member_count=None, category_confidence=0.5),
        # Nur das Bundesland erkannt: eine eigene Ortsstufe, nicht "nichts".
        _full_group(
            member_count=None, city=None, city_confidence=0.0,
            bundesland="Nordrhein-Westfalen",
        ),
    ]
    scores = {score_group(g, config).score for g in varianten}
    assert len(scores) == len(varianten)


def test_groesse_allein_schlaegt_die_passung_nicht(config) -> None:
    """Die Mitgliederzahl ist die Haelfte der Haelfte, nicht das Wichtigste.

    Bis zum 27.08.2026 trug sie 45 von 100 Punkten und gewann damit gegen
    jede Passung. Jetzt sind es 25 - eine Grossgruppe ohne erkennbares Thema,
    ohne Ort und mit halber Zielgruppenkonfidenz steht damit zu Recht hinter
    einer perfekt passenden Gruppe unbekannter Groesse.
    """
    gross_ohne_passung = _full_group(
        group_id="739201847362915",
        name="Gruppe",              # kein Zielgruppen-, Stadt- oder Kategoriebezug
        audience_confidence=0.5,
        city=None,
        city_confidence=0.0,
        category=None,
        category_confidence=0.0,
        member_count=90000,
    )
    passend_ohne_zahl = _full_group(member_count=None)

    assert (
        score_group(gross_ohne_passung, config).score
        < score_group(passend_ohne_zahl, config).score
    )


def test_grosse_stille_gruppe_verliert_gegen_kleine_lebendige(config) -> None:
    """Der Fall aus der Anforderung, als Zahl.

    Gruppe A: 100.000 Mitglieder, kaum neue Beitraege.
    Gruppe B:  20.000 Mitglieder, sehr viel Betrieb.
    B muss gewinnen - sonst waere die Aktivitaet eine Beschriftung ohne Wirkung.
    """
    still = _full_group(
        member_count=100_000,
        activity_factor=0.02, activity_source=ActivitySource.FACEBOOK,
        activity_confidence=1.0,
    )
    lebendig = _full_group(
        group_id="739201847362915",
        member_count=20_000,
        activity_factor=1.0, activity_source=ActivitySource.FACEBOOK,
        activity_confidence=1.0,
    )

    a = score_group(still, config)
    b = score_group(lebendig, config)

    assert b.score > a.score
    assert a.score_breakdown.members > b.score_breakdown.members    # A ist groesser
    assert b.score_breakdown.activity > a.score_breakdown.activity  # B ist lebendiger


def test_die_konfidenz_veraendert_den_score_nicht(config) -> None:
    """"Wie sicher?" und "wie gut?" sind zwei Fragen.

    Dieselben Zahlen aus verschiedenen Quellen ergeben denselben Score - und
    eine verschiedene data_confidence. Wuerde die Confidence in den Score
    einfliessen, waeren beide Auskuenfte unlesbar.
    """
    from fbgroups.models import MemberCountSource

    belegt = score_group(
        _full_group(member_count=25_000, member_count_source=MemberCountSource.FACEBOOK),
        config,
    )
    zitiert = score_group(
        _full_group(member_count=25_000, member_count_source=MemberCountSource.SEARCH),
        config,
    )

    assert belegt.score == zitiert.score
    assert belegt.data_confidence > zitiert.data_confidence


def test_die_konfidenz_faellt_mit_der_datenlage(config) -> None:
    """Wenig belegt heisst niedrige Confidence - auch wenn das Belegte sicher ist.

    Ohne diesen Anteil bekaeme eine Gruppe, von der nur die Stadt bekannt ist,
    aber zweifelsfrei, die volle Confidence - und die Zahl sagte das Gegenteil
    dessen aus, wozu sie da ist.
    """
    viel = score_group(_full_group(member_count=25_000), config)
    wenig = score_group(
        _full_group(member_count=None, category=None, category_confidence=0.0),
        config,
    )

    assert viel.data_confidence > wenig.data_confidence
    assert 0.0 <= wenig.data_confidence <= 1.0


def test_nebenkategorien_heben_die_hauptkategorie_gedeckelt(config) -> None:
    """Drei Themen sind mehr wert als eines - aber nicht beliebig viel mehr.

    Ohne Deckel gewaenne die Gruppe mit dem laengsten Namen.
    """
    eine = score_group(_full_group(category_confidence=0.5), config)
    drei = score_group(
        _full_group(category_confidence=0.5, secondary_categories=["a", "b", "c"]), config
    )
    zehn = score_group(
        _full_group(category_confidence=0.5, secondary_categories=list("abcdefghij")), config
    )

    assert drei.score_breakdown.category > eine.score_breakdown.category
    assert zehn.score_breakdown.category == drei.score_breakdown.category


def test_der_ort_kennt_vier_stufen(config) -> None:
    """Stadt vor Bundesland vor Land vor unbekannt - und unbekannt ist nicht null."""
    stadt = score_group(_full_group(), config)
    land_teil = score_group(
        _full_group(city=None, city_confidence=0.0, bundesland="Nordrhein-Westfalen"), config
    )
    land = score_group(
        _full_group(city=None, city_confidence=0.0, country="Deutschland"), config
    )
    nichts = score_group(_full_group(city=None, city_confidence=0.0), config)

    punkte = [g.score_breakdown.location for g in (stadt, land_teil, land)]
    assert punkte[0] > punkte[1] > punkte[2] > 0

    # Unbekannt heisst: der Bestandteil entfaellt, score_max sinkt.
    assert nichts.score_breakdown.location == 0.0
    assert nichts.score_max < land.score_max
    assert "Ort unbekannt" in nichts.score_reason


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
        _full_group(member_count=50000),
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
