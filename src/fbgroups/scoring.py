"""Priorisierung der Gruppen.

Vier Regeln bestimmen den Entwurf:

1. **Kein Score ohne Grundlage.** Reicht die Datenlage nicht, ist der Score
   ``None`` und ``score_reason`` nennt den Grund. Ein Ersatzwert waere eine
   Behauptung ueber Daten, die nicht vorliegen. Dasselbe gilt je Bestandteil:
   Ein Bestandteil ohne Grundlage liefert ``None`` und nicht ``0`` - "keine
   Aktivitaet" und "Aktivitaet unbekannt" sind zwei verschiedene Aussagen.
2. **Jeder Bestandteil zaehlt genau einmal.** Groesse, Betrieb, Ort, Thema und
   Zielgruppe haben je ein Gewicht. Frueher vergab ``name_quality`` zusaetzlich
   Punkte fuer Zielgruppe und Stadt - beide zaehlten damit doppelt und
   erreichten stets gemeinsam ihr Maximum.
3. **Fehlende Angaben senken die erreichbare Punktzahl, statt den Rest
   hochzurechnen.** Der Score ist die Summe der tatsaechlich belegten Punkte,
   ``score_max`` nennt das bei dieser Datenlage Erreichbare. Die frueher
   verwendete Normierung auf 100 fuehrte dazu, dass eine Gruppe, von der nur
   der Name bekannt war, denselben Hoechstwert erhielt wie eine Gruppe mit
   belegten 50.000 Mitgliedern: 27 Gruppen standen auf exakt 100.
4. **Die Konfidenz steht neben dem Score, nie darin.** Ein maessiger Score aus
   belegten Zahlen und ein guter aus duennen Hinweisen sind zwei verschiedene
   Aussagen; sie zu verrechnen machte beide unlesbar. ``data_confidence``
   beantwortet "wie sicher?", der Score "wie gut?".

**Ein Bestandteil ergaenzen** heisst: eine Funktion schreiben, sie mit
``@bestandteil(...)`` eintragen und in ``config/settings.yaml`` ein Gewicht
setzen. Mehr nicht - ``score_group`` laeuft ueber die Registry und kennt keinen
einzigen Bestandteil beim Namen. Alle Gewichte stehen in der Konfiguration.

Die Aufteilung der 100 Punkte:

===================  =======  ================================================
Bestandteil          Gewicht  Grundlage
===================  =======  ================================================
``members``               25  Mitgliederzahl, logarithmisch gestuft
``activity``              25  Betrieb in der Gruppe (mehrere Quellen)
``category``              20  Haupt- und Nebenkategorien
``location``              15  Stadt, sonst Bundesland, sonst Land
``target_audience``       15  erkannte Zielgruppen
===================  =======  ================================================

Reichweite und Betrieb tragen damit zusammen die Haelfte - das ist die
fachliche Vorgabe und keine Feinheit: Eine Gruppe, die thematisch perfekt
passt und in der nichts geschieht, ist kein guter Platz fuer einen Beitrag.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from fbgroups.config import AppConfig
from fbgroups.models import (
    ActivitySource,
    DataQuality,
    Group,
    MemberCountSource,
    RecordStatus,
    ScoreBreakdown,
    ValidationStatus,
)
from fbgroups.validation import assess_data_quality, determine_status, has_sufficient_data

_NICHT_BEWERTBAR = {
    ValidationStatus.TEST_DATA: "test_data: Platzhalter-Kennung, keine Bewertung",
    ValidationStatus.INVALID: "invalid: unbrauchbare URL, keine Bewertung",
    ValidationStatus.UNREACHABLE: (
        "unreachable: von Hand geprueft, Gruppe nicht erreichbar - keine Bewertung"
    ),
}

# Von der Suchmaschine gekuerzter Titel - der Name ist nachweislich unvollstaendig.
_TRUNCATION_MARKERS = ("...", "…", "..")
# Satzzeichen eines Beitrags, nicht eines Gruppennamens. "؟" ist das arabische
# Fragezeichen; ohne diese Zeichen bliebe die Haelfte der Faelle unerkannt.
_SENTENCE_MARKERS = ("?", "؟", "!")

# Wie belastbar eine Mitgliederzahl je nach Herkunft ist. Die Zahl auf der
# Gruppenseite ist die Zahl; ein Suchtreffer-Snippet ist ein Zitat davon,
# moeglicherweise Monate alt. Von Hand gepflegt heisst: Ein Mensch hat
# nachgesehen - belastbar, aber auch nur zum Zeitpunkt des Nachsehens.
_KONFIDENZ_MITGLIEDER = {
    MemberCountSource.FACEBOOK: 1.0,
    MemberCountSource.MANUAL: 0.9,
    MemberCountSource.SEARCH: 0.6,
}

# Dieselbe Abstufung fuer die Aktivitaet. ``search_dates`` ist bewusst niedrig:
# Ein indexierter Beitrag von vor zwei Wochen belegt, dass die Gruppe lebt -
# mehr nicht. Eine Beitragszahl je Tag ist daraus nicht abzuleiten.
_KONFIDENZ_AKTIVITAET = {
    ActivitySource.FACEBOOK: 1.0,
    ActivitySource.SEARCH_DATES: 0.35,
}


# ---------------------------------------------------------------------------
# Die Registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Befund:
    """Was ein Bestandteil ergeben hat - Anteil, Belastbarkeit, Herkunft.

    ``faktor`` ist der Anteil des Gewichts (0-1) und damit die Note.
    ``konfidenz`` sagt, wie belastbar die Grundlage war, und geht **nicht** in
    den Score ein - sie wird zu ``data_confidence`` verrechnet und daneben
    angezeigt. ``quelle`` steht in der Begruendung: "Mitglieder 22 (facebook)"
    ist eine Auskunft, "Mitglieder 22" ist eine Zahl.

    Ein Bestandteil, der ``None`` statt eines Befunds liefert, gilt als
    unbekannt: Er senkt ``score_max``, statt eine Null zu behaupten.
    """

    faktor: float
    konfidenz: float = 1.0
    quelle: str = ""


@dataclass(frozen=True)
class Lage:
    """Alles, was ein Bestandteil zum Beurteilen braucht.

    Ein Objekt statt vieler Argumente, damit eine neue Grundlage (etwa Zahlen
    aus einer weiteren Quelle) keine Signaturaenderung an jedem Bestandteil
    erzwingt - genau der Fall, der eine Registry sonst wieder aufloest.
    """

    group: Group
    config: AppConfig
    jetzt: datetime | None = None

    @property
    def zeitpunkt(self) -> datetime:
        return self.jetzt or datetime.now(UTC)


@dataclass(frozen=True)
class Bestandteil:
    """Ein Bewertungsbestandteil: Name, Beschriftung, Vorgabegewicht, Regel."""

    name: str
    label: str
    gewicht: float
    pruefe: Callable[[Lage], Befund | None]


#: Alle bekannten Bestandteile in Anzeigereihenfolge. Der Score liest
#: ausschliesslich hier - ``score_group`` nennt keinen Bestandteil beim Namen.
BESTANDTEILE: dict[str, Bestandteil] = {}


def bestandteil(
    name: str, label: str, gewicht: float
) -> Callable[[Callable[[Lage], Befund | None]], Callable[[Lage], Befund | None]]:
    """Traegt eine Bewertungsregel in die Registry ein.

    ``gewicht`` ist die **Vorgabe**; ``config/settings.yaml`` schlaegt sie.
    Ein Gewicht von 0 nimmt den Bestandteil vollstaendig aus der Bewertung -
    er zaehlt weder Punkte noch erscheint er als "unbekannt".
    """

    def deko(fn: Callable[[Lage], Befund | None]) -> Callable[[Lage], Befund | None]:
        assert name not in BESTANDTEILE, f"Bestandteil {name} gibt es zweimal"
        BESTANDTEILE[name] = Bestandteil(name, label, gewicht, fn)
        return fn

    return deko


def gewichte(config: AppConfig) -> dict[str, float]:
    """Die geltenden Gewichte - Konfiguration schlaegt Vorgabe, 0 faellt raus.

    Ohne den Filter setzten die Vorgaben der Registry ein abgeschaltetes
    Gewicht gegen die Konfiguration wieder ein.
    """
    aus_config = config.get("scoring", "weights", default={}) or {}
    zusammen = {name: teil.gewicht for name, teil in BESTANDTEILE.items()}
    for name, wert in aus_config.items():
        if name in zusammen:
            zusammen[name] = float(wert)
    return {name: wert for name, wert in zusammen.items() if wert > 0}


# ---------------------------------------------------------------------------
# Die Bestandteile
# ---------------------------------------------------------------------------


@bestandteil("members", "Mitglieder", 25.0)
def _members(lage: Lage) -> Befund | None:
    """Die Groesse der Gruppe, logarithmisch gestuft.

    **Nicht linear**, und das ist der Kern: Eine Gruppe mit 100.000
    Mitgliedern ist nicht zehnmal so wertvoll wie eine mit 10.000. Oberhalb
    einiger tausend Mitglieder entscheidet nicht mehr die Groesse, sondern ob
    dort etwas geschieht - dafuer gibt es ``activity``. Die Stufen stehen in
    ``scoring.member_count_buckets``; wer eine echte Logarithmusformel
    bevorzugt, ersetzt hier eine Funktion und laesst alles andere stehen.

    ``None`` heisst unbekannt. Eine geschaetzte Zahl waere hier der
    gefaehrlichste Fehler des ganzen Moduls: Sie wanderte in die Datenbank,
    saehe wie eine gemessene aus und entschiede darueber, wo die naechsten
    dreihundert Beitraege geschrieben werden.
    """
    if lage.group.member_count is None:
        return None

    buckets = lage.config.get("scoring", "member_count_buckets", default=[]) or []
    faktor = 0.0
    # Absteigend und nicht in Dateireihenfolge: Stuende die kleinste Klasse
    # versehentlich zuerst, bekaeme sonst jede Gruppe deren Faktor.
    for bucket in sorted(buckets, key=lambda b: int(b.get("min", 0)), reverse=True):
        if lage.group.member_count >= int(bucket.get("min", 0)):
            faktor = float(bucket.get("factor", 0.0))
            break

    quelle = lage.group.member_count_source
    return Befund(
        faktor=faktor,
        konfidenz=_KONFIDENZ_MITGLIEDER.get(quelle, 0.5) if quelle else 0.5,
        quelle=quelle.value if quelle else "ohne Angabe",
    )


@bestandteil("activity", "Aktivitaet", 25.0)
def _activity(lage: Lage) -> Befund | None:
    """Wie viel in der Gruppe geschieht - aus der besten verfuegbaren Quelle.

    Genauso schwer wie die Mitgliederzahl, und bewusst **unabhaengig** von
    ihr: Eine Gruppe mit 100.000 Mitgliedern und kaum neuen Beitraegen ist
    ein schlechterer Platz als eine mit 20.000 und taeglichem Betrieb. Wer
    beides aus derselben Zahl ableitete, koennte den Fall nicht abbilden.

    Die Quelle ist ``group.activity_factor`` - die erhobene Zahl. Woher sie
    stammt, steht in ``activity_source``; gepflegt wird sie von Hand bzw.
    ueber die Uebersicht. Fehlt sie, ist das Ergebnis ``None``: kein
    Ersatzwert, keine Null.

    Bis zum 25.09.2026 gab es eine zweite Quelle, die gemessene Resonanz
    (Klicks und Registrierungen aus unseren Tracking-Links). Mit dem Tracking
    ist sie entfallen; der Enum-Wert ``ActivitySource.RESONANZ`` bleibt, weil
    Bestandsdaten ihn tragen koennen.

    Eine dritte Quelle gab es bis zum 20.09.2026: die Frische des juengsten
    indexierten Suchtreffers (``ActivitySource.SEARCH_DATES``). Mit der
    Suchschicht ist sie entfallen - ``last_post_at`` wird von nichts mehr
    geschrieben, und eine Quelle, die nie etwas liefert, ist keine. Der
    Enum-Wert bleibt: Bestandsdaten koennen ihn tragen.
    """
    group = lage.group

    # 1. Eine erhobene Zahl - aus der Beitragsliste der Gruppenseite.
    if group.activity_factor is not None:
        quelle = group.activity_source
        return Befund(
            faktor=max(0.0, min(1.0, group.activity_factor)),
            konfidenz=(
                group.activity_confidence
                or (_KONFIDENZ_AKTIVITAET.get(quelle, 0.5) if quelle else 0.5)
            ),
            quelle=quelle.value if quelle else "ohne Angabe",
        )

    return None


@bestandteil("location", "Ort", 15.0)
def _location(lage: Lage) -> Befund | None:
    """Wie genau die Gruppe geografisch trifft - Stadt vor Land.

    Vier Stufen, und die unterste ist nicht Null: "Deutschland allgemein"
    ist eine schwaechere Passung als "Bonn", aber immer noch eine. Erst wenn
    gar nichts erkennbar ist, gilt der Bestandteil als unbekannt.

    Die Konfidenz der Stadt kommt aus der Klassifikation und unterscheidet
    einen Treffer im Namen (1,0) von einem im Beschreibungstext (0,5) - ohne
    diese Abstufung erreichten alle Bestandteile gleichzeitig ihr Maximum.

    Die Anteile stehen in ``scoring.location_stufen``: Ob ein Bundesland
    sieben oder zehn der fuenfzehn Punkte wert ist, ist eine fachliche Frage.
    """
    stufen = lage.config.get("scoring", "location_stufen", default={}) or {}
    group = lage.group

    if group.city:
        return Befund(
            faktor=max(0.0, min(1.0, group.city_confidence or 1.0)),
            konfidenz=group.city_confidence or 1.0,
            quelle=f"Stadt {group.city}",
        )
    if group.bundesland:
        anteil = float(stufen.get("bundesland", 0.45))
        return Befund(faktor=anteil, konfidenz=0.6, quelle=f"Bundesland {group.bundesland}")
    if group.country:
        anteil = float(stufen.get("land", 0.20))
        return Befund(faktor=anteil, konfidenz=0.4, quelle=str(group.country))
    return None


@bestandteil("category", "Kategorie", 20.0)
def _category(lage: Lage) -> Befund | None:
    """Wie gut das Thema passt - Hauptkategorie, angehoben durch Nebenthemen.

    Die Hauptkategorie traegt die Note. Jede erkannte Nebenkategorie hebt sie
    an, weil eine Gruppe, die drei gesuchte Themen bedient, mehr wert ist als
    eine, die eines bedient - aber gedeckelt: Zehn Themen zu streifen ist
    nicht dasselbe wie eines zu treffen, und ohne Deckel gewaenne die Gruppe
    mit dem laengsten Namen.
    """
    group = lage.group
    if not group.category:
        return None

    je_neben = float(lage.config.get("scoring", "kategorie_nebenbonus", default=0.08) or 0.0)
    deckel = float(lage.config.get("scoring", "kategorie_nebenbonus_max", default=0.24) or 0.0)
    bonus = min(deckel, je_neben * len(group.secondary_categories))

    grund = group.category
    if group.secondary_categories:
        grund += f" +{len(group.secondary_categories)}"
    return Befund(
        faktor=max(0.0, min(1.0, (group.category_confidence or 1.0) + bonus)),
        konfidenz=group.category_confidence or 1.0,
        quelle=grund,
    )


@bestandteil("target_audience", "Zielgruppe", 15.0)
def _target_audience(lage: Lage) -> Befund | None:
    """Ob die Gruppe die gesuchten Menschen anspricht.

    ``None``, solange keine Zielgruppe erkannt ist - 115 von 313 Gruppen
    tragen keinen Zielgruppen-Tag, und eine Null hiesse dort "spricht die
    Zielgruppe nachweislich nicht an". Das ist etwas anderes als "wir wissen
    es nicht", und der Unterschied entscheidet ueber die Rangfolge.
    """
    group = lage.group
    if not group.audience_tags:
        return None
    return Befund(
        faktor=max(0.0, min(1.0, group.audience_confidence or 1.0)),
        konfidenz=group.audience_confidence or 1.0,
        quelle=", ".join(group.audience_tags[:3]),
    )


@bestandteil("name_quality", "Name", 0.0)
def _name_quality(lage: Lage) -> Befund | None:
    """Beurteilt allein die Form des Gruppennamens. Vorgabegewicht 0.

    Abgeschaltet, weil die Form des Namens etwas ueber **unsere Daten** sagt
    und nichts ueber die Gruppe - das gehoert in ``data_confidence``, nicht in
    den Score. Der Bestandteil bleibt erhalten, damit das Wiedereinschalten
    eine Zahlenaenderung in der Konfiguration ist und keine Codeaenderung.

    Geprueft wird, ob der Name ueberhaupt ein Gruppenname ist: vollstaendig,
    kurz, keine Satzform. Ein Beitragstitel wie "Deutschland geht erst unter
    seit Mutter Merkel den Syrern ..." erfuellt das nicht - er stand bislang
    ueber echten Gruppen.
    """
    name = lage.group.name.strip()
    if not name:
        return None
    return Befund(faktor=_namensform(name), konfidenz=1.0, quelle="Form")


def _namensform(name: str) -> float:
    """Der Formfaktor eines Namens (0-1). Auch von ``data_confidence`` benutzt."""
    faktor = 1.0
    if name.endswith(_TRUNCATION_MARKERS):
        faktor -= 0.4
    if any(marker in name for marker in _SENTENCE_MARKERS):
        faktor -= 0.3
    if len(name.split()) > 10:
        faktor -= 0.2
    if len(name) < 6:
        faktor -= 0.3
    return max(round(faktor, 2), 0.0)


# ---------------------------------------------------------------------------
# Die Berechnung
# ---------------------------------------------------------------------------


def _reason(
    punkte: dict[str, float],
    befunde: dict[str, Befund],
    score: float,
    score_max: float,
    fehlend: list[str],
) -> str:
    """Macht die Zahl nachvollziehbar - sie steht so im Export.

    Mit der Herkunft je Bestandteil: "Mitglieder 22 (facebook)" beantwortet
    die naechste Frage gleich mit, "Mitglieder 22" wirft sie auf.
    """
    teile = []
    for name, wert in punkte.items():
        if wert <= 0:
            continue
        label = BESTANDTEILE[name].label
        quelle = befunde[name].quelle
        teile.append(f"{label} {wert:g}" + (f" ({quelle})" if quelle else ""))

    satz = f"{' + '.join(teile) or 'keine Punkte'} = {score:g} von hoechstens {score_max:g}"
    if fehlend:
        offen = ", ".join(f"{BESTANDTEILE[name].label} unbekannt" for name in fehlend)
        satz += f" ({offen})"
    return satz


def _data_confidence(befunde: dict[str, Befund], gewichtung: dict[str, float]) -> float:
    """Wie belastbar die Grundlage des Scores ist (0-1).

    Zwei Dinge fliessen ein, und beide gehoeren dazu: **wie sicher** die
    vorhandenen Angaben sind (die Konfidenzen der Befunde) und **wie viel**
    ueberhaupt vorlag (der Anteil der beurteilten am moeglichen Gewicht). Ohne
    das zweite bekaeme eine Gruppe, von der nur die Stadt bekannt ist, aber
    zweifelsfrei, die volle Confidence - und die Zahl wuerde das Gegenteil
    dessen aussagen, wozu sie da ist.

    Sie geht **nie** in den Score ein. Ein Score von 84 bei Confidence 0,4 ist
    eine andere Auskunft als 84 bei 0,95, und beide sollen lesbar bleiben.
    """
    moeglich = sum(gewichtung.values())
    if not moeglich:
        return 0.0
    beurteilt = sum(gewichtung[name] for name in befunde)
    if not beurteilt:
        return 0.0
    gewogen = sum(befunde[name].konfidenz * gewichtung[name] for name in befunde) / beurteilt
    return round(gewogen * (beurteilt / moeglich), 3)


def score_group(
    group: Group,
    config: AppConfig,
    *,
    jetzt: datetime | None = None,
) -> Group:
    """Berechnet Score, Aufschluesselung, Konfidenz, Datenqualitaet und Status."""
    group.data_quality = assess_data_quality(group)

    # 1. Ungueltige, erfundene oder geprueft tote URLs werden nicht bewertet.
    if group.validation_status is not ValidationStatus.VALID:
        group.score = None
        group.score_max = None
        group.score_breakdown = ScoreBreakdown()
        group.data_confidence = 0.0
        group.score_reason = _NICHT_BEWERTBAR.get(
            group.validation_status, "invalid: unbrauchbare URL, keine Bewertung"
        )
        group.status = determine_status(group)
        return group

    # 2. Zu duenne Datenlage: kein Score, sondern eine Begruendung.
    if not has_sufficient_data(group):
        group.score = None
        group.score_max = None
        group.score_breakdown = ScoreBreakdown()
        group.data_confidence = 0.0
        missing = "kein Gruppenname" if not group.name.strip() else "nur der Gruppenname"
        group.score_reason = f"insufficient_data: {missing} vorhanden"
        group.status = determine_status(group)
        return group

    gewichtung = gewichte(config)
    lage = Lage(group=group, config=config, jetzt=jetzt)

    befunde: dict[str, Befund] = {}
    for name in gewichtung:
        befund = BESTANDTEILE[name].pruefe(lage)
        if befund is not None:
            befunde[name] = befund

    punkte = {
        name: round(gewichtung[name] * befund.faktor, 2) for name, befund in befunde.items()
    }
    fehlend = [name for name in gewichtung if name not in befunde]

    group.score_breakdown = ScoreBreakdown(**punkte)
    group.score = round(sum(punkte.values()), 1)
    group.score_max = round(sum(gewichtung[name] for name in befunde), 1)
    group.data_confidence = _data_confidence(befunde, gewichtung)
    group.score_reason = _reason(punkte, befunde, group.score, group.score_max, fehlend)

    group.status = determine_status(group)
    return group


def _rangfolge(group: Group) -> tuple:
    """Sortierschluessel: die beste Gruppe zuerst.

    Erst die erreichten Punkte. Bei Gleichstand entscheidet der **Anteil** der
    erreichten an den erreichbaren Punkten: 40 von 55 ist eine gute Gruppe mit
    unbekannter Groesse, 40 von 100 eine schwache mit vollstaendigen Daten.
    Zuletzt der Name, damit die Reihenfolge zwischen zwei Laeufen stabil bleibt.
    """
    anteil = (group.score or 0.0) / group.score_max if group.score_max else 0.0
    return (
        group.score is None,
        -(group.score or 0.0),
        -anteil,
        group.name.lower() or group.group_id,
    )


def sort_by_rank(groups: list[Group]) -> list[Group]:
    """Sortiert bereits bewertete Gruppen - die beste zuerst.

    Der Export nutzt dieselbe Reihenfolge wie ein frischer Lauf; sonst stuende
    die Liste in der Datei anders als auf dem Bildschirm.
    """
    return sorted(groups, key=_rangfolge)


def score_all(groups: list[Group], config: AppConfig) -> list[Group]:
    """Bewertet alle Gruppen und sortiert sie - die beste zuerst.

    Nicht bewertbare Datensaetze stehen am Ende - sie sind kein schlechtes
    Ergebnis, sondern ein offener Punkt fuer die manuelle Nachpflege.
    """
    for group in groups:
        score_group(group, config)

    return sorted(groups, key=_rangfolge)


def summarize_quality(groups: list[Group]) -> dict[str, int]:
    """Kurzuebersicht ueber Bewertbarkeit und Datenqualitaet."""
    return {
        "scored": sum(1 for g in groups if g.score is not None),
        "unscored": sum(1 for g in groups if g.score is None),
        "invalid": sum(1 for g in groups if g.status is RecordStatus.INVALID),
        "insufficient_data": sum(
            1 for g in groups if g.status is RecordStatus.INSUFFICIENT_DATA
        ),
        "duplicate": sum(1 for g in groups if g.status is RecordStatus.DUPLICATE),
        "validated": sum(1 for g in groups if g.status is RecordStatus.VALIDATED),
        "quality_none": sum(1 for g in groups if g.data_quality is DataQuality.NONE),
    }
