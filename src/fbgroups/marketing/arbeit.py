"""Ein einzelner Arbeitsschritt - unabhaengig davon, wer ihn ausloest.

Der Arbeiter (``worker.py``) laeuft in einer Schleife und schlaeft zwischen
zwei Beitraegen. Die Uebersicht kann das nicht: Zwischen zwei Beitraegen steht
dort keine Schleife, sondern ein Mensch, der eine Seite neu laedt. Beide
brauchen aber **dieselben** Regeln - Tageslimit, Reihenfolge, Wartezeit,
Protokoll, Zustand der Warteschlange.

Deshalb steht der Schritt hier und nicht in einem von beiden: ``hole_auftrag``
und ``melde_ergebnis`` sind die ganze Fachlichkeit, und die Schleife des
Arbeiters ist nur eine von zwei Arten, sie aufzurufen. Eine zweite Fassung der
Regeln fuer die Weboberflaeche waere ein zweites Tageslimit, das vom ersten
abweicht - und niemand bemerkte es, bis vierzig Beitraege an einem Tag
hinausgegangen waeren.

**Die Wartezeit kommt aus dem Bestand, nicht aus dem Ablauf.** Ein Mensch, der
die Seite neu laedt, umginge ein ``sleep`` muehelos; ``store.letzter_versuch``
laesst sich nicht neu laden. Derselbe Gedanke wie beim Tageslimit.

**Der Auftrag ist bereits begonnen, wenn er herausgeht.** ``hole_auftrag``
setzt ``processing`` und schreibt die Protokollzeile, bevor jemand den Text zu
sehen bekommt. Wer den Reiter schliesst, hinterlaesst damit einen Job in
``processing`` - und genau den bekommt er beim naechsten Aufruf zurueck, statt
dass ein zweiter angefangen wird. Ohne das blutete die Warteschlange bei jedem
geschlossenen Fenster einen Beitrag aus.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime

from fbgroups.marketing.beitrag import beitragstext
from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    JobStatus,
    PostVersuch,
    QueueZustand,
)
from fbgroups.marketing.store import MarketingStore
from fbgroups.marketing.veroeffentlicher import Ergebnis
from fbgroups.models import Group


class Grund:
    """Warum gerade kein Auftrag herausgeht. Wird dem Menschen angezeigt."""

    FERTIG = "Warteschlange leer"
    TAGESLIMIT = "Tageslimit erreicht"
    PAUSIERT = "Warteschlange pausiert"
    GESTOPPT = "Warteschlange gestoppt"
    WARTEZEIT = "Wartezeit laeuft noch"


@dataclass(frozen=True)
class Sperre:
    """Es geht gerade nichts - und warum nicht.

    ``wartet_noch`` ist nur bei ``WARTEZEIT`` gesetzt. Die Sekunden gehoeren
    dazu: "Wartezeit laeuft" ohne Zahl ist eine Auskunft, mit der niemand
    entscheiden kann, ob er kurz wartet oder etwas anderes tut.
    """

    grund: str
    wartet_noch: int = 0
    heute_schon: int = 0
    tageslimit: int = 0


@dataclass(frozen=True)
class Auftrag:
    """Ein Beitrag, der jetzt geschrieben werden soll.

    ``versuch_id`` gehoert dazu, weil der Versuch beim Herausgeben schon
    begonnen hat: Die Rueckmeldung muss dieselbe Zeile abschliessen und darf
    keine neue anfangen - sonst zaehlte ein Beitrag zweimal.
    """

    link: CampaignGroup
    gruppe: Group | None
    text: str
    versuch_id: int
    heute_schon: int
    tageslimit: int
    offen: int

    @property
    def name(self) -> str:
        return (self.gruppe.name if self.gruppe else "") or self.link.group_id

    @property
    def url(self) -> str:
        return self.gruppe.url_canonical if self.gruppe else ""


def hole_auftrag(
    store: MarketingStore,
    campaign: Campaign,
    gruppen: dict[str, Group],
    grenzen,
    *,
    ausgeloest_von: str,
    sitzung: str = "",
    wuerfel: random.Random | None = None,
    jetzt: datetime | None = None,
) -> Auftrag | Sperre:
    """Der naechste Beitrag - oder der Grund, warum gerade keiner drankommt.

    Die Reihenfolge der Pruefungen ist nicht beliebig: Zustand, Tageslimit,
    angefangener Auftrag, **leere Warteschlange**, dann erst Wartezeit. Wer
    pausiert hat, will nicht lesen, dass die Wartezeit laeuft - er will lesen,
    dass er pausiert hat. Und wer nichts mehr in der Schlange hat, soll das
    sofort erfahren und nicht erst nach sieben Minuten Zaehlen.

    ``wuerfel`` ist eine Ausnahme fuer Tests. Ohne ihn wird der Zufall der
    Wartezeit **aus dem Zeitpunkt des letzten Versuchs gezogen** statt frisch:
    Sonst wuerfelte jeder Seitenaufruf eine neue Zahl, der Zaehler spraenge hin
    und her, und wer oft genug neu laedt, erwischte irgendwann eine kurze. So
    steht die Zahl zwischen zwei Beitraegen fest und ist ueber mehrere Beitraege
    hinweg trotzdem verschieden.
    """
    jetzt = jetzt or datetime.now(UTC)

    zustand = store.queue_zustand(campaign.campaign_id)
    if zustand is QueueZustand.PAUSIERT:
        return Sperre(Grund.PAUSIERT)
    if zustand is QueueZustand.GESTOPPT:
        return Sperre(Grund.GESTOPPT)

    heute_schon = store.versuche_heute()
    if heute_schon >= grenzen.tageslimit:
        return Sperre(
            Grund.TAGESLIMIT, heute_schon=heute_schon, tageslimit=grenzen.tageslimit
        )

    # Ein angefangener Job kommt zurueck, bevor ein neuer angefangen wird.
    laufend = store.jobs_mit_status(campaign.campaign_id, JobStatus.PROCESSING)
    offene = {v.group_id: v for v in store.offene_versuche(campaign.campaign_id)}
    for link in laufend:
        versuch = offene.get(link.group_id)
        versuch_id = versuch.versuch_id if versuch and versuch.versuch_id else 0
        if not versuch_id:
            # ``processing`` ohne offene Zeile: Der Versuch wurde abgeschlossen,
            # der Stand aber nicht weitergesetzt (Absturz zwischen beidem).
            # Eine neue Zeile ist hier richtig - der Beitrag steht noch aus.
            versuch_id = _beginne(store, campaign, link, ausgeloest_von, sitzung, jetzt)
        return _auftrag(store, campaign, gruppen, link, versuch_id, heute_schon, grenzen)

    # **Vor** der Wartezeit nachsehen, ob ueberhaupt etwas ansteht. Eine
    # Wartezeit vor einer leeren Warteschlange verspricht einen naechsten
    # Beitrag, den es nicht gibt: Der Zaehler liefe ab, die Seite laedt neu -
    # und dann stuende doch "Warteschlange leer" da. Bei einer Kampagne mit
    # genau einer Gruppe ist das der Normalfall und nicht der Sonderfall.
    # ``naechster_job`` liest nur, es veraendert nichts.
    naechster = store.naechster_job(campaign.campaign_id)
    if naechster is None:
        return Sperre(Grund.FERTIG, heute_schon=heute_schon, tageslimit=grenzen.tageslimit)

    # Die Wartezeit gilt erst ab dem zweiten Beitrag - und nur, wenn wirklich
    # ein neuer angefangen wird. Ein zurueckgegebener Auftrag ist keiner.
    letzte = store.letzter_versuch()
    if letzte is not None:
        vergangen = (jetzt - letzte).total_seconds()
        noetig = grenzen.pause(wuerfel or random.Random(int(letzte.timestamp())))
        if vergangen < noetig:
            return Sperre(
                Grund.WARTEZEIT,
                wartet_noch=int(noetig - vergangen),
                heute_schon=heute_schon,
                tageslimit=grenzen.tageslimit,
            )

    store.set_job_status(campaign.campaign_id, naechster.group_id, JobStatus.PROCESSING)
    versuch_id = _beginne(store, campaign, naechster, ausgeloest_von, sitzung, jetzt)
    return _auftrag(store, campaign, gruppen, naechster, versuch_id, heute_schon, grenzen)


def melde_ergebnis(
    store: MarketingStore,
    campaign_id: str,
    group_id: str,
    versuch_id: int,
    ergebnis: Ergebnis,
) -> JobStatus:
    """Schliesst den Versuch ab und setzt den Stand. Returns: der neue Stand.

    Die einzige Stelle, an der ein Ausgang eingetragen wird - Schleife und
    Weboberflaeche rufen beide hier an. Liefe die Zuordnung von Ausgang zu
    Stand an zwei Stellen, wanderte ein ``uebersprungen`` irgendwann nach
    ``failed``, und ``campaign retry`` holte eine bewusste Entscheidung zurueck.
    """
    if ergebnis.uebersprungen:
        store.beende_versuch(versuch_id, erfolg=False, fehler="uebersprungen")
        store.set_job_status(campaign_id, group_id, JobStatus.CANCELLED)
        return JobStatus.CANCELLED

    if ergebnis.erfolg:
        store.beende_versuch(versuch_id, erfolg=True, post_url=ergebnis.post_url)
        store.set_job_status(campaign_id, group_id, JobStatus.PUBLISHED)
        return JobStatus.PUBLISHED

    if ergebnis.abbrechen and not ergebnis.fehler:
        # Schluss auf Wunsch, kein Fehlschlag. ``erzwingen``, weil
        # ``processing -> queued`` in der Uebergangstabelle bewusst fehlt.
        store.beende_versuch(versuch_id, erfolg=False, fehler="Lauf beendet")
        store.set_job_status(campaign_id, group_id, JobStatus.QUEUED, erzwingen=True)
        return JobStatus.QUEUED

    store.beende_versuch(versuch_id, erfolg=False, fehler=ergebnis.fehler)
    store.set_job_status(campaign_id, group_id, JobStatus.FAILED, fehler=ergebnis.fehler)
    return JobStatus.FAILED


def _beginne(
    store: MarketingStore,
    campaign: Campaign,
    link: CampaignGroup,
    ausgeloest_von: str,
    sitzung: str,
    jetzt: datetime,
) -> int:
    """Schreibt die Protokollzeile - **vor** dem Beitrag, nie danach."""
    return store.beginne_versuch(
        PostVersuch(
            campaign_id=campaign.campaign_id,
            group_id=link.group_id,
            tracking_code=link.tracking_code,
            job_status=JobStatus.PROCESSING,
            ausgeloest_von=ausgeloest_von,
            browser_session=sitzung,
            begonnen_am=jetzt,
        )
    )


def _auftrag(
    store: MarketingStore,
    campaign: Campaign,
    gruppen: dict[str, Group],
    link: CampaignGroup,
    versuch_id: int,
    heute_schon: int,
    grenzen,
) -> Auftrag:
    return Auftrag(
        link=link,
        gruppe=gruppen.get(link.group_id),
        text=beitragstext(campaign, link),
        versuch_id=versuch_id,
        heute_schon=heute_schon,
        tageslimit=grenzen.tageslimit,
        offen=store.job_counts(campaign.campaign_id).get(JobStatus.QUEUED.value, 0),
    )
