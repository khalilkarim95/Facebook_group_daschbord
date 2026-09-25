"""Der Treiber der Kommentarautomatik - hier trifft der Plan auf den Browser.

``lauf.py`` entscheidet, **was** als naechstes kommt, und kennt weder Netz
noch Playwright. Dieses Modul fuehrt es aus: Es oeffnet **einen** Browser fuer
den ganzen Lauf, holt sich Schritt fuer Schritt den naechsten Auftrag und
meldet jeden Ausgang ueber ``arbeit.melde_vorschlag`` - denselben Weg, den
auch die Arbeitsseite nimmt. Eine zweite Buchungsart daneben waere eine zweite
Zaehlweise fuer dieselben Kommentare.

## Ein Browser fuer den ganzen Lauf

``campaign auto`` oeffnet je Aufruf einen eigenen Browser. Fuer einen Lauf
ueber vierzig Gruppen waeren das vierzig Starts, jeder mit Profilaufbau und
Anmeldepruefung - und vierzig Fenster, die auf- und zugehen. Der Lauf haelt
den Kontext deshalb offen und gibt ihn erst am Ende zurueck.

## Warum die Datenbank zwischendurch zugeht

Ein Schritt dauert Minuten. Waehrend der Browser arbeitet, ist keine
Verbindung offen - dieselbe Aufteilung wie in ``campaign auto`` (lesen,
handeln, schreiben). Sonst hielte ein Lauf ueber Stunden eine Sperre auf einer
Datei, die zugleich die Weiterleitung bedient.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console

from fbgroups.config import AppConfig
from fbgroups.marketing import (
    entscheidung as entscheidung_modul,
)
from fbgroups.marketing import (
    grenzen,
    inhalt,
    kaltmodus,
    lauf,
)
from fbgroups.marketing.ausgang import Ausgangsart, klassifiziere
from fbgroups.marketing.models import (
    CampaignStatus,
    KampagnenLaufStatus,
    LaufStatus,
    PostStatus,
    Texttyp,
)
from fbgroups.marketing.store import MarketingStore
from fbgroups.storage import SqliteStore

console = Console()


@dataclass(frozen=True)
class Schrittergebnis:
    """Was ein einzelner Kommentarversuch ergeben hat."""

    erfolg: bool
    fehler: str = ""
    post_url: str = ""
    # **Wird seit dem 23.09.2026 nicht mehr gesetzt.** Es stand fuer "keine
    # Beitraege gefunden" und "alle sichtbaren schon kommentiert" - zwei
    # Aussagen ueber diesen Augenblick (Facebook zeigt oft nur einen einzigen
    # Beitrag an), die als dauerhaftes Urteil ueber die Gruppe gebucht
    # wurden. Beide sind jetzt ``kein_anlass``. Das Feld bleibt, weil ein
    # aelterer Arbeitsrechner es noch sendet; es gilt dann wie ``kein_anlass``.
    erschoepft: bool = False

    text: str = ""
    """Der Text, der wirklich hinausging - leer, wenn es der vorbereitete war.

    Seit dem 13.09.2026 waehlt der Lauf den Kommentar erst, wenn er den
    Beitrag kennt (``vorlagen.anlasstext``): Was in der Fassung vorbereitet
    stand, ist dann nicht mehr das, was in der Gruppe steht. Der Ausgang
    traegt ihn deshalb zurueck, damit ``_buche`` ihn in die Fassung schreiben
    kann - sonst stuenden zwei Texte fuer denselben Vorgang im Bestand.
    """

    vorlage_key: str = ""
    """Aus welcher Vorlage er kam (``ar/anlaesse/geschenk/hadiye``)."""

    gruppe_beiseite: bool = False
    #: Diesen **Beitrag** gibt es nicht mehr. Weder ein Urteil ueber die
    #: Gruppe noch ein technischer Fehlschlag: Die Adresse zeigt ins Leere,
    #: und der naechste Beitrag derselben Gruppe kann gehen.
    beitrag_weg: bool = False
    """Diese Gruppe fuer **diesen Lauf** beiseitelegen - ohne Urteil.

    Der Unterschied zu ``kein_anlass`` ist der Grund, nicht die Folge: Dort
    stand heute nichts Passendes, hier hat die Technik nicht mitgespielt.
    Beides legt die Gruppe fuer diesen Durchgang beiseite, und beides ist
    **kein** Urteil ueber sie - aber im Protokoll muss der Unterschied
    stehen, sonst sieht "kein Anlass" aus wie "Browser tot".

    Eingefuehrt am 15.09.2026: Ohne dieses Feld bot der Server nach einem
    technischen Fehlschlag dieselbe Gruppe sofort wieder an, und der Lauf
    lief in derselben Gruppe im Kreis.
    """

    bezuege: tuple[tuple[str, tuple[str, ...]], ...] = ()
    """Die Bezuege jedes **gelesenen** Beitrags: ``((post_url, (bezug, ...)), ...)``.

    Seit dem 23.09.2026 die Grundlage, auf der eine Gruppe beurteilt wird
    (``bezug.fuer_gruppe``). Sie reist mit dem Ausgang, weil sie dort
    entsteht, wo der Browser ist, und dort gespeichert wird, wo der Bestand
    ist - oertlich im selben Prozess, im Fernbetrieb auf dem Server.
    Schlagwoerter, nie der Text.
    """

    gescheiterte_posts: tuple[str, ...] = ()
    """Beitraege, unter denen der Kommentar **technisch** nicht ging (24.09.2026).

    Kein Kommentarfeld, nicht beschreibbar, Beitrag geloescht. Gespeichert in
    ``gescheiterte_beitraege``; fuer ``SPERRE_GESCHEITERT_STUNDEN`` faesst der
    Lauf sie nicht wieder an. Nur Adressen, kein Text.
    """

    ausschliessen: str = ""
    """Nicht leer: In dieser Gruppe ist **wirklich** nichts zu machen - der Grund.

    Gesetzt nur nach einer vollen Suche (``scroll_runden`` Runden, alle
    Beitraege beurteilt), wenn keiner kommentierbar war. Die Gruppe wird
    dann aus der Bearbeitung genommen (``store.schliesse_gruppe_aus``) -
    sichtbar und mit einem Haken in der Uebersicht zuruecknehmbar.
    """

    kein_anlass: bool = False
    """Heute stand hier nichts, worauf eine Antwort etwas beigetragen haette.

    Der dritte Ausgang neben Erfolg und Fehlschlag, und der wichtigste der
    neuen Fassung (Anforderung vom 12.09.2026, Punkt 45): ``NO_REPLY`` ist
    ein **Ergebnis**. Er wird deshalb nicht als Fehlversuch gebucht - das
    zaehlte gegen die Fassung und irgendwann gegen die Gruppe - sondern legt
    die Gruppe fuer **diesen Lauf** beiseite. Morgen stehen dort andere
    Beitraege.
    """


def mitgliedschaft_pflicht(config: AppConfig) -> bool:
    """Muss das Konto Mitglied sein, bevor die Automatik es versucht?

    Vorgabe hier **wahr**, in ``config/settings.yaml`` vom Nutzer auf ``false``
    gesetzt (01.09.2026). Die beiden Werte widersprechen sich nicht: Der Code
    behaelt den Schutz fuer den Fall, dass niemand etwas gesagt hat, und die
    Konfiguration ist die Stelle, an der etwas gesagt wird.

    Ausgeschaltet versucht der Lauf auch Gruppen ohne vermerkte Mitgliedschaft.
    Der Grund steht in ``lauf.Gruppenfortschritt.mitgliedschaft_noetig``: Der
    Vermerk ist eine Angabe ueber **unseren** Stand, nicht ueber die Gruppe -
    in ``Betaraqiq-Test Syrer in Berlin`` standen drei Kommentare, waehrend er
    auf ``beitritt_angefragt`` stand.
    """
    return bool(config.get("automatik", "mitgliedschaft_pflicht", default=True))


def aktive_kampagnen(store: MarketingStore) -> list[str]:
    """Die Kampagnen, die ein Lauf abarbeiten wuerde - in fester Reihenfolge.

    ``active`` und nichts anderes: Ein Entwurf ist nicht in Betrieb, eine
    pausierte Kampagne ist ausdruecklich angehalten, eine abgeschlossene ist
    fertig. Sortiert nach ``created_at``, damit zwei Laeufe dieselbe Folge
    ergeben - bei gleicher Zeit entscheidet die Kennung.
    """
    kampagnen = store.load_campaigns(CampaignStatus.ACTIVE)
    return [k.campaign_id for k in sorted(kampagnen, key=lambda k: (k.created_at, k.campaign_id))]


def hole_oder_starte_lauf(
    store: MarketingStore,
    *,
    ziel_je_gruppe: int,
    nur: list[str] | None = None,
    neu: bool = False,
) -> tuple[int, bool]:
    """``(lauf_id, neu)`` - einen offenen Lauf fortsetzen oder einen neuen einfrieren.

    Das ist Punkt 17: Wird der Vorgang waehrend Kampagne 3 unterbrochen, setzt
    der naechste Start dort auf, statt Kampagne 1 noch einmal zu fahren. Und
    es ist Punkt 16: Ein **laufender** Lauf behaelt seine Liste; eine Kampagne,
    die inzwischen auf ``active`` gesetzt wurde, greift nicht in ihn ein.

    ``nur`` schraenkt die einzufrierende Liste auf bestimmte Kampagnen ein und
    wirkt **ausschliesslich beim Anlegen**. Geschnitten wird gegen die aktiven:
    Eine pausierte Kampagne kommt auch dann nicht dran, wenn sie ausdruecklich
    genannt wird - sonst waere "pausiert" eine Beschriftung ohne Wirkung.

    ``neu`` schliesst einen offenen Lauf ab, damit hier eine frische Liste
    entsteht. Der Ausweg aus Punkt 16, und ausdruecklich ein eigener
    Handgriff: Die eingefrorene Liste ist richtig, solange sie den Vorgang
    schuetzt - sie wird zum Hindernis, sobald Kampagnen **dazukommen**, denn
    die kaemen sonst nie dran. Verloren geht dabei nur die Liste: Der
    Fortschritt steht in den Fassungen und wird gelesen, nicht gefuehrt.
    """
    offen = store.offener_lauf()
    if offen is not None and neu:
        store.setze_lauf_status(
            int(offen["lauf_id"]),
            LaufStatus.FERTIG.value,
            meldung="Von Hand abgeschlossen, um eine neue Kampagnenliste "
            "einzufrieren (campaign automatik --neu).",
        )
        offen = None
    if offen is not None:
        lauf_id = int(offen["lauf_id"])
        # **Neue Kampagnen kommen hinten dazu** (24.09.2026). Die Liste blieb
        # bis dahin eingefroren, und der Waechter startet ohne ``--neu`` -
        # eine neu angelegte Kampagne kam damit nie dran, solange der Lauf
        # offen war. Angehaengt wird nur: Was schon drinsteht, behaelt Platz
        # und Stand; der laufende Vorgang wird nicht umgestellt.
        if dazu := store.ergaenze_lauf_kampagnen(lauf_id, aktive_kampagnen(store)):
            console.print(f"[dim]Neue Kampagnen im Lauf: {', '.join(dazu)}[/dim]")
        return lauf_id, False

    kampagnen = aktive_kampagnen(store)
    if nur:
        gewuenscht = set(nur)
        kampagnen = [cid for cid in kampagnen if cid in gewuenscht]
    if not kampagnen:
        # **Eine leere Liste wird nicht eingefroren.** Ein Lauf ohne
        # Kampagnen kann nie ``fertig`` werden (``Lauffortschritt.fertig``
        # verlangt ``bool(self.kampagnen)``), bleibt also auf
        # ``angehalten`` stehen - und ``offener_lauf`` bietet ihn bei jedem
        # Start wieder an. Am 10.09.2026 ist genau das passiert: Die letzte
        # aktive Kampagne wurde beim Abschluss auf ``completed`` gesetzt,
        # der naechste Start fror nichts ein und meldete "0 / 0", und von da
        # an tat die Automatik nichts mehr. ``0`` heisst: kein Lauf - der
        # Aufrufer sagt, warum.
        return 0, False
    # Die oeffentlichen Kurzcodes der eingefrorenen Kampagnen nachtragen -
    # einmal beim Einfrieren und nicht bei jedem Schritt. Neue Zuordnungen
    # bringen ihren Deckname selbst mit (``add_link``); was hier noch fehlt,
    # stammt aus der Zeit vor dem 14.09.2026. Ohne diesen Griff bekaemen
    # genau die dreihundert Bestandsgruppen weiterhin die lange Adresse mit
    # dem Kampagnencode - also gerade die, um die es geht.
    for campaign_id in kampagnen:
        store.kurzcodes_nachtragen(campaign_id)
    return store.starte_lauf(kampagnen, ziel_je_gruppe=ziel_je_gruppe), True


def texte_sicherstellen(
    store: MarketingStore,
    schritt: lauf.Schritt,
    gruppen: dict,
    config: AppConfig,
):  # noqa: ANN201 - Vorschlag, der Typ liegt im Speicher
    """Den Vorschlag zu diesem Schritt holen - und die Fassungen nachziehen.

    **Der Lauf bereitet selbst vor.** Dieselbe Entscheidung wie bei "Arbeiten"
    auf ``/arbeit/{kampagne}``, und aus demselben Grund zulaessig: Texte zu
    erzeugen veroeffentlicht nichts und ueberschreibt nichts
    (``stelle_texte_bereit`` laesst Vorhandenes unangetastet). Das Schlimmste,
    was ein ueberfluessiger Aufruf anrichtet, ist eine Zeile im Protokoll.

    Ohne diesen Schritt las der Lauf nur - und was er nicht fand, erklaerte er
    fuer erschoepft. Am 11.09.2026 endete ein Lauf deshalb mit
    "45 Gruppe(n) erschoepft" und vier Kommentaren: Fuer 44 Gruppen war nie
    ``campaign text`` gelaufen. Eine Gruppe, in der noch nichts steht, ist das
    Gegenteil einer erschoepften.

    Returns: den Vorschlag mit Text, oder ``None`` - dann fehlt die **Vorlage**
    und nicht der Handgriff.
    """
    from fbgroups.marketing.arbeit import stelle_texte_bereit
    from fbgroups.marketing.vorlagen import VorlageFehlt

    def lies():  # noqa: ANN202
        vorschlag = store.vorschlag(
            schritt.campaign_id, schritt.group_id, schritt.texttyp, schritt.nummer
        )
        return vorschlag if vorschlag is not None and vorschlag.text.strip() else None

    if (vorhanden := lies()) is not None:
        return vorhanden

    campaign = store.load_campaign(schritt.campaign_id)
    gruppe = gruppen.get(schritt.group_id)
    if campaign is None or gruppe is None:
        return None

    try:
        # Ohne Beitraege im Lauf (23.09.2026) nur die Kommentare: Der Lauf
        # postet nicht, also legt er auch keine Beitragstexte an. Mit
        # Beitraegen entstehen beide wie bisher - sonst fehlte dem Beitrag
        # seine Fassung, und er kaeme nie an die Reihe.
        stelle_texte_bereit(
            store,
            campaign,
            gruppe,
            config,
            nur=None if beitraege_automatisch(config) else Texttyp.KOMMENTAR,
        )
    except VorlageFehlt:
        # Eine Luecke in ``textvorlagen.yaml`` - fuer *diese* Gruppe entsteht
        # kein Text, und der Grund steht im Bericht. ``config-check`` nennt
        # sie vorher beim Namen.
        return None
    return lies()


def _gruppen(pfad: Path) -> dict:
    with SqliteStore(pfad) as gruppen_store:
        return {g.group_id: g for g in gruppen_store.load_groups()}


def aktionslage(store: MarketingStore, config: AppConfig) -> dict:
    """Was jede Aktion **jetzt** darf - Tagesmenge, Takt und Bremse zusammen.

    Die eine Stelle, an der die Zahlen aus ``settings.yaml`` auf die Zaehler
    des Bestands treffen. ``grenzen.py`` rechnet, dieser Speicher zaehlt, und
    ``lauf.naechster_schritt`` entscheidet damit - keiner der drei kennt die
    Arbeit der anderen beiden.

    Gezaehlt wird **ueber alle Kampagnen**: Die Gegenseite hat kein
    Kampagnenmodell. Zwei Kampagnen mit je fuenf Kommentaren sind zehn
    Kommentare aus einem Konto.
    """
    einstellungen = grenzen.einstellungen(config)
    jetzt = datetime.now(UTC)
    tag = jetzt.date().isoformat()
    lagen: dict = {}

    for aktion in grenzen.Aktion:
        if aktion is grenzen.Aktion.BEITRITT:
            heute = store.anfragen_heute(tag)
            roh = store.letzte_anfrage()
        else:
            heute = store.versuche_heute(tag, aktion.value)
            roh = store.letzter_versuch(aktion.value)
        gesperrt_bis, _stufe = store.sperre(aktion.value)
        lagen[aktion] = grenzen.pruefe(
            aktion,
            einstellungen.fuer(aktion),
            heute=heute,
            letzte=datetime.fromisoformat(roh) if roh else None,
            gesperrt_bis=gesperrt_bis,
            jetzt=jetzt,
        )
    return lagen


def merke_bremse(store: MarketingStore, aktion: str, jetzt: datetime | None = None) -> int:
    """Die Gegenseite hat gebremst - diese Aktion ruht, die anderen nicht.

    Returns: die neue Stufe. Sie verdoppelt die Ruhezeit
    (``grenzen.backoff_minuten``) und ueberlebt einen Neustart: Ohne sie
    finge der Backoff jedes Mal wieder bei einer Stunde an, und genau das
    Muster, das zur Bremsung gefuehrt hat, begaenne von vorn.
    """
    jetzt = jetzt or datetime.now(UTC)
    _bis, stufe = store.sperre(aktion)
    neue_stufe = stufe + 1
    store.merke_sperre(aktion, grenzen.sperre_bis(neue_stufe, jetzt=jetzt), neue_stufe)
    return neue_stufe


class _Schleifenwaechter:
    """Haelt fest, ob derselbe Schritt wiederkommt, ohne dass etwas vorangeht.

    Die Notbremse der Fehlerisolierung. Jeder bekannte Weg schreibt seinen
    Ausgang in den Bestand, und damit steht beim naechsten Durchgang ein
    anderer Schritt an - aber "jeder bekannte Weg" ist genau die Annahme, die
    eine Endlosschleife widerlegt. Kommt derselbe Schritt viermal, wird seine
    Gruppe fuer diesen Lauf beiseitegelegt: Ein Lauf, der nichts mehr tut,
    aber auch nicht aufhoert, ist schlimmer als eine Gruppe weniger.
    """

    GRENZE = 3

    def __init__(self) -> None:
        self.letzter: tuple | None = None
        self.wiederholungen = 0

    def vergiss(self) -> None:
        """Nach einem Schlaf faengt die Zaehlung neu an.

        Der Waechter sucht einen Schritt, der sich **ohne Fortschritt**
        wiederholt. Eine Ruhezeit ist Fortschritt: Die Gruppe war zwischen
        den beiden Malen gar nicht an der Reihe.
        """
        self.letzter = None
        self.wiederholungen = 0

    def haengt(self, schritt: lauf.Schritt) -> bool:
        kennung = (schritt.art, schritt.campaign_id, schritt.group_id, schritt.nummer)
        if kennung == self.letzter:
            self.wiederholungen += 1
        else:
            self.letzter = kennung
            self.wiederholungen = 1
        return self.wiederholungen > self.GRENZE


#: Wie viele **verschiedene** Beitraege ein Kommentarschritt anfassen darf,
#: bevor er die Gruppe beiseitelegt. Der haeufigste Grund fuer ein fehlendes
#: Kommentarfeld ist der Beitrag selbst (Kommentare abgeschaltet, Freigabe
#: noetig, geteilter Beitrag) - der naechste geht dann meist. Seit dem
#: 24.09.2026 fuenf statt drei: Die Suche sammelt jetzt mindestens zehn
#: Beitraege, und Ausweichziele sind nur etwas wert, wenn sie auch versucht
#: werden. Was hier scheitert, wird gemerkt und nicht wieder versucht.
MAX_BEITRAEGE_JE_SCHRITT = 5

#: Wie viele Beitraege eine Gruppe **mindestens** angesehen bekommt, bevor
#: kommentiert wird (24.09.2026, Anweisung des Nutzers: "bis 15 mal runter
#: scrollen und mindestens 10 Beitraege anschauen").
MINDEST_BEITRAEGE = 10


class _Technikwaechter:
    """Wann hoert ein Lauf wegen der Technik auf? **Nur wenn nichts mehr geht.**

    Bis zum 15.09.2026 zaehlte dieser Waechter fuenf technische Fehlschlaege
    **hintereinander** und beendete dann den ganzen Lauf. Die Absicht war
    richtig (ein geschlossenes Fenster hat am 11. und 12.09.2026 je einen
    Lauf entwertet), die Regel war es nicht: "Kommentarfeld nicht gefunden"
    ist in fuenf verschiedenen Gruppen fuenfmal derselbe Zaehlerstand - und
    fuenfmal eine Aussage ueber **einen Beitrag**, nicht ueber den Rechner.
    Eine aktive Kampagne endete damit an einem gewoehnlichen Tag.

    Jetzt gilt:

    * **Sitzungsfehler** (Fenster zu, Anmeldung abgelaufen) beenden den Lauf
      sofort. Dort hilft keine naechste Gruppe.
    * **Gewoehnliche technische Fehlschlaege** legen ihre **Gruppe** beiseite
      (``Schrittergebnis.gruppe_beiseite``) und nehmen sie aus der Kampagne -
      der Lauf geht weiter, und die Fehlerisolierung je Gruppe ist genau
      dafuer da.

    **Die gezaehlte Notbremse ist weg** (20.09.2026, Anweisung des Nutzers).
    Bis dahin endete der Lauf nach ``GRENZE`` = 12 technischen Fehlschlaegen
    in Folge mit dem Satz "ueber verschiedene Gruppen hinweg - dann liegt es
    am Rechner". Im Betrieb war das zweimal dieselbe Fehldiagnose: Es war
    **eine** Gruppe, die der Lauf zwoelfmal anfasste, weil der Rueckfall ohne
    gelesene Texte sie nie beiseitelegte. Der Nutzer hat entschieden, was
    stattdessen geschehen soll - "nicht abbrechen, sondern die Gruppe aus der
    Kampagne entfernen und es mit der naechsten versuchen" -, und genau das
    tut jeder technische Fehlschlag jetzt.

    Damit gibt es **einen** Grund, einen Lauf zu beenden: die Sitzung. Was
    bleibt, ist die Zaehlung (``folge``) - sie erklaert im Protokoll, wie oft
    es hintereinander nicht ging, ohne daraus ein Ende zu machen.
    """

    def __init__(self) -> None:
        self.folge = 0
        self.sitzung = ""

    def melde(self, ergebnis: Schrittergebnis) -> bool:
        """Returns: ob der Lauf jetzt aufhoeren soll. **Nur bei der Sitzung.**"""
        if ergebnis.erfolg or not ist_technisch(ergebnis.fehler):
            self.folge = 0
            self.sitzung = ""
            return False
        if ist_sitzungsfehler(ergebnis.fehler):
            # Kein Zaehlen: Ein geschlossenes Fenster ist beim ersten Mal so
            # eindeutig wie beim fuenften.
            self.sitzung = ergebnis.fehler
            return True
        self.folge += 1
        return False

    def meldung(self) -> str:
        """Warum der Lauf aufgehoert hat - im Klartext fuer den Menschen."""
        return ABBRUCH_SITZUNG.format(fehler=self.sitzung[:120])


def ist_technisch(fehler: str) -> bool:
    """Ein Fehlschlag der Technik - und damit kein Urteil ueber die Gruppe."""
    return klassifiziere(fehler or "") is Ausgangsart.TECHNISCH


#: Woran ein **Sitzungs**-Fehler zu erkennen ist: Der Browser ist weg oder
#: die Anmeldung ist es. Das ist etwas anderes als "dieser Beitrag nimmt
#: keinen Kommentar" - dort hilft der naechste Beitrag, hier hilft nichts
#: mehr, was dieser Lauf tun koennte.
_SITZUNG = (
    "target closed", "browser has been closed", "browser closed",
    "context was destroyed", "connection closed", "websocket",
    "browser wurde geschlossen", "playwright wurde beendet",
    "logged in", "nicht angemeldet", "login", "anmeld",
    "session closed", "targetclosederror", "disconnected",
)


def ist_sitzungsfehler(fehler: str) -> bool:
    """Ist der Browser weg oder die Anmeldung abgelaufen?

    **Der einzige Grund, einen Lauf wirklich zu beenden** (15.09.2026). Alles
    andere - ein fehlendes Kommentarfeld, ein gesperrtes Beitragsformular,
    eine Seite, die nicht laedt - betrifft **einen Beitrag oder eine
    Gruppe**, und dort hilft die naechste. Ist dagegen das Fenster zu, hilft
    keine naechste Gruppe und keine naechste Kampagne: Jeder weitere Schritt
    scheiterte genauso, und am Ende staende ein Lauf voller Vermerke ueber
    Gruppen, an denen nichts liegt.

    Bis dahin beendete ``_Technikwaechter`` den Lauf nach **fuenf beliebigen**
    technischen Fehlschlaegen - also auch nach fuenf Gruppen, in denen bloss
    kein Kommentarfeld stand. Eine aktive Kampagne endete damit an einem
    ganz gewoehnlichen Tag.
    """
    text = (fehler or "").lower()
    return any(muster in text for muster in _SITZUNG)


ABBRUCH_SITZUNG = (
    "Angehalten: Der Browser oder die Anmeldung ist weg ({fehler}). "
    "Hier hilft keine naechste Gruppe - jeder weitere Schritt scheiterte "
    "genauso. Es wurde nichts als Urteil ueber eine Gruppe vermerkt, und "
    "die Kampagne bleibt aktiv. Anmelden mit 'fbgroups auth login', dann "
    "den Lauf erneut starten."
)


def fuehre_lauf_aus(
    config: AppConfig,
    *,
    ausfuehren: Callable[[str, str, str, str], Schrittergebnis],
    beitreten: Callable[[str], tuple[str, str]] | None = None,
    max_schritte: int = 0,
    trocken: bool = False,
    warte: Callable[[float], None] | None = None,
    nur: list[str] | None = None,
    frisch: bool = False,
) -> lauf.Lauffortschritt:
    """Arbeitet den Lauf ab - in der Reihenfolge des Kampagnenablaufs.

    Kampagne waehlen, Beitrittsanfragen, Beitrag und Kommentare, dann die
    naechste Kampagne. Entschieden wird das
    nicht hier, sondern in ``lauf.naechster_schritt``; dieses Modul fuehrt
    aus, was dort ansteht, und kennt die Reihenfolge nicht.

    ``ausfuehren`` bekommt (Gruppen-URL, Gruppen-ID, Text, Zweck) und liefert
    ein ``Schrittergebnis``. Der Zweck ist ``post`` oder ``kommentar``: Ein
    Beitrag wird abgesetzt, ein Kommentar unter einen fremden Beitrag
    gesetzt - zwei Handgriffe im Browser, ein Vertrag.

    ``beitreten`` bekommt die Gruppen-URL und liefert ``(ausgang, bemerkung)``
    wie frueher ``actions.request_join`` (entfernt). **Fehlt es, entstehen keine
    Beitrittsschritte** - ein Treiber ohne Browser soll nicht so tun, als
    koennte er beitreten.

    Der **Browserkontext gehoert dem Aufrufer**: Er oeffnet ihn einmal fuer
    den ganzen Lauf und bindet ihn in diese Funktion ein. Dadurch steht in
    diesem Modul keine Zeile Playwright, und der Test reicht eine Funktion
    herein, die zaehlt statt zu kommentieren.

    ``max_schritte`` begrenzt einen Lauf (0 = ohne Grenze); ``trocken`` zeigt
    nur, was geschaehe; ``warte`` haelt den Takt der Beitrittsanfragen ein -
    im Test eine Funktion, die nichts tut.

    **Kein Fehler einer Gruppe beendet den Lauf.** Was in einem Schritt
    hochkommt, wird gemeldet, die Gruppe wird fuer diesen Lauf beiseitegelegt
    (``ueberspringe_gruppe``), und es geht mit der naechsten weiter - und wenn
    die Kampagne nichts mehr hergibt, mit der naechsten Kampagne.
    """
    pfad = config.path("sqlite_path")
    aktiv, _pro_tag, abstand = kaltmodus.einstellungen(config)
    pflicht = mitgliedschaft_pflicht(config)
    schlafen = warte if warte is not None else _schlafe

    with MarketingStore(pfad) as store:
        lauf_id, neu = hole_oder_starte_lauf(
            store,
            ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE,
            nur=list(nur or []),
            neu=frisch,
        )
        if lauf_id == 0:
            console.print(
                "[yellow]Keine aktive Kampagne - es wurde kein Lauf begonnen.[/yellow]"
            )
            return lauf.Lauffortschritt(lauf_id=0, status=LaufStatus.FERTIG, kampagnen=[])
        if neu:
            console.print(f"[cyan]Neuer Lauf {lauf_id}[/cyan]")
        else:
            console.print(f"[cyan]Lauf {lauf_id} wird fortgesetzt[/cyan]")

    gruppen = _gruppen(pfad)
    getan = 0
    waechter = _Schleifenwaechter()
    technik = _Technikwaechter()

    def stand(store: MarketingStore) -> lauf.Lauffortschritt:
        """Den Stand lesen - mit den Grenzen aller Aktionen."""
        lagen = aktionslage(store, config)
        if beitreten is None:
            # Ein Treiber ohne Browser soll nicht so tun, als koennte er
            # beitreten - dann entstehen auch keine Beitrittsschritte.
            lagen[grenzen.Aktion.BEITRITT] = grenzen.Lage(
                grenzen.Aktion.BEITRITT, False, grund="dieser Treiber stellt keine Anfragen"
            )
        heute = datetime.now(UTC).date().isoformat()
        return lauf.lies_fortschritt(
            store,
            lauf_id,
            gruppen,
            mitgliedschaft_pflicht=pflicht,
            aktionen=lagen,
            # Die Bezuege der Gruppen (23.09.2026) - bei jedem Durchgang neu
            # gelesen, denn jeder Kommentarschritt liest Beitraege und legt
            # ihre Bezuege ab. Sie entscheiden noch nichts; siehe
            # ``lauf.Gruppenfortschritt.bezuege``.
            bezuege=store.gruppenbezuege(gruppen),
            heute_je_gruppe=store.versuche_heute_je_gruppe(
                heute, Texttyp.KOMMENTAR.value
            ),
            gruppenlimit=grenzen.einstellungen(config)
            .fuer(grenzen.Aktion.KOMMENTAR)
            .je_gruppe_taeglich,
            ziel_kommentare=ziel_kommentare(config),
            kommentare_zuerst=kommentare_zuerst(config),
            beitraege=beitraege_automatisch(config),
        )

    while True:
        # 1. LESEN: Stand holen, naechsten Schritt bestimmen.
        with MarketingStore(pfad) as store:
            fortschritt = stand(store)
            schritt = lauf.naechster_schritt(fortschritt)

            if schritt is None:
                # Kein Schritt heisst nicht "fertig". Es kann auch heissen:
                # Der Lauf wartet auf den Takt der Beitrittsanfragen, oder die
                # naechste Gruppe hat alle Fassungen verbraucht, ohne voll zu
                # werden - dann ist sie erschoepft, und danach geht es weiter.
                wartezeit = fortschritt.wartet_auf_beitritt
                if wartezeit and not trocken:
                    console.print(f"[yellow]Beitrittstakt: {wartezeit}[/yellow]")
                    schlafen(wartesekunden(wartezeit))
                    continue
                # **Dieselbe Frage fuer jede andere Aktion** (14.09.2026).
                # Den Warteweg gab es nur fuer die Beitrittsanfrage; stand
                # der Takt der Kommentare im Weg, endete der Lauf - mit
                # zweitausend offenen Kommentaren und zwei Minuten Restzeit.
                # Der oertliche Lauf geht hier denselben Weg wie der
                # Fernbetrieb: zwei Antworten auf dieselbe Frage waeren zwei
                # Laeufe mit verschiedenem Ausgang.
                takt = fortschritt.wartet_auf_takt
                if takt and not trocken:
                    console.print(f"[yellow]Takt: {takt}[/yellow]")
                    schlafen(wartesekunden(takt))
                    continue
                if _erschoepfung_eintragen(store, fortschritt):
                    continue
                # **Ruht noch eine Gruppe, ist der Lauf nicht durch**
                # (20.09.2026). Der Lauf soll zwischen den Gruppen hin und
                # her gehen, bis jede ihre zehn Kommentare hat - nicht nach
                # einem Durchgang aufhoeren, weil gerade in keiner etwas
                # Passendes stand.
                ruhend = store.naechste_rueckkehr(lauf_id)
                if ruhend and not trocken:
                    anzahl, wann = ruhend
                    sekunden = ruhesekunden(wann)
                    console.print(
                        f"[yellow]{anzahl} Gruppe(n) ruhen - naechste in "
                        f"{int(sekunden / 60)} Min[/yellow]"
                    )
                    schlafen(sekunden)
                    # Der Schlaf ist Fortschritt: Danach ist eine andere
                    # Gruppe an der Reihe, auch wenn derselbe Schritt
                    # herauskommt. Sonst zaehlte der Schleifenwaechter ihn
                    # als Stillstand und legte die Gruppe endgueltig weg.
                    waechter.vergiss()
                    continue
                break

            if waechter.haengt(schritt):
                # Derselbe Schritt zum vierten Mal - hier kommt nichts voran.
                _ueberspringen(
                    store, lauf_id, schritt, "Schritt wiederholt sich ohne Fortschritt"
                )
                continue

            # **Die Runde zaehlt den Besuch, nicht den Ausgang** (23.09.2026).
            # Vermerkt wird, bevor der Browser anfaengt: Was immer dort
            # geschieht - Erfolg, kein Anlass, ein Absturz -, die Gruppe war
            # in dieser Runde dran, und beim naechsten Durchgang ist die
            # naechste an der Reihe.
            if schritt.runde and not trocken:
                store.merke_besuch(
                    lauf_id, schritt.campaign_id, schritt.group_id, schritt.runde
                )

        try:
            fertig = _fuehre_schritt_aus(
                config,
                pfad,
                lauf_id,
                schritt,
                gruppen,
                ausfuehren=ausfuehren,
                beitreten=beitreten,
                trocken=trocken,
                kaltmodus_aktiv=aktiv,
                abstand=abstand,
                technik=technik,
            )
        except Exception as exc:  # noqa: BLE001 - eine Gruppe, nicht der Lauf
            # **Die Fehlerisolierung.** Was hier hochkommt, ist ein Fehler
            # dieser einen Gruppe: ein abgebrochener Browser, eine Vorlage,
            # die wirft, eine Zuordnung, die verschwunden ist. Der Lauf geht
            # weiter; die Gruppe kostet das diesen Durchgang und nicht mehr.
            grund = str(exc).splitlines()[0][:160] or exc.__class__.__name__
            console.print(f"[red]  Fehler: {grund}[/red]")
            with MarketingStore(pfad) as store:
                _ueberspringen(store, lauf_id, schritt, grund)
            continue

        if fertig:
            break

        getan += 1
        if max_schritte and getan >= max_schritte:
            console.print(f"[dim]Grenze von {max_schritte} Schritten erreicht.[/dim]")
            break

    # Abschluss: Der Zustand wird aus dem Stand abgeleitet, nicht behauptet.
    with MarketingStore(pfad) as store:
        fortschritt = stand(store)
        _stand_fortschreiben(store, lauf_id, fortschritt)
        return stand(store)


def _schlafe(sekunden: float) -> None:
    """Die einzige Stelle, an der dieses Modul die Zeit anhaelt."""
    import time

    time.sleep(sekunden)


#: Wie lange eine Gruppe ruht, in der gerade nichts Passendes stand.
#:
#: Die Zahl beantwortet eine Abwaegung: Zu kurz, und der Lauf holt dieselbe
#: Gruppenseite alle paar Minuten neu, ohne dass sich dort etwas geaendert
#: haette; zu lang, und eine Kampagne mit wenigen Gruppen steht still.
#: Dreissig Minuten sind ungefaehr die Zeit, in der eine lebendige Gruppe
#: einen neuen Beitrag bekommt - und der ist der einzige Grund, es noch
#: einmal zu versuchen.
RUHE_MINUTEN = 30


def beitraege_automatisch(config: AppConfig) -> bool:
    """Setzt der Lauf auch den eigenen **Beitrag** einer Gruppe ab?

    Seit dem 23.09.2026 **nein** (Anweisung des Nutzers: "Aktuell brauchen
    wir keinen automatischen Post-Workflow mehr"). Der Lauf verarbeitet dann
    ausschliesslich Kommentare; ein offener Beitrag haelt keine Gruppe offen
    und keine Kampagne vom Abschluss ab (``lauf.lies_fortschritt``).

    Die Vorgabe **im Code** ist ``False``: Automatisch zu posten ist die
    auffaelligere Handlung, und sie braucht eine ausdrueckliche Ansage in
    ``settings.yaml`` (``automatik.beitraege: true``). Der Weg dafuer bleibt
    vollstaendig erhalten - Einschalten ist eine Zeile, keine Codeaenderung.
    Von Hand (Arbeitsseite) bleibt der Beitrag unberuehrt.
    """
    return bool(config.get("automatik", "beitraege", default=False))


def scroll_runden(config: AppConfig) -> int:
    """Wie viele Scroll-Runden eine Gruppe hoechstens bekommt (``automatik.scroll_runden``).

    Vorgabe 15 (23.09.2026, Anweisung des Nutzers): In jeder Runde wird ein
    Stueck weiter gescrollt und das Sichtbare beurteilt; findet sich ein
    geeigneter Beitrag, wird kommentiert, sonst nach der letzten Runde zur
    naechsten Gruppe gegangen. Zwischen 1 und 30.
    """
    from fbgroups.automation.actions import SCROLL_RUNDEN

    wert = config.get("automatik", "scroll_runden", default=SCROLL_RUNDEN)
    try:
        return min(max(int(wert), 1), 30)
    except (TypeError, ValueError):
        return SCROLL_RUNDEN


def geeignet_fuer(
    config: AppConfig,
    group_id: str,
    *,
    erlaubnis,  # noqa: ANN001 - entscheidung.Erlaubnis
    anspruch=None,  # noqa: ANN001 - entscheidung.Anspruch
    verbrauchte_vorlagen=None,
    rueckfall: str = "",
) -> Callable[[dict], bool]:
    """Das Urteil, nach dem die Suche in einer Gruppe aufhoert - **dasselbe** wie beim Kommentieren.

    Ein Beitrag ist geeignet, wenn die bestehende Kette ihn nehmen wuerde:
    Inhalt und Relevanz (``beurteile_beitraege``, Schwelle aus ``anspruch``
    - nicht pauschal ``hoch``) und ein Text dafuer (``text_zur_gelegenheit``).
    Ein einzelnes Wort wie "سفر" oder "نقل" genuegt dafuer nicht; das
    entscheidet ``inhalt.lies`` und nicht eine Wortliste. Eine zweite Regel
    daneben koennte abweichen, und die Suche hielte dann bei einem Beitrag
    an, unter dem nie kommentiert wird.
    """
    verbraucht = set(verbrauchte_vorlagen or ())

    def pruefe(post: dict) -> bool:
        if not str(post.get("text", "")).strip():
            return False
        gelegenheit = beurteile_beitraege([post], erlaubnis, anspruch)[0]
        if not gelegenheit.taugt:
            return False
        text, _ = text_zur_gelegenheit(
            config, group_id, gelegenheit,
            rueckfall=rueckfall, bisherige=verbraucht, leise=True,
        )
        return text is not None

    return pruefe


def kommentare_zuerst(config: AppConfig) -> bool:
    """Kommt in einer Gruppe der Kommentar vor dem Beitrag?

    Vorgabe **falsch**, und das ist die Regel vom 10.09.2026: Der eigene
    Beitrag ist der Anlass und steht in der Gruppe; ein Kommentar haengt an
    einem fremden.

    Eingeschaltet (``automatik.kommentare_zuerst: true``) gilt die Anweisung
    des Nutzers vom 21.09.2026: *"Prioritaet Nr. 1 ist das Kommentieren, der
    Beitrag ist unwichtig."* Umgedreht wird allein die Reihenfolge - der
    Beitrag geht hinaus, sobald gerade kein Kommentar zustande kommt.
    """
    return bool(config.get("automatik", "kommentare_zuerst", default=False))


def ruhe_minuten(config: AppConfig) -> int:
    """Wie lange eine Gruppe ruht - aus ``settings.yaml``, sonst die Vorgabe."""
    wert = config.get("automatik", "ruhe_minuten", default=RUHE_MINUTEN)
    try:
        return max(int(wert), 1)
    except (TypeError, ValueError):
        return RUHE_MINUTEN


def ruhesekunden(wiederholen_ab: str, *, jetzt: datetime | None = None) -> float:
    """Bis zur Rueckkehr der naechsten Gruppe - gedeckelt wie jeder Schlaf.

    Dieselbe Obergrenze wie bei ``wartesekunden`` (eine Viertelstunde):
    Danach wird neu gefragt, statt einer Zahl zu vertrauen, die vor einer
    halben Stunde gerechnet wurde. Nach unten dreissig Sekunden - ein
    Zeitpunkt, der gerade verstrichen ist, soll keine Schleife ohne Pause
    ergeben.
    """
    jetzt = jetzt or datetime.now(UTC)
    try:
        ziel = datetime.fromisoformat(wiederholen_ab)
    except ValueError:
        return 60.0
    if ziel.tzinfo is None:
        ziel = ziel.replace(tzinfo=UTC)
    return float(min(max((ziel - jetzt).total_seconds(), 30.0), 15 * 60))


def wartesekunden(wartezeit: str) -> float:
    """Aus "noch 2 Min" werden 180 Sekunden - eine Minute Aufschlag.

    Der Text kommt aus ``beitritt.wartezeit`` und ist auf Minuten
    aufgerundet; genau zur vollen Minute zurueckzukommen hiesse, es noch
    einmal zu versuchen und wieder zu warten. Gedeckelt, damit ein Lauf nicht
    stundenlang vor einem Wert steht, den niemand vorhergesagt hat.
    """
    ziffern = "".join(z for z in wartezeit if z.isdigit())
    minuten = int(ziffern) if ziffern else 1
    return float(min(minuten + 1, 15) * 60)


def _ueberspringen(
    store: MarketingStore,
    lauf_id: int,
    schritt: lauf.Schritt,
    grund: str,
    *,
    ruhe: int = 0,
) -> None:
    """Eine Gruppe beiseitelegen - mit Grund und mit Ansage.

    ``ruhe`` in Minuten macht daraus eine **Ruhezeit**: Die Gruppe kommt von
    selbst zurueck. Ohne sie gilt der Uebersprung fuer den ganzen Lauf.
    """
    store.ueberspringe_gruppe(
        lauf_id, schritt.campaign_id, schritt.group_id, grund, ruhe_minuten=ruhe
    )
    console.print(
        f"[yellow]  {schritt.gruppe_name}: "
        + (f"ruht {ruhe} Min, dann wieder dran" if ruhe else "in diesem Lauf uebersprungen")
        + "[/yellow]"
    )


def _fuehre_schritt_aus(
    config: AppConfig,
    pfad: Path,
    lauf_id: int,
    schritt: lauf.Schritt,
    gruppen: dict,
    *,
    ausfuehren: Callable[[str, str, str, str], Schrittergebnis],
    beitreten: Callable[[str], tuple[str, str]] | None,
    trocken: bool,
    kaltmodus_aktiv: bool,
    abstand: int,
    technik: _Technikwaechter,
) -> bool:
    """Genau einen Schritt ausfuehren. Returns: ob der Lauf enden soll.

    Die beiden Arten stehen hier nebeneinander, weil sie denselben Rahmen
    teilen: lesen, handeln (ohne offene Datenbank), buchen. Was sie
    unterscheidet, ist allein die Handlung in der Mitte.
    """
    if schritt.art is lauf.Schrittart.BEITRITT:
        return _beitritt_schritt(pfad, lauf_id, schritt, gruppen, beitreten, trocken=trocken)
    return _text_schritt(
        config,
        pfad,
        lauf_id,
        schritt,
        gruppen,
        ausfuehren=ausfuehren,
        trocken=trocken,
        kaltmodus_aktiv=kaltmodus_aktiv,
        abstand=abstand,
        technik=technik,
    )


def _beitritt_schritt(
    pfad: Path,
    lauf_id: int,
    schritt: lauf.Schritt,
    gruppen: dict,
    beitreten: Callable[[str], tuple[str, str]] | None,
    *,
    trocken: bool,
) -> bool:
    """Schritt 2: eine Beitrittsanfrage an eine Gruppe dieser Kampagne.

    Die riskanteste Handlung des Projekts, deshalb die vorsichtigste
    Buchung: Ein **uebersprungener oder gescheiterter** Versuch hinterlaesst
    nichts - ``beitritt_angefragt`` zu setzen waere die Behauptung, es sei
    etwas abgeschickt worden. Damit dieselbe Gruppe trotzdem nicht bei jedem
    Durchgang wiederkommt, wird sie fuer diesen Lauf beiseitegelegt.
    """
    gruppe = gruppen.get(schritt.group_id)
    if beitreten is None or gruppe is None or not gruppe.url_canonical:
        with MarketingStore(pfad) as store:
            _ueberspringen(store, lauf_id, schritt, "keine Gruppen-URL")
        return False

    console.print(f"[bold]{schritt.gruppe_name}[/bold] - Beitrittsanfrage")
    if trocken:
        console.print("[dim]  --dry-run: es wird nichts abgeschickt[/dim]")
        return True

    ausgang, bemerkung = beitreten(gruppe.url_canonical)

    with MarketingStore(pfad) as store:
        if ausgang in ("angefragt", "bereits_mitglied"):
            store.merke_anfrage(schritt.group_id, mitglied=ausgang == "bereits_mitglied")
            console.print(
                "[green]  angefragt[/green]"
                if ausgang == "angefragt"
                else "[green]  bereits Mitglied[/green]"
            )
        else:
            # "Die Gruppe stellt Beitrittsfragen" und "es ging schief" sind
            # verschiedene Dinge, aber beide heissen: heute nicht hier.
            console.print(f"[yellow]  {ausgang}: {bemerkung or 'ohne Angabe'}[/yellow]")
            _ueberspringen(store, lauf_id, schritt, f"{ausgang}: {bemerkung}"[:160])
    return False


def _text_schritt(
    config: AppConfig,
    pfad: Path,
    lauf_id: int,
    schritt: lauf.Schritt,
    gruppen: dict,
    *,
    ausfuehren: Callable[[str, str, str, str], Schrittergebnis],
    trocken: bool,
    kaltmodus_aktiv: bool,
    abstand: int,
    technik: _Technikwaechter,
) -> bool:
    """Schritt 5: ein Beitrag oder ein Kommentar - in der besten Gruppe zuerst."""
    from fbgroups.marketing.beitrag import kommentar_adresse, mit_link

    with MarketingStore(pfad) as store:
        vorschlag = texte_sicherstellen(store, schritt, gruppen, config)
        if vorschlag is None:
            _ohne_text(store, schritt)
            return False

        campaign = store.load_campaign(schritt.campaign_id)
        link = store.link_for(schritt.campaign_id, schritt.group_id)
        # Der oeffentliche Deckname, falls er noch fehlt - der oertliche Lauf
        # geht denselben Weg wie der Fernbetrieb. Zwei Wege mit zwei Adressen
        # fuer dieselbe Gruppe waere genau der Unterschied, den niemand
        # bemerkt, bis er in zwei Beitraegen steht.
        if link is not None and not link.public_code:
            link = store.vergib_kurzcodes(schritt.campaign_id, schritt.group_id) or link
        if campaign is None or link is None:
            # Frueher endete hier der ganze Lauf. Eine verschwundene
            # Zuordnung ist aber ein Fall dieser einen Gruppe - die naechste
            # hat damit nichts zu tun.
            _ueberspringen(store, lauf_id, schritt, "Kampagne oder Zuordnung fehlt")
            return False

        ziel = lauf.ziel_zu_nummer(schritt.nummer)
        text = mit_link(
            campaign, link, vorschlag.text, config=config, ziel=ziel,
            texttyp=schritt.texttyp,
        )
        # Die **Adresse** getrennt vom Text - und seit dem 23.09.2026 nur noch
        # fuer den Beitrag. **Ein Kommentar traegt keinen Link** (Anweisung
        # des Nutzers); ``mit_link`` nimmt ihn samt Hinfuehrung heraus, und
        # eine Adresse, die hier mitreiste, koennte ihn nur zurueckbringen.
        link_url = (
            link.url_fuer(ziel)
            if schritt.texttyp is Texttyp.POST
            # Fuer den Kommentar die **freie** Adresse ohne Tracking
            # (``marketing.kommentar_adresse``, 23.09.2026).
            else kommentar_adresse(config)
        )
        # **Ohne Kurzcode geht die Buchhaltung hinaus.** ``url_fuer`` faellt
        # auf den inneren Code zurueck, und der nennt jedem Leser Kanal,
        # Zielgruppe, Stadt und laufende Nummer ("FB-SYR-BER-010-B"). Das ist
        # kein Fehlschlag - ein Beitrag ohne Link waere schlimmer -, aber es
        # ist genau die lange rohe Adresse, die im Beitrag nichts zu suchen
        # hat. Gesagt wird es hier, weil es sonst erst auffaellt, wenn der
        # Beitrag in der Gruppe steht; nachgetragen wird es mit
        # ``campaign kurzlinks``.
        ohne_kurzcode = bool(link_url) and link.oeffentlicher_code_fuer(
            ziel
        ) == link.code_fuer(ziel)
        wartezeit = _wartezeit(store, kaltmodus_aktiv, abstand)

    gruppe = gruppen.get(schritt.group_id)
    if gruppe is None or not gruppe.url_canonical:
        with MarketingStore(pfad) as store:
            store.setze_kommentar_erschoepft(
                schritt.campaign_id, schritt.group_id, "keine Gruppen-URL"
            )
        return False

    console.print(
        f"[bold]{schritt.gruppe_name}[/bold] - {_zweck(schritt)} "
        f"{schritt.kommentar_nr}/{schritt.kommentar_ziel} (Fassung {schritt.nummer})"
    )
    if schritt.runde:
        console.print(
            f"[dim]  Runde {schritt.runde} - Gruppe {schritt.runde_platz} "
            f"von {schritt.runde_gruppen}[/dim]"
        )

    if ohne_kurzcode:
        console.print(
            f"[yellow]  Kein Kurzcode - die lange Adresse geht hinaus: {link_url}[/yellow]"
        )
        console.print(
            f"[dim]  Nachtragen mit: fbgroups campaign kurzlinks {schritt.campaign_id}[/dim]"
        )

    if trocken:
        console.print("[dim]  --dry-run: nichts wird abgesetzt[/dim]")
        return True

    if wartezeit:
        console.print(f"[yellow]Abstandsregel: {wartezeit}[/yellow]")
        return True

    # 2. HANDELN: Der Browser ist dran, die Datenbank ist zu.
    ergebnis = ausfuehren(
        gruppe.url_canonical, schritt.group_id, text, schritt.texttyp.value, link_url
    )
    if ergebnis.bezuege:
        with MarketingStore(pfad) as store:
            for post_url, bezuege in ergebnis.bezuege:
                store.merke_bezuege(schritt.group_id, post_url, bezuege)
    if ergebnis.gescheiterte_posts or ergebnis.ausschliessen:
        with MarketingStore(pfad) as store:
            # Gescheiterte Beitraege nicht wieder anfassen (24.09.2026) -
            # dieselbe Stelle wie im Fernbetrieb (``POST /automatik/ergebnis``).
            store.merke_gescheiterte_beitraege(
                schritt.group_id, ergebnis.gescheiterte_posts
            )
            if ergebnis.ausschliessen:
                # Wirklich nichts zu machen - siehe ``nichts_zu_machen``.
                console.print(
                    f"[yellow]  Gruppe ausgeschlossen: {ergebnis.ausschliessen}[/yellow]"
                )
                store.schliesse_gruppe_aus(schritt.group_id, ergebnis.ausschliessen)

    # **NO_REPLY ist ein Ergebnis, kein Fehlversuch.** In dieser Gruppe stand
    # heute kein Beitrag, unter dem eine Antwort von uns etwas beigetragen
    # haette. Das als Fehlschlag zu buchen zaehlte gegen die Fassung und
    # irgendwann gegen die Gruppe - obwohl nichts gegen sie vorliegt. Sie
    # wird stattdessen fuer diesen Lauf beiseitegelegt; morgen stehen dort
    # andere Beitraege.
    # Ein ``erschoepft`` gilt wie "kein Anlass" (23.09.2026) - siehe das Feld.
    if ergebnis.kein_anlass or ergebnis.erschoepft:
        console.print(f"[dim]  kein Anlass: {ergebnis.fehler}[/dim]")
        with MarketingStore(pfad) as store:
            # **Auf Zeit, nicht fuer den ganzen Lauf** (20.09.2026). "Hier
            # steht gerade nichts Passendes" ist eine Aussage ueber diesen
            # Augenblick; in einer halben Stunde stehen dort andere
            # Beitraege. Als Uebersprung fuer den Lauf gebucht war eine
            # Kampagne mit zwoelf Gruppen nach zwoelf Schritten zu Ende.
            _ueberspringen(
                store,
                lauf_id,
                schritt,
                f"kein Anlass: {ergebnis.fehler}",
                ruhe=ruhe_minuten(config),
            )
        return False

    # 3. SCHREIBEN: Ausgang buchen - ueber denselben Weg wie die Arbeitsseite.
    with MarketingStore(pfad) as store:
        _buche(store, campaign, link, schritt, ergebnis)
        store.setze_lauf_status(lauf_id, LaufStatus.LAEUFT.value)

    # **Diese Gruppe gibt heute technisch nichts her.** Gebucht ist der
    # Ausgang (er gehoert ins Protokoll), aber die Gruppe wird fuer diesen
    # Lauf beiseitegelegt - sonst boete der naechste Durchgang dieselbe
    # Gruppe und dieselben Beitraege wieder an. Kein Urteil ueber sie: Das
    # steht ausdruecklich woanders (``kommentar_erschoepft``).
    if ergebnis.gruppe_beiseite:
        with MarketingStore(pfad) as store:
            # **Sie ruht, sie faellt nicht heraus** (21.09.2026). Bis dahin
            # bekam nur die tote Adresse eine Ruhezeit, der technische
            # Fehlschlag dagegen einen Schlussstrich fuer den ganzen Lauf -
            # und danach den Ausschluss aus der Kampagne. Beides widerspricht
            # der Anweisung "keine der zugewiesenen Gruppen darf dauerhaft
            # uebersprungen werden": Ein Fehlschlag kostet jetzt einen
            # Durchgang, nicht die Gruppe.
            _ueberspringen(
                store,
                lauf_id,
                schritt,
                f"technisch: {ergebnis.fehler}"[:160],
                ruhe=ruhe_minuten(config),
            )
            # **Ausgeschlossen wird nicht mehr** (21.09.2026, Anweisung des
            # Nutzers: "Keine der zugewiesenen Gruppen darf dauerhaft
            # uebersprungen werden"). Bis dahin nahm ein technischer
            # Fehlschlag die Gruppe ueber ``bearbeiten = 0`` aus der Kampagne
            # - dauerhaft und ohne dass jemand es anordnete. Der Uebersprung
            # oben bleibt: Er gilt fuer **diesen** Lauf, und beim naechsten
            # steht dieselbe Gruppe wieder in der Runde.
            #
            # Von Hand bleibt der Ausschluss erreichbar (Haken in der
            # Uebersicht, ``store.schliesse_gruppe_aus``) - dort faellt ihn
            # ein Mensch.

    # Technische Fehlschlaege in Folge ueber **verschiedene** Gruppen: Dann
    # liegt es nicht mehr an den Gruppen. Ein Sitzungsfehler haelt sofort an.
    if not ergebnis.beitrag_weg and technik.melde(ergebnis):
        console.print(f"[red]{technik.meldung()}[/red]")
        return True
    return False


def _wartezeit(store: MarketingStore, aktiv: bool, abstand: int) -> str:
    """Der Kaltmodus gilt auch hier - er ist der Takt, nicht eine Sperre der Hand."""
    if not aktiv:
        return ""
    jetzt = datetime.now(UTC)
    letzter = store.letzter_versuch()
    if not letzter:
        return ""
    frei_ab = kaltmodus.naechster_zeitpunkt(
        datetime.fromisoformat(letzter),
        abstand_minuten=abstand,
        jetzt=jetzt,
        erledigt_heute=store.versuche_heute(jetzt.date().isoformat()),
    )
    return kaltmodus.wartezeit_text(frei_ab, jetzt=jetzt)


def _erschoepfung_eintragen(store: MarketingStore, fortschritt: lauf.Lauffortschritt) -> bool:
    """Traegt fuer die naechste haengende Gruppe die Erschoepfung ein.

    Returns: ob etwas eingetragen wurde - dann lohnt ein weiterer Durchgang.
    Ohne diesen Schritt bliebe der Lauf an einer Gruppe stehen, die keine
    Fassung mehr offen hat, aber ihr Ziel nie erreicht.
    """
    kampagne = fortschritt.naechste_kampagne
    if kampagne is None:
        return False
    gruppe = kampagne.naechste_gruppe
    if gruppe is None or not lauf.gruppe_ist_erschoepft(gruppe):
        return False
    store.setze_kommentar_erschoepft(
        gruppe.campaign_id,
        gruppe.group_id,
        f"nur {gruppe.veroeffentlicht} von {gruppe.ziel} Kommentaren moeglich",
    )
    console.print(
        f"[yellow]{gruppe.name}: erschoepft "
        f"({gruppe.veroeffentlicht}/{gruppe.ziel})[/yellow]"
    )
    return True


def _zweck(schritt: lauf.Schritt) -> str:
    """Wie der Schritt in der Ausgabe heisst - "Beitrag" oder "Kommentar"."""
    return "Beitrag" if schritt.texttyp is Texttyp.POST else "Kommentar"


def _ohne_text(store: MarketingStore, schritt: lauf.Schritt) -> None:
    """Auch nach dem Nachziehen kein Text - und die Folge haengt am Zweck.

    Beim **Kommentar** ist die Gruppe damit am Ende: Es gibt nichts, was
    dort noch hingehen koennte. Beim **Beitrag** ist sie es ausdruecklich
    nicht - der Beitrag faellt aus, die zehn Kommentare bleiben. Beides in
    ``kommentar_erschoepft`` zu schreiben hiesse, wegen eines fehlenden
    Beitragstextes auf alle Kommentare zu verzichten.
    """
    if schritt.texttyp is Texttyp.POST:
        store.set_post_status(
            schritt.campaign_id,
            schritt.group_id,
            PostStatus.FEHLGESCHLAGEN,
            lauf.KEINE_VORLAGE,
        )
        return
    store.setze_kommentar_erschoepft(
        schritt.campaign_id, schritt.group_id, lauf.KEINE_VORLAGE
    )


def _buche(store: MarketingStore, campaign, link, schritt: lauf.Schritt, ergebnis) -> None:
    """Den Ausgang eintragen - ueber ``arbeit.melde_vorschlag``, wie sonst auch."""
    from fbgroups.marketing.arbeit import Ergebnis, melde_vorschlag

    # **Zuerst der Text, dann der Ausgang.** Was der Lauf abgesetzt hat, ist
    # seit dem 13.09.2026 nicht mehr zwingend der vorbereitete Text: Der
    # Anlasstext wird erst gewaehlt, wenn der Beitrag bekannt ist. Ihn danach
    # einzutragen waere zu spaet - ``melde_vorschlag`` spiegelt den Text bei
    # Erfolg ins Paar, und dort stuende sonst eine Fassung, die nie
    # hinausging.
    if ergebnis.text:
        store.merke_verwendeten_text(
            schritt.campaign_id,
            schritt.group_id,
            schritt.texttyp.value,
            schritt.nummer,
            ergebnis.text,
            ergebnis.vorlage_key,
        )

    melde_vorschlag(
        store,
        campaign,
        link,
        schritt.texttyp,
        schritt.nummer,
        Ergebnis(
            erfolg=ergebnis.erfolg,
            fehler="" if ergebnis.erfolg else (ergebnis.fehler or "ohne Angabe"),
            post_url=ergebnis.post_url,
        ),
        ausgeloest_von="automatik",
        sitzung="auto",
    )
    # Die Bremse der Gegenseite pausiert **ihre** Aktion - und nur sie.
    aktion = grenzen.aus_texttyp(schritt.texttyp.value)
    if ergebnis.erfolg:
        console.print("[green]  veroeffentlicht[/green]")
        # Ein Erfolg beendet die Sperre und setzt die Stufe zurueck: Der
        # Backoff soll die naechste Bremsung messen, nicht die letzte Woche.
        store.loesche_sperre(aktion.value)
    else:
        console.print(f"[red]  fehlgeschlagen: {ergebnis.fehler}[/red]")
        art = klassifiziere(ergebnis.fehler)
        if art is Ausgangsart.GRUPPENLIMIT:
            # Nur **diese** Gruppe nimmt nichts mehr an. Die Aktion laeuft
            # weiter; in der naechsten Gruppe geht es sofort los.
            console.print(
                "[yellow]  Diese Gruppe nimmt gerade nichts mehr an - "
                "andere Gruppen laufen weiter.[/yellow]"
            )
        elif art is Ausgangsart.RATE_LIMIT:
            stufe = merke_bremse(store, aktion.value)
            console.print(
                f"[yellow]  Die Gegenseite bremst: {aktion.value} pausiert "
                f"{grenzen.backoff_minuten(stufe)} Min. Andere Aktionen laufen "
                f"weiter.[/yellow]"
            )


def _stand_fortschreiben(
    store: MarketingStore, lauf_id: int, fortschritt: lauf.Lauffortschritt
) -> None:
    """Schreibt die abgeleiteten Zustaende zurueck - Kampagnen und Lauf.

    Hier steht Punkt 8: ``fertig`` wird **nicht** gesetzt, weil eine Gruppe
    durch ist, sondern weil ``Lauffortschritt.fertig`` es sagt - und das
    verlangt jede Gruppe jeder Kampagne. Die Bedingung steht an genau einer
    Stelle (``lauf.py``), damit sie nicht an zweien auseinanderlaufen kann.

    Seit dem 12.09.2026 wird zwischen "gerade geht hier nichts" und "hier ist
    nichts mehr zu tun" unterschieden (``Kampagnenfortschritt.lauf_status``).
    Der Unterschied stand am selben Tag im Bild: vier Kampagnen auf "fertig",
    78 Gruppen durch, null Kommentare veroeffentlicht.
    """
    gebremst = not any(
        fortschritt.lage(aktion).moeglich
        for aktion in (grenzen.Aktion.POST, grenzen.Aktion.KOMMENTAR)
    )
    for kampagne in fortschritt.kampagnen:
        neu = (
            kampagne.lauf_status(
                beitritt_frei=fortschritt.beitritt_frei, gebremst=gebremst
            )
            if kampagne is fortschritt.naechste_kampagne or kampagne.fertig
            else KampagnenLaufStatus.WARTET
        )
        if neu is not kampagne.status:
            store.setze_lauf_kampagne_status(lauf_id, kampagne.campaign_id, neu.value)

        # **Dauerhaft abgeschlossen wird nur, was sein Ziel erreicht hat.**
        # ``fertig`` zaehlt eine erschoepfte Gruppe mit; auf ``completed``
        # gesetzt verschwaende das die Kampagne fuer jeden kuenftigen Lauf,
        # obwohl in denselben Gruppen morgen neue Beitraege stehen. Wer sie
        # trotzdem beenden will, tut das von Hand - das ist eine Entscheidung
        # und keine Ableitung.
        if kampagne.abgeschlossen:
            gespeichert = store.load_campaign(kampagne.campaign_id)
            if gespeichert is not None and gespeichert.status is CampaignStatus.ACTIVE:
                gespeichert.status = CampaignStatus.COMPLETED
                store.save_campaign(gespeichert)

    # Ein Lauf **ohne jede Kampagne** gilt als beendet, ohne als erfolgreich
    # zu gelten. Er kann seit dem 10.09.2026 nicht mehr entstehen, aber die
    # vorhandenen muessen geschlossen werden koennen: Sonst holt
    # ``offener_lauf`` bei jedem Start denselben leeren Lauf zurueck, und die
    # Automatik meldet "0 / 0", ohne je etwas zu tun.
    erledigt = fortschritt.fertig or not fortschritt.kampagnen
    store.setze_lauf_status(
        lauf_id,
        LaufStatus.FERTIG.value if erledigt else LaufStatus.ANGEHALTEN.value,
        meldung=lauf.abschlusstext(fortschritt),
    )


def browser_schritt(
    context,
    config: AppConfig,
    gruppen_url: str,
    group_id: str,
    text: str,
    link_url: str = "",
) -> Schrittergebnis:
    """Ein Kommentar im Browser: Beitraege lesen, den besten waehlen, kommentieren.

    Gelesen werden nur Kennzahlen (Rueckmeldungen, Kommentarzahl) und die
    Beitrags-URL - nie ein Beitragstext, nie ein Name. Die Auswahl faellt auf
    den lebendigsten Beitrag, der noch keinen Kommentar von uns traegt;
    ``bisherige_post_urls`` haelt fest, welche das sind.

    ``limit=10`` statt der fuenf aus ``campaign auto``: Fuenf Kommentare
    brauchen fuenf **verschiedene** Beitraege, und schon der zweite Durchgang
    faende sonst nichts Neues mehr.
    """
    from fbgroups.automation.actions import comment_on_post, fetch_top_posts

    # Bis zu ``scroll_runden`` Runden, bis ein geeigneter Beitrag in Sicht
    # ist (23.09.2026) - beurteilt mit derselben Kette, die gleich
    # kommentiert. Schon kommentierte Beitraege zaehlen dabei nicht mit.
    with MarketingStore(config.path("sqlite_path")) as store:
        # Kommentiert **oder** gescheitert (24.09.2026): Ein Beitrag, dessen
        # Feld gerade nicht beschreibbar war, kommt nicht Runde fuer Runde
        # wieder - die anderen Beitraege der Gruppe sind dran.
        bisherige = store.gesperrte_post_urls(group_id)
        verbrauchte = store.verwendete_vorlagen(group_id, Texttyp.KOMMENTAR.value)
    bericht: dict = {}
    roh = fetch_top_posts(
        context,
        gruppen_url,
        group_id,
        limit=10,
        runden=scroll_runden(config),
        bekannt=bisherige,
        geeignet=geeignet_fuer(
            config,
            group_id,
            erlaubnis=entscheidung_modul.Erlaubnis(),
            anspruch=anspruch_aus_config(config),
            verbrauchte_vorlagen=verbrauchte,
            rueckfall=text,
        ),
        mindestens=MINDEST_BEITRAEGE,
        bericht=bericht,
    )
    if not roh:
        # Die Seite hat gerade nichts Neues hergegeben - die Gruppe ruht,
        # die Runde geht weiter; nach einer vollen Suche siehe
        # ``nichts_zu_machen``.
        ergebnis = Schrittergebnis(
            erfolg=False, fehler="keine Beitraege zum Kommentieren gefunden", kein_anlass=True
        )
    else:
        # **Die rohen Funde gehen weiter, nicht die gespeicherten.** Nur sie
        # tragen den Text, und der wird fuer die Auswahl gebraucht -
        # ``GroupPost`` hat dafuer kein Feld und soll auch keines bekommen.
        ergebnis = waehle_und_kommentiere(
            context, config, roh, group_id, text,
            kommentieren=_mit_bild(comment_on_post, config), link_url=link_url,
        )
    return replace(ergebnis, ausschliessen=nichts_zu_machen(bericht, roh, ergebnis))


def _mit_bild(kommentieren, config: AppConfig):  # noqa: ANN001, ANN202
    """``comment_on_post`` mit dem Kommentarbild aus ``marketing.kommentar_bild``.

    Die Auswahlkette ruft ``kommentieren(context, post_url, text)`` - drei
    Angaben, damit sie im Test durch eine Zaehlfunktion ersetzbar bleibt.
    Das Bild kommt deshalb hier dazu und nicht dort.
    """
    from functools import partial

    from fbgroups.marketing.beitrag import kommentar_bild

    bild = kommentar_bild(config)
    return partial(kommentieren, bild=bild) if bild else kommentieren


def nichts_zu_machen(bericht: dict, roh: list[dict], ergebnis: Schrittergebnis) -> str:
    """Ist in dieser Gruppe **wirklich** nichts zu machen? Returns: der Grund oder ``""``.

    Anweisung des Nutzers vom 24.09.2026: bis 15 Mal herunterscrollen,
    mindestens 10 Beitraege ansehen - und erst wenn dann wirklich nichts zu
    machen ist, die Gruppe ausschliessen. Wirklich nichts heisst hier alles
    zugleich:

    * die Suche lief **alle** Runden (``fetch_top_posts`` hoert vorher nur
      auf, wenn ein geeigneter Beitrag gefunden ist),
    * es wurde ueberhaupt etwas gesehen - eine Seite, die nichts anzeigt,
      ist ein Befund ueber die Seite, nicht ueber die Gruppe,
    * kein Beitrag bestand das Urteil (Inhalt, Relevanz, Vorlage),
    * es wurde etwas **gelesen** - ohne einen einzigen Text wissen wir
      nichts ueber die Beitraege,
    * und der Schritt endete mit "kein Anlass", nicht mit einem Erfolg,
      einem technischen Fehlschlag oder einem Sitzungsfehler.

    Beitraege, deren Feld nicht beschreibbar war, fuehren nicht sofort
    hierher: Sie werden gesperrt (``gescheiterte_beitraege``), und erst wenn
    danach nichts Geeignetes mehr uebrig ist, greift diese Regel.
    """
    if ergebnis.erfolg or not ergebnis.kein_anlass or ergebnis.gruppe_beiseite:
        return ""
    gesehen = int(bericht.get("gesehen", 0))
    runden = int(bericht.get("runden", 0))
    if not gesehen or bericht.get("geeignet") or runden < int(bericht.get("runden_max", 1)):
        return ""
    if roh and not any(str(p.get("text", "")).strip() for p in roh):
        return ""
    return (
        f"automatisch: {runden} Scroll-Runden, {gesehen} Beitraege angesehen - "
        f"nichts Kommentierbares ({ergebnis.fehler})"
    )[:160]


@dataclass(frozen=True)
class Gelegenheit:
    """Ein Beitrag, sein Urteil und die Entscheidung dazu.

    Das Zwischenergebnis zwischen "hier sind zehn Beitraege" und "unter
    diesen einen schreiben wir". Es traegt den **Befund**, nie den Text: Was
    von hier aus weitergegeben oder gespeichert wird, ist ein Schlagwort und
    ein Urteil.
    """

    post_url: str
    interactions: int = 0
    comments: int = 0
    befund: inhalt.Inhaltsbefund = field(default_factory=inhalt.Inhaltsbefund)
    entscheidung: entscheidung_modul.Entscheidung = field(
        default_factory=entscheidung_modul.Entscheidung
    )

    @property
    def taugt(self) -> bool:
        return self.entscheidung.antwortet

    @property
    def rang(self) -> tuple:
        """Wonach der beste Beitrag gewaehlt wird - Relevanz **vor** Betrieb.

        Frueher entschied allein ``interactions + comments``: der lauteste
        Beitrag, nicht der passendste. Ein Kommentar ueber Paketmitnahme
        unter einem Wohnungsgesuch ist Spam, gleich wie gut er formuliert
        ist - und er steht ausgerechnet dort, wo ihn die meisten sehen.

        Die Betriebsamkeit bleibt das zweite Kriterium: Unter zwei gleich
        passenden Beitraegen ist der belebtere der bessere Platz.
        """
        naehe = {
            entscheidung_modul.Antwortart.DIRECT_APP_RECOMMENDATION: 3,
            entscheidung_modul.Antwortart.CONTEXTUAL_APP_MENTION: 3,
            entscheidung_modul.Antwortart.PRIVATE_CONTACT_SUGGESTION: 2,
            entscheidung_modul.Antwortart.HELPFUL_REPLY: 1,
            entscheidung_modul.Antwortart.NO_REPLY: 0,
        }
        return (
            naehe.get(self.entscheidung.art, 0),
            self.interactions + self.comments,
        )


def beurteile_beitraege(
    roh: list[dict],
    erlaubnis,
    anspruch=None,  # noqa: ANN001 - entscheidung.Anspruch
) -> list[Gelegenheit]:  # noqa: ANN001
    """Aus den rohen Funden Gelegenheiten machen - mit Urteil und Entscheidung.

    Die Stelle, an der der durchgereichte Text endet: Was zurueckkommt,
    traegt ``Inhaltsbefund`` und ``Entscheidung``, nicht den Satz.

    ``anspruch`` sagt, wie viel ein Beitrag **an dieser Stelle** hergeben
    muss - in einer Reisegruppe weniger als in einer allgemeinen. Fehlt er,
    gilt die bisherige Regel (jeder Bezug genuegt).
    """
    gelegenheiten = []
    for p in roh:
        befund = inhalt.lies(p.get("text", ""))
        gelegenheiten.append(
            Gelegenheit(
                post_url=p["post_url"],
                interactions=int(p.get("interactions", 0) or 0),
                comments=int(p.get("comments", 0) or 0),
                befund=befund,
                entscheidung=entscheidung_modul.entscheide(befund, erlaubnis, anspruch),
            )
        )
    return gelegenheiten


def mindestrelevanz(config: AppConfig) -> inhalt.Relevanz:
    """Welche Relevanz ein Beitrag mindestens haben muss - ``marketing.mindestrelevanz``.

    **Eine Schwelle fuer alle Gruppen** (23.09.2026). Bis dahin hing sie an
    der Zielklasse der Gruppe (``A``-``D``) oder an ihrer gepflegten Note;
    beide sind als Entscheidungsgrundlage entfallen. Wie die Bezuege einer
    Gruppe die Schwelle kuenftig bestimmen, ist noch nicht festgelegt - bis
    dahin gilt ``mittel``, der Wert, den am 21.09.2026 ohnehin jede Klasse
    trug.

    "niedrig" heisst ``mittel``: Darunter liegt nur ``Relevanz.KEINE``, also
    "kein Zusammenhang mit unserem Angebot", und darauf wird nicht
    geantwortet. Ein unbekanntes Wort ergibt die Vorgabe und keine geratene
    Stufe.
    """
    wort = str(config.get("marketing", "mindestrelevanz", default="mittel")).strip().lower()
    if wort == "hoch":
        return inhalt.Relevanz.HOCH
    return inhalt.Relevanz.MITTEL


def anspruch_aus_config(config: AppConfig):  # noqa: ANN201 - entscheidung.Anspruch
    """Was ein Beitrag hergeben muss - fuer **jede** Gruppe gleich (23.09.2026).

    Vorher ``anspruch_fuer`` mit der Gruppe: die Schwelle je Zielklasse
    oder Note. Die ausgeschriebene Strecke verlangt keine Gruppe mehr; das
    Feld ``verlangt_strecke`` bleibt in ``entscheidung.Anspruch``, weil der
    Fernbetrieb es in ``vorgaben`` liest und ein aelterer Arbeitsrechner es
    erwartet.
    """
    return entscheidung_modul.Anspruch(
        mindestrelevanz=mindestrelevanz(config),
        verlangt_strecke=False,
        anlass_pflicht=anlass_pflicht(config),
    )


def waehle_gelegenheit(
    gelegenheiten: list[Gelegenheit], bisherige: set[str]
) -> Gelegenheit | None:
    """Der beste Beitrag, unter dem noch nichts von uns steht - oder ``None``.

    ``None`` heisst **kein Anlass** und ist ein Ergebnis, kein Fehlschlag:
    Heute steht in dieser Gruppe nichts, worauf eine Antwort von uns etwas
    beitruege. Morgen stehen dort andere Beitraege.
    """
    offen = [g for g in gelegenheiten if g.post_url not in bisherige and g.taugt]
    return max(offen, key=lambda g: g.rang) if offen else None


def waehle_und_kommentiere(
    context,
    config: AppConfig,
    posts,
    group_id: str,
    text: str,
    *,
    kommentieren,
    erlaubnis=None,  # noqa: ANN001 - entscheidung.Erlaubnis
    link_url: str = "",
) -> Schrittergebnis:
    """Den passendsten noch unkommentierten Beitrag nehmen - oder keinen.

    Eigene Funktion, weil hier die Auswahlregel steht - sie laesst sich damit
    ohne Browser pruefen, indem ``kommentieren`` durch eine Zaehlfunktion
    ersetzt wird.

    ``posts`` sind ``GroupPost``-Objekte (aus dem Bestand, ohne Text) oder
    rohe Funde mit ``text``. Fehlt der Text, faellt die Auswahl auf die
    Kennzahlen zurueck: Ein Beitrag ohne lesbaren Text ist kein Grund, die
    Gruppe auszulassen - es ist ein Grund, nichts ueber ihn zu behaupten.
    """
    pfad = config.path("sqlite_path")
    roh = [p if isinstance(p, dict) else _als_roh(p) for p in posts]

    with SqliteStore(pfad) as gruppen_store:
        gruppen_store.upsert_group_posts(group_id, _als_group_posts(roh, group_id))
    with MarketingStore(pfad) as store:
        bisherige = store.gesperrte_post_urls(group_id)
        verbrauchte_vorlagen = store.verwendete_vorlagen(
            group_id, Texttyp.KOMMENTAR.value
        )
        if erlaubnis is None:
            erlaubnis = entscheidung_modul.Erlaubnis()

    return entscheide_und_kommentiere(
        context,
        config,
        roh,
        group_id,
        text,
        kommentieren=kommentieren,
        bisherige=bisherige,
        erlaubnis=erlaubnis,
        anspruch=anspruch_aus_config(config),
        verbrauchte_vorlagen=verbrauchte_vorlagen,
        link_url=link_url,
    )


def bezuege_der_beitraege(roh: list[dict]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Die Bezuege aller gelesenen Beitraege - der Text geht hinein, nicht hinaus.

    Beitraege ohne Text fehlen: Ohne Text ist nichts gelesen, und eine leere
    Zeile im Bestand hiesse "gelesen, nichts gefunden".
    """
    from fbgroups.marketing import bezug

    return tuple(
        (p["post_url"], tuple(b.value for b in bezug.erkenne(p["text"]).sortiert))
        for p in roh
        if p.get("post_url") and (p.get("text") or "").strip()
    )


def entscheide_und_kommentiere(
    context,
    config: AppConfig,
    roh: list[dict],
    group_id: str,
    text: str,
    **kwargs,
) -> Schrittergebnis:
    """Lesen, beurteilen, entscheiden, kommentieren - und die Bezuege mitgeben.

    Der Kern steht in ``_entscheide_und_kommentiere``. Die Bezuege werden
    hier an **jeden** Ausgang gehaengt, statt an jeder der Rueckgabestellen
    darin: Gelesen wurden die Beitraege in jedem Fall, auch wenn am Ende
    nichts geschrieben wurde - und eine Stelle, die es vergaesse, liesse
    Gruppen ohne Bezug erscheinen, die welche haben.
    """
    gescheitert: set[str] = set()
    ergebnis = _entscheide_und_kommentiere(
        context, config, roh, group_id, text, gescheitert=gescheitert, **kwargs
    )
    return replace(
        ergebnis,
        bezuege=bezuege_der_beitraege(roh),
        gescheiterte_posts=tuple(sorted(gescheitert)),
    )


def _entscheide_und_kommentiere(
    context,
    config: AppConfig,
    roh: list[dict],
    group_id: str,
    text: str,
    *,
    kommentieren,
    bisherige,
    erlaubnis,  # noqa: ANN001 - entscheidung.Erlaubnis
    anspruch=None,  # noqa: ANN001 - entscheidung.Anspruch
    verbrauchte_vorlagen=None,
    link_url: str = "",
    gescheitert: set[str] | None = None,
) -> Schrittergebnis:
    """Lesen, beurteilen, entscheiden, **dann erst** kommentieren.

    **Die eine Stelle, an der aus zehn gelesenen Beitraegen ein Kommentar
    wird** - und sie kennt keine Datenbank. Das ist der Punkt (14.09.2026):
    Bis dahin stand diese Kette nur im oertlichen Lauf. Der Fernbetrieb
    (``campaign automatik --server``) nahm stattdessen den **lautesten**
    Beitrag und setzte den vorbereiteten Text darunter - ohne Inhaltspruefung
    und ohne Anspruch. Wer den Fernbetrieb faehrt -
    und das ist der Regelfall -, hatte damit einen Runner, der genau die
    Pruefungen ausliess, die ``campaign pruefe-inhalt`` vorfuehrt.

    Alles, was sonst aus dem Bestand kaeme, wird hereingereicht:

    * ``bisherige`` - unter welchen Beitraegen schon etwas von uns steht.
    * ``erlaubnis`` - was die Gruppe zulaesst (seit dem 23.09.2026 fuer jede
      Gruppe dasselbe: ``entscheidung.Erlaubnis()``).
    * ``anspruch`` - wie viel ein Beitrag **an dieser Stelle** hergeben muss
      (``automatik.anspruch_aus_config``, fuer jede Gruppe gleich).
    * ``verbrauchte_vorlagen`` - damit derselbe Satz nicht zweimal in
      derselben Gruppe steht.
    * ``link_url`` - die **fertige** Adresse dieser Gruppe. Der Anlasstext
      traegt ``{link}`` als Platzhalter; aufgeloest wird er hier, unmittelbar
      bevor er hinausgeht (``beitrag.setze_adresse``).

    Die reine Regel laeuft auf dem Rechner, der den Browser hat; der Bestand liegt dort, wo gezaehlt
    wird. Zwei Auswertungen koennten auseinanderlaufen - eine kann es nicht.
    """
    bisherige = set(bisherige or ())
    unkommentiert = [p for p in roh if p["post_url"] not in bisherige]
    if not unkommentiert:
        # **Ein Befund ueber diesen Augenblick, nicht ueber die Gruppe**
        # (23.09.2026). Facebook zeigt oft nur einen einzigen Beitrag an
        # ("Found 1 post(s) ... articles last seen: 3"), und unter dem steht
        # schon unser Kommentar. Bis dahin hiess das ``erschoepft``: Der
        # Server vermerkte die Gruppe als erschoepft, legte sie aber nicht
        # beiseite - und weil ihr Beitrag noch offen war, bot er sie sofort
        # wieder an. Im Betrieb stand dieselbe Gruppe so gut fuenfzigmal
        # hintereinander, waehrend die hinteren nie drankamen. Jetzt gilt es
        # wie "kein Anlass": Die Gruppe ruht, die Runde geht weiter.
        return Schrittergebnis(
            erfolg=False,
            fehler="alle sichtbaren Beitraege sind bereits kommentiert",
            kein_anlass=True,
        )

    # Ohne einen einzigen lesbaren Text ist keine Entscheidung moeglich -
    # dann gilt die alte Regel (der belebteste Beitrag), statt gar nichts zu
    # tun. Das ist die ehrlichere Stelle fuer den Rueckfall: Wir wissen
    # nichts ueber die Beitraege, nicht "sie passen nicht".
    if gescheitert is None:
        gescheitert = set()
    if not any(p.get("text", "").strip() for p in unkommentiert):
        return _ohne_urteil_kommentieren(
            context, unkommentiert, text, kommentieren=kommentieren, link_url=link_url,
            gescheitert=gescheitert,
        )

    # **Erst das Urteil, dann die Wahl.** Jeder gelesene Beitrag bekommt
    # seinen Befund (Thema, Absicht, Bezug, Anlass) und seine Entscheidung
    # (welche Form einer Antwort passt, mit oder ohne Link) - und zwar
    # bevor irgendetwas geschrieben wird.
    gelegenheiten = beurteile_beitraege(unkommentiert, erlaubnis, anspruch)
    from fbgroups.marketing.beitrag import (
        mit_kommentaradresse,
        offene_platzhalter,
        ohne_link,
    )
    from fbgroups.urls import adresse_im_text

    verbraucht = set(verbrauchte_vorlagen or ())
    letzter: Schrittergebnis | None = None

    # **Ein Beitrag, der technisch nicht annimmt, kostet den Schritt nicht.**
    #
    # Bis zum 15.09.2026 wurde genau ein Beitrag gewaehlt, und ein
    # Fehlschlag beendete den Schritt. Der naechste Durchgang waehlte
    # **denselben** Beitrag: ``bisherige_post_urls`` traegt nur, worunter
    # wirklich etwas steht, und die Rangfolge ist deterministisch. Im
    # Betrieb sah das so aus - viermal hintereinander derselbe Beitrag,
    # dazwischen eine Viertelstunde Schlaf:
    #
    #     Could not find the comment box. (Textfelder: 0)
    #     fehlgeschlagen: Kommentarfeld nicht gefunden
    #     Takt: noch 58 Min
    #
    # Jetzt geht der Schritt zum naechsten Beitrag weiter - genau das, was
    # ein Mensch taete. Nur bei **technischen** Ausgaengen: Eine Ablehnung
    # der Gruppe ist eine Aussage, die fuer den naechsten Beitrag genauso
    # gilt, und sie noch dreimal zu wiederholen hiesse, gegen die Gruppe zu
    # arbeiten.
    # **Ein ungeeigneter Beitrag beendet die Suche nicht** (23.09.2026). Fand
    # sich fuer den besten Beitrag kein Text, endete der Schritt bis dahin
    # sofort - auch wenn der naechste gepasst haette. Jetzt geht es zum
    # naechsten; gezaehlt wird nur, was wirklich versucht wurde.
    ohne_text: set[str] = set()
    ohne_text_grund = ""
    versuch = 0
    while versuch < MAX_BEITRAEGE_JE_SCHRITT:
        gewaehlt = waehle_gelegenheit(gelegenheiten, gescheitert | ohne_text)
        if gewaehlt is None:
            break

        gewaehlter_text, schluessel = text_zur_gelegenheit(
            config, group_id, gewaehlt, rueckfall=text, bisherige=verbraucht
        )
        if gewaehlter_text is None:
            # **Kein Anlass, kein Text, kein Kommentar.** "Ich fliege
            # naechste Woche nach Syrien" ist noch kein Grund fuer einen
            # Kommentar. Kein Fehlschlag - gegen die Gruppe spricht nichts.
            # **Der Grund steht im Linkmodus, nicht im Anlass.** "kein
            # vorbereiteter Text fuer geschenk" liest sich wie eine Luecke in
            # ``textvorlagen.yaml`` - in Wahrheit war meist die Erlaubnis das
            # Hindernis: Bei ``NO_LINK`` (bloss hilfreiche Antwort, privater
            # Hinweis) gibt es **absichtlich** keinen Vorrat, und ein Text
            # dafuer waere erfunden. Wer nur den Anlass liest, sucht die
            # Ursache an der falschen Stelle - und genau das ist am
            # 21.09.2026 eine Runde nach der anderen passiert.
            modus = entscheidung_modul.LINKMODUS.get(gewaehlt.entscheidung.art)
            grund = (
                f"kein vorbereiteter Text fuer {gewaehlt.befund.anlass.value}"
                if modus is not entscheidung_modul.Linkmodus.NO_LINK
                else (
                    f"{gewaehlt.entscheidung.art.value} nennt die App nicht - "
                    f"dafuer gibt es keinen Textvorrat "
                    f"({gewaehlt.entscheidung.grund})"
                )
            )
            ohne_text.add(gewaehlt.post_url)
            ohne_text_grund = grund
            continue

        versuch += 1
        console.print(
            f"[dim]  [Versuch {versuch}/{MAX_BEITRAEGE_JE_SCHRITT}] "
            f"{gewaehlt.entscheidung.art.value}: {gewaehlt.entscheidung.grund}[/dim]"
        )

        # **Ein Kommentar traegt keinen Tracking-Link** (23.09.2026,
        # Anweisung des Nutzers). ``{link}`` faellt samt Hinfuehrung weg
        # (``beitrag.ohne_link``), und ans Ende kommt die **freie** Adresse,
        # die der Server als ``link_url`` mitschickt
        # (``marketing.kommentar_adresse``, ``https://b-tarikak.de/home``).
        # Eine Tracking-Adresse wird dabei nie angehaengt - auch nicht, wenn
        # ein aelterer Server sie noch schickt.
        hinausgehend = mit_kommentaradresse(ohne_link(gewaehlter_text), link_url)
        if adresse := adresse_im_text(hinausgehend, erlaubt=(link_url,)):
            # Die letzte Pruefung vor dem Browser: Steht eine andere Adresse
            # im Text (von Hand eingetragen, alter Server), geht der
            # Kommentar nicht hinaus. Ein Fehler bei uns, kein Urteil ueber
            # die Gruppe.
            return Schrittergebnis(
                erfolg=False,
                fehler=f"Adresse im Kommentar ({adresse}) - nicht abgesetzt",
                kein_anlass=True,
            )
        if offen := offene_platzhalter(hinausgehend):
            # **Lieber kein Kommentar als ein kaputter.** Ein Text mit
            # ``{link}`` sieht richtig aus, und seine Gruppe bekommt nie
            # einen Klick gutgeschrieben - zurueckholen laesst er sich
            # nicht. Ein Fehler bei uns, kein Urteil ueber die Gruppe.
            return Schrittergebnis(
                erfolg=False,
                fehler=f"Platzhalter nicht aufgeloest: {', '.join(sorted(set(offen)))}",
                kein_anlass=True,
            )

        ergebnis = _ausgang(
            kommentieren(context, gewaehlt.post_url, hinausgehend), gewaehlt.post_url
        )
        # Gespeichert wird, was wirklich hinausging - ohne Link, wie jeder
        # Kommentar seit dem 23.09.2026.
        letzter = replace(ergebnis, text=hinausgehend, vorlage_key=schluessel)

        if letzter.beitrag_weg:
            # **Diese Adresse zeigt ins Leere.** Weitergehen zum naechsten
            # Beitrag - wie bei einem technischen Ausgang, aber ohne dessen
            # Folgen: Er zaehlt nicht gegen den Technikwaechter und macht aus
            # der Gruppe kein Urteil. Im Betrieb war genau das der Fall, der
            # den Lauf Dutzende Male dieselbe geloeschte Adresse ansteuern
            # liess - und am Ende die Gruppe dafuer bezahlen.
            console.print(
                "[dim]  [Ergebnis] Beitrag nicht mehr vorhanden"
                "[/dim] [dim][Aktion] naechster Beitrag[/dim]"
            )
            gescheitert.add(gewaehlt.post_url)
            continue

        if letzter.erfolg or letzter.gruppe_beiseite:
            return letzter

        if ist_sitzungsfehler(letzter.fehler):
            # Browser weg oder abgemeldet: Das liegt an keinem Beitrag, und
            # der naechste scheiterte genauso. Gesperrt wird deshalb keiner.
            return letzter

        if not ist_technisch(letzter.fehler):
            # **Eine Ablehnung gilt der Gruppe, nicht dieser Fassung**
            # (20.09.2026, Regel 5 des Nutzers). Sagt Facebook hier nein,
            # sagt es das beim naechsten Beitrag und bei der naechsten
            # Fassung genauso - die Gruppe wird deshalb fuer **diesen Lauf**
            # beiseitegelegt, und der Lauf geht sofort zur naechsten weiter.
            #
            # Vorher kam sie gleich wieder an die Reihe, nur mit einer
            # anderen Fassung: In einer Gruppe, die gerade nichts annimmt,
            # verbrauchte der Lauf so eine Fassung nach der anderen, bis die
            # Gruppe als erschoepft galt - ein dauerhaftes Urteil aus einer
            # Stunde. Beiseitegelegt ist kein Urteil: Morgen wird sie neu
            # beurteilt.
            return replace(letzter, gruppe_beiseite=True)

        console.print(
            f"[yellow]  [Ergebnis] technisch fehlgeschlagen: {letzter.fehler}"
            f"[/yellow] [dim][Aktion] naechster Beitrag[/dim]"
        )
        gescheitert.add(gewaehlt.post_url)

    if letzter is not None:
        return _abschluss(letzter, len(gescheitert))
    if ohne_text_grund:
        return Schrittergebnis(erfolg=False, fehler=ohne_text_grund, kein_anlass=True)

    gruende = ", ".join(sorted({g.entscheidung.grund for g in gelegenheiten})[:2])
    return Schrittergebnis(
        erfolg=False,
        fehler=f"kein passender Beitrag ({gruende})",
        kein_anlass=True,
    )


def _ohne_urteil_kommentieren(
    context,
    unkommentiert: list[dict],
    text: str,
    *,
    kommentieren,
    link_url: str = "",
    gescheitert: set[str] | None = None,
) -> Schrittergebnis:
    """Der Rueckfall, wenn **kein** Beitrag lesbaren Text hat - der Reihe nach.

    Ohne einen einzigen Text ist keine Entscheidung moeglich; dann gilt die
    alte Regel (der belebteste Beitrag zuerst), statt gar nichts zu tun. Wir
    wissen dann nichts ueber die Beitraege - das ist etwas anderes als "sie
    passen nicht".

    **Neu ist nur, dass es hier nicht bei einem Beitrag bleibt** (20.09.2026).
    Bis dahin stand hier eine einzige Zeile: der lauteste Beitrag, ein
    Versuch, fertig. Die Rangfolge ist deterministisch, und ein Fehlschlag
    aendert nichts an ihr - also steuerte der naechste Durchgang **dieselbe**
    Adresse an. Im Betrieb war genau das der Lauf, der eine geloeschte
    Adresse Dutzende Male aufrief:

        Found 3 post(s) in 5 round(s)
        Navigating to post .../959155973211617/
        Diesen Beitrag gibt es nicht mehr: هذا المحتوى غير متوفر حاليًا
        fehlgeschlagen: ...                      (und wieder von vorn)

    Der Weg mit gelesenen Texten ging diesen Schritt seit dem 15.09.2026
    weiter; dieser hier nicht - und weil die Gruppenseite ihre Artikel oft
    gar nicht hergibt (``urls.beitragslinks`` findet dann Adressen ohne
    Text), ist er im Betrieb keineswegs der Sonderfall. Jetzt gelten hier
    dieselben drei Regeln wie dort: tote Adresse -> naechster Beitrag,
    technischer Fehlschlag -> naechster Beitrag, Ablehnung der Gruppe ->
    Gruppe beiseite.
    """
    from fbgroups.marketing.beitrag import mit_kommentaradresse, ohne_link
    from fbgroups.urls import adresse_im_text

    # **Auch hier kein Tracking-Link** (23.09.2026). ``text`` kommt fertig
    # vom Server und traegt die freie Adresse schon; angehaengt wird sie nur,
    # wo sie fehlt. Setzte ein aelterer Server noch den Tracking-Link ein,
    # geht dieser Rueckfall lieber gar nicht hinaus.
    text = mit_kommentaradresse(ohne_link(text), link_url)
    if adresse := adresse_im_text(text, erlaubt=(link_url,)):
        return Schrittergebnis(
            erfolg=False,
            fehler=f"Adresse im Kommentar ({adresse}) - nicht abgesetzt",
            kein_anlass=True,
        )

    nach_rang = sorted(
        unkommentiert, key=lambda p: p["interactions"] + p["comments"], reverse=True
    )
    versucht = 0
    letzter: Schrittergebnis | None = None
    if gescheitert is None:
        gescheitert = set()

    for gewaehlt in nach_rang[:MAX_BEITRAEGE_JE_SCHRITT]:
        versucht += 1
        # ``text`` ist der vorbereitete und bereits aufgeloeste Text des
        # Servers - hier wird nichts ersetzt und nichts gewaehlt.
        letzter = _ausgang(
            kommentieren(context, gewaehlt["post_url"], text), gewaehlt["post_url"]
        )
        if letzter.erfolg or letzter.gruppe_beiseite:
            return letzter

        if letzter.beitrag_weg:
            console.print(
                "[dim]  [Ergebnis] Beitrag nicht mehr vorhanden"
                "[/dim] [dim][Aktion] naechster Beitrag[/dim]"
            )
            gescheitert.add(gewaehlt["post_url"])
            continue

        if ist_sitzungsfehler(letzter.fehler):
            return letzter

        if not ist_technisch(letzter.fehler):
            # Eine Ablehnung gilt der Gruppe und beim naechsten Beitrag
            # genauso - sie zu wiederholen hiesse, gegen die Gruppe zu
            # arbeiten.
            return replace(letzter, gruppe_beiseite=True)

        console.print(
            f"[yellow]  [Ergebnis] technisch fehlgeschlagen: {letzter.fehler}"
            f"[/yellow] [dim][Aktion] naechster Beitrag[/dim]"
        )
        gescheitert.add(gewaehlt["post_url"])

    if letzter is None:  # pragma: no cover - ``unkommentiert`` ist nie leer
        return Schrittergebnis(
            erfolg=False, fehler="kein Beitrag zum Kommentieren", kein_anlass=True
        )
    return _abschluss(letzter, versucht)


def _abschluss(letzter: Schrittergebnis, versucht: int) -> Schrittergebnis:
    """Was aus einem Schritt wird, in dem **kein** Beitrag angenommen hat.

    Die gemeinsame Stelle beider Wege (mit und ohne gelesene Texte) - zwei
    Fassungen waeren zwei Regeln fuer denselben Ausgang.

    In beiden Faellen wird die Gruppe fuer **diesen Lauf** beiseitegelegt,
    statt beim naechsten Durchgang dieselben Beitraege noch einmal
    anzufassen. Der Unterschied steht im Grund - und er entscheidet
    daneben ueber den Ausschluss aus der Kampagne:

    * ``beitrag_weg`` - alle versuchten Adressen zeigen ins Leere. Die Gruppe
      hat damit nichts zu tun: Ihre Beitragsliste ist bloss aelter als unser
      Bestand, und sie dafuer auszuschliessen hiesse, die falsche Stelle zu
      bestrafen.
    * sonst - die Technik hat nicht mitgespielt. Kein Urteil ueber die Gruppe
      (``ist_technisch``), aber hier ist heute nichts zu holen.
    """
    if letzter.beitrag_weg:
        return replace(
            letzter,
            fehler=f"{versucht} Beitraege nicht mehr vorhanden",
            gruppe_beiseite=True,
            beitrag_weg=True,
        )
    return replace(
        letzter,
        fehler=f"{letzter.fehler} ({versucht} Beitraege versucht)",
        gruppe_beiseite=True,
    )


def text_zur_gelegenheit(
    config: AppConfig,
    group_id: str,
    gelegenheit: Gelegenheit,
    *,
    rueckfall: str,
    bisherige: set[str],
    leise: bool = False,
) -> tuple[str | None, str]:
    """Welcher Text unter **diesen** Beitrag gehoert. Returns: ``(text, schluessel)``.

    Die Umkehrung der bisherigen Reihenfolge, und darin liegt die ganze
    Aenderung vom 13.09.2026: Bis dahin stand der Text fest, bevor ein
    Beitrag gelesen war - fuenf Fassungen je Gruppe, gewaehlt nach der
    Gruppenkennung. Er passte deshalb auf jeden Beitrag gleich gut, also auf
    keinen.

    Jetzt entscheidet der **Anlass** des Beitrags (``inhalt.Anlass``), und
    der Link folgt dem ``Linkmodus`` der Entscheidung:

    * ``TRACKING_LINK`` - die Gruppe erlaubt Links und der Bezug ist belegt:
      Anlasstext mit ``{link}`` in eigener Zeile.
    * ``APP_NAME_ONLY`` - der Regelfall: Anlasstext, der die App nennt, ohne
      Adresse. Viele Gruppen lehnen einen Link im Kommentar automatisch ab.
    * ``NO_LINK`` - es soll gar nichts von uns dastehen (blosse Hilfe,
      privater Hinweis). Dafuer gibt es keinen Vorrat, und einen zu erfinden
      waere das Gegenteil dessen, was "keine KI" bedeutet. Also: kein
      Kommentar. ``(None, "")``

    ``rueckfall`` ist der vorbereitete Text der Fassung. Er greift nur, wenn
    zu diesem Anlass **gar kein** Vorrat konfiguriert ist - etwa in einer
    deutschen Kampagne, fuer die ``anlaesse.de`` noch fehlt. Ohne ihn stuende
    eine Sprache ohne Anlassvorrat still, und das waere eine Aenderung, die
    niemand angeordnet hat.
    """
    from fbgroups.marketing import vorlagen

    entscheid = gelegenheit.entscheidung
    modus = _gedeckelt(config, entscheid.linkmodus)
    if modus is entscheidung_modul.Linkmodus.NO_LINK:
        return None, ""

    anlass = gelegenheit.befund.anlass
    if anlass is inhalt.Anlass.KEINER and anlass_pflicht(config):
        return None, ""

    sprache = str(
        config.get("marketing", "posting", "sprache", default="arabisch")
    ).strip().lower()
    sprache = {"arabisch": "ar", "ar": "ar", "deutsch": "de", "de": "de"}.get(
        sprache, "ar"
    )

    treffer = vorlagen.anlasstext(
        config,
        sprache=sprache,
        anlass=anlass.value,
        group_id=group_id,
        daten=vorlagen.Personalisierung(zielgruppe="", stadt=""),
        mit_link=modus is entscheidung_modul.Linkmodus.TRACKING_LINK,
        bisherige=frozenset(bisherige),
    )
    if treffer is not None:
        schluessel, fertig = treffer
        if not leise:
            console.print(f"[dim]  Vorlage {schluessel} ({modus.value})[/dim]")
        return fertig, schluessel

    if anlass_pflicht(config):
        return None, ""
    return rueckfall, ""


#: Die Stufen in der Reihenfolge ihrer Naehe zur Werbung - fuer den Deckel.
_MODUSRANG = (
    entscheidung_modul.Linkmodus.NO_LINK,
    entscheidung_modul.Linkmodus.APP_NAME_ONLY,
    entscheidung_modul.Linkmodus.TRACKING_LINK,
)


def _gedeckelt(config: AppConfig, modus):  # noqa: ANN001, ANN202 - Linkmodus
    """Der Modus, aber hoechstens so weit wie ``marketing.linkmodus_max``.

    Ein **Deckel**, keine zweite Entscheidung: Er kann nur nach unten wirken.
    Was die Gruppe verbietet, bleibt verboten; was sie erlaubt, darf man
    trotzdem sein lassen.

    Wozu er da ist: Die Anforderung vom 13.09.2026 nennt "kein Link" als
    Vorgabe und zaehlt auf, wann einer doch erlaubt ist - gelesene Regeln
    ohne Linkverbot, belegter Bezug, kein bekannter Linkfilter. Genau das
    prueft ``entscheidung.soll_link_nutzen`` schon, und deshalb steht der
    Deckel hier auf ``tracking_link``: Die Bedingungen sind die Regel, nicht
    der Schalter.

    Wer den Link trotzdem grundsaetzlich nicht will, setzt
    ``marketing.linkmodus_max: app_name_only``. Das ist eine bewusste
    Entscheidung mit Preis: Ein Kommentar ohne Link bekommt nie einen Klick
    gutgeschrieben - die Zuordnung haengt dann allein am eigenen Beitrag
    daneben, der seinen Link immer traegt (``pruefe_platzhalter``). Deshalb
    ist es ein Schalter und keine Vorgabe.
    """
    roh = str(
        config.get("marketing", "linkmodus_max", default="tracking_link")
    ).strip().lower()
    try:
        deckel = entscheidung_modul.Linkmodus(roh)
    except ValueError:
        # Ein Tippfehler soll nicht heimlich alles abschalten - dieselbe
        # Ueberlegung wie bei ``sprache_der_kampagne``.
        deckel = entscheidung_modul.Linkmodus.TRACKING_LINK
    return min(modus, deckel, key=_MODUSRANG.index)


def ziel_kommentare(config: AppConfig) -> int:
    """Wie viele erfolgreiche Kommentare eine Kampagne erreichen soll.

    ``0`` heisst: kein eigenes Ziel - dann gilt wie bisher, dass jede Gruppe
    ihre Fassungen veroeffentlicht haben muss. Die Vorgabe **im Code** ist
    deshalb 0 und nicht 100: Ein Ziel ist eine Entscheidung, und die steht in
    ``settings.yaml`` (``marketing.kampagne.ziel_kommentare``).
    """
    wert = config.get("marketing", "kampagne", "ziel_kommentare", default=0)
    try:
        return max(0, int(wert))
    except (TypeError, ValueError):
        return 0


def anlass_pflicht(config: AppConfig) -> bool:
    """Braucht ein Kommentar einen erkannten Anlass - oder genuegt der Bezug?

    Vorgabe **wahr**, und das ist die Anforderung vom 13.09.2026: Nur bei
    einem konkreten Reise- oder Versandbedarf wird geantwortet. Ein Beitrag
    mit Bezug, aber ohne Anlass ("ich fliege bald nach Damaskus"), bekommt
    keinen Kommentar.

    Abgeschaltet (``marketing.anlass_pflicht: false``) gilt wieder die alte
    Regel: Der vorbereitete Text der Fassung geht hinaus, sobald die Relevanz
    reicht. Der Schalter steht da, weil die Erkennung eng gefasst ist - wer
    feststellt, dass zu wenig hinausgeht, aendert eine Zahl und keinen Code.
    """
    return bool(config.get("marketing", "anlass_pflicht", default=True))


def _ausgang(roh, post_url: str) -> Schrittergebnis:  # noqa: ANN001
    """Aus dem, was ``kommentieren`` liefert, ein ``Schrittergebnis``.

    Die eingereichte Funktion liefert im Betrieb einen ``Kommentarausgang``
    (mit Hinweis, Freigabestand und Gruppenlimit) und im Test ein blosses
    ``True``/``False`` - dort zaehlt sie, statt zu kommentieren. Beides wird
    hier auf dieselbe Form gebracht, damit die Auswahlregel ohne Browser
    pruefbar bleibt.

    **Das Gruppenlimit wird als Fehlschlag mit Grund weitergereicht.** Es
    zaehlt weder gegen die Fassung noch gegen die Gruppe
    (``klassifiziere`` -> ``GRUPPENLIMIT``); was folgt, ist ein Uebersprung
    fuer diesen Lauf.
    """
    if isinstance(roh, bool):
        return Schrittergebnis(
            erfolg=roh,
            fehler="" if roh else "Kommentarfeld nicht gefunden oder blockiert",
            post_url=post_url,
        )
    if roh.wartet_auf_freigabe:
        console.print(
            "[yellow]  Der Kommentar steht, ist aber bis zur Freigabe unsichtbar - "
            "bis dahin klickt niemand den Link.[/yellow]"
        )
    return Schrittergebnis(
        erfolg=roh.erfolg,
        fehler="" if roh.erfolg else (roh.hinweis or "Kommentar nicht angenommen"),
        post_url=post_url if roh.erfolg else "",
        # **Als Flagge, nicht nur als Satz.** Ob der naechste Beitrag noch
        # versucht wird, darf nicht an Facebooks Wortwahl haengen: Das
        # Gruppenlimit gilt fuer die ganze Gruppe, und drei weitere Anlaeufe
        # waeren drei sichere Fehlschlaege.
        gruppe_beiseite=bool(getattr(roh, "gruppenlimit", False)),
        beitrag_weg=bool(getattr(roh, "beitrag_weg", False)),
    )


def _als_roh(post) -> dict:  # noqa: ANN001 - GroupPost
    """Ein gespeicherter Beitrag als roher Fund - ohne Text, den hat er nie."""
    return {
        "post_url": post.post_url,
        "interactions": post.interactions,
        "comments": post.comments,
        "text": "",
    }


def _als_group_posts(roh: list[dict], group_id: str) -> list:
    """Was in den Bestand geht: Adresse und Kennzahlen. **Kein Text.**"""
    from fbgroups.models import GroupPost

    return [
        GroupPost(
            group_id=group_id,
            post_url=p["post_url"],
            interactions=int(p.get("interactions", 0) or 0),
            comments=int(p.get("comments", 0) or 0),
        )
        for p in roh
    ]


__all__ = [
    "Schrittergebnis",
    "aktionslage",
    "aktive_kampagnen",
    "browser_schritt",
    "fuehre_lauf_aus",
    "hole_oder_starte_lauf",
    "merke_bremse",
    "entscheide_und_kommentiere",
    "kommentare_zuerst",
    "ruhe_minuten",
    "ruhesekunden",
    "waehle_und_kommentiere",
    "wartesekunden",
]
