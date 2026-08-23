"""Priorisierung der Gruppen.

Drei Regeln bestimmen den Entwurf:

1. **Kein Score ohne Grundlage.** Reicht die Datenlage nicht, ist der Score
   ``None`` und ``score_reason`` nennt den Grund. Ein Ersatzwert waere eine
   Behauptung ueber Daten, die nicht vorliegen.
2. **Jeder Bestandteil zaehlt genau einmal.** Zielgruppe, Stadt und Kategorie
   haben eigene Gewichte; ``name_quality`` beurteilt deshalb nur noch die Form
   des Namens. Zuvor vergab ``name_quality`` erneut Punkte fuer Zielgruppe und
   Stadt - beide zaehlten damit doppelt und erreichten stets gemeinsam ihr
   Maximum.
3. **Fehlende Angaben senken die erreichbare Punktzahl, statt den Rest
   hochzurechnen.** Der Score ist die Summe der tatsaechlich belegten Punkte,
   ``score_max`` nennt das bei dieser Datenlage Erreichbare. Die frueher
   verwendete Normierung auf 100 fuehrte dazu, dass eine Gruppe, von der nur
   der Name bekannt war, denselben Hoechstwert erhielt wie eine Gruppe mit
   belegten 50.000 Mitgliedern: 27 Gruppen standen auf exakt 100.

Alle Gewichte stehen in ``config/settings.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fbgroups.config import AppConfig
from fbgroups.models import DataQuality, Group, RecordStatus, ScoreBreakdown, ValidationStatus
from fbgroups.validation import assess_data_quality, determine_status, has_sufficient_data

DEFAULT_WEIGHTS = {
    "member_count": 45.0,
    "audience_match": 25.0,
    "city_match": 15.0,
    "category_match": 8.0,
    "name_quality": 7.0,
    # Gemessene Resonanz. Vorgabe 0: Ohne uebergebene Zahlen bleibt die
    # Bewertung exakt die bisherige - wer die Bestandteile will, schaltet sie
    # in config/settings.yaml ein, wie bei jedem anderen Gewicht auch.
    "resonanz_engagement": 0.0,
    "resonanz_reichweite": 0.0,
    "resonanz_aktualitaet": 0.0,
}

# Klartext fuer die Begruendung im Export.
_LABELS = {
    "audience_match": "Zielgruppe",
    "city_match": "Stadt",
    "category_match": "Kategorie",
    "member_count": "Mitgliederzahl",
    "name_quality": "Name",
    "resonanz_engagement": "Resonanz",
    "resonanz_reichweite": "Reichweite",
    "resonanz_aktualitaet": "Aktualitaet",
}

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


@dataclass(frozen=True)
class Resonanz:
    """Was eine Gruppe tatsaechlich gebracht hat - gemessen, nicht geschaetzt.

    Die Zahlen stammen aus den eigenen Tracking-Ereignissen: jemand hat den
    Link in dieser Gruppe angeklickt und sich danach registriert. Das ist das
    einzige Aktivitaetsmass, das dieses Projekt ohne facebook.com erheben
    kann - und es beantwortet die eigentliche Frage besser als eine
    Beitragszahl: Eine Gruppe mit 500 Mitgliedern, die 40 Registrierungen
    bringt, ist mehr wert als eine mit 5.000, die zwei bringt.

    ``beitraege`` ist die Zahl der **veroeffentlichten** Beitraege. Sie
    entscheidet, ob ueberhaupt gemessen wurde: Ohne Beitrag sagen null Klicks
    nichts ueber die Gruppe aus, sondern nur ueber uns.

    Das Modul kennt dabei weder MarketingStore noch Datenbank - die Zahlen
    werden hereingereicht. Der Kern bleibt frei von der Marketing-Erweiterung,
    so wie diese den Bestand nicht veraendert.
    """

    beitraege: int = 0
    klicks: int = 0
    registrierungen: int = 0
    letzte_regung: datetime | None = None
    # Aeltester veroeffentlichter Beitrag dieser Gruppe. Er sagt, seit wann
    # gemessen wird; ein Beitrag von heute Morgen hatte noch keine Gelegenheit,
    # Klicks zu sammeln.
    erster_beitrag_am: datetime | None = None


def _tage_seit(zeitpunkt: datetime | None, jetzt: datetime) -> float | None:
    """Tage zwischen damals und jetzt - unabhaengig von der Zeitzonenangabe.

    Aus der Datenbank kommen die Zeitpunkte mit Zeitzone, aus einem Test
    gelegentlich ohne. Ein direkter Vergleich der beiden wirft einen
    TypeError, und der faellt erst im Betrieb auf.
    """
    if zeitpunkt is None:
        return None
    if zeitpunkt.tzinfo is None:
        zeitpunkt = zeitpunkt.replace(tzinfo=UTC)
    return max((jetzt - zeitpunkt).total_seconds() / 86400.0, 0.0)


def _resonanz_faktoren(
    resonanz: Resonanz, config: AppConfig, jetzt: datetime
) -> dict[str, float] | None:
    """Die drei Resonanz-Faktoren (0-1) - oder ``None``, wenn nicht messbar.

    ``None`` heisst ausdruecklich **nicht** "null Resonanz". Es heisst: Wir
    haben noch nicht gemessen. Beide Faelle gleich zu behandeln waere der
    schwerste Fehler an dieser Stelle - eine Gruppe, in der wir nie gepostet
    haben, stuende dann neben einer, deren Beitrag niemand angeklickt hat.
    Nicht messbar sind zwei Faelle:

    * **Kein veroeffentlichter Beitrag.** Null Klicks sind dann eine Aussage
      ueber uns, nicht ueber die Gruppe.
    * **Der Beitrag ist zu frisch.** Wer vor zwei Stunden gepostet hat, hat
      noch keine Klicks - eine Null waere hier eine Behauptung ueber die
      Zukunft. Die Schonfrist steht in ``resonanz.schonfrist_tage``.
    """
    if resonanz.beitraege <= 0:
        return None

    schonfrist = float(config.get("scoring", "resonanz", "schonfrist_tage", default=3) or 0)
    alter = _tage_seit(resonanz.erster_beitrag_am, jetzt)
    if alter is not None and alter < schonfrist:
        return None

    ziel_quote = float(config.get("scoring", "resonanz", "ziel_quote", default=0.15) or 0.15)
    mindest_klicks = float(config.get("scoring", "resonanz", "mindest_klicks", default=20) or 1)
    ziel_klicks = float(
        config.get("scoring", "resonanz", "ziel_klicks_je_beitrag", default=25) or 1
    )
    halbwert = float(config.get("scoring", "resonanz", "aktualitaet_tage", default=30) or 30)

    # Engagement: die Registrierungsquote, gemessen an einer erreichbaren
    # Zielquote - nicht an 100 %. Eine Quote von 15 % ist hervorragend; wer
    # dagegen auf 1,0 normiert, gibt selbst der besten Gruppe ein Sechstel
    # der Punkte und macht den Bestandteil wirkungslos.
    #
    # Der zweite Faktor ist die Belastbarkeit: 1 Klick und 1 Registrierung
    # sind 100 % und beweisen nichts. Erst ab "mindest_klicks" zaehlt die
    # Quote voll - darunter anteilig.
    quote = resonanz.registrierungen / resonanz.klicks if resonanz.klicks else 0.0
    belastbar = min(1.0, resonanz.klicks / mindest_klicks) if mindest_klicks > 0 else 1.0
    engagement = min(1.0, quote / ziel_quote) * belastbar if ziel_quote > 0 else 0.0

    # Reichweite: Klicks je veroeffentlichtem Beitrag. Je Beitrag, nicht
    # absolut - sonst gewaenne die Gruppe, in der wir am oeftesten gepostet
    # haben, statt der, die am besten wirkt.
    je_beitrag = resonanz.klicks / resonanz.beitraege
    reichweite = min(1.0, je_beitrag / ziel_klicks) if ziel_klicks > 0 else 0.0

    # Aktualitaet: linearer Abfall bis "aktualitaet_tage". Eine Gruppe, die
    # vor einem halben Jahr zuletzt reagiert hat, ist heute keine gute Wahl -
    # auch wenn ihre Gesamtzahlen gut aussehen.
    tage = _tage_seit(resonanz.letzte_regung, jetzt)
    aktualitaet = 0.0 if tage is None else max(0.0, 1.0 - tage / halbwert) if halbwert > 0 else 0.0

    return {
        "resonanz_engagement": round(engagement, 4),
        "resonanz_reichweite": round(reichweite, 4),
        "resonanz_aktualitaet": round(aktualitaet, 4),
    }


def _member_count_factor(member_count: int, config: AppConfig) -> float:
    """Groessenklasse laut ``member_count_buckets``.

    Die Klassen werden absteigend sortiert ausgewertet und nicht in
    Dateireihenfolge: Stuende in der Konfiguration versehentlich die kleinste
    Klasse zuerst, bekaeme sonst jede Gruppe deren Faktor.
    """
    buckets = config.get("scoring", "member_count_buckets", default=[]) or []
    for bucket in sorted(buckets, key=lambda b: int(b.get("min", 0)), reverse=True):
        if member_count >= int(bucket.get("min", 0)):
            return float(bucket.get("factor", 0.0))
    return 0.0


def _name_quality_factor(group: Group) -> float:
    """Beurteilt allein die Form des Gruppennamens.

    Zielgruppe, Stadt und Kategorie sind eigene Bestandteile mit eigenem
    Gewicht und duerfen hier nicht erneut zaehlen. Geprueft wird nur, ob der
    Name ueberhaupt ein Gruppenname ist: vollstaendig, kurz, keine Satzform.
    Ein Beitragstitel wie "Deutschland geht erst unter seit Mutter Merkel den
    Syrern ..." erfuellt das nicht - er stand bislang ueber echten Gruppen.
    """
    name = group.name.strip()
    if not name:
        return 0.0

    factor = 1.0
    if name.endswith(_TRUNCATION_MARKERS):
        factor -= 0.4
    if any(marker in name for marker in _SENTENCE_MARKERS):
        factor -= 0.3
    if len(name.split()) > 10:
        factor -= 0.2
    if len(name) < 6:
        factor -= 0.3
    return max(round(factor, 2), 0.0)


def _reason(points: dict[str, float], score: float, score_max: float, fehlend: list[str]) -> str:
    """Macht die Zahl nachvollziehbar - sie steht so im Export."""
    teile = " + ".join(f"{_LABELS.get(k, k)} {v:g}" for k, v in points.items() if v > 0)
    teile = teile or "keine Punkte"
    satz = f"{teile} = {score:g} von hoechstens {score_max:g}"
    if fehlend:
        offen = ", ".join(f"{_LABELS.get(k, k)} unbekannt" for k in fehlend)
        satz += f" ({offen})"
    return satz


def score_group(
    group: Group,
    config: AppConfig,
    resonanz: Resonanz | None = None,
) -> Group:
    """Berechnet Score, Begruendung, Datenqualitaet und Status einer Gruppe.

    ``resonanz`` sind die gemessenen Tracking-Zahlen dieser Gruppe. Ohne sie
    bleibt die Bewertung genau die bisherige: Die Bestandteile erscheinen dann
    als "unbekannt", wie jede andere fehlende Angabe auch. Der Aufrufer holt
    die Zahlen (siehe ``marketing.resonanz.resonanz_je_gruppe``) - dieses
    Modul kennt die Marketing-Erweiterung nicht und soll sie nicht kennen.
    """
    group.data_quality = assess_data_quality(group)

    # 1. Ungueltige, erfundene oder geprueft tote URLs werden nicht bewertet.
    if group.validation_status is not ValidationStatus.VALID:
        group.score = None
        group.score_max = None
        group.score_breakdown = ScoreBreakdown()
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
        missing = "kein Gruppenname" if not group.name.strip() else "nur der Gruppenname"
        group.score_reason = f"insufficient_data: {missing} vorhanden"
        group.status = determine_status(group)
        return group

    # Ein Gewicht von 0 nimmt den Bestandteil vollstaendig aus der Bewertung -
    # er zaehlt weder Punkte noch erscheint er als "unbekannt" in der
    # Begruendung. So laesst sich die Mitgliederzahl abschalten, solange sie
    # nicht beschaffbar ist, ohne den Code anzufassen. Ohne diesen Filter
    # setzte DEFAULT_WEIGHTS die 45 Punkte gegen die Konfiguration wieder ein.
    weights = {
        name: float(gewicht)
        for name, gewicht in {
            **DEFAULT_WEIGHTS,
            **(config.get("scoring", "weights", default={}) or {}),
        }.items()
        if float(gewicht) > 0
    }

    # Bestandteile, die anhand der vorliegenden Angaben beurteilbar sind.
    # Die Konfidenz unterscheidet dabei Treffer im Namen (1,0) von Treffern im
    # Beschreibungstext (0,5) - ohne diese Abstufung erreichten alle Teile
    # gleichzeitig ihr Maximum.
    faktoren: dict[str, float] = {
        "audience_match": group.audience_confidence if group.audience_tags else 0.0,
        "city_match": group.city_confidence if group.city else 0.0,
        "category_match": group.category_confidence if group.category else 0.0,
        "name_quality": _name_quality_factor(group),
    }

    # Die Mitgliederzahl zaehlt nur mit, wenn sie belegt ist. Fehlt sie, sinkt
    # die erreichbare Punktzahl - erfunden wird nichts, aber die Gruppe gilt
    # auch nicht als geprueft gross.
    if group.member_count_hint is not None:
        faktoren["member_count"] = _member_count_factor(group.member_count_hint, config)

    # Gemessene Resonanz. Liegt sie nicht vor oder ist sie noch nicht
    # messbar, bleiben die Bestandteile "unbekannt" - sie senken dann die
    # erreichbare Punktzahl, statt eine Null zu behaupten. Der Unterschied
    # zwischen "wirkt nicht" und "noch nicht gemessen" ist hier der ganze
    # Punkt; siehe _resonanz_faktoren.
    if resonanz is not None:
        gemessen = _resonanz_faktoren(resonanz, config, datetime.now(UTC))
        if gemessen is not None:
            faktoren.update(gemessen)

    parts: dict[str, tuple[float, float]] = {
        name: (weights[name], faktor) for name, faktor in faktoren.items() if name in weights
    }

    points = {name: round(weight * factor, 2) for name, (weight, factor) in parts.items()}
    fehlend = [name for name in weights if name not in parts]

    group.score_breakdown = ScoreBreakdown(**points)
    group.score = round(sum(points.values()), 1)
    group.score_max = round(sum(weight for weight, _ in parts.values()), 1)
    group.score_reason = _reason(points, group.score, group.score_max, fehlend)

    group.status = determine_status(group)
    return group


def _rangfolge(group: Group) -> tuple:
    """Sortierschluessel: die beste Gruppe zuerst.

    Erst die erreichten Punkte. Bei Gleichstand entscheidet der **Anteil** der
    erreichten an den erreichbaren Punkten: 40 von 55 ist eine gute Gruppe mit
    unbekannter Groesse, 40 von 100 eine kleine mit belegter Groesse. Zuletzt
    der Name, damit die Reihenfolge zwischen zwei Laeufen stabil bleibt.
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


def score_all(
    groups: list[Group],
    config: AppConfig,
    resonanz: dict[str, Resonanz] | None = None,
) -> list[Group]:
    """Bewertet alle Gruppen und sortiert sie - die beste zuerst.

    Nicht bewertbare Datensaetze stehen am Ende - sie sind kein schlechtes
    Ergebnis, sondern ein offener Punkt fuer die manuelle Nachpflege.
    """
    for group in groups:
        score_group(group, config, (resonanz or {}).get(group.group_id))

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
