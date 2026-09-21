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
    qualifikation,
    zielgruppe,
)
from fbgroups.marketing.models import (
    CampaignStatus,
    KampagnenLaufStatus,
    LaufStatus,
    MarketingStatus,
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
    # Die Gruppe gibt nichts mehr her - kein Fehlschlag, sondern ein Ende.
    # Der Unterschied entscheidet, ob wiederholt oder weitergegangen wird.
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


def regeln_zuerst(config: AppConfig) -> bool:
    """Muessen die Regeln einer Gruppe gelesen sein, bevor eine Anfrage geht?

    Vorgabe hier **wahr** - die Anforderung vom 13.09.2026 nennt die
    Reihenfolge ausdruecklich: finden, bewerten, einstufen, Regeln lesen,
    dann erst anfragen. Abgeschaltet wird auf Ansage in ``settings.yaml``
    (``beitritt.regeln_zuerst``), und dafuer gibt es einen echten Fall:
    Laesst sich eine Gruppenseite nicht lesen - Anmeldewand, geschlossene
    Gruppe -, ginge die Anfrage sonst nie hinaus.

    Dieselbe Aufteilung wie bei ``mitgliedschaft_pflicht``: Der Code behaelt
    den Schutz fuer den Fall, dass niemand etwas gesagt hat.
    """
    return bool(config.get("beitritt", "regeln_zuerst", default=True))


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
        return int(offen["lauf_id"]), False

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
        stelle_texte_bereit(store, campaign, gruppe, config)
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
#: bevor er die Gruppe beiseitelegt. Drei, weil der haeufigste Grund fuer ein
#: fehlendes Kommentarfeld der Beitrag selbst ist (Kommentare abgeschaltet,
#: Freigabe noetig, geteilter Beitrag) - der naechste geht dann meist. Mehr
#: waere ein Dauerlauf in einer Gruppe, die heute nichts annimmt.
MAX_BEITRAEGE_JE_SCHRITT = 3


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
    from fbgroups.marketing.qualifikation import Ausgangsart, klassifiziere

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
    regeln_lesen: Callable[[str], str] | None = None,
    max_schritte: int = 0,
    trocken: bool = False,
    warte: Callable[[float], None] | None = None,
    nur: list[str] | None = None,
    frisch: bool = False,
) -> lauf.Lauffortschritt:
    """Arbeitet den Lauf ab - in der Reihenfolge des Kampagnenablaufs.

    Kampagne waehlen, Beitrittsanfragen, Neubewertung, beste Gruppen zuerst,
    Beitrag und Kommentare, dann die naechste Kampagne. Entschieden wird das
    nicht hier, sondern in ``lauf.naechster_schritt``; dieses Modul fuehrt
    aus, was dort ansteht, und kennt die Reihenfolge nicht.

    ``ausfuehren`` bekommt (Gruppen-URL, Gruppen-ID, Text, Zweck) und liefert
    ein ``Schrittergebnis``. Der Zweck ist ``post`` oder ``kommentar``: Ein
    Beitrag wird abgesetzt, ein Kommentar unter einen fremden Beitrag
    gesetzt - zwei Handgriffe im Browser, ein Vertrag.

    ``beitreten`` bekommt die Gruppen-URL und liefert ``(ausgang, bemerkung)``
    wie ``actions.request_join``. **Fehlt es, entstehen keine
    Beitrittsschritte** - ein Treiber ohne Browser soll nicht so tun, als
    koennte er beitreten.

    ``regeln_lesen`` bekommt die Gruppen-URL und liefert den Seitentext wie
    ``actions.fetch_group_html``. Fehlt es, wird die Gruppe fuer diesen Lauf
    uebersprungen statt beigetreten: Ohne gelesene Regeln geht keine Anfrage
    hinaus (``beitritt.regeln_zuerst``), und so zu tun, als waeren sie
    gelesen, waere die Erlaubnis aus dem Nichts.

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
    # Beobachtet und angezeigt wird immer, gesperrt nur auf Ansage - siehe
    # ``qualifikation.pflicht``. Was die Gruppe selbst verbietet, bindet
    # unabhaengig davon (``qualifikation.darf_nach_regeln``).
    qual_pflicht = qualifikation.pflicht(config)
    # Einmal je Lauf: Die Begriffe stehen in der Konfiguration und aendern
    # sich waehrend eines Laufs nicht. Die **Einstufung** wird trotzdem bei
    # jedem Durchgang neu gerechnet - siehe ``stand``.
    zielregeln = zielgruppe.regeln_aus_config(config)
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
            qualifikation_pflicht=qual_pflicht,
            aktionen=lagen,
            # Bei jedem Durchgang neu gerechnet und nicht einmal am Anfang:
            # ``_bewerten`` schreibt neue Kategorien und Zielgruppen in
            # ``gruppen``, und eine Einstufung von vor der Neubewertung waere
            # genau die veraltete zweite Wahrheit, die dieses Projekt
            # vermeidet. Die Rechnung kostet nichts gegen einen Seitenabruf.
            zielbefunde={
                gid: zielgruppe.aus_group(g, zielregeln) for gid, g in gruppen.items()
            },
            # Ohne Leser keine Regelschritte - dieselbe Ueberlegung wie bei
            # ``beitreten is None``: Ein Treiber ohne Browser soll nicht so
            # tun, als koennte er nachsehen. Wuerde die Pflicht trotzdem
            # gelten, bliebe jede Gruppe mit ungelesenen Regeln liegen, und
            # der Lauf taete gar nichts - obwohl an ihm nichts fehlt ausser
            # einer Faehigkeit, die er nie hatte.
            regeln_pflicht=regeln_zuerst(config) and regeln_lesen is not None,
            heute_je_gruppe=store.versuche_heute_je_gruppe(
                heute, Texttyp.KOMMENTAR.value
            ),
            gruppenlimit=grenzen.einstellungen(config)
            .fuer(grenzen.Aktion.KOMMENTAR)
            .je_gruppe_taeglich,
            # Aus derselben Tabelle wie die Mindestrelevanz: Eine Klasse ohne
            # Schwelle ist eine, in der nicht gearbeitet wird.
            klassen=zielgruppe.bearbeitbare_klassen(config),
            kommentare_zuerst=kommentare_zuerst(config),
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

        try:
            fertig = _fuehre_schritt_aus(
                config,
                pfad,
                lauf_id,
                schritt,
                gruppen,
                ausfuehren=ausfuehren,
                beitreten=beitreten,
                regeln_lesen=regeln_lesen,
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

        # Die Neubewertung zaehlt nicht mit: ``--limit`` begrenzt, was **nach
        # aussen** geht, nicht die Buchfuehrung. Sonst kostete ein Lauf mit
        # ``--limit 5`` in fuenf Kampagnen fuenf Bewertungen und keinen
        # einzigen Beitrag.
        if schritt.art is lauf.Schrittart.BEWERTEN:
            continue

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

    Bei einem Schritt ohne Gruppe (der Neubewertung) wird stattdessen die
    Bewertung als erledigt vermerkt: Sonst stuende die Kampagne bei jedem
    Durchgang wieder davor, und der Lauf kaeme nie zur Arbeit.
    """
    if not schritt.group_id:
        store.merke_bewertung(lauf_id, schritt.campaign_id)
        return
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
    regeln_lesen: Callable[[str], str] | None,
    trocken: bool,
    kaltmodus_aktiv: bool,
    abstand: int,
    technik: _Technikwaechter,
) -> bool:
    """Genau einen Schritt ausfuehren. Returns: ob der Lauf enden soll.

    Die vier Arten stehen hier nebeneinander, weil sie denselben Rahmen
    teilen: lesen, handeln (ohne offene Datenbank), buchen. Was sie
    unterscheidet, ist allein die Handlung in der Mitte.
    """
    if schritt.art is lauf.Schrittart.REGELN:
        return _regeln_schritt(pfad, lauf_id, schritt, gruppen, regeln_lesen, trocken=trocken)
    if schritt.art is lauf.Schrittart.BEWERTEN:
        return _bewerten(config, pfad, lauf_id, schritt, gruppen, trocken=trocken)
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


def _bewerten(
    config: AppConfig,
    pfad: Path,
    lauf_id: int,
    schritt: lauf.Schritt,
    gruppen: dict,
    *,
    trocken: bool,
) -> bool:
    """Schritt 3: die Gruppen dieser Kampagne neu bewerten.

    Kein Browser, kein Netz - gerechnet wird aus dem, was inzwischen bekannt
    ist: Mitgliederzahl aus ``enrich``, Resonanz aus den Klicks, Aktivitaet
    aus der Beitragsliste. Erst danach steht die Rangfolge fest, nach der
    gearbeitet wird; ohne diesen Schritt arbeitete der Lauf nach den Zahlen
    von vorgestern.

    Der Vermerk wird **auch nach einem Fehlschlag** gesetzt (der Aufrufer tut
    das ueber ``_ueberspringen``): Eine Bewertung, die jedes Mal scheitert,
    hielte die Kampagne sonst fuer immer vor der Arbeit fest.
    """
    from fbgroups.rescoring import bewerte_neu

    console.print(f"[bold]{schritt.gruppe_name}[/bold] - Neubewertung")
    if trocken:
        console.print("[dim]  --dry-run: es wird nichts geschrieben[/dim]")
        return True

    with MarketingStore(pfad) as store:
        nur = {link.group_id for link in store.links_for_campaign(schritt.campaign_id)}

    ergebnis = bewerte_neu(config, nur=nur)
    console.print(
        f"[dim]  {ergebnis.bewertet} Gruppen bewertet, "
        f"{ergebnis.geaendert} mit geaendertem Score[/dim]"
    )
    # Der Lauf haelt die Gruppen im Gedaechtnis; ohne diese Zeile sortierte
    # der naechste Durchgang nach den Scores von vor der Bewertung.
    gruppen.update({g.group_id: g for g in ergebnis.gruppen})

    with MarketingStore(pfad) as store:
        store.merke_bewertung(lauf_id, schritt.campaign_id)
    return False


def _regeln_schritt(
    pfad: Path,
    lauf_id: int,
    schritt: lauf.Schritt,
    gruppen: dict,
    regeln_lesen: Callable[[str], str] | None,
    *,
    trocken: bool,
) -> bool:
    """Schritt 1: nachsehen, was diese Gruppe erlaubt - **vor** der Anfrage.

    Ein Seitenabruf, keine Handlung in der Gruppe: Niemand sieht ihn, nichts
    wird geschrieben, kein Kontingent verbraucht. Genau deshalb steht er vor
    der Beitrittsanfrage und nicht danach - er kostet am wenigsten und
    entscheidet am meisten.

    **Ein nicht gelesener Befund schreibt nichts** (``store.merke_regeln``).
    Eine Anmeldewand ist kein Beleg dafuer, dass eine frueher gelesene Regel
    weg ist - dieselbe Ueberlegung wie bei ``upsert_groups`` mit COALESCE.
    Damit dieselbe Gruppe trotzdem nicht bei jedem Durchgang wiederkommt,
    wird sie fuer **diesen** Lauf beiseitegelegt: ein Uebersprung, kein
    Urteil.
    """
    from fbgroups.marketing.qualifikation import lies_regeln

    gruppe = gruppen.get(schritt.group_id)
    if regeln_lesen is None or gruppe is None or not gruppe.url_canonical:
        with MarketingStore(pfad) as store:
            _ueberspringen(store, lauf_id, schritt, "Regeln nicht lesbar")
        return False

    console.print(f"[bold]{schritt.gruppe_name}[/bold] - Gruppenregeln lesen")
    if trocken:
        console.print("[dim]  --dry-run: es wird nichts abgerufen[/dim]")
        return True

    befund = lies_regeln(regeln_lesen(gruppe.url_canonical))

    with MarketingStore(pfad) as store:
        if not befund.gelesen:
            console.print("[yellow]  Seite nicht lesbar - in diesem Lauf uebersprungen[/yellow]")
            _ueberspringen(store, lauf_id, schritt, "Gruppenseite nicht lesbar")
            return False
        store.merke_regeln(schritt.group_id, befund)
        console.print(f"[green]  gelesen:[/green] {befund.zusammenfassung() or 'nichts verboten'}")
    return False


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
    from fbgroups.marketing.beitrag import mit_link

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
        text = mit_link(campaign, link, vorschlag.text, config=config, ziel=ziel)
        # Die **Adresse** getrennt vom Text: Waehlt der Lauf gleich einen
        # Anlasstext statt dieses vorbereiteten, traegt jener wieder
        # ``{link}`` - und braucht dieselbe Adresse. Ohne sie stand am
        # 14.09.2026 "{link}" woertlich in einem Kommentar.
        link_url = link.url_fuer(ziel)
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

    # **NO_REPLY ist ein Ergebnis, kein Fehlversuch.** In dieser Gruppe stand
    # heute kein Beitrag, unter dem eine Antwort von uns etwas beigetragen
    # haette. Das als Fehlschlag zu buchen zaehlte gegen die Fassung und
    # irgendwann gegen die Gruppe - obwohl nichts gegen sie vorliegt. Sie
    # wird stattdessen fuer diesen Lauf beiseitegelegt; morgen stehen dort
    # andere Beitraege.
    if ergebnis.kein_anlass:
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
            # Eine tote Adresse ist kein Fehler der Gruppe: Sie ruht und
            # kommt zurueck. Ein technischer Fehlschlag legt sie dagegen
            # fuer den Lauf beiseite - sie wird gleich darunter ohnehin aus
            # der Kampagne genommen.
            _ueberspringen(
                store,
                lauf_id,
                schritt,
                f"technisch: {ergebnis.fehler}"[:160],
                ruhe=ruhe_minuten(config) if ergebnis.beitrag_weg else 0,
            )
            # **Und dauerhaft aus der Kampagne** (20.09.2026). Der Uebersprung
            # gilt nur fuer diesen Lauf; beim naechsten Start stuende dieselbe
            # Gruppe wieder ganz vorn. "Kein Kommentarfeld" ist dort keine
            # Eigenschaft des Browsers, sondern eine der Gruppe.
            # **Nicht bei einer toten Adresse.** Die Gruppe kann voellig in
            # Ordnung sein; was fehlt, ist ein Beitrag, den es nicht mehr
            # gibt. Sie dafuer auszuschliessen hiesse, die falsche Stelle zu
            # bestrafen - dieselbe Verwechslung wie "Technik ist kein
            # Urteil", nur eine Ebene tiefer.
            # **Und nicht bei einem Sitzungsfehler** (21.09.2026). Eine
            # abgemeldete Sitzung findet in **jeder** Gruppe kein
            # Kommentarfeld; jeder dieser Fehlschlaege gilt als technisch,
            # und technisch heisst hier: raus aus der Kampagne. Ein
            # abgelaufener Anmeldestand haette so eine Kampagne nach der
            # anderen leergeraeumt - mit einem Grund an jeder Gruppe, an dem
            # nichts liegt. Der Lauf haelt stattdessen gleich darunter an.
            if (
                not ergebnis.beitrag_weg
                and not ist_sitzungsfehler(ergebnis.fehler)
                and store.schliesse_gruppe_aus(
                    schritt.group_id, f"automatisch: {ergebnis.fehler}"
                )
            ):
                console.print(
                    f"[yellow]  {schritt.gruppe_name}: aus der Bearbeitung genommen "
                    f"({ergebnis.fehler[:60]})[/yellow]"
                )

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
        art = qualifikation.klassifiziere(ergebnis.fehler)
        if art is qualifikation.Ausgangsart.GRUPPENLIMIT:
            # Nur **diese** Gruppe nimmt nichts mehr an. Die Aktion laeuft
            # weiter; in der naechsten Gruppe geht es sofort los.
            console.print(
                "[yellow]  Diese Gruppe nimmt gerade nichts mehr an - "
                "andere Gruppen laufen weiter.[/yellow]"
            )
        elif art is qualifikation.Ausgangsart.RATE_LIMIT:
            stufe = merke_bremse(store, aktion.value)
            console.print(
                f"[yellow]  Die Gegenseite bremst: {aktion.value} pausiert "
                f"{grenzen.backoff_minuten(stufe)} Min. Andere Aktionen laufen "
                f"weiter.[/yellow]"
            )
    if ergebnis.erschoepft:
        store.setze_kommentar_erschoepft(
            schritt.campaign_id, schritt.group_id, ergebnis.fehler or "keine Beitraege mehr"
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

    roh = fetch_top_posts(context, gruppen_url, group_id, limit=10)
    if not roh:
        return Schrittergebnis(
            erfolg=False, fehler="keine Beitraege zum Kommentieren gefunden", erschoepft=True
        )

    # **Die rohen Funde gehen weiter, nicht die gespeicherten.** Nur sie
    # tragen den Text, und der wird fuer die Auswahl gebraucht -
    # ``GroupPost`` hat dafuer kein Feld und soll auch keines bekommen.
    return waehle_und_kommentiere(
        context, config, roh, group_id, text,
        kommentieren=comment_on_post, link_url=link_url,
    )


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


def anspruch_fuer(config: AppConfig, group_id: str):  # noqa: ANN201
    """Was ein Beitrag **in dieser Gruppe** hergeben muss.

    Die Uebersetzung von der Gruppenklasse in die Schwelle: ``zielgruppe``
    sagt, wo wir sind, ``entscheidung.Anspruch`` sagt, was dort gilt. Beides
    steht in ``settings.yaml``; hier wird nur zusammengefuehrt.

    Eine Gruppe, die es im Bestand nicht gibt, bekommt die Vorgabe - nicht
    den strengsten Wert: Eine fehlende Angabe ist kein Urteil, und der
    strengste Wert waere hier eines.

    **Gefragt wird zuerst die gepflegte Note** ("A++" bis "B" aus der
    Mitgliederliste), danach erst die gerechnete Klasse. Wer die Gruppe
    angesehen und eingestuft hat, weiss mehr als jede Worterkennung an einem
    Namen - dieselbe Rangfolge wie zwischen gepflegter Kategorie und
    ``kategoriebegriffe``.
    """
    from fbgroups.models import Group  # noqa: F401 - nur fuer die Typangabe im Kopf

    with SqliteStore(config.path("sqlite_path")) as gruppen_store:
        gruppe = gruppen_store.get_group(group_id)
    # Der Schalter gehoert zur Schwelle, nicht zur Gruppe: Er sagt, ob neben
    # ihr noch ein Halbsatz verlangt wird. Deshalb steht er in **jedem**
    # Rueckgabewert hier - auch in der Vorgabe.
    pflicht = anlass_pflicht(config)
    if gruppe is None:
        return entscheidung_modul.Anspruch(anlass_pflicht=pflicht)

    # **Die gepflegte Note geht vor** (21.09.2026). Sie steht am Datensatz,
    # weil ein Mensch die Gruppe angesehen hat; die Klasse wird aus Namen und
    # Feldern erschlossen. Bis hierhin entschied die erschlossene Klasse auch
    # dort, wo eine Note dastand - und weil die Mitgliederliste keine
    # Kategorie mitbringt, war das fast immer "C: hoch + Strecke", also die
    # Schwelle, die im Betrieb jeden Kommentar verhindert hat.
    noten = zielgruppe.anspruch_aus_note(config)
    if (aus_note := noten.get((gruppe.listenprioritaet or "").strip().upper())) is not None:
        relevanz, strecke = aus_note
        return entscheidung_modul.Anspruch(
            mindestrelevanz=relevanz, verlangt_strecke=strecke, anlass_pflicht=pflicht
        )

    befund = zielgruppe.aus_group(gruppe, zielgruppe.regeln_aus_config(config))
    tabelle = zielgruppe.anspruch_aus_config(config)
    stufe = tabelle.get(befund.prioritaet)
    if stufe is None:
        # Klasse D: Dort wird gar nicht geantwortet. Der Lauf kommt hier
        # normalerweise nicht hin (``bearbeitbar`` schliesst sie aus); wer es
        # doch versucht, bekommt die Schwelle, die nichts durchlaesst.
        return entscheidung_modul.Anspruch(
            mindestrelevanz=inhalt.Relevanz.HOCH,
            verlangt_strecke=True,
            anlass_pflicht=pflicht,
        )
    relevanz, strecke = stufe
    return entscheidung_modul.Anspruch(
        mindestrelevanz=relevanz, verlangt_strecke=strecke, anlass_pflicht=pflicht
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
        bisherige = store.bisherige_post_urls(group_id)
        verbrauchte_vorlagen = store.verwendete_vorlagen(
            group_id, Texttyp.KOMMENTAR.value
        )
        if erlaubnis is None:
            erlaubnis = erlaubnis_fuer(store, group_id)

    return entscheide_und_kommentiere(
        context,
        config,
        roh,
        group_id,
        text,
        kommentieren=kommentieren,
        bisherige=bisherige,
        erlaubnis=erlaubnis,
        anspruch=anspruch_fuer(config, group_id),
        verbrauchte_vorlagen=verbrauchte_vorlagen,
        link_url=link_url,
    )


def entscheide_und_kommentiere(
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
) -> Schrittergebnis:
    """Lesen, beurteilen, entscheiden, **dann erst** kommentieren.

    **Die eine Stelle, an der aus zehn gelesenen Beitraegen ein Kommentar
    wird** - und sie kennt keine Datenbank. Das ist der Punkt (14.09.2026):
    Bis dahin stand diese Kette nur im oertlichen Lauf. Der Fernbetrieb
    (``campaign automatik --server``) nahm stattdessen den **lautesten**
    Beitrag und setzte den vorbereiteten Text darunter - ohne Inhaltspruefung,
    ohne die Regeln der Gruppe, ohne Anspruch. Wer den Fernbetrieb faehrt -
    und das ist der Regelfall -, hatte damit einen Runner, der genau die
    Pruefungen ausliess, die ``campaign pruefe-inhalt`` vorfuehrt.

    Alles, was sonst aus dem Bestand kaeme, wird hereingereicht:

    * ``bisherige`` - unter welchen Beitraegen schon etwas von uns steht.
    * ``erlaubnis`` - was die Gruppe laut ihren **gelesenen** Regeln zulaesst
      (``qualifikation.beurteile`` → ``Erlaubnis.aus_regeln``).
    * ``anspruch`` - wie viel ein Beitrag **an dieser Stelle** hergeben muss
      (``zielgruppe.anspruch_aus_config``).
    * ``verbrauchte_vorlagen`` - damit derselbe Satz nicht zweimal in
      derselben Gruppe steht.
    * ``link_url`` - die **fertige** Adresse dieser Gruppe. Der Anlasstext
      traegt ``{link}`` als Platzhalter; aufgeloest wird er hier, unmittelbar
      bevor er hinausgeht (``beitrag.setze_adresse``).

    Dieselbe Aufteilung wie bei ``lies_regeln``: Die reine Regel laeuft auf
    dem Rechner, der den Browser hat; der Bestand liegt dort, wo gezaehlt
    wird. Zwei Auswertungen koennten auseinanderlaufen - eine kann es nicht.
    """
    bisherige = set(bisherige or ())
    unkommentiert = [p for p in roh if p["post_url"] not in bisherige]
    if not unkommentiert:
        return Schrittergebnis(
            erfolg=False,
            fehler="alle sichtbaren Beitraege sind bereits kommentiert",
            erschoepft=True,
        )

    # Ohne einen einzigen lesbaren Text ist keine Entscheidung moeglich -
    # dann gilt die alte Regel (der belebteste Beitrag), statt gar nichts zu
    # tun. Das ist die ehrlichere Stelle fuer den Rueckfall: Wir wissen
    # nichts ueber die Beitraege, nicht "sie passen nicht".
    if not any(p.get("text", "").strip() for p in unkommentiert):
        return _ohne_urteil_kommentieren(
            context, unkommentiert, text, kommentieren=kommentieren
        )

    # **Erst das Urteil, dann die Wahl.** Jeder gelesene Beitrag bekommt
    # seinen Befund (Thema, Absicht, Bezug, Anlass) und seine Entscheidung
    # (welche Form einer Antwort passt, mit oder ohne Link) - und zwar
    # bevor irgendetwas geschrieben wird.
    gelegenheiten = beurteile_beitraege(unkommentiert, erlaubnis, anspruch)
    from fbgroups.marketing.beitrag import offene_platzhalter, setze_adresse

    verbraucht = set(verbrauchte_vorlagen or ())
    gescheitert: set[str] = set()
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
    for versuch in range(1, MAX_BEITRAEGE_JE_SCHRITT + 1):
        gewaehlt = waehle_gelegenheit(gelegenheiten, gescheitert)
        if gewaehlt is None:
            break

        console.print(
            f"[dim]  [Versuch {versuch}/{MAX_BEITRAEGE_JE_SCHRITT}] "
            f"{gewaehlt.entscheidung.art.value}: {gewaehlt.entscheidung.grund}[/dim]"
        )

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
            return Schrittergebnis(erfolg=False, fehler=grund, kein_anlass=True)

        # **Erst jetzt die Adresse.** Der gespeicherte und der
        # weitergereichte Text tragen ``{link}``; was in die Gruppe geht,
        # traegt die Adresse.
        hinausgehend = (
            setze_adresse(gewaehlter_text, link_url) if link_url else gewaehlter_text
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
        # Gespeichert wird der Text **mit** dem Platzhalter: Er ist die
        # Fassung, nicht ihre Ausfertigung.
        letzter = replace(ergebnis, text=gewaehlter_text, vorlage_key=schluessel)

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
    nach_rang = sorted(
        unkommentiert, key=lambda p: p["interactions"] + p["comments"], reverse=True
    )
    versucht = 0
    letzter: Schrittergebnis | None = None

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
            continue

        if not ist_technisch(letzter.fehler):
            # Eine Ablehnung gilt der Gruppe und beim naechsten Beitrag
            # genauso - sie zu wiederholen hiesse, gegen die Gruppe zu
            # arbeiten.
            return replace(letzter, gruppe_beiseite=True)

        console.print(
            f"[yellow]  [Ergebnis] technisch fehlgeschlagen: {letzter.fehler}"
            f"[/yellow] [dim][Aktion] naechster Beitrag[/dim]"
        )

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


#: Ab welchem Stand das Konto als Mitglied gilt. Wer die Zusammenarbeit
#: angebahnt oder abgeschlossen hat, ist erst recht drin;
#: ``beitritt_angefragt`` zaehlt ausdruecklich **nicht** - eine offene
#: Anfrage ist keine Mitgliedschaft. Dieselbe Menge wie in
#: ``lauf.lies_fortschritt``.
MITGLIEDSCHAFT = frozenset(
    {
        MarketingStatus.MEMBER,
        MarketingStatus.CONTACTED,
        MarketingStatus.INTERESTED,
        MarketingStatus.APPROVED,
        MarketingStatus.ACTIVE,
    }
)


def erlaubnis_fuer(store: MarketingStore, group_id: str):  # noqa: ANN201
    """Was diese Gruppe zulaesst - aus ihren gelesenen Regeln und der Beobachtung.

    Uebersetzt, nicht neu entschieden: Die Rangfolge (Regeln binden,
    Beobachtung schraenkt ein) steht in ``qualifikation.beurteile``.
    """
    stand = store.load_marketing(group_id)
    regeln = qualifikation.Regelbefund(
        gelesen=bool(stand and stand.regeln_gelesen_am),
        keine_links=bool(stand and stand.regel_keine_links),
        keine_werbung=bool(stand and stand.regel_keine_werbung),
        freigabe_noetig=bool(stand and stand.regel_freigabe_noetig),
        neue_ohne_links=bool(stand and stand.regel_neue_ohne_links),
    )
    befund = qualifikation.beurteile(
        mitglied=bool(stand and stand.marketing_status in MITGLIEDSCHAFT),
        beitritt_angefragt=bool(
            stand and stand.marketing_status is MarketingStatus.JOIN_REQUESTED
        ),
        regeln=regeln,
        beobachtung=store.beobachtungen().get(group_id),
    )
    return entscheidung_modul.Erlaubnis.aus_regeln(regeln, befund.qualifikation)


#: Wie viele Netzfehler hintereinander der Fernbetrieb hinnimmt, bevor er
#: aufgibt. Ein einzelner Aussetzer ist ein Schluckauf des Tunnels; fuenf
#: hintereinander heissen, dass der Dienst nicht mehr da ist - und dann
#: weiterzulaufen hiesse, im Browser zu arbeiten, ohne dass irgendwo etwas
#: gebucht wird. Genau dieser Fall ist **kein** Fall fuer die
#: Fehlerisolierung: Er betrifft nicht eine Gruppe, sondern alle.
MAX_NETZFEHLER = 5


def fuehre_lauf_fern_aus(
    basis_url: str,
    *,
    ausfuehren: Callable[[str, str, str, list[str], str, dict, str], Schrittergebnis],
    beitreten: Callable[[str], tuple[str, str]] | None = None,
    regeln_lesen: Callable[[str], str] | None = None,
    max_schritte: int = 0,
    zeitlimit: float = 300.0,
    nur: list[str] | None = None,
    frisch: bool = False,
    warte: Callable[[float], None] | None = None,
) -> str:
    """Denselben Lauf fahren, aber **auf dem Bestand des Servers**.

    Der Unterschied zu ``fuehre_lauf_aus`` ist keine Feinheit, sondern der
    ganze Zweck: Dort liest und bucht die Automatik in der Datei, in der sie
    laeuft - auf dem Arbeitsrechner also in einer Kopie. Der Kommentar ging
    hinaus, gebucht wurde daneben, und der Server bot dieselbe Gruppe weiter
    als offen an. Am 31.08.2026 stand deshalb ein Beitrag lokal auf
    ``veroeffentlicht`` und auf dem Server auf ``offen``.

    Hier faellt das weg: Der Server sagt, was zu tun ist, und nimmt das
    Ergebnis entgegen. Dieser Rechner haelt **keinen** Stand - er steuert den
    Browser und meldet zurueck. Eine Wahrheit, und sie liegt dort, wo auch
    die Klicks gezaehlt werden.

    Auch die **Reihenfolge** liegt dort: Beitrittsanfragen vor Neubewertung
    vor Arbeit, beste Gruppen zuerst. Dieser Rechner sieht nur ``art`` und
    tut, was dort steht - er kann die Reihenfolge deshalb nicht anders
    auslegen als der oertliche Lauf.

    ``beitreten`` fuehrt einen Beitrittsschritt aus; fehlt es, wird ein
    solcher Schritt als Fehlschlag gemeldet und die Gruppe vom Server fuer
    diesen Lauf beiseitegelegt. ``regeln_lesen`` holt eine Gruppenseite;
    fehlt es, gilt sie als nicht lesbar - und ohne gelesene Regeln geht keine
    Beitrittsanfrage hinaus. **Ein Fehler bei einer Gruppe beendet den
    Lauf nicht**: Er wird gemeldet, gebucht und der naechste Schritt geholt.

    Returns: die Abschlussmeldung des Servers.
    """
    import httpx

    basis = basis_url.rstrip("/")
    getan = 0
    netzfehler = 0
    frische_liste = frisch
    technik = _Technikwaechter()
    letzte_meldung = "Kein Lauf."
    schlafen = warte if warte is not None else _schlafe

    # Ein Ursprung, den der Dienst als oertlich annimmt - ``_nur_lokal``
    # prueft neben der Adresse auch die Herkunft der Seite.
    kopf = {"Origin": basis, "Content-Type": "application/json"}

    with httpx.Client(timeout=zeitlimit, headers=kopf) as klient:
        while True:
            try:
                antwort = klient.post(
                    f"{basis}/automatik/naechster",
                    json={"kampagnen": list(nur or []), "neu": frische_liste},
                )
                # **Nur beim ersten Aufruf.** Jeder weitere schloesse sonst den
                # Lauf, den er gerade abarbeitet - und der naechste faengt mit
                # einer neuen Liste an, in der die uebersprungenen Gruppen
                # wieder stehen. Ein Lauf, der sich selbst neu startet, wird
                # nie fertig.
                frische_liste = False
                if antwort.status_code == 404:
                    raise RuntimeError(
                        "Der Dienst haelt den Aufruf fuer nicht-oertlich. Laeuft der "
                        "SSH-Tunnel, und zeigt --server auf 127.0.0.1?"
                    )
                antwort.raise_for_status()
                daten = antwort.json()
                netzfehler = 0
            except RuntimeError:
                raise
            except Exception as exc:  # noqa: BLE001 - der Tunnel, nicht die Gruppe
                netzfehler += 1
                console.print(
                    f"[red]Server nicht erreichbar ({str(exc).splitlines()[0][:80]}) - "
                    f"Versuch {netzfehler}/{MAX_NETZFEHLER}[/red]"
                )
                if netzfehler >= MAX_NETZFEHLER:
                    return (
                        f"Abgebrochen: Der Dienst antwortet seit {MAX_NETZFEHLER} "
                        "Versuchen nicht. Es wurde nichts gebucht, was nicht gemeldet "
                        "wurde."
                    )
                schlafen(5.0)
                continue

            if daten.get("meldung") and daten.get("weiter"):
                # Zwischenmeldungen des Servers (Neubewertung, uebersprungene
                # Gruppen) - sie erklaeren, warum gerade kein Schritt kommt.
                console.print(f"[dim]{daten['meldung']}[/dim]")

            if daten.get("schritt") is None:
                # ``warten`` heisst: Der Takt der Beitrittsanfragen laesst
                # gerade keine zu. Gewartet wird, nicht vorgezogen - sonst
                # waere die Reihenfolge eine Empfehlung.
                if (pause := float(daten.get("warten") or 0)) > 0:
                    console.print(f"[yellow]{daten.get('meldung', 'Takt')}[/yellow]")
                    schlafen(pause)
                    continue
                # ``weiter`` heisst: Der Server hat etwas geklaert (eine
                # erschoepfte Gruppe, eine Neubewertung) und hat gleich den
                # naechsten Schritt.
                if daten.get("weiter"):
                    continue
                letzte_meldung = daten.get("meldung", "Nichts mehr zu tun.")
                break

            s = daten["schritt"]
            art = s.get("art", "text")

            if art == "regeln":
                # Schritt 1: die Gruppenseite lesen. Der Browser steht hier,
                # der Bestand dort - ausgewertet wird mit derselben reinen
                # Funktion wie oertlich, und hinueber geht nur der Befund.
                from fbgroups.marketing.qualifikation import lies_regeln

                console.print(f"[bold]{s['gruppe_name']}[/bold] - Gruppenregeln lesen")
                seite = regeln_lesen(s["gruppen_url"]) if regeln_lesen is not None else ""
                befund = lies_regeln(seite)
                if not _melde(
                    klient,
                    f"{basis}/automatik/regeln/ergebnis",
                    {
                        "group_id": s["group_id"],
                        "campaign_id": s["campaign_id"],
                        "gelesen": befund.gelesen,
                        "keine_links": befund.keine_links,
                        "keine_werbung": befund.keine_werbung,
                        "freigabe_noetig": befund.freigabe_noetig,
                        "neue_ohne_links": befund.neue_ohne_links,
                    },
                ):
                    # Nicht gebucht heisst: Der naechste Schritt ist derselbe.
                    # Weiterzumachen hiesse, dieselbe Seite erneut zu holen -
                    # und zwar bis zum Schleifenwaechter.
                    return (
                        "Abgebrochen: Ein Regelbefund liess sich nicht buchen. "
                        "Ein zweiter Anlauf laese dieselbe Seite noch einmal."
                    )
                if befund.gelesen:
                    console.print(
                        f"[green]  gelesen:[/green] "
                        f"{befund.zusammenfassung() or 'nichts verboten'}"
                    )
                else:
                    console.print("[yellow]  Seite nicht lesbar - uebersprungen[/yellow]")
                getan += 1
                if max_schritte and getan >= max_schritte:
                    console.print(f"[dim]Grenze von {max_schritte} Schritten erreicht.[/dim]")
                    return f"{getan} Schritt(e) ausgefuehrt, Grenze erreicht."
                continue

            if art == "beitritt":
                ausgang, bemerkung = (
                    beitreten(s["gruppen_url"])
                    if beitreten is not None
                    else ("fehler", "dieser Rechner stellt keine Beitrittsanfragen")
                )
                console.print(f"[bold]{s['gruppe_name']}[/bold] - Beitrittsanfrage")
                if not _melde(
                    klient,
                    f"{basis}/automatik/beitritt/ergebnis",
                    {
                        "group_id": s["group_id"],
                        "campaign_id": s["campaign_id"],
                        "ausgang": str(ausgang),
                        "bemerkung": bemerkung,
                    },
                ):
                    # Konnte der Ausgang nicht gebucht werden, ist der
                    # naechste Schritt derselbe. Weiterzumachen hiesse, eine
                    # zweite Anfrage an dieselbe Gruppe zu stellen.
                    return (
                        "Abgebrochen: Der Ausgang einer Beitrittsanfrage liess sich "
                        "nicht buchen. Ein zweiter Anlauf koennte dieselbe Anfrage "
                        "wiederholen."
                    )
                if ausgang in ("angefragt", "bereits_mitglied"):
                    console.print(f"[green]  {ausgang}[/green]")
                else:
                    console.print(f"[yellow]  {ausgang}: {bemerkung or 'ohne Angabe'}[/yellow]")
                if technik.melde(
                    Schrittergebnis(
                        erfolg=ausgang in ("angefragt", "bereits_mitglied"),
                        fehler=bemerkung,
                    )
                ):
                    return technik.meldung()
                getan += 1
                if max_schritte and getan >= max_schritte:
                    console.print(f"[dim]Grenze von {max_schritte} Schritten erreicht.[/dim]")
                    return f"{getan} Schritt(e) ausgefuehrt, Grenze erreicht."
                continue

            # Der Zweck kommt vom Server, nicht aus einer Annahme dieses
            # Rechners: Welcher Schritt ansteht, entscheidet der Bestand.
            texttyp = s.get("texttyp") or "kommentar"
            console.print(
                f"[bold]{s['gruppe_name']}[/bold] - "
                f"{'Beitrag' if texttyp == 'post' else 'Kommentar'} "
                f"{s['kommentar_nr']}/{s['kommentar_ziel']} (Fassung {s['nummer']})"
            )
            if daten.get("fortschritt"):
                console.print(f"[dim]{daten['fortschritt'].splitlines()[2].strip()}[/dim]")

            # ``vorgaben`` traegt die Entscheidungsgrundlagen des Servers:
            # was die Gruppe erlaubt, was ein Beitrag dort hergeben muss und
            # welche Saetze in ihr schon standen. Ein Server ohne dieses Feld
            # (aelterer Stand) fuehrt zu den vorsichtigen Vorgaben - siehe
            # ``vorgaben_lesen``.
            ergebnis = ausfuehren(
                s["gruppen_url"],
                s["group_id"],
                s["text"],
                s["bisherige_post_urls"],
                texttyp,
                s.get("vorgaben") or {},
                s.get("link_url", ""),
            )

            if not _melde(
                klient,
                f"{basis}/automatik/ergebnis",
                {
                    "campaign_id": s["campaign_id"],
                    "group_id": s["group_id"],
                    "nummer": s["nummer"],
                    "texttyp": texttyp,
                    "erfolg": ergebnis.erfolg,
                    "fehler": ergebnis.fehler,
                    "post_url": ergebnis.post_url,
                    "erschoepft": ergebnis.erschoepft,
                    # **"Kein Anlass" ist kein Fehlschlag** - und der
                    # Fernbetrieb hat das bis zum 14.09.2026 nicht gesagt.
                    # Der Server buchte es als gescheiterten Versuch gegen die
                    # Fassung; nach dreien galt sie als verbraucht, und
                    # irgendwann die Gruppe als erschoepft. Genau die
                    # Verwechslung, an der am 11.09.2026 45 Gruppen zu
                    # Unrecht ausgeschieden sind - nur an einer anderen
                    # Stelle und ein Vierteljahr spaeter.
                    "kein_anlass": ergebnis.kein_anlass,
                    # **Diese Gruppe fuer diesen Lauf beiseitelegen.** Der
                    # Ausgang wird trotzdem gebucht - er gehoert ins
                    # Protokoll -, aber der Server bietet die Gruppe nicht
                    # gleich wieder an. Ohne dieses Feld lief der Lauf am
                    # 14.09.2026 in derselben Gruppe im Kreis.
                    "gruppe_beiseite": ergebnis.gruppe_beiseite,
                    # **Eine tote Adresse ist kein Fehler der Gruppe.** Ohne
                    # dieses Feld schloss der Server sie trotzdem aus der
                    # Kampagne aus: Er sah nur ``gruppe_beiseite`` und konnte
                    # "hier nimmt niemand einen Kommentar an" nicht von "diese
                    # drei Beitraege gibt es nicht mehr" unterscheiden. Der
                    # oertliche Lauf las es seit dem 20.09.2026, der
                    # Fernbetrieb - der Regelfall - meldete es nicht einmal.
                    "beitrag_weg": ergebnis.beitrag_weg,
                    # Damit der Server die Gruppe fuer **diesen** Lauf
                    # beiseitelegen kann, wie es der oertliche Lauf tut.
                    "lauf_id": s.get("lauf_id", 0),
                    # **Die Kennung, nicht der Text.** Der Fernbetrieb waehlt
                    # seinen Kommentar seit dem 14.09.2026 selbst (nach dem
                    # Anlass des gelesenen Beitrags); ohne diese Zeile stuende
                    # im Bestand weiter die vorbereitete Fassung, und die
                    # Uebersicht zeigte einen Satz, der nie abgesetzt wurde.
                    # Der Text selbst reist nicht zurueck - der Server baut
                    # ihn aus der Kennung neu (``vorlagen.anlasstext_zu``).
                    "vorlage_key": ergebnis.vorlage_key,
                    # Ob der abgesetzte Satz den Link trug - derselbe Vorrat
                    # ergibt mit und ohne zwei verschiedene Texte. Gelesen am
                    # Platzhalter und nicht am Code: Der Arbeitsrechner sieht
                    # den fertigen Text, der Server den gespeicherten.
                    "mit_link": "{link}" in ergebnis.text
                    or bool(ergebnis.text and s.get("tracking_code", "") in ergebnis.text),
                },
            ):
                # Ein gebuchter Ausgang ist die Grundlage des naechsten
                # Schrittes. Laesst er sich nicht buchen, boete der Server
                # denselben Kommentar erneut an - und der stuende dann zweimal
                # in der Gruppe. Das ist kein Fall fuer "weitermachen".
                return (
                    "Abgebrochen: Ein Ausgang liess sich nicht buchen. Der Beitrag "
                    "ist moeglicherweise heraus - vor dem naechsten Lauf pruefen "
                    "(fbgroups campaign abgleich --server ...)."
                )

            if ergebnis.erfolg:
                console.print("[green]  veroeffentlicht (auf dem Server gebucht)[/green]")
            elif ergebnis.gruppe_beiseite:
                console.print(
                    f"[yellow]  [Ergebnis] technisch fehlgeschlagen: "
                    f"{ergebnis.fehler}[/yellow]"
                )
                console.print("[dim]  [Aktion] naechste Gruppe[/dim]")
            elif ergebnis.kein_anlass:
                # Nicht rot: Es ist ein Ergebnis. Der Grund steht trotzdem
                # da - "Platzhalter nicht aufgeloest" liest sich anders als
                # "kein passender Beitrag", und der Unterschied entscheidet,
                # ob jemand nachsehen muss.
                console.print(f"[dim]  kein Anlass: {ergebnis.fehler}[/dim]")
            else:
                console.print(f"[red]  fehlgeschlagen: {ergebnis.fehler}[/red]")

            # Derselbe Waechter wie oertlich: Ein toter Browser ist kein Fall
            # fuer die Fehlerisolierung je Gruppe - er betrifft alle. Ein
            # "kein Anlass" gehoert nicht dazu: Der Browser arbeitet ja. Eine
            # tote Adresse ebenso wenig - sie sagt nichts ueber den Rechner,
            # und oertlich stand diese Ausnahme schon.
            if (
                not ergebnis.kein_anlass
                and not ergebnis.beitrag_weg
                and technik.melde(ergebnis)
            ):
                return technik.meldung()

            getan += 1
            if max_schritte and getan >= max_schritte:
                console.print(f"[dim]Grenze von {max_schritte} Schritten erreicht.[/dim]")
                letzte_meldung = f"{getan} Schritt(e) ausgefuehrt, Grenze erreicht."
                break

    return letzte_meldung


def _melde(klient, adresse: str, nutzlast: dict) -> bool:
    """Einen Ausgang buchen - mit einem zweiten Anlauf. Returns: ob es gelang.

    Ein Versuch, der hinausging, aber nicht gebucht wurde, ist der teuerste
    Zustand des Projekts: Der Server bietet denselben Schritt erneut an, und
    derselbe Text stuende zweimal in derselben Gruppe. Deshalb wird
    wiederholt, und deshalb faellt der Lauf hinterher aus - nicht, weil es
    ein Fehler *einer Gruppe* waere, sondern weil hier niemand mehr weiss,
    was geschehen ist.
    """
    for versuch in (1, 2):
        try:
            klient.post(adresse, json=nutzlast).raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001 - die Meldung, nicht die Handlung
            console.print(
                f"[red]  Buchung fehlgeschlagen ({str(exc).splitlines()[0][:80]})[/red]"
            )
            if versuch == 2:
                return False
            _schlafe(3.0)
    return False


def vorgaben_lesen(vorgaben: dict | None):  # noqa: ANN201 - (Erlaubnis, Anspruch, set)
    """Aus der Nutzlast des Servers die Entscheidungsgrundlagen bauen.

    Die Gegenseite von ``web``: Dort werden sie aus dem Bestand gerechnet,
    hier wieder zusammengesetzt. Uebertragen werden **Wahrheitswerte und
    Kennungen**, kein Datensatz - dieselbe Sparsamkeit wie beim Regelbefund.

    **Fehlt die Angabe, gilt die vorsichtige Vorgabe.** Ein aelterer Server,
    der ``vorgaben`` noch nicht mitschickt, fuehrt damit zu ``links=False``
    und ``werbung=False``: Aus nichts entsteht keine Erlaubnis. Das ist
    derselbe Grundsatz wie bei ``Erlaubnis`` ohne gelesene Regeln - und hier
    besonders wichtig, weil ein Arbeitsrechner und ein Server verschiedene
    Staende haben koennen.
    """
    from fbgroups.marketing.inhalt import Relevanz

    vorgaben = vorgaben or {}
    roh_erlaubnis = vorgaben.get("erlaubnis") or {}
    erlaubnis = entscheidung_modul.Erlaubnis(
        kommentare=bool(roh_erlaubnis.get("kommentare", True)),
        beitraege=bool(roh_erlaubnis.get("beitraege", True)),
        links=bool(roh_erlaubnis.get("links", False)),
        werbung=bool(roh_erlaubnis.get("werbung", False)),
        privatkontakt=bool(roh_erlaubnis.get("privatkontakt", True)),
        regeln_gelesen=bool(roh_erlaubnis.get("regeln_gelesen", False)),
    )

    roh_anspruch = vorgaben.get("anspruch") or {}
    try:
        stufe = Relevanz(str(roh_anspruch.get("mindestrelevanz", "mittel")))
    except ValueError:
        stufe = Relevanz.MITTEL
    anspruch = entscheidung_modul.Anspruch(
        mindestrelevanz=stufe,
        verlangt_strecke=bool(roh_anspruch.get("verlangt_strecke", False)),
        # **Der Server entscheidet, nicht dieser Rechner.** Dieselbe Regel
        # wie bei der Mitgliedschaftspflicht: Der Stand liegt dort, also
        # liegt auch der Schalter dort. Ein aelterer Server sendet das Feld
        # nicht - dann gilt die vorsichtige Vorgabe.
        anlass_pflicht=bool(roh_anspruch.get("anlass_pflicht", True)),
    )
    return erlaubnis, anspruch, set(vorgaben.get("verbrauchte_vorlagen") or ())


def browser_schritt_fern(
    context,
    gruppen_url: str,
    group_id: str,
    text: str,
    bisherige: list[str],
    vorgaben: dict | None = None,
    link_url: str = "",
) -> Schrittergebnis:
    """Wie ``browser_schritt``, aber ohne jeden Datenbankzugriff.

    ``bisherige`` und ``vorgaben`` kommen vom Server mit; auf diesem Rechner
    steht kein Bestand, der befragt werden koennte - und genau das ist
    beabsichtigt.

    **Dieselbe Kette wie oertlich** (14.09.2026). Hier stand bis dahin:

        bester = max(offen, key=lambda p: p["interactions"] + p["comments"])
        return _ausgang(comment_on_post(context, bester["post_url"], text), ...)

    Der **lauteste** Beitrag, der vorbereitete Text, kein Blick auf den
    Inhalt und keiner auf die Regeln der Gruppe. Wer den Fernbetrieb faehrt -
    und das ist der Regelfall - hatte damit einen Runner, der genau die
    Pruefungen ausliess, die ``campaign pruefe-inhalt`` vorfuehrt: Der
    Kommentar ueber Paketmitnahme landete unter dem Wohnungsgesuch mit
    hundert Reaktionen.
    """
    from fbgroups.automation.actions import comment_on_post, fetch_top_posts

    roh = fetch_top_posts(context, gruppen_url, group_id, limit=10)
    if not roh:
        return Schrittergebnis(
            erfolg=False, fehler="keine Beitraege zum Kommentieren gefunden", erschoepft=True
        )

    erlaubnis, anspruch, verbrauchte = vorgaben_lesen(vorgaben)
    if not erlaubnis.kommentare:
        # Die Gruppe laesst laut ihren gelesenen Regeln keine Kommentare zu.
        # Das ist kein Fehlschlag und kein Urteil ueber den Beitrag - es ist
        # die Regel der Gruppe, und sie bindet ohne Schalter.
        return Schrittergebnis(
            erfolg=False,
            fehler="die Gruppe laesst keine Kommentare zu (gelesene Regeln)",
            kein_anlass=True,
        )

    return entscheide_und_kommentiere(
        context,
        _config_fuer_fern(),
        roh,
        group_id,
        text,
        kommentieren=comment_on_post,
        bisherige=bisherige,
        erlaubnis=erlaubnis,
        anspruch=anspruch,
        verbrauchte_vorlagen=verbrauchte,
        link_url=link_url,
    )


def _config_fuer_fern() -> AppConfig:
    """Die Konfiguration dieses Rechners - fuer die Vorlagen, nicht den Bestand.

    ``entscheide_und_kommentiere`` braucht sie nur, um den Anlasstext zu
    waehlen (``config/textvorlagen.yaml``, ``marketing.linkmodus_max``,
    ``marketing.anlass_pflicht``). Der **Bestand** wird davon nicht
    angefasst - alles, was aus ihm kaeme, steht in ``vorgaben``.

    Sie kommt aus derselben Datei wie oertlich: Arbeitsrechner und Server
    fahren dasselbe Repository (``ausrollen.sh`` uebertraegt ``config/``).
    Liefen sie auseinander, waere der Vorlagentopf verschieden - und dann
    stuende in einer Gruppe ein Text, den der Server nie gesehen hat.
    """
    from fbgroups.config import load_config

    return load_config()


def browser_schritt_post(context, gruppen_url: str, text: str) -> Schrittergebnis:
    """Den eigenen Beitrag in der Gruppe absetzen - ohne jeden Datenbankzugriff.

    Das Gegenstueck zu ``browser_schritt_fern``: Dort wird ein fremder
    Beitrag gesucht und kommentiert, hier ein eigener geschrieben. Deshalb
    gibt es hier weder ``bisherige`` noch ``erschoepft`` - ein eigener
    Beitrag braucht keinen fremden, unter den er passt, und "hier gibt es
    nichts mehr zu holen" kann es fuer ihn nicht geben.

    ``post_url`` bleibt leer: ``post_to_group`` meldet Erfolg oder
    Misserfolg, nicht die Adresse des entstandenen Beitrags. Eine geratene
    Adresse waere schlimmer als keine - sie wanderte in
    ``bisherige_post_urls`` und spaerre einen fremden Beitrag fuer den
    Kommentarschritt aus.
    """
    from fbgroups.automation.actions import post_to_group

    ausgang = post_to_group(context, gruppen_url, text)
    if ausgang.erfolg:
        # Ein Erfolg **mit Vorbehalt** wird als solcher gemeldet. Steht die
        # nackte Adresse im Beitrag, weil die Vorschaukarte ohne sie nicht
        # gehalten hat, ist das kein Fehlschlag - der Beitrag steht, sein Link
        # wird gezaehlt -, aber es ist auch nicht das, was vorgesehen war.
        # Das im Protokoll zu verschweigen waere dieselbe Art stiller Erfolg,
        # die ``comment_on_post`` bis zum 12.09.2026 gemeldet hat.
        return Schrittergebnis(erfolg=True, fehler=ausgang.hinweis)
    return Schrittergebnis(
        erfolg=False,
        fehler=ausgang.hinweis or "Beitragsformular nicht gefunden oder blockiert",
    )


__all__ = [
    "Schrittergebnis",
    "aktionslage",
    "aktive_kampagnen",
    "browser_schritt",
    "browser_schritt_fern",
    "browser_schritt_post",
    "fuehre_lauf_aus",
    "fuehre_lauf_fern_aus",
    "hole_oder_starte_lauf",
    "merke_bremse",
    "entscheide_und_kommentiere",
    "kommentare_zuerst",
    "ruhe_minuten",
    "ruhesekunden",
    "vorgaben_lesen",
    "waehle_und_kommentiere",
    "wartesekunden",
]
