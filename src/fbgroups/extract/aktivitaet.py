"""Aus Rohangaben ein Aktivitaetsmass machen - ohne Netz, ohne Raten.

Der Bestandteil ``activity`` traegt 25 der 100 Punkte und ist damit genauso
schwer wie die Groesse. Das ist Absicht: Eine Gruppe mit 100.000 Mitgliedern
und kaum neuen Beitraegen ist ein schlechterer Platz fuer einen Beitrag als
eine mit 20.000 und taeglichem Betrieb. Beide Bestandteile duerfen einander
deshalb nicht ersetzen, und keiner darf aus dem anderen abgeleitet werden.

Dieses Modul rechnet nur. Es holt nichts, es fragt nichts ab und es faellt
niemals auf einen Ersatzwert zurueck: Wo die Grundlage fehlt, ist das Ergebnis
``None``. "Keine Aktivitaet" und "Aktivitaet unbekannt" sind zwei verschiedene
Aussagen - die erste ist ein Urteil ueber die Gruppe, die zweite eines ueber
unsere Daten.

Zwei Quellen mit sehr verschiedener Aussagekraft:

``posts_per_day``
    Beitraege je Tag, aus der Beitragsliste der Gruppenseite. Das ist die
    eigentliche Antwort; sie wird ueber ``scoring.aktivitaet_buckets``
    abgestuft - aus demselben Grund wie bei der Mitgliederzahl: Zwanzig
    Beitraege am Tag sind nicht zwanzigmal so gut wie einer.

``date``-Angaben der Suchtreffer
    Wann ein indexierter Beitrag dieser Gruppe entstanden ist. Das belegt,
    **dass** die Gruppe lebt, und sonst nichts - eine Beitragszahl je Tag ist
    daraus nicht abzuleiten, weil niemand weiss, welcher Bruchteil der
    Beitraege ueberhaupt indexiert wurde. Bewertet wird deshalb allein die
    Frische des juengsten Fundes, und die Konfidenz bleibt niedrig.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from fbgroups.config import AppConfig

# Relative Zeitangaben, wie Google/Serper sie ausliefert. Deutsch und
# englisch, Singular und Plural. Arabische Formen kommen in den gespeicherten
# Antworten nicht vor - taeten sie es, gehoerten sie hier ergaenzt.
_RELATIV = re.compile(
    r"(?:vor\s+)?(\d+)\s*"
    r"(sekunde|sekunden|minute|minuten|stunde|stunden|tag|tagen|tage|"
    r"woche|wochen|monat|monaten|monate|jahr|jahren|jahre|"
    r"second|seconds|minute|minutes|hour|hours|day|days|week|weeks|"
    r"month|months|year|years)"
    r"(?:\s+ago)?",
    re.IGNORECASE,
)

# Tage je Einheit. Naeherungen, und das genuegt: Der Unterschied zwischen
# 30 und 30,44 Tagen je Monat verschiebt keine Gruppe in der Rangliste.
_TAGE_JE_EINHEIT = {
    "sekunde": 1 / 86400, "sekunden": 1 / 86400, "second": 1 / 86400, "seconds": 1 / 86400,
    "minute": 1 / 1440, "minuten": 1 / 1440, "minutes": 1 / 1440,
    "stunde": 1 / 24, "stunden": 1 / 24, "hour": 1 / 24, "hours": 1 / 24,
    "tag": 1.0, "tagen": 1.0, "tage": 1.0, "day": 1.0, "days": 1.0,
    "woche": 7.0, "wochen": 7.0, "week": 7.0, "weeks": 7.0,
    "monat": 30.0, "monaten": 30.0, "monate": 30.0, "month": 30.0, "months": 30.0,
    "jahr": 365.0, "jahren": 365.0, "jahre": 365.0, "year": 365.0, "years": 365.0,
}


def parse_relative_datum(text: str, jetzt: datetime | None = None) -> datetime | None:
    """"vor 2 Wochen" -> Zeitpunkt. ``None``, wenn nichts Zaehlbares dasteht.

    Absichtlich streng: Nur eine Zahl mit einer bekannten Einheit ergibt ein
    Datum. "gestern" oder "letzte Woche" werden **nicht** geraten - der
    Gewinn waere ein Datum mehr, der Preis ein Datum, das niemand geprueft
    hat, in einer Spalte, die spaeter wie eine Messung aussieht.
    """
    if not text:
        return None
    treffer = _RELATIV.search(text)
    if treffer is None:
        return None
    tage = _TAGE_JE_EINHEIT.get(treffer.group(2).lower())
    if tage is None:
        return None
    return (jetzt or datetime.now(UTC)) - timedelta(days=int(treffer.group(1)) * tage)


def faktor_aus_posts_pro_tag(posts_per_day: float, config: AppConfig) -> float:
    """Beitraege je Tag -> Faktor 0-1 laut ``scoring.aktivitaet_buckets``.

    Abgestuft und nicht linear, aus demselben Grund wie bei der
    Mitgliederzahl: Der Unterschied zwischen 0 und 2 Beitraegen am Tag ist
    fuer die Frage "lohnt sich hier ein Beitrag?" gewaltig, der zwischen 20
    und 40 fast bedeutungslos - in beiden Faellen geht der eigene Beitrag im
    Strom unter oder eben nicht.
    """
    buckets = config.get("scoring", "aktivitaet_buckets", default=[]) or []
    # Absteigend und nicht in Dateireihenfolge: Stuende die unterste Stufe
    # versehentlich zuerst, bekaeme jede Gruppe deren Faktor.
    for bucket in sorted(buckets, key=lambda b: float(b.get("min", 0)), reverse=True):
        if posts_per_day >= float(bucket.get("min", 0)):
            return float(bucket.get("factor", 0.0))
    return 0.0


def faktor_aus_treffer_daten(
    daten: list[datetime],
    config: AppConfig,
    jetzt: datetime | None = None,
) -> float | None:
    """Frische der indexierten Beitraege -> Faktor 0-1, oder ``None``.

    Die schwaechste der Quellen, und sie sagt bewusst weniger, als man ihr
    entlocken koennte: Die **Anzahl** der datierten Treffer wird nicht
    bewertet. Sie haengt daran, wie oft eine Gruppe in unseren Suchanfragen
    auftauchte, und das ist eine Eigenschaft unserer Anfragen, nicht der
    Gruppe - eine Gruppe aus der ersten Ausbaustufe haette sonst dauerhaft
    mehr "Aktivitaet" als eine gleich lebendige aus einer neuen Stadt.

    Bewertet wird allein der **juengste** Fund: Ein Beitrag von gestern
    belegt eine lebende Gruppe, einer von vor zwei Jahren das Gegenteil.
    Dazwischen faellt der Faktor linear.
    """
    hole = config.get
    mindest = int(hole("scoring", "aktivitaet_aus_treffern", "mindest_treffer", default=1) or 1)
    if len(daten) < mindest:
        return None

    frisch = float(hole("scoring", "aktivitaet_aus_treffern", "frisch_bis_tagen", default=7) or 7)
    tot_ab = float(hole("scoring", "aktivitaet_aus_treffern", "tot_ab_tagen", default=180) or 180)
    if tot_ab <= frisch:
        return None

    zeitpunkt = jetzt or datetime.now(UTC)
    juengster = max(
        d.replace(tzinfo=UTC) if d.tzinfo is None else d for d in daten
    )
    tage = max((zeitpunkt - juengster).total_seconds() / 86400.0, 0.0)

    if tage <= frisch:
        return 1.0
    if tage >= tot_ab:
        return 0.0
    return round(1.0 - (tage - frisch) / (tot_ab - frisch), 4)
