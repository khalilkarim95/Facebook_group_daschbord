"""Der Arbeiter - eine Aufgabe nach der anderen, nie zwei zugleich.

Dieses Modul ist die **Ablaufsteuerung** und sonst nichts: Es nimmt den
naechsten Job aus der Warteschlange, laesst ihn von einem ``Veroeffentlicher``
absetzen, schreibt den Ausgang mit und wartet. Wie ein Beitrag in eine Gruppe
kommt, steht hier nirgends - das ist Sache des Adapters.

Diese Trennung ist dieselbe wie bei ``providers/base.py`` in der Suchschicht,
und sie hat denselben Grund: Der Teil, der ueber Tageslimit, Reihenfolge und
Abbruch entscheidet, muss ohne Netz, ohne Browser und ohne Facebook pruefbar
sein. Ein Regelwerk, das nebenbei einen Browser steuert, laesst sich nicht
mehr fuer sich testen - und genau diese Regeln sind es, die verhindern, dass
310 Beitraege an einem Nachmittag hinausgehen.

Vier Entscheidungen, die man dem Code sonst nicht ansieht:

**Streng nacheinander.** Kein Thread, kein ``asyncio``, keine Parallelitaet.
Zwei gleichzeitig laufende Beitraege waeren nicht doppelt so schnell, sondern
der Unterschied zwischen einem Menschen, der arbeitet, und einem Programm, das
sendet. Die Wartezeit dazwischen ist deshalb kein Schoenheitsfehler des
Ablaufs, sondern sein Kern.

**Der Zustand wird vor jedem Job neu gelesen**, nicht einmal am Anfang. Nur so
wirken ``pause``, ``resume`` und ``stop`` waehrend eines laufenden Arbeiters -
sie werden von einem anderen Prozess geschrieben (CLI oder Uebersicht), und
ein Arbeiter, der seinen Zustand im Speicher haelt, saehe sie nie.

**Das Tageslimit zaehlt aus ``post_versuche``, nicht aus einem Zaehler im
Speicher.** Ein Arbeiter, der um 08:00 zwanzig Beitraege setzt, abstuerzt und
um 14:00 neu gestartet wird, saehe sonst einen leeren Zaehler und setzte
zwanzig weitere. Die Wahrheit steht in der Datenbank, und nur dort.

**Der Versuch wird vor dem Absetzen protokolliert.** ``beginne_versuch``
laeuft, bevor der Adapter etwas tut. Bricht der Arbeiter mitten im Absetzen
ab, bleibt eine Zeile ohne ``beendet_am`` stehen - unangenehm, aber
beantwortbar. Ohne sie wuesste niemand, ob in der Gruppe nun ein Beitrag steht.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fbgroups.config import AppConfig
from fbgroups.marketing.beitrag import beitragstext
from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    JobStatus,
    PostVersuch,
    QueueZustand,
)
from fbgroups.marketing.store import MarketingStore
from fbgroups.marketing.veroeffentlicher import Ergebnis, Veroeffentlicher
from fbgroups.models import Group

# Wer den Versuch ausgeloest hat - landet so in ``post_versuche.ausgeloest_von``.
AUSLOESER = "worker"


class Abbruchgrund:
    """Warum ein Lauf geendet hat. Als Text, weil er dem Menschen angezeigt wird."""

    FERTIG = "Warteschlange leer"
    TAGESLIMIT = "Tageslimit erreicht"
    LAUFGRENZE = "Grenze dieses Laufs erreicht"
    PAUSIERT = "Warteschlange pausiert"
    GESTOPPT = "Warteschlange gestoppt"
    ADAPTER = "Vom Veroeffentlicher beendet"
    ABGEBROCHEN = "Von Hand abgebrochen"


@dataclass(frozen=True)
class Grenzen:
    """Was der Lauf einhaelt. Kommt aus ``config/settings.yaml``.

    Keine dieser Zahlen steht im Programm fest: Ein Tageslimit, das man nur
    durch eine Codeaenderung heben kann, wird beim ersten Engpass umgangen
    statt angepasst.
    """

    tageslimit: int = 20
    max_pro_lauf: int = 10
    pause_min: float = 180.0
    pause_max: float = 420.0

    def pause(self, wuerfel: random.Random) -> float:
        """Wie lange bis zum naechsten Beitrag gewartet wird."""
        if self.pause_max <= self.pause_min:
            return max(0.0, self.pause_min)
        return wuerfel.uniform(self.pause_min, self.pause_max)


def lade_grenzen(config: AppConfig) -> Grenzen:
    """Liest die Grenzen aus der Konfiguration - mit den Vorgaben als Rueckfall."""

    def zahl(schluessel: str, standard: float) -> float:
        wert = config.get("marketing", "posting", schluessel, default=standard)
        try:
            return float(wert)
        except (TypeError, ValueError):
            return standard

    return Grenzen(
        tageslimit=int(zahl("max_pro_tag", 20)),
        max_pro_lauf=int(zahl("max_pro_lauf", 10)),
        pause_min=zahl("pause_sekunden_min", 180.0),
        pause_max=zahl("pause_sekunden_max", 420.0),
    )


@dataclass
class Bericht:
    """Was ein Lauf getan hat. Wird angezeigt und in Tests geprueft."""

    versucht: int = 0
    veroeffentlicht: int = 0
    fehlgeschlagen: int = 0
    uebersprungen: int = 0
    grund: str = Abbruchgrund.FERTIG
    #: Je Gruppe eine Zeile - fuer die Anzeige am Ende.
    zeilen: list[tuple[str, str]] = field(default_factory=list)

    @property
    def offen_geblieben(self) -> bool:
        return self.grund not in (Abbruchgrund.FERTIG,)


def arbeite(
    store: MarketingStore,
    campaign: Campaign,
    gruppen: dict[str, Group],
    veroeffentlicher: Veroeffentlicher,
    grenzen: Grenzen,
    *,
    schlafen: Callable[[float], None] = time.sleep,
    wuerfel: random.Random | None = None,
    melde: Callable[[str], None] = lambda _: None,
) -> Bericht:
    """Arbeitet die Warteschlange ab, bis eine Grenze greift.

    ``schlafen`` und ``wuerfel`` sind Parameter, damit die Tests den Ablauf in
    Millisekunden pruefen koennen, ohne die Wartezeiten zu aendern - eine
    Grenze, die nur im Test gilt, prueft nicht die Grenze des Betriebs.

    Der Rueckgabewert nennt auch den **Grund** des Endes. "8 von 20
    veroeffentlicht" ist ohne ihn nicht zu deuten: fertig, pausiert oder an
    der Tagesgrenze sind drei verschiedene Lagen mit drei verschiedenen
    naechsten Schritten.
    """
    wuerfel = wuerfel or random.Random()
    bericht = Bericht()
    # Ueber **alle** Kampagnen: Die knappe Ressource ist das Facebook-Konto,
    # nicht die Kampagne. Je Kampagne gezaehlt waeren zwei Kampagnen vierzig
    # Beitraege aus demselben Konto, und das Tageslimit eine Beschriftung ohne
    # Wirkung.
    heute_schon = store.versuche_heute()

    while True:
        # -- Grenzen, bevor irgendetwas geschieht -------------------------
        if heute_schon + bericht.versucht >= grenzen.tageslimit:
            bericht.grund = Abbruchgrund.TAGESLIMIT
            break
        if grenzen.max_pro_lauf and bericht.versucht >= grenzen.max_pro_lauf:
            bericht.grund = Abbruchgrund.LAUFGRENZE
            break

        # Frisch aus der Datenbank: pause/resume/stop kommen aus einem anderen
        # Prozess und waeren in einer gemerkten Fassung nicht zu sehen.
        zustand = store.queue_zustand(campaign.campaign_id)
        if zustand is QueueZustand.PAUSIERT:
            bericht.grund = Abbruchgrund.PAUSIERT
            break
        if zustand is QueueZustand.GESTOPPT:
            bericht.grund = Abbruchgrund.GESTOPPT
            break

        link = store.naechster_job(campaign.campaign_id)
        if link is None:
            bericht.grund = Abbruchgrund.FERTIG
            break

        # -- Ein Beitrag --------------------------------------------------
        if bericht.versucht:
            wartezeit = grenzen.pause(wuerfel)
            melde(f"warte {wartezeit:.0f}s")
            schlafen(wartezeit)
            # Waehrend der Wartezeit kann jemand pausiert haben. Nachsehen,
            # bevor der naechste Beitrag hinausgeht - sonst wirkt "pause" erst
            # sieben Minuten spaeter, und in der Zwischenzeit steht ein
            # Beitrag in einer Gruppe, den niemand mehr wollte.
            zustand = store.queue_zustand(campaign.campaign_id)
            if zustand is not QueueZustand.LAUFEND:
                bericht.grund = (
                    Abbruchgrund.PAUSIERT
                    if zustand is QueueZustand.PAUSIERT
                    else Abbruchgrund.GESTOPPT
                )
                break

        ergebnis = _ein_beitrag(store, campaign, gruppen, link, veroeffentlicher, bericht)

        if ergebnis.abbrechen:
            bericht.grund = Abbruchgrund.ADAPTER
            break

    return bericht


def _ein_beitrag(
    store: MarketingStore,
    campaign: Campaign,
    gruppen: dict[str, Group],
    link: CampaignGroup,
    veroeffentlicher: Veroeffentlicher,
    bericht: Bericht,
) -> Ergebnis:
    """Setzt genau einen Beitrag ab und schreibt alles mit.

    Getrennt vom Ablauf, weil hier die Reihenfolge zaehlt: erst ``processing``,
    dann die Protokollzeile, dann der Versuch. Wer die Zeile hinter den Versuch
    schoebe, verloere genau den Fall, fuer den sie da ist.
    """
    gruppe = gruppen.get(link.group_id)
    name = (gruppe.name if gruppe else "") or link.group_id
    text = beitragstext(campaign, link)

    store.set_job_status(campaign.campaign_id, link.group_id, JobStatus.PROCESSING)
    versuch_id = store.beginne_versuch(
        PostVersuch(
            campaign_id=campaign.campaign_id,
            group_id=link.group_id,
            tracking_code=link.tracking_code,
            job_status=JobStatus.PROCESSING,
            ausgeloest_von=AUSLOESER,
            browser_session=veroeffentlicher.name,
            begonnen_am=datetime.now(UTC),
        )
    )
    bericht.versucht += 1

    try:
        ergebnis = veroeffentlicher.veroeffentliche(gruppe=gruppe, text=text, link=link)
    except Exception as exc:  # noqa: BLE001
        # Ein Adapter, der wirft, darf den Lauf nicht mitreissen: Der Job
        # stuende sonst fuer immer auf ``processing``, und die Gruppe waere
        # weder offen noch fertig. Der Grund wandert ins Protokoll.
        ergebnis = Ergebnis(erfolg=False, fehler=f"{type(exc).__name__}: {exc}")

    if ergebnis.uebersprungen:
        store.beende_versuch(versuch_id, erfolg=False, fehler="uebersprungen")
        store.set_job_status(campaign.campaign_id, link.group_id, JobStatus.CANCELLED)
        bericht.uebersprungen += 1
        bericht.zeilen.append((name, "uebersprungen"))
        return ergebnis

    if ergebnis.erfolg:
        store.beende_versuch(versuch_id, erfolg=True, post_url=ergebnis.post_url)
        store.set_job_status(campaign.campaign_id, link.group_id, JobStatus.PUBLISHED)
        bericht.veroeffentlicht += 1
        bericht.zeilen.append((name, "veroeffentlicht"))
        return ergebnis

    if ergebnis.abbrechen and not ergebnis.fehler:
        # Schluss auf Wunsch, kein Fehlschlag: Der Job geht zurueck in die
        # Warteschlange und ist beim naechsten Lauf wieder der naechste.
        #
        # ``erzwingen``, weil ``processing -> queued`` in der Uebergangstabelle
        # bewusst fehlt: Ein Job, der von selbst dorthin zurueckfaende, koennte
        # zwischen beiden Zustaenden kreisen, ohne dass es auffiele. Hier steht
        # aber genau der Fall, fuer den die Ausnahme gedacht ist - ein Mensch
        # loest ein ``processing`` auf, in dem nichts geschehen ist.
        store.beende_versuch(versuch_id, erfolg=False, fehler="Lauf beendet")
        store.set_job_status(
            campaign.campaign_id, link.group_id, JobStatus.QUEUED, erzwingen=True
        )
        # Der Versuch bleibt gezaehlt, obwohl nichts hinausging: Die Zeile steht
        # in ``post_versuche`` und wird morgen mitgezaehlt. Zu viel zu zaehlen
        # heisst, an einem Tag weniger zu posten - die harmlose Richtung.
        bericht.zeilen.append((name, "zurueck in die Warteschlange"))
        return ergebnis

    store.beende_versuch(versuch_id, erfolg=False, fehler=ergebnis.fehler)
    store.set_job_status(
        campaign.campaign_id, link.group_id, JobStatus.FAILED, fehler=ergebnis.fehler
    )
    bericht.fehlgeschlagen += 1
    bericht.zeilen.append((name, f"fehlgeschlagen: {ergebnis.fehler or 'ohne Angabe'}"))
    return ergebnis
