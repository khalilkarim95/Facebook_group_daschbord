"""Die Zustandsmaschine der Beitrags-Warteschlange.

Beantwortet zwei Fragen und sonst nichts: Welcher Uebergang ist erlaubt, und
darf der Arbeiter gerade ueberhaupt etwas nehmen? Sie kennt weder Claude noch
Facebook noch einen Browser - das ist Absicht. Ein Regelwerk, das nebenbei
Text erzeugt oder Beitraege absetzt, laesst sich nicht mehr fuer sich pruefen,
und genau diese Regeln sind es, die verhindern, dass 310 Beitraege auf einmal
hinausgehen.

Die erlaubten Uebergaenge stehen als **Tabelle** da, nicht als Kette von
if-Abfragen. Der Unterschied zeigt sich, wenn ein Zustand dazukommt: Eine
Tabelle laesst sich lesen und pruefen, verstreute Bedingungen nicht.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fbgroups.marketing.models import JobStatus, QueueZustand

# Schluessel im Meta-Speicher. Je Kampagne einer - eine angehaltene Kampagne
# darf eine andere nicht mit anhalten.
_ZUSTAND_SCHLUESSEL = "queue_zustand:{campaign_id}"


class UngueltigerUebergang(ValueError):
    """Dieser Schritt ist von dort aus nicht erlaubt."""


# Von wo nach wo. Was hier nicht steht, geht nicht.
#
# Zwei Wege sind bewusst offen, obwohl sie rueckwaerts fuehren:
# ``approved -> pending_review`` (eine Freigabe muss zuruecknehmbar sein,
# solange nichts veroeffentlicht ist) und ``published -> draft`` (in derselben
# Gruppe spaeter ein zweiter Beitrag; ``posted_at`` bleibt dabei stehen, es
# zaehlt der erste - siehe CampaignGroup).
UEBERGAENGE: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.DRAFT: frozenset(
        {JobStatus.AI_GENERATED, JobStatus.PENDING_REVIEW, JobStatus.CANCELLED}
    ),
    JobStatus.AI_GENERATED: frozenset(
        {JobStatus.PENDING_REVIEW, JobStatus.DRAFT, JobStatus.CANCELLED}
    ),
    JobStatus.PENDING_REVIEW: frozenset(
        {JobStatus.APPROVED, JobStatus.DRAFT, JobStatus.CANCELLED}
    ),
    JobStatus.APPROVED: frozenset(
        {JobStatus.QUEUED, JobStatus.PENDING_REVIEW, JobStatus.CANCELLED}
    ),
    JobStatus.QUEUED: frozenset(
        {JobStatus.PROCESSING, JobStatus.APPROVED, JobStatus.CANCELLED}
    ),
    JobStatus.PROCESSING: frozenset(
        {JobStatus.PUBLISHED, JobStatus.FAILED, JobStatus.CANCELLED}
    ),
    JobStatus.PUBLISHED: frozenset({JobStatus.DRAFT}),
    JobStatus.FAILED: frozenset({JobStatus.QUEUED, JobStatus.DRAFT, JobStatus.CANCELLED}),
    JobStatus.CANCELLED: frozenset({JobStatus.DRAFT}),
}

# Zustaende, aus denen nichts mehr von selbst geschieht. Nur zur Anzeige und
# fuer die Zaehler - die Uebergangstabelle bleibt massgeblich.
ENDZUSTAENDE: frozenset[JobStatus] = frozenset({JobStatus.PUBLISHED, JobStatus.CANCELLED})

# Ohne Text darf nichts weiter als bis hierher. Ein Beitrag, der zur Freigabe
# vorgelegt wird und leer ist, ist kein Beitrag - und ein leerer Text, der in
# einer Gruppe landet, waere ein Fehler, den niemand mehr zuruecknehmen kann.
BRAUCHT_TEXT: frozenset[JobStatus] = frozenset(
    {
        JobStatus.PENDING_REVIEW,
        JobStatus.APPROVED,
        JobStatus.QUEUED,
        JobStatus.PROCESSING,
        JobStatus.PUBLISHED,
    }
)


def uebergang_erlaubt(von: JobStatus, nach: JobStatus) -> bool:
    """Darf ein Job von ``von`` nach ``nach``?"""
    return nach in UEBERGAENGE.get(von, frozenset())


def pruefe_uebergang(von: JobStatus, nach: JobStatus, *, hat_text: bool) -> None:
    """Wirft ``UngueltigerUebergang``, wenn der Schritt nicht erlaubt ist.

    Die Fehlermeldung nennt die erlaubten Ziele mit. Bei neun Zustaenden ist
    "nicht erlaubt" allein keine Hilfe - die Frage ist immer sofort danach,
    was denn dann.
    """
    if von is nach:
        return
    if not uebergang_erlaubt(von, nach):
        moeglich = sorted(z.value for z in UEBERGAENGE.get(von, frozenset()))
        raise UngueltigerUebergang(
            f"{von.value} -> {nach.value} ist nicht vorgesehen. "
            f"Von {von.value} aus geht: {', '.join(moeglich) or 'nichts'}"
        )
    if nach in BRAUCHT_TEXT and not hat_text:
        raise UngueltigerUebergang(
            f"{nach.value} braucht einen Beitragstext. "
            f"Erst erzeugen (campaign draft) oder von Hand schreiben."
        )


def darf_arbeiten(zustand: QueueZustand) -> bool:
    """Darf der Arbeiter sich gerade einen neuen Job nehmen?

    Nur bei ``laufend``. ``pausiert`` und ``gestoppt`` unterscheiden sich
    nicht hier, sondern in dem, was beim Umschalten mit der Warteschlange
    geschieht - siehe ``QueueZustand``.
    """
    return zustand is QueueZustand.LAUFEND


def zustand_schluessel(campaign_id: str) -> str:
    """Meta-Schluessel, unter dem der Zustand dieser Kampagne steht."""
    return _ZUSTAND_SCHLUESSEL.format(campaign_id=campaign_id)


def ist_verwaist(begonnen_am: datetime | None, grenze_minuten: int) -> bool:
    """Haengt dieser ``processing``-Job seit zu langer Zeit?

    Ein Arbeiter, der abstuerzt, laesst seinen Job in ``processing`` stehen.
    Ohne diese Pruefung bliebe die Gruppe fuer immer belegt: Sie ist weder
    offen noch fertig, taucht in keiner Liste auf und wird nie wieder
    angefasst. Der Job wird deshalb **nicht** automatisch neu abgeschickt,
    sondern nur als verwaist gemeldet - ob dort schon ein Beitrag steht, weiss
    niemand ausser einem Menschen, der nachsieht.
    """
    if begonnen_am is None:
        return True
    if begonnen_am.tzinfo is None:
        begonnen_am = begonnen_am.replace(tzinfo=UTC)
    return datetime.now(UTC) - begonnen_am > timedelta(minutes=grenze_minuten)
