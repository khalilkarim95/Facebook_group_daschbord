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
    def landing_visits(self) -> int:
        return self.wert(EventType.LANDING_VISIT)

    @property
    def registrations(self) -> int:
        return self.wert(EventType.REGISTRATION)

    @property
    def downloads(self) -> int:
        """Wie viele Menschen von hier aus die App geholt haben.

        Unabhaengig von ``registrations``: Beide Zahlen zaehlen dasselbe
        Publikum an verschiedenen Stellen. "10 Registrierungen, 3 Downloads"
        heisst nicht, dass sieben Registrierungen fehlerhaft sind.
        """
        return self.wert(EventType.DOWNLOAD)

    @property
    def activations(self) -> int:
        return self.wert(EventType.ACTIVATION)

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
        "downloads": counts.get(EventType.DOWNLOAD.value, 0),
        "activated": counts.get(EventType.ACTIVATION.value, 0),
        "qualified": counts.get(EventType.QUALIFIED.value, 0),
        "conversions": counts.get(EventType.CONVERSION.value, 0),
        "referrals": sum(referrals.values()),
        "referrals_qualified": referrals.get("qualified", 0) + referrals.get("converted", 0),
        "rewards": len(store.all_rewards()),
    }


@dataclass
class Benutzerweg:
    """Ein Mensch und die Stufen, die er unter diesem Code erreicht hat.

    ``kennungen`` nennt alle Namen, unter denen er aufgetreten ist - erst der
    anonyme Besuch, spaeter das Konto. Sie stehen nebeneinander, damit sich
    nachvollziehen laesst, **warum** ein Download zu diesem Code gehoert, statt
    es glauben zu muessen.
    """

    user_ref: str
    kennungen: list[str] = field(default_factory=list)
    stufen: list[EventType] = field(default_factory=list)

    def hat(self, event_type: EventType) -> bool:
        return event_type in self.stufen


@dataclass
class CodeBericht:
    """Der Trichter eines einzelnen Tracking-Codes.

    Die Zahlen stehen je Code getrennt und werden nirgends zusammengeworfen:
    ``FB-SYR-KLN-002`` mit zwei Downloads und ``FB-SYR-BER-001`` mit fuenf
    sind zwei Zeilen, nie eine Summe mit sieben.
    """

    tracking_code: str
    campaign_id: str = ""
    group_id: str = ""
    group_name: str = ""
    zahlen: dict[str, int] = field(default_factory=dict)
    benutzer: list[Benutzerweg] = field(default_factory=list)

    def wert(self, event_type: EventType) -> int:
        return self.zahlen.get(event_type.value, 0)

    @property
    def stufen(self) -> list[tuple[EventType, int]]:
        """Alle Stufen in der Reihenfolge der Anzeige, auch die leeren.

        Eine fehlende Stufe erscheint als 0 und nicht gar nicht: "keine
        Downloads" ist eine Aussage, eine fehlende Zeile ist ein Raetsel.
        """
        return [(stufe, self.wert(stufe)) for stufe in FUNNEL_ORDER]


def code_bericht(
    store: MarketingStore, tracking_code: str, labels: dict[str, str] | None = None
) -> CodeBericht:
    """Alles, was zu einem Tracking-Code gehoert - Zahlen und Menschen dahinter.

    Beantwortet die Frage, an der die letzte Fassung gescheitert ist: Gehoeren
    dieser Download und diese Aktivierung wirklich zu **diesem** Code? Die
    Benutzerwege sind die Begruendung: Sie zeigen je Mensch, welche Stufen
    unter diesem Code auf ihn entfallen.
    """
    bericht = CodeBericht(tracking_code=tracking_code)

    link = store.resolve_code(tracking_code)
    if link is not None:
        bericht.campaign_id = link.campaign_id
        bericht.group_id = link.group_id
        bericht.group_name = (labels or {}).get(link.group_id, "")

    for (code, event_type), anzahl in store.counts_by("tracking_code").items():
        if code == tracking_code:
            bericht.zahlen[event_type] = anzahl

    # Je Mensch die Stufen sammeln. Der Klick hat keine Kennung - er gehoert
    # zum Code, nicht zu einer Person, und erscheint deshalb nur in den
    # Zahlen, nicht in den Wegen.
    wege: dict[str, Benutzerweg] = {}
    for event in store.events_for_code(tracking_code):
        if not event.user_ref:
            continue
        ident = store.identitaet(event.user_ref)
        weg = wege.setdefault(
            ident, Benutzerweg(user_ref=ident, kennungen=store.kennungen(ident))
        )
        if event.event_type not in weg.stufen:
            weg.stufen.append(event.event_type)

    for weg in wege.values():
        weg.stufen.sort(key=FUNNEL_ORDER.index)
    bericht.benutzer = sorted(wege.values(), key=lambda w: (-len(w.stufen), w.user_ref))
    return bericht
