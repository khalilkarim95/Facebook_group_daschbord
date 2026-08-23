"""Die gemessene Resonanz je Gruppe - aus den eigenen Tracking-Ereignissen.

Bindeglied zwischen der Marketing-Erweiterung und der Bewertung im Kern.
``scoring.Resonanz`` beschreibt die Form der Zahlen, dieses Modul beschafft
sie. Die Richtung ist Absicht: ``scoring.py`` kennt weder ``MarketingStore``
noch die Ereignistabelle, so wie die Marketing-Erweiterung den Bestand nicht
veraendert. Ein Import in die andere Richtung machte den Kern von einem
Aufsatz abhaengig.

Warum diese Zahlen und nicht Beitragszahlen aus der Gruppe: Mitgliederzahl,
Beitraege je Woche und aktive Poster stehen ausschliesslich auf facebook.com,
und dorthin greift dieses Projekt nicht. Was hier gemessen wird, beantwortet
die eigentliche Frage ohnehin genauer - nicht "wie viel wird dort geredet?",
sondern "wie viele Menschen kommen von dort zu uns?".
"""

from __future__ import annotations

from fbgroups.marketing.models import EventType, PostStatus
from fbgroups.marketing.store import MarketingStore
from fbgroups.scoring import Resonanz

# Ereignisse, die als "Regung" gelten, wenn es um die Aktualitaet geht. Ein
# Klick genuegt: Er belegt, dass der Beitrag in der Gruppe noch gesehen wird.
_REGUNG = (EventType.CLICK, EventType.REGISTRATION)


def resonanz_je_gruppe(store: MarketingStore) -> dict[str, Resonanz]:
    """Sammelt fuer jede Gruppe, was ihre Beitraege gebracht haben.

    Drei Abfragen statt drei je Gruppe: Bei 300 Gruppen waeren das sonst 900
    Einzelabfragen, nur um festzustellen, dass die meisten noch nichts haben.

    Enthalten sind ausschliesslich Gruppen mit **mindestens einem
    veroeffentlichten Beitrag**. Alle anderen fehlen im Ergebnis, und
    ``score_group`` behandelt sie damit als "nicht gemessen" - nicht als
    "wirkt nicht". Ohne Beitrag sagen null Klicks nichts ueber die Gruppe aus.
    """
    beitraege: dict[str, int] = {}
    erster: dict[str, object] = {}
    for row in store.conn.execute(
        """
        SELECT group_id, COUNT(*) AS n, MIN(posted_at) AS erster
          FROM campaign_groups
         WHERE post_status = ? AND posted_at IS NOT NULL
         GROUP BY group_id
        """,
        (PostStatus.VEROEFFENTLICHT.value,),
    ):
        beitraege[row["group_id"]] = row["n"]
        erster[row["group_id"]] = row["erster"]

    if not beitraege:
        return {}

    # counts_by liefert {(group_id, event_type): anzahl} - dieselbe Quelle wie
    # die Uebersicht, damit Score und angezeigte Trichterzahlen nicht
    # auseinanderlaufen koennen.
    zaehler = store.counts_by("group_id")

    letzte: dict[str, str] = {}
    for row in store.conn.execute(
        """
        SELECT group_id, MAX(occurred_at) AS letzte
          FROM tracking_events
         WHERE group_id <> '' AND event_type IN (?, ?)
         GROUP BY group_id
        """,
        tuple(e.value for e in _REGUNG),
    ):
        letzte[row["group_id"]] = row["letzte"]

    from datetime import datetime

    def _zeit(wert: object) -> datetime | None:
        if not isinstance(wert, str) or not wert:
            return None
        try:
            return datetime.fromisoformat(wert)
        except ValueError:
            # Ein unlesbarer Zeitstempel darf die Bewertung des ganzen
            # Bestands nicht anhalten - er macht nur diese eine Angabe
            # unbekannt.
            return None

    return {
        group_id: Resonanz(
            beitraege=anzahl,
            klicks=zaehler.get((group_id, EventType.CLICK.value), 0),
            registrierungen=zaehler.get((group_id, EventType.REGISTRATION.value), 0),
            letzte_regung=_zeit(letzte.get(group_id)),
            erster_beitrag_am=_zeit(erster.get(group_id)),
        )
        for group_id, anzahl in beitraege.items()
    }
