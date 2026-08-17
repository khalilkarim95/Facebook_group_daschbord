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

from fbgroups.config import AppConfig
from fbgroups.models import DataQuality, Group, RecordStatus, ScoreBreakdown, ValidationStatus
from fbgroups.validation import assess_data_quality, determine_status, has_sufficient_data

DEFAULT_WEIGHTS = {
    "member_count": 45.0,
    "audience_match": 25.0,
    "city_match": 15.0,
    "category_match": 8.0,
    "name_quality": 7.0,
}

# Klartext fuer die Begruendung im Export.
_LABELS = {
    "audience_match": "Zielgruppe",
    "city_match": "Stadt",
    "category_match": "Kategorie",
    "member_count": "Mitgliederzahl",
    "name_quality": "Name",
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


def score_group(group: Group, config: AppConfig) -> Group:
    """Berechnet Score, Begruendung, Datenqualitaet und Status einer Gruppe."""
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
