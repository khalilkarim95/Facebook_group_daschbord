"""Auswertung der Kampagnen.

Beantwortet die eine Frage, fuer die das ganze Modul da ist: **Welche
Facebook-Gruppe bringt Benutzer?**

Alle Zahlen kommen aus ``tracking_events``. Fehlt eine Stufe, steht dort 0 und
nicht etwa ein geschaetzter Wert - eine erfundene Zwischenzahl wuerde jede
Quote daneben legen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fbgroups.marketing.models import FUNNEL_ORDER, EventType
from fbgroups.marketing.store import MarketingStore


@dataclass
class Zeile:
    """Eine Zeile der Bestenliste - Gruppe oder Kampagne."""

    schluessel: str
    label: str = ""
    zahlen: dict[str, int] = field(default_factory=dict)

    def wert(self, event_type: EventType) -> int:
        return self.zahlen.get(event_type.value, 0)

    @property
    def clicks(self) -> int:
        return self.wert(EventType.CLICK)

    @property
    def registrations(self) -> int:
        return self.wert(EventType.REGISTRATION)

    @property
    def qualified(self) -> int:
        return self.wert(EventType.QUALIFIED)

    @property
    def conversions(self) -> int:
        return self.wert(EventType.CONVERSION)

    @property
    def conversion_rate(self) -> float | None:
        """Anteil der Klicks, die zu einem Abschluss fuehrten.

        ``None`` bei null Klicks - eine Quote ohne Grundgesamtheit gibt es
        nicht, und 0,0 % waere eine Aussage, die niemand belegen kann.
        """
        if self.clicks == 0:
            return None
        return round(self.conversions / self.clicks * 100, 1)


def _bestenliste(counts: dict[tuple[str, str], int], labels: dict[str, str]) -> list[Zeile]:
    zeilen: dict[str, Zeile] = {}
    for (schluessel, event_type), anzahl in counts.items():
        zeile = zeilen.setdefault(
            schluessel, Zeile(schluessel=schluessel, label=labels.get(schluessel, schluessel))
        )
        zeile.zahlen[event_type] = anzahl

    return sorted(
        zeilen.values(),
        key=lambda z: (-z.conversions, -z.qualified, -z.registrations, -z.clicks, z.label.lower()),
    )


def top_groups(store: MarketingStore, labels: dict[str, str]) -> list[Zeile]:
    """Bestenliste der Gruppen."""
    return _bestenliste(store.counts_by("group_id"), labels)


def top_campaigns(store: MarketingStore, labels: dict[str, str]) -> list[Zeile]:
    """Bestenliste der Kampagnen."""
    return _bestenliste(store.counts_by("campaign_id"), labels)


def funnel(store: MarketingStore) -> list[tuple[EventType, int, float | None]]:
    """Trichter vom Klick bis zum Abschluss.

    Je Stufe: absolute Zahl und Anteil an der **ersten** Stufe. Der Bezug auf
    die Klicks statt auf die jeweils vorige Stufe macht die Zahlen zwischen
    zwei Auswertungen vergleichbar.
    """
    counts = store.event_counts()
    basis = counts.get(EventType.CLICK.value, 0)

    stufen: list[tuple[EventType, int, float | None]] = []
    for event_type in FUNNEL_ORDER:
        anzahl = counts.get(event_type.value, 0)
        anteil = round(anzahl / basis * 100, 1) if basis else None
        stufen.append((event_type, anzahl, anteil))
    return stufen


def kennzahlen(store: MarketingStore) -> dict[str, int]:
    """Die Zahlen fuer die Uebersicht."""
    counts = store.event_counts()
    referrals = store.referral_counts()
    return {
        "clicks": counts.get(EventType.CLICK.value, 0),
        "landing_visits": counts.get(EventType.LANDING_VISIT.value, 0),
        "registrations": counts.get(EventType.REGISTRATION.value, 0),
        "activated": counts.get(EventType.ACTIVATION.value, 0),
        "qualified": counts.get(EventType.QUALIFIED.value, 0),
        "conversions": counts.get(EventType.CONVERSION.value, 0),
        "referrals": sum(referrals.values()),
        "referrals_qualified": referrals.get("qualified", 0) + referrals.get("converted", 0),
        "rewards": len(store.all_rewards()),
    }
