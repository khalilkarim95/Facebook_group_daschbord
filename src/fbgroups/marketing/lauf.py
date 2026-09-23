"""Die Kommentarautomatik: Kampagne fuer Kampagne, Gruppe fuer Gruppe.

## Was dieses Modul ist - und was nicht

``arbeit.py`` haelt **einen Schritt**: einen Auftrag holen, ein Ergebnis
melden. Kommandozeile und Weboberflaeche rufen ihn seit jeher auf. Dieses
Modul ist die **Schleife darueber** - es beantwortet allein die Frage "was
kommt als naechstes?" und fuehrt selbst nichts aus.

Die Trennung ist dieselbe wie zwischen ``scoring.py`` und den Modulen, die
Zahlen beschaffen: Hier steht keine Zeile Playwright, kein ``webbrowser``,
kein Netz. Der Treiber (``campaign automatik``, spaeter die Weboberflaeche)
holt sich den naechsten Schritt, fuehrt ihn aus und meldet zurueck.

## Wo der Fortschritt steht

**Nicht hier.** Welche Fassung einer Gruppe veroeffentlicht ist, steht in
``campaign_group_texte.status`` - dort steht es, seit es Fassungen gibt. Ein
eigener Zaehler daneben waere eine zweite Wahrheit ueber dieselbe Zahl, und
die beiden liefen beim ersten Abbruch auseinander.

Der Fortschritt wird deshalb **gelesen, nicht gefuehrt**. Das hat eine
angenehme Folge: Ein Abbruch braucht keine Aufraeumarbeit. Wer den Lauf
mitten in Gruppe 7 abwuergt, findet beim naechsten Start genau die Fassungen
offen, die noch nicht heraus sind - weil nie etwas anderes behauptet wurde.

Gespeichert wird nur, was sich **nicht** ableiten laesst:

* dass gerade ein Lauf im Gange ist (``automatik_lauf``),
* welche Kampagnen zu ihm gehoeren (``automatik_lauf_kampagnen``),
* und dass eine Gruppe nichts mehr hergibt (``kommentar_erschoepft``).

## Die Vorlagenwahl ist keine Rotation, sondern eine Folge

Gefordert war ein Wechsel 1-2-3-4-5. Herausgekommen ist die einfachere
Regel: **die kleinste Nummer, die noch nicht veroeffentlicht ist.** Sie
ergibt genau diese Folge, braucht aber keinen gespeicherten Zeiger und
ueberlebt jeden Abbruch - nach einem Neustart in Gruppe 7 bei Kommentar 3
steht Fassung 3 immer noch als naechste da. Ein Zaehler haette hier gemerkt
werden muessen, und ein gemerkter Zaehler kann von der Wirklichkeit abweichen.

Fuenf Fassungen, fuenf Kommentare, jede genau einmal: Kein Text geht zweimal
in dieselbe Gruppe. Zwei wortgleiche Kommentare untereinander sind das
deutlichste Zeichen einer Maschine, das man hinterlassen kann.

## Warum die Kampagnenliste eingefroren wird

Ein Lauf arbeitet die Liste ab, die beim Start galt. Wer waehrend des Laufs
eine Kampagne auf ``active`` setzt, greift nicht in den laufenden Vorgang
ein - sie kommt beim naechsten Start dran. Sonst waere "alle aktiven
Kampagnen" bei jedem Schritt eine andere Menge, und der Lauf koennte nie
fertig werden, weil die Bedingung unter ihm wegwandert.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field, replace
from enum import StrEnum

from fbgroups.marketing.grenzen import Aktion, Lage
from fbgroups.marketing.models import (
    KampagnenLaufStatus,
    LaufStatus,
    PostStatus,
    Texttyp,
)

# Zehn Kommentare je Gruppe, aus fuenf Vorlagen. Die beiden Zahlen sind
# **nicht** dasselbe, und genau deshalb stehen sie getrennt:
#
#   MAX_VORSCHLAEGE  = 5   die Zahl der *Texte*
#   ZIEL_JE_GRUPPE   = 10  die Zahl des *Vorhabens*
#
# Fassung 6 traegt wieder Vorlage 1, Fassung 7 wieder Vorlage 2 und so fort
# (``vorlage_zu_nummer``). Zwei gleiche Texte gehen dabei nie unter denselben
# Beitrag: ``bisherige_post_urls`` sorgt dafuer, dass jeder Kommentar einen
# anderen Beitrag bekommt.
ZIEL_JE_GRUPPE = 10

# Wie viele verschiedene Texte im Topf stehen. Aus ihnen wird gedreht.
VORLAGEN_JE_TOPF = 5

# Wie oft eine einzelne Fassung in einem Lauf wiederholt wird, bevor der Lauf
# sie liegenlaesst und weitergeht. Drei, weil der haeufigste Fehlschlag ein
# voruebergehender ist (Seite langsam, Element noch nicht da) und der
# zweithaeufigste ein dauerhafter (Kommentare abgeschaltet) - beim dritten Mal
# ist die Unterscheidung getroffen.
MAX_VERSUCHE_JE_FASSUNG = 3

# --- Drei Gruende, und nur einer davon ist ein Urteil ueber die Gruppe ------
#
# Am 11.09.2026 endete ein Lauf mit "45 Gruppe(n) erschoepft" und vier
# Kommentaren. Erschoepft waren sie nicht: Es war fuer 44 von ihnen nie ein
# Text erzeugt worden. Der Lauf traf damit eine Aussage ueber **die Gruppe**
# ("gibt nichts mehr her"), obwohl die Luecke bei **uns** lag - und weil der
# Vermerk gespeichert wird, war sie danach dauerhaft uebersprungen.
#
# Seitdem zieht der Lauf fehlende Fassungen selbst nach
# (``automatik.texte_sicherstellen``). Die beiden alten Gruende sind deshalb
# keine mehr und werden beim **Lesen** uebergangen - dieselbe Trennung wie bei
# ``drop_shared_snippets``: verworfen wird bei der Auswertung, nie im
# Bestand. Die Zeilen bleiben stehen; was einmal geschah, wird nicht
# umgeschrieben.
OHNE_KOMMENTARTEXT = "kein Kommentartext vorhanden"
OHNE_BEITRAGSTEXT = "kein Beitragstext vorhanden"

# Bleibt die Gruppe auch **nach** dem Nachziehen ohne Text, dann fehlt die
# Vorlage und nicht der Handgriff. Dieser Grund wird ernst genommen - sonst
# liefe der Lauf im Kreis: kein Text, nachziehen, wieder kein Text.
KEINE_VORLAGE = "keine Vorlage fuer diese Gruppe"


# --- Fortschritt -----------------------------------------------------------
@dataclass(frozen=True)
class Gruppenfortschritt:
    """Wie weit **eine** Gruppe in **einer** Kampagne ist.

    ``erschoepft`` ist der dritte Ausgang neben "voll" und "offen": Die Gruppe
    gibt nicht mehr her - zu wenige Beitraege zum Kommentieren, Kommentare
    abgeschaltet, kein Zugang. Ohne diesen Zustand haengt eine Kampagne fuer
    immer bei vier von fuenf, weil eine einzige Gruppe nur vier Beitraege hat.
    """

    campaign_id: str
    group_id: str
    name: str
    veroeffentlicht: int
    ziel: int = ZIEL_JE_GRUPPE
    erschoepft: bool = False
    erschoepft_grund: str = ""
    gescheiterte_fassungen: frozenset[int] = field(default_factory=frozenset)

    mitglied: bool = False
    """Ist das Konto Mitglied dieser Gruppe?

    **Die Vorbedingung fuer alles Uebrige.** Facebook laesst Nichtmitglieder
    in den meisten Gruppen weder posten noch kommentieren; ein Versuch dort
    scheitert nicht zufaellig, sondern immer. Am 31.08.2026 waren von 36
    zugeordneten Gruppen **null** auf ``mitglied`` - ein Lauf haette 180
    Versuche gemacht, die alle fehlschlagen mussten, und genau diese Folge
    aus einem Konto ist das Muster, das zur Sperre fuehrt.

    Der Weg dorthin ist Handarbeit und bleibt es: ``marketing beitritt``
    schreibt mit, was ein Mensch im Browser getan hat. Die Automatik wartet.
    """

    @property
    def offen(self) -> int:
        return max(self.ziel - self.veroeffentlicht, 0)

    @property
    def voll(self) -> bool:
        """Alle vorgesehenen Kommentare sind heraus."""
        return self.offen == 0

    mitgliedschaft_noetig: bool = True
    """Gilt die Mitgliedschaft hier als Vorbedingung?

    Vom Nutzer am 01.09.2026 abschaltbar gemacht (``automatik.
    mitgliedschaft_pflicht`` in ``settings.yaml``): In der Gruppe
    ``Betaraqiq-Test Syrer in Berlin`` standen drei veroeffentlichte
    Kommentare, waehrend der Stand auf ``beitritt_angefragt`` stand - dort
    laesst Facebook also schreiben, und die Sperre hielt einen Lauf auf, der
    nachweislich moeglich war.

    Ausgeschaltet wird die Gruppe **versucht**, nicht als Mitgliedschaft
    behauptet: ``mitglied`` bleibt die beobachtete Angabe. Scheitert der
    Versuch, greift dieselbe Grenze wie sonst -
    ``MAX_VERSUCHE_JE_FASSUNG`` und danach ``erschoepft``.
    """

    post_status: PostStatus = PostStatus.OFFEN
    """Steht der eigene Beitrag dieser Gruppe schon - oder ist er gescheitert?

    Der Beitrag geht dem Kommentar **voraus**: Ein Kommentar unter einem
    fremden Beitrag erreicht die Leser dieses Beitrags, der eigene Beitrag
    die Gruppe. Wer beides will, faengt mit dem eigenen an.

    Gelesen wird der Stand aus ``campaign_groups.post_status`` - dieselbe
    Spalte, die auch die Arbeitsseite fuellt. Ein von Hand abgesetzter
    Beitrag wird deshalb nicht ein zweites Mal abgesetzt.
    """

    post_fassungen: frozenset[int] = frozenset()
    """Welche Beitragsfassungen es fuer diese Gruppe ueberhaupt gibt.

    Leer heisst: kein Beitragstext erzeugt. Dann wird auch keiner versucht -
    ein Schritt ohne Text koennte nur scheitern.
    """

    beitritt_noetig: bool = False
    """Steht fuer diese Gruppe noch eine Beitrittsanfrage aus?

    Der **erste** Schritt des Ablaufs (12.09.2026): Zuerst die Gruppen der
    Kampagne, dann ihre Beitrittsanfragen, dann die Bewertung, dann die
    Arbeit. Wahr heisst: kein Mitglied, keine Anfrage gestellt, die Gruppe
    wird bearbeitet und hat eine Adresse.

    Ob daraus wirklich ein Schritt wird, entscheidet nicht die Gruppe,
    sondern die Tagesmenge (``Lauffortschritt.beitritt_kontingent``): Die
    Beitrittsanfrage ist die riskanteste Handlung des Projekts, und ihr Takt
    gilt ueber alle Kampagnen zusammen.
    """

    uebersprungen: bool = False
    ruht: bool = False
    """Beiseite **auf Zeit** - die Gruppe kommt von selbst zurueck.

    Steht neben ``uebersprungen`` und nicht darin: Beide fallen gerade aus,
    aber nur bei diesem hier ist der Ausfall eine Frage der Uhr. Der
    Unterschied entscheidet ueber den Kampagnenwechsel - siehe
    ``Lauffortschritt.naechste_kampagne``.
    """
    """In **diesem** Lauf beiseitegelegt - meist nach einem Fehlschlag.

    Die Fehlerisolierung je Gruppe. Ein Browser, der mitten im Schritt
    abbricht, eine Gruppenseite, die sich nicht lesen laesst, eine
    Textherstellung, die wirft: Die Gruppe kostet das diesen Durchgang, nicht
    mehr. Der Vermerk haengt an der ``lauf_id`` und ist mit dem naechsten Lauf
    weg.

    **Ausdruecklich kein Urteil ueber die Gruppe.** ``erschoepft`` sagt "gibt
    nichts mehr her" und bleibt stehen; hier steht "heute ging es nicht".
    """

    uebersprungen_grund: str = ""
    """Warum sie beiseiteliegt - nie das eine ohne das andere."""

    bezuege: tuple[str, ...] = ()
    """Die Bezuege, die in den Beitraegen dieser Gruppe erkannt wurden.

    Seit dem 23.09.2026 die Grundlage, auf der eine Gruppe beurteilt wird -
    an Stelle der Zielklassen ``A``-``D``, der Region und der Note
    (``bezug.fuer_gruppe``). **Sie entscheidet hier noch nichts**: Leer
    heisst "spaeter definierte Sonderbehandlung", und die ist ausdruecklich
    noch nicht festgelegt. Bis dahin werden alle Gruppen gleich behandelt.
    """

    heute_in_gruppe: int = 0
    """Wie viele Versuche heute schon in **dieser** Gruppe standen."""

    gruppenlimit: int = 0
    """Wie viele an einem Tag hoechstens - ``0`` heisst ohne Schranke.

    Kommt aus ``grenzen.Grenze.je_gruppe_taeglich``. Die Zahl steht hier und
    nicht in der Rechnung, damit die Anzeige sie nennen kann, ohne die
    Konfiguration zu lesen.
    """

    @property
    def tageslimit_erreicht(self) -> bool:
        """Nimmt diese Gruppe heute nichts mehr an?

        **Kein Urteil ueber die Gruppe**, sondern ueber unseren Takt - und
        deshalb weder ``erschoepft`` noch ``gesperrt``: Morgen ist sie
        unveraendert offen. Sie wirkt wie ein Uebersprung, nur ohne Vermerk;
        der waere eine Erklaerung fuer etwas, das sich von selbst aufloest.
        """
        return 0 < self.gruppenlimit <= self.heute_in_gruppe

    @property
    def post_offen(self) -> bool:
        """Steht der Beitrag dieser Gruppe noch aus?

        **Genau ein Anlauf je Lauf**, und das ist Absicht: Scheitert der
        Beitrag (Gruppe erlaubt keine Links, Formular nicht gefunden), setzt
        ``melde_vorschlag`` das Paar auf ``fehlgeschlagen``, und der Lauf
        geht zu den Kommentaren weiter, statt an derselben Stelle drei
        Anlaeufe zu verbrauchen. Zurueckgeholt wird ein Fehlschlag mit
        ``campaign retry`` - also von Hand, wie jede andere Entscheidung.
        """
        return self.post_status is PostStatus.OFFEN and bool(self.post_fassungen)

    @property
    def post_nummer(self) -> int | None:
        """Welche Beitragsfassung abgesetzt wird - die kleinste vorhandene."""
        return min(self.post_fassungen) if self.post_offen else None

    @property
    def kommentare_fertig(self) -> bool:
        """Die Kommentare sind durch - erreicht oder nachweislich am Ende."""
        return self.voll or self.erschoepft

    @property
    def wartet(self) -> bool:
        """Noch keine Mitgliedschaft - die Gruppe ist blockiert, nicht erledigt.

        Ohne Mitgliedschaftspflicht wartet nichts: Dann ist eine Gruppe ohne
        Mitgliedschaft offen wie jede andere.
        """
        return self.mitgliedschaft_noetig and not self.mitglied and not self.fertig

    @property
    def bearbeitbar(self) -> bool:
        """Darf die Automatik hier ueberhaupt etwas versuchen?

        Vier Gruende sprechen dagegen, und sie bedeuten Verschiedenes: heute
        schon genug in dieser Gruppe (Takt), keine Mitgliedschaft
        (blockiert), in diesem Lauf fehlgeschlagen (uebersprungen) oder
        schlicht erledigt (fertig). Nur der letzte ist ein Erfolg.

        Die Qualifikation (Gruppenregeln, Sperre nach wiederholter
        Ablehnung) ist am 23.09.2026 entfallen - Anweisung des Nutzers.

        Der sechste Grund - die Zielklasse ``D`` - ist am 23.09.2026
        entfallen: Beurteilt wird eine Gruppe seither nach den Bezuegen in
        ihren Beitraegen (``bezuege``), und was eine Gruppe ohne Bezug
        erfaehrt, ist noch nicht festgelegt.
        """
        return (
            # Seit dem 23.09.2026 steht hier keine Zielklasse und keine Note
            # mehr: Bis die Behandlung der Gruppen ohne Bezug festgelegt ist,
            # wird jede zugeordnete Gruppe gleich behandelt.
            # **Die Tagesmenge je Gruppe gilt dem Kommentar, nicht der
            # Gruppe** (15.09.2026). Sie kommt aus
            # ``limits.comments.je_gruppe_taeglich`` und wird aus den
            # Kommentarversuchen gezaehlt - sie hier auf die ganze Gruppe
            # anzuwenden sperrte auch ihren **Beitrag**, und damit galt
            # wieder, was Punkt 8 der Anforderung vom 12.09.2026
            # ausdruecklich ausschliesst: "Ein Limit fuer eine Aktion ist
            # keines fuer die andere."
            #
            # Bei ``je_gruppe_taeglich: 1`` war das keine Feinheit: Der erste
            # Kommentar nahm der Gruppe den Beitrag fuer denselben Tag.
            (not self.tageslimit_erreicht or self.post_offen)
            and (self.mitglied or not self.mitgliedschaft_noetig)
            and not self.uebersprungen
            and not self.fertig
        )

    @property
    def fertig(self) -> bool:
        """Nichts mehr zu tun - entweder voll oder nachweislich am Ende.

        Beides zaehlt als abgeschlossen, aber nur eines davon als Erfolg; die
        Abschlussmeldung weist sie deshalb getrennt aus.

        Eine wartende Gruppe ist **nicht** fertig. Sie fehlt nicht wegen
        unserer Daten, sondern weil die Arbeit dort noch nicht getan werden
        *kann* - und eine Kampagne, die daraufhin "erfolgreich abgeschlossen"
        meldete, behauptete Beitraege, die es nicht gibt.

        Seit dem Beitragsschritt gehoert er dazu: Eine Gruppe mit zehn
        Kommentaren, aber ohne den eigenen Beitrag, ist nicht abgearbeitet.
        """
        return self.kommentare_fertig and not self.post_offen


class Phase(StrEnum):
    """Wo eine Kampagne innerhalb ihres Ablaufs steht.

    Die Reihenfolge ist der Ablauf, und sie gilt **je Kampagne**: Erst gehen
    die Beitrittsanfragen an die Gruppen dieser Kampagne hinaus, dann wird
    gearbeitet - und erst danach kommt die naechste Kampagne.

    Die Abschnitte "Gruppenregeln lesen" und "Neubewertung" sind am
    23.09.2026 entfallen (Anweisung des Nutzers).
    """

    BEITRITT = "beitritt"
    ARBEIT = "arbeit"
    FERTIG = "fertig"


@dataclass(frozen=True)
class Kampagnenfortschritt:
    """Wie weit eine Kampagne ist - aus den Staenden ihrer Gruppen."""

    campaign_id: str
    name: str
    gruppen: list[Gruppenfortschritt]
    status: KampagnenLaufStatus = KampagnenLaufStatus.WARTET
    @property
    def gruppen_gesamt(self) -> int:
        return len(self.gruppen)

    @property
    def gruppen_ruhend(self) -> int:
        """Wie viele Gruppen gerade **auf Zeit** beiseite liegen.

        Sie sind der Grund, warum eine Kampagne ihren Platz behaelt, obwohl
        sie in diesem Augenblick nichts hergibt: Sie kommen von selbst
        zurueck (``automatik.ruhe_minuten``).
        """
        return sum(1 for g in self.gruppen if g.ruht)

    @property
    def gruppen_fertig(self) -> int:
        return sum(1 for g in self.gruppen if g.fertig)

    @property
    def gruppen_voll(self) -> int:
        return sum(1 for g in self.gruppen if g.voll)

    @property
    def gruppen_erschoepft(self) -> int:
        return sum(1 for g in self.gruppen if g.erschoepft and not g.voll)

    @property
    def gruppen_am_tageslimit(self) -> int:
        """Gruppen, die heute ihren Kommentar schon hatten.

        **Kein Urteil und kein Fehlschlag** - morgen sind sie unveraendert
        offen. Gezaehlt wird es trotzdem, und zwar seit dem 15.09.2026 aus
        einem genauen Anlass: Es ist der haeufigste Grund, warum ein Lauf
        endet, und er stand in der Abschlussmeldung nirgends. Dort las man
        "Offen bei: ... (1 / 10 Kommentare)" und darunter nur "post:
        Tagesmenge erreicht" - also sah es aus, als haette der Lauf die
        Kommentare grundlos liegenlassen.
        """
        return sum(
            1
            for g in self.gruppen
            if g.tageslimit_erreicht and not g.fertig and not g.uebersprungen
        )

    ziel_kommentare: int = 0
    """Wie viele erfolgreiche Kommentare diese Kampagne erreichen soll.

    ``0`` heisst **kein eigenes Ziel**: Dann gilt wie bisher allein, dass
    jede Gruppe ihre Fassungen veroeffentlicht hat.

    Gesetzt wird es in ``settings.yaml``
    (``marketing.kampagne.ziel_kommentare``, seit 21.09.2026 auf 100 -
    Anweisung des Nutzers: "Eine Kampagne bleibt aktiv, bis 100 erfolgreiche
    Kommentare erreicht wurden. Erst danach zur naechsten Kampagne
    wechseln."). Gezaehlt werden **veroeffentlichte** Fassungen aus
    ``campaign_group_texte.status`` - ein Fehlschlag bringt eine Kampagne
    damit nie naeher an ihre hundert.
    """

    @property
    def kommentare_veroeffentlicht(self) -> int:
        return sum(g.veroeffentlicht for g in self.gruppen)

    @property
    def kommentare_ziel(self) -> int:
        return sum(g.ziel for g in self.gruppen)

    @property
    def beitraege_veroeffentlicht(self) -> int:
        return sum(1 for g in self.gruppen if g.post_status is PostStatus.VEROEFFENTLICHT)

    @property
    def beitraege_ziel(self) -> int:
        """Nur Gruppen, fuer die es ueberhaupt einen Beitragstext gibt.

        Sonst stuende in der Meldung ein Ziel, das niemand erreichen kann.
        """
        return sum(
            1
            for g in self.gruppen
            if g.post_fassungen or g.post_status is PostStatus.VEROEFFENTLICHT
        )

    @property
    def fertig(self) -> bool:
        """Jede Gruppe ist durch - **und es gibt ueberhaupt Gruppen**.

        Die zweite Haelfte ist kein Zierrat: ``all()`` ueber eine leere Liste
        ist wahr, und eine Kampagne ohne Zuordnungen waere damit im selben
        Augenblick "erfolgreich abgeschlossen", in dem sie angelegt wurde.
        Genau diese Art falscher Erfolgsmeldung soll der Lauf nicht geben.
        """
        return bool(self.gruppen) and all(g.fertig for g in self.gruppen)

    @property
    def leer(self) -> bool:
        """Keine einzige Gruppe zugeordnet - hier ist nichts zu tun.

        **Nicht dasselbe wie fertig**, und die Trennung ist der Punkt:
        Erfolgreich abgeschlossen wird hier nichts, und die Kampagne wird
        auch nicht auf ``completed`` gesetzt (siehe ``_stand_fortschreiben``)
        - sie hat ja nie etwas bekommen. Aber sie darf den **Lauf** nicht
        aufhalten: Eine Kampagne, die geloescht wurde, waehrend der Lauf
        offen war, steht mit ihrer Kennung weiter in der eingefrorenen Liste
        und hat per CASCADE keine Zuordnungen mehr. Ohne diese
        Unterscheidung wurde der Lauf nie fertig, ``offener_lauf`` bot ihn
        bei jedem Start wieder an, und die Automatik meldete "0 / 1", ohne je
        etwas zu tun (10.09.2026).
        """
        return not self.gruppen

    @property
    def gruppen_wartend(self) -> int:
        """Gruppen ohne Mitgliedschaft - blockiert, nicht erledigt."""
        return sum(1 for g in self.gruppen if g.wartet)

    @property
    def gruppen_uebersprungen(self) -> int:
        """In diesem Lauf beiseitegelegt - meist nach einem Fehlschlag."""
        return sum(1 for g in self.gruppen if g.uebersprungen)

    @property
    def gruppen_ohne_bezug(self) -> int:
        """Gruppen, in deren Beitraegen kein Bezug erkannt wurde (``[]``).

        **Nur gezaehlt, nicht behandelt** (23.09.2026): Fuer sie ist eine
        eigene Behandlung vorgesehen, die noch nicht festgelegt ist. Bis dahin
        arbeitet der Lauf dort wie ueberall; die Zahl steht in der Meldung,
        damit sichtbar ist, wie viele es betrifft.
        """
        return sum(1 for g in self.gruppen if not g.bezuege)

    @property
    def beitritt_kandidaten(self) -> list[Gruppenfortschritt]:
        """Die Gruppen, an die eine Anfrage gehen soll - **vor** der Regelfrage.

        Getrennt von ``beitritt_offen``, weil die Regelpruefung dazwischen
        liegt: Hier stehen die Gruppen, die eine Anfrage verdienen; dort die,
        bei denen sie jetzt hinausgehen darf.
        """
        # **Keine Zielklasse mehr** (23.09.2026). Die Anfrage ging bis dahin
        # nur an die Klassen ``A`` und ``B``; die Klassen sind entfallen, und
        # welche Bezuege eine Anfrage rechtfertigen, ist noch nicht
        # festgelegt. Bis dahin ist sie **in der Konfiguration abgeschaltet**
        # (``limits.join_requests.daily: 0``) und nicht hier: Eine
        # Abschaltung gehoert in ``settings.yaml``, damit das Einschalten eine
        # Zahl ist und keine Codeaenderung.
        return [
            g
            for g in self.arbeitsliste
            if g.beitritt_noetig and not g.uebersprungen
        ]

    @property
    def beitritt_offen(self) -> list[Gruppenfortschritt]:
        """Die Gruppen dieser Kampagne, an die noch eine Anfrage gehen muss.

        Bis zum 22.09.2026 nur die Zielklassen ``A`` und ``B``, bis zum
        23.09.2026 nur mit gelesenen Gruppenregeln; seither jede Gruppe, der
        eine Anfrage fehlt. Abgeschaltet ist die Anfrage bis auf Weiteres in
        ``settings.yaml`` (``limits.join_requests.daily: 0``).
        """
        return self.beitritt_kandidaten

    @property
    def arbeitsliste(self) -> list[Gruppenfortschritt]:
        """Die Gruppen in der Reihenfolge, in der gearbeitet wird.

        Die Reihenfolge, in der die Liste hereinkam: ``sort_by_rank``, also
        der Score. Bis zum 22.09.2026 standen davor Note, Zielklasse
        (``A``-``D``) und Region, bis zum 23.09.2026 der Vorrang aus der
        Qualifikation (was die Gruppe erlaubt). Beides ist entfallen; wie die
        Bezuege einer Gruppe die Reihenfolge bestimmen, ist noch nicht
        festgelegt.
        """
        return list(self.gruppen)

    @property
    def naechste_gruppe(self) -> Gruppenfortschritt | None:
        """Die erste Gruppe, an der **gearbeitet werden kann**.

        Ohne Mitgliedschaft wird uebersprungen, nicht versucht: Ein Kommentar
        in einer Gruppe, in der das Konto nicht Mitglied ist, scheitert nicht
        zufaellig, sondern immer. Dasselbe gilt fuer eine Gruppe, die in
        diesem Lauf schon einmal fehlgeschlagen ist.

        Die Rangfolge kommt aus ``arbeitsliste``: beste qualifizierte zuerst.
        """
        return next((g for g in self.arbeitsliste if g.bearbeitbar), None)

    @property
    def naechste_kommentargruppe(self) -> Gruppenfortschritt | None:
        """Die erste Gruppe, in der **heute noch ein Kommentar** darf.

        Neben ``naechste_gruppe`` und aus einem genauen Grund (15.09.2026):
        Seit die Tagesmenge je Gruppe nur noch den Kommentar sperrt, kann
        ``naechste_gruppe`` eine Gruppe liefern, die **nur** noch ihren
        Beitrag offen hat. Ist der Beitrag gerade getaktet, stuende der Lauf
        vor ihr still - obwohl in der naechsten Gruppe ein Kommentar
        hinausgehen koennte.
        """
        return next(
            (g for g in self.arbeitsliste if g.bearbeitbar and not g.tageslimit_erreicht),
            None,
        )

    @property
    def abgeschlossen(self) -> bool:
        """Wirklich durch - **jede** Gruppe hat ihr Ziel erreicht.

        Der Unterschied zu ``fertig`` ist der Kern von Punkt 12 der
        Anforderung: ``fertig`` heisst "der Lauf versucht hier nichts mehr"
        und zaehlt eine erschoepfte Gruppe mit; ``abgeschlossen`` heisst
        "erreicht". Nur das Zweite rechtfertigt es, eine Kampagne dauerhaft
        auf ``completed`` zu setzen.

        Am 12.09.2026 stand genau dieser Unterschied im Bild: vier Kampagnen
        auf "FERTIG", 78 von 78 Gruppen durch, **null** Kommentare
        veroeffentlicht. Fertig war daran nur der Lauf.

        **Seit dem 21.09.2026 gibt es zwei Arten, erreicht zu sein**, und die
        erste ist die des Nutzers: hundert erfolgreiche Kommentare
        (``ziel_kommentare``). Die zweite ist die alte - jede Gruppe hat ihre
        zehn Fassungen veroeffentlicht. Sie bleibt daneben stehen, weil sie
        den Abschluss garantiert: Bei dreizehn Gruppen sind hoechstens 130
        Fassungen moeglich, und ohne die zweite Bedingung liefe eine
        Kampagne, deren Vorrat vor der Hundert endet, fuer immer weiter.
        """
        if not self.gruppen:
            return False
        if 0 < self.ziel_kommentare <= self.kommentare_veroeffentlicht:
            return True
        return all(g.voll and not g.post_offen for g in self.gruppen)

    def lauf_status(
        self, *, beitritt_frei: bool = False, gebremst: bool = False
    ) -> KampagnenLaufStatus:
        """Wo diese Kampagne **in diesem Lauf** steht - mit Grund, nicht nur "fertig".

        Die Reihenfolge der Pruefungen ist die der Aussagekraft: Ein
        erreichtes Ziel ist ein Ergebnis, eine Bremse eine Auskunft ueber
        heute, eine offene Beitrittsanfrage eine ueber naechste Woche. Wer
        alles drei ``fertig`` nennt, sagt nichts von alledem.
        """
        if self.leer:
            return KampagnenLaufStatus.FERTIG
        if self.abgeschlossen:
            return KampagnenLaufStatus.FERTIG
        if (
            self.naechste_gruppe is not None
            or (beitritt_frei and self.beitritt_offen)
        ):
            return KampagnenLaufStatus.LAEUFT
        if gebremst:
            return KampagnenLaufStatus.VORERST_GEBREMST
        if self.gruppen_wartend or self.beitritt_offen:
            return KampagnenLaufStatus.WARTET_AUF_BEITRITT
        return KampagnenLaufStatus.ERSCHOEPFT_VORERST

    def phase(self, *, beitritt_frei: bool = False) -> Phase:
        """In welchem Abschnitt des Ablaufs diese Kampagne gerade steht.

        ``beitritt_frei`` sagt, ob die Tagesmenge ueberhaupt noch eine Anfrage
        zulaesst - sie gilt ueber alle Kampagnen zusammen und kann deshalb
        nicht aus der Kampagne selbst kommen.
        """
        if self.fertig or self.leer:
            return Phase.FERTIG
        if beitritt_frei and self.beitritt_offen:
            return Phase.BEITRITT
        return Phase.ARBEIT


@dataclass(frozen=True)
class Lauffortschritt:
    """Der ganze Lauf ueber alle eingefrorenen Kampagnen."""

    lauf_id: int
    status: LaufStatus
    kampagnen: list[Kampagnenfortschritt]

    kommentare_zuerst: bool = False
    """Kommt in einer Gruppe der Kommentar vor dem Beitrag?

    Vorgabe ``False``: erst der eigene Beitrag, dann die zehn Kommentare
    (Regel vom 10.09.2026 - der Beitrag ist der Anlass und steht in der
    Gruppe, ein Kommentar haengt an einem fremden).

    ``True`` dreht die **Reihenfolge** um, nicht die Menge: Kommt gerade kein
    Kommentar zustande, geht der Beitrag hinaus. Anweisung des Nutzers vom
    21.09.2026 (*"Prioritaet Nr. 1 ist das Kommentieren"*), gesagt wird es in
    ``settings.yaml`` unter ``automatik.kommentare_zuerst`` - der Schalter
    steht dort und nicht hier, wie jeder andere auch.
    """

    aktionen: dict[Aktion, Lage] = field(default_factory=dict)
    """Was jede Aktion **jetzt** darf - Tagesmenge, Takt und Bremse zusammen.

    Gerechnet wird das in ``grenzen.pruefe`` und hereingereicht, weil es
    ueber **alle** Kampagnen gilt und aus ``settings.yaml`` stammt: Dieses
    Modul liest keine Konfiguration, so wie es kein Netz kennt.

    **Je Aktion getrennt**, und das ist der ganze Punkt (Anforderung vom
    12.09.2026, Punkte 7 und 8): Sind Kommentare gebremst, gehen Beitraege
    und Beitrittsanfragen weiter. Wer wegen einer Aktion alles anhaelt,
    verliert die Arbeit dort, wo nichts dagegen spricht.

    Ein fehlender Eintrag heisst **erlaubt**: Ein Aufrufer, der keine Grenzen
    kennt (Tests, die aeltere Anzeige), soll nicht alles gesperrt sehen.
    """

    @property
    def kampagnen_gesamt(self) -> int:
        return len(self.kampagnen)

    @property
    def kampagnen_fertig(self) -> int:
        return sum(1 for k in self.kampagnen if k.fertig)

    @property
    def gruppen_gesamt(self) -> int:
        return sum(k.gruppen_gesamt for k in self.kampagnen)

    @property
    def gruppen_fertig(self) -> int:
        return sum(k.gruppen_fertig for k in self.kampagnen)

    @property
    def kommentare_veroeffentlicht(self) -> int:
        return sum(k.kommentare_veroeffentlicht for k in self.kampagnen)

    @property
    def kommentare_ziel(self) -> int:
        return sum(k.kommentare_ziel for k in self.kampagnen)

    @property
    def beitraege_veroeffentlicht(self) -> int:
        return sum(k.beitraege_veroeffentlicht for k in self.kampagnen)

    @property
    def beitraege_ziel(self) -> int:
        return sum(k.beitraege_ziel for k in self.kampagnen)

    @property
    def fertig(self) -> bool:
        """Alles durch. Siehe ``Kampagnenfortschritt.fertig`` zur leeren Liste.

        Eine **leere** Kampagne zaehlt hier als erledigt, ohne als erfolgreich
        zu gelten: Sie gibt nichts her, und der Lauf soll nicht an ihr
        haengenbleiben. Der Unterschied steht in der Abschlussmeldung.
        """
        return bool(self.kampagnen) and all(k.fertig or k.leer for k in self.kampagnen)

    @property
    def kampagnen_leer(self) -> int:
        """Kampagnen ohne eine einzige zugeordnete Gruppe."""
        return sum(1 for k in self.kampagnen if k.leer)

    @property
    def gruppen_wartend(self) -> int:
        return sum(k.gruppen_wartend for k in self.kampagnen)

    @property
    def gruppen_uebersprungen(self) -> int:
        return sum(k.gruppen_uebersprungen for k in self.kampagnen)

    @property
    def gruppen_ohne_bezug(self) -> int:
        return sum(k.gruppen_ohne_bezug for k in self.kampagnen)

    def lage(self, aktion: Aktion) -> Lage:
        """Was diese Aktion jetzt darf. Unbekannt heisst erlaubt.

        Die Vorgabe ist hier **nicht** die vorsichtige, und das hat einen
        anderen Grund als bei ``Erlaubnis``: Dort geht es darum, was eine
        fremde Gruppe zulaesst (unbekannt = nicht fragen), hier um unsere
        eigene Buchfuehrung (unbekannt = niemand hat etwas eingeschraenkt).
        """
        return self.aktionen.get(aktion, Lage(aktion, True))

    @property
    def beitritt_frei(self) -> bool:
        """Darf heute ueberhaupt noch eine Beitrittsanfrage hinaus?

        Der Takt gehoert **nicht** hierher: Er sagt "noch nicht", nicht
        "nicht mehr". Eine Kampagne mit offenen Anfragen bleibt deshalb in der
        Beitrittsphase und wartet, statt vorzeitig zur Arbeit ueberzugehen -
        deswegen zaehlt hier ``wartet`` als frei.
        """
        lage = self.lage(Aktion.BEITRITT)
        return lage.moeglich or lage.wartet

    @property
    def beitritt_kontingent(self) -> int:
        """Wie viele Anfragen heute noch hinausduerfen - fuer die Anzeige."""
        return self.lage(Aktion.BEITRITT).rest_heute

    @property
    def beitritt_wartezeit(self) -> str:
        """Der Takt zwischen zwei Anfragen, als Text - oder leer."""
        return self.lage(Aktion.BEITRITT).wartezeit

    @property
    def naechste_kampagne(self) -> Kampagnenfortschritt | None:
        """Streng der Reihe nach: erst wenn eine fertig ist, kommt die naechste.

        Eine Kampagne, in der nur noch Gruppen ohne Mitgliedschaft warten,
        ist nicht fertig - aber sie gibt auch nichts her. ``naechster_schritt``
        geht deshalb zur naechsten weiter, statt vor ihr stehenzubleiben.

        Eine offene Beitrittsanfrage zaehlt dabei als Arbeit: Sonst uebersaehe
        der Lauf ausgerechnet die Kampagne, in der noch keine einzige
        Mitgliedschaft besteht - und das ist der Fall, fuer den der
        Beitrittsschritt da ist.
        """

        def hat_arbeit(k: Kampagnenfortschritt) -> bool:
            if k.naechste_gruppe is not None:
                return True
            # **Eine ruhende Gruppe ist Arbeit, die gleich wiederkommt**
            # (21.09.2026). Ohne diese Zeile loeste ein vollstaendiger
            # Durchlauf den Kampagnenwechsel aus: Liegen am Ende einer Runde
            # alle dreizehn Gruppen fuer zwei Minuten beiseite, gibt die
            # Kampagne in genau diesem Augenblick nichts her - und die
            # naechste uebernahm, obwohl die erste nicht fertig war.
            #
            # Verlangt ist das Gegenteil (Anforderung vom 21.09.2026):
            # Kampagne A, Runde um Runde, bis sie **erreicht** ist; erst
            # dann B. Gewartet wird dann auf die Rueckkehr
            # (``store.naechste_rueckkehr``), nicht gewechselt.
            if k.gruppen_ruhend:
                return True
            return self.beitritt_frei and bool(k.beitritt_offen)

        return next(
            (k for k in self.kampagnen if not k.fertig and hat_arbeit(k)),
            next((k for k in self.kampagnen if not k.fertig), None),
        )

    @property
    def gruppen_am_tageslimit(self) -> int:
        """Ueber alle Kampagnen: wie viele hatten heute ihren Kommentar schon."""
        return sum(k.gruppen_am_tageslimit for k in self.kampagnen)

    @property
    def wartet_auf_beitritt(self) -> str:
        """Der Lauf steht vor einer Anfrage, die der Takt noch nicht zulaesst.

        Leer heisst: nichts zu warten. Sonst die Wartezeit als Text - der
        Treiber haelt so lange an und fragt dann erneut. Er soll dabei nicht
        selbst rechnen, sonst gaebe es die Regel zweimal.
        """
        kampagne = self.naechste_kampagne
        if kampagne is None or not self.beitritt_frei:
            return ""
        if not kampagne.beitritt_offen:
            return ""
        return self.beitritt_wartezeit

    @property
    def wartet_auf_takt(self) -> str:
        """Der Takt haelt gerade die **einzige** Arbeit an, die noch ansteht.

        Die allgemeine Fassung von ``wartet_auf_beitritt`` (14.09.2026), und
        der Anlass ist ein Lauf, der sich selbst beendet hat:

            Beitraege:  2 / 23      Kommentare: 31 / 2700
            Heute nicht mehr moeglich:
              beitritt:  Tagesmenge erreicht (50/50)
              kommentar: Abstandsregel - noch 2 Min

        Zwei Minuten. Danach waeren 2669 Kommentare uebrig gewesen, und der
        Lauf hoerte auf - weil der Warteweg nur fuer die **Beitrittsanfrage**
        gebaut war. Fuer jede andere Aktion galt weiterhin: kein Schritt =
        Ende.

        **"heute nicht mehr" ist etwas anderes als "noch nicht".** Genau
        diesen Unterschied kennt ``Lage.wartet`` bereits: Eine erschoepfte
        Tagesmenge und eine abgeschaltete Aktion tragen keine Wartezeit, ein
        Takt und eine Bremse der Gegenseite schon. Nur das Zweite ist eine
        Frage der Zeit.

        **Gewartet wird nur, wenn Warten etwas bringt.** Dafuer wird der
        Schritt ein zweites Mal gesucht - mit den gebremsten Aktionen als
        frei. Kommt dann einer heraus, war der Takt der einzige Grund; kommt
        keiner, ist der Lauf wirklich durch, und ein Schlaf waere eine
        Viertelstunde fuer nichts.

        Leer heisst also: aufhoeren ist richtig. Sonst die Wartezeit als
        Text - der Treiber deckelt sie auf eine Viertelstunde
        (``automatik.wartesekunden``) und fragt danach erneut.
        """
        # **Nur der eigene Takt.** Eine Bremse der Gegenseite traegt auch eine
        # Wartezeit, ist aber keine Frage der Zeit, sondern eine Ansage: Sie
        # zu verschlafen hiesse, eine Stunde zu warten und danach genau das
        # Muster fortzusetzen, das zu ihr gefuehrt hat. Der Lauf endet
        # stattdessen; der Backoff ueberlebt den Neustart.
        gebremst = {
            aktion: lage for aktion, lage in self.aktionen.items() if lage.nur_takt
        }
        if not gebremst:
            return ""

        # **Wer arbeiten kann, wartet nicht.** Der Treiber fragt das hier
        # ohnehin nur, wenn kein Schritt kam - aber eine Eigenschaft, die
        # "noch 137 Min" sagt, waehrend ein Kommentar bereitliegt, ist fuer
        # sich genommen falsch, und die naechste Stelle, die sie liest,
        # glaubte es. Dieselbe Vorsicht wie bei ``wartet_auf_beitritt``.
        if naechster_schritt(self) is not None:
            return ""

        # **Jede Bremse einzeln fragen, und die kuerzeste gewinnt.**
        #
        # Die erste Fassung hob alle Bremsen auf einmal auf und nahm die
        # Aktion des Schrittes, der dabei herauskam. ``naechster_schritt``
        # prueft aber den **Beitrag vor dem Kommentar** - also gewann immer
        # der Beitrag, und der ist mit 120-240 Minuten der langsamste Takt
        # im Haus. Am 14.09.2026 stand deshalb "Takt: noch 58 Min" auf dem
        # Schirm, waehrend der Kommentar in 11 Minuten frei gewesen waere:
        # Der Lauf verschlief eine Stunde fuer eine Aktion, auf die er gar
        # nicht angewiesen war.
        #
        # Gefragt wird deshalb je Aktion: Bringt **sie allein** einen
        # Schritt? Von denen, die es tun, wartet der Lauf auf die naechste.
        kandidaten: list[tuple[int, str]] = []
        for aktion, lage in gebremst.items():
            ohne_diese = replace(
                self,
                aktionen={**self.aktionen, aktion: replace(lage, moeglich=True)},
            )
            if naechster_schritt(ohne_diese) is None:
                continue
            kandidaten.append((_minuten(lage.wartezeit), lage.wartezeit))

        if not kandidaten:
            return ""
        return min(kandidaten)[1]


def _minuten(wartezeit: str) -> int:
    """Die Minutenzahl aus "noch 58 Min" - fuer den Vergleich zweier Bremsen.

    Ohne Deckel, anders als ``automatik.wartesekunden``: Dort wird
    geschlafen (und deshalb begrenzt), hier nur verglichen. Eine Bremse von
    24 Stunden soll gegen eine von 11 Minuten auch wirklich verlieren.
    """
    ziffern = "".join(z for z in wartezeit if z.isdigit())
    return int(ziffern) if ziffern else 0


# --- Der naechste Schritt --------------------------------------------------
class Schrittart(StrEnum):
    """Was fuer eine Handlung ansteht - die Reihenfolge des Ablaufs.

    Sie stehen in der Reihenfolge, in der sie je Kampagne drankommen:

    1. ``BEITRITT`` - eine Beitrittsanfrage an eine Gruppe dieser Kampagne.
    2. ``TEXT`` - ein Beitrag oder ein Kommentar.

    ``REGELN`` (Gruppenseite lesen) und ``BEWERTEN`` (Neubewertung) sind am
    23.09.2026 entfallen - Anweisung des Nutzers.
    """

    BEITRITT = "beitritt"
    TEXT = "text"


@dataclass(frozen=True)
class Schritt:
    """Genau eine auszufuehrende Handlung: dieser Kommentar in dieser Gruppe.

    Der Treiber bekommt hier alles, was er braucht, und trifft selbst keine
    Entscheidung mehr. Waere die Wahl der Fassung beim Treiber, gaebe es sie
    zweimal - einmal in der Kommandozeile, einmal im Web -, und die beiden
    koennten verschiedene Fassungen waehlen.

    Bei ``BEITRITT`` ist ``texttyp`` bedeutungslos und bleibt auf der
    Vorgabe stehen.
    """

    campaign_id: str
    group_id: str
    nummer: int
    texttyp: Texttyp = Texttyp.KOMMENTAR
    art: Schrittart = Schrittart.TEXT

    # Nur zur Anzeige - der Treiber soll den Fortschritt nennen koennen, ohne
    # ihn selbst auszurechnen.
    gruppe_name: str = ""
    kommentar_nr: int = 0
    kommentar_ziel: int = ZIEL_JE_GRUPPE


def vorlage_zu_nummer(nummer: int, *, vorlagen: int = VORLAGEN_JE_TOPF) -> int:
    """Welche der fuenf Vorlagen der n-te Kommentar traegt.

    ``1..5 → 1..5``, ``6..10 → 1..5``. Eine Rechnung und kein gespeicherter
    Zeiger: Nach einem Abbruch ergibt dieselbe Nummer wieder dieselbe Vorlage,
    ohne dass sich jemand etwas merken muss.
    """
    return ((nummer - 1) % max(vorlagen, 1)) + 1


def ziel_zu_nummer(nummer: int) -> str:
    """Welches Ziel der n-te Kommentar traegt - ``browser`` oder ``store``.

    Ungerade in den Browser, gerade in den Store: ``1 → browser``,
    ``2 → store``, ``3 → browser`` ... Eine Rechnung und kein gespeicherter
    Zeiger, aus demselben Grund wie bei der Vorlagenwahl - dieselbe Fassung
    ergibt nach einem Abbruch wieder dasselbe Ziel.

    Beide Ziele werden **vollstaendig gezaehlt**; der Unterschied ist allein,
    wohin die Weiterleitung fuehrt. Dadurch laesst sich im Trichter
    unterscheiden, ob ein Mensch ueber den Play Store oder ueber die
    Web-Anwendung kam.
    """
    return "browser" if nummer % 2 == 1 else "store"


def naechste_nummer(
    veroeffentlicht: set[int],
    gescheitert: set[int] | None = None,
    *,
    max_nummer: int = ZIEL_JE_GRUPPE,
) -> int | None:
    """Die kleinste Fassung, die weder heraus noch aufgegeben ist.

    Das ist die ganze "Rotation": 1-2-3-4-5 ergibt sich von selbst, und nach
    einem Abbruch steht dieselbe Nummer wieder an - ohne gespeicherten Zeiger,
    der von der Wirklichkeit abweichen koennte.

    ``gescheitert`` sind die Fassungen, die in diesem Lauf zu oft erfolglos
    waren. Sie werden uebersprungen, statt den Lauf an derselben Stelle
    festzuhalten; ``None`` heisst "keine Fassung ist im Lauf aufgegeben
    worden".
    """
    aufgegeben = gescheitert or set()
    return next(
        (n for n in range(1, max_nummer + 1) if n not in veroeffentlicht and n not in aufgegeben),
        None,
    )


def naechster_schritt(fortschritt: Lauffortschritt) -> Schritt | None:
    """Was jetzt zu tun ist - oder ``None``, wenn der Lauf durch ist.

    **Die eine Stelle, an der die Reihenfolge des Ablaufs steht** (12.09.2026):

    1. Die Kampagne, die an der Reihe ist - streng sequentiell, nie zwei
       gleichzeitig.
    2. Darin die **Beitrittsanfragen** an ihre Gruppen, solange die
       Tagesmenge es zulaesst (derzeit 0, siehe ``beitritt_kandidaten``).
    3. Dann die erste Gruppe der ``arbeitsliste``, in der gearbeitet werden
       kann - in Score-Reihenfolge.
    4. Darin Beitrag und Kommentare in der Reihenfolge von
       ``kommentare_zuerst``. Scheitert der eine, geht es mit dem anderen
       weiter - ein Fehlschlag laesst die Gruppe nicht ausfallen.
    5. Erst wenn die Kampagne durch ist, kommt die naechste.

    Gruppenregeln lesen und Neubewertung waren bis zum 23.09.2026 eigene
    Schritte; sie sind entfallen.

    ``None`` heisst nicht "fertig". Es kann auch heissen: Der Lauf wartet auf
    den Takt der Beitrittsanfragen (``wartet_auf_beitritt``) oder die naechste
    Gruppe ist erschoepft und muss erst vermerkt werden. Der Treiber fragt
    beides, bevor er aufhoert - so war es beim Erschoepfen schon, und der
    Beitrittstakt fuegt sich in dieselbe Stelle.
    """
    kampagne = fortschritt.naechste_kampagne
    if kampagne is None:
        return None

    phase = kampagne.phase(beitritt_frei=fortschritt.beitritt_frei)

    # 3. Beitrittsanfragen - vor der Arbeit in dieser Kampagne, aber **nur
    #    wenn der Takt sie jetzt zulaesst**.
    #
    #    Bis zum 13.09.2026 stand hier ``return None``, und der Treiber legte
    #    sich schlafen. Die Begruendung war richtig, die Folge falsch:
    #    Gewartet werden sollte, damit die geforderte Reihenfolge (erst
    #    Beitritt, dann Arbeit) keine blosse Empfehlung wird, die jeder
    #    Mindestabstand aushebelt. Das stimmt, solange der Abstand klein ist.
    #
    #    Im Betrieb stand dann "Beitrittstakt: noch 31 Min" auf dem Schirm,
    #    und bei 50 offenen Anfragen wurde daraus ein **ganzer Tag Schlaf**
    #    fuer einen einzigen Kommentar. Das widerspricht zwei Zusagen des
    #    Projekts auf einmal:
    #
    #    * Punkt 16 der Anforderung: "Wenn eine Gruppe noch auf die Aufnahme
    #      wartet, soll der Runner andere bereits freigegebene Gruppen
    #      weiterbearbeiten koennen."
    #    * Dem Grundsatz, fuer den es ``grenzen.py`` ueberhaupt gibt: "Wer
    #      wegen einer gebremsten Aktion alles anhaelt, verliert die Arbeit
    #      dort, wo nichts dagegen spricht." Beitrag und Kommentar halten sich
    #      daran schon gegenseitig (Punkt 6 weiter unten) - der Beitritt tat
    #      es als einziger nicht.
    #
    #    **Die Reihenfolge bleibt.** Steht eine Anfrage an und der Takt laesst
    #    sie zu, geht sie vor jeder Arbeit hinaus. Was entfaellt, ist allein
    #    das Warten: Der Lauf arbeitet inzwischen weiter und stellt die
    #    Anfrage, sobald ihr Abstand um ist.
    if phase is Phase.BEITRITT and not fortschritt.beitritt_wartezeit:
        ziel = kampagne.beitritt_offen[0]
        return Schritt(
            campaign_id=kampagne.campaign_id,
            group_id=ziel.group_id,
            nummer=0,
            art=Schrittart.BEITRITT,
            gruppe_name=ziel.name,
            kommentar_nr=1,
            kommentar_ziel=1,
        )

    # Die erste Gruppe, in der gearbeitet werden kann.
    gruppe = kampagne.naechste_gruppe
    if gruppe is None:
        return None

    # 6. Jede Art an ihrer eigenen Grenze. Ist der Beitrag gebremst, kommen
    #    die Kommentare trotzdem dran - und umgekehrt. Nur so bleibt die
    #    Zusage aus Punkt 8 wahr: Ein Limit fuer eine Aktion ist keines fuer
    #    die andere.
    darf_post = fortschritt.lage(Aktion.POST).moeglich
    darf_kommentar = fortschritt.lage(Aktion.KOMMENTAR).moeglich

    def _beitragsschritt() -> Schritt | None:
        if not darf_post:
            return None
        post_nummer = gruppe.post_nummer
        if post_nummer is None:
            return None
        return Schritt(
            campaign_id=kampagne.campaign_id,
            group_id=gruppe.group_id,
            nummer=post_nummer,
            texttyp=Texttyp.POST,
            gruppe_name=gruppe.name,
            # Beim Beitrag gibt es nur einen: 1 von 1, und nicht die
            # Kommentarzaehlung, die hier eine falsche Auskunft waere.
            kommentar_nr=1,
            kommentar_ziel=1,
        )

    def _kommentarschritt() -> Schritt | None:
        if not darf_kommentar:
            # Kommentare sind heute erschoepft, gebremst oder abgeschaltet.
            # Das ist kein Urteil ueber die Gruppe: Der naechste Lauf findet
            # sie unveraendert vor.
            return None

        # **Fuer den Kommentar wird die Gruppe neu gewaehlt.** Seit die
        # Tagesmenge je Gruppe nur noch den Kommentar sperrt (und nicht mehr
        # die ganze Gruppe), kann ``naechste_gruppe`` eine liefern, die
        # **nur** noch ihren Beitrag offen hat. Ist der gerade getaktet,
        # stuende der Lauf vor ihr still - obwohl in der naechsten Gruppe ein
        # Kommentar hinausgehen koennte. Die Rangfolge bleibt dieselbe (beide
        # lesen ``arbeitsliste``), nur die Frage ist eine andere: "wer darf
        # heute noch kommentieren?"
        ziel_gruppe = kampagne.naechste_kommentargruppe
        if ziel_gruppe is None:
            return None

        nummer = naechste_nummer(
            set(range(1, ziel_gruppe.veroeffentlicht + 1)),
            set(ziel_gruppe.gescheiterte_fassungen),
        )
        if nummer is None:
            # Alle Fassungen sind heraus oder aufgegeben, die Gruppe gilt
            # aber noch nicht als fertig: Dann ist sie erschoepft, und der
            # Treiber traegt das ein. Ein Schritt waere hier eine
            # Endlosschleife.
            return None
        return Schritt(
            campaign_id=kampagne.campaign_id,
            group_id=ziel_gruppe.group_id,
            nummer=nummer,
            texttyp=Texttyp.KOMMENTAR,
            gruppe_name=ziel_gruppe.name,
            kommentar_nr=ziel_gruppe.veroeffentlicht + 1,
            kommentar_ziel=ziel_gruppe.ziel,
        )

    # **Welcher von beiden zuerst** (21.09.2026). Die Vorgabe ist der Beitrag
    # (Regel vom 10.09.2026: Er ist der Anlass und steht in der Gruppe).
    # ``automatik.kommentare_zuerst`` dreht es um - Anweisung des Nutzers:
    # "Prioritaet Nr. 1 ist das Kommentieren, der Beitrag ist unwichtig."
    #
    # Umgedreht wird nur die **Reihenfolge**, nicht die Menge: Kommt gerade
    # kein Kommentar zustande (Takt, Tagesmenge, keine Fassung mehr), geht
    # der Beitrag hinaus. Ein Zweig, der nichts hergibt, ist kein Ende des
    # Laufs - genau deshalb sind es zwei Kandidaten und keine zwei Ausgaenge.
    kandidaten = (
        (_kommentarschritt, _beitragsschritt)
        if fortschritt.kommentare_zuerst
        else (_beitragsschritt, _kommentarschritt)
    )
    for kandidat in kandidaten:
        if (schritt := kandidat()) is not None:
            return schritt
    return None



def gruppe_ist_erschoepft(gruppe: Gruppenfortschritt) -> bool:
    """Hat die Gruppe alle Fassungen verbraucht, ohne voll zu werden?

    Der Treiber fragt das, nachdem ein Schritt ``None`` ergeben hat: Dann ist
    keine Fassung mehr offen, aber das Ziel nicht erreicht - die Gruppe gibt
    nichts mehr her.
    """
    if gruppe.voll or gruppe.erschoepft or gruppe.post_offen:
        return False
    return (
        naechste_nummer(
            set(range(1, gruppe.veroeffentlicht + 1)),
            set(gruppe.gescheiterte_fassungen),
        )
        is None
    )


def _post_status(link) -> PostStatus:
    """Der Beitragsstand der Zuordnung - ohne den Fehlschlag "kein Text".

    ``fehlgeschlagen`` heisst sonst: In dieser Gruppe ging der Beitrag nicht,
    und ein zweiter Anlauf aus demselben Konto wiederholte nur das. Der Grund
    ``OHNE_BEITRAGSTEXT`` sagt aber etwas anderes - es wurde nie einer
    versucht, weil keiner dastand. Seit der Lauf die Fassungen nachzieht, ist
    das kein Ausgang mehr, sondern ein Schritt, der uebersprungen wurde.
    """
    if link is None:
        return PostStatus.OFFEN
    if (
        link.post_status is PostStatus.FEHLGESCHLAGEN
        and link.post_error.strip() == OHNE_BEITRAGSTEXT
    ):
        return PostStatus.OFFEN
    return link.post_status


# --- Den Stand aus dem Bestand lesen ---------------------------------------
def lies_fortschritt(
    store,
    lauf_id: int,
    gruppen: dict,
    *,
    mitgliedschaft_pflicht: bool = True,
    aktionen: dict[Aktion, Lage] | None = None,
    bezuege: dict | None = None,
    heute_je_gruppe: dict | None = None,
    gruppenlimit: int = 0,
    ziel_kommentare: int = 0,
    kommentare_zuerst: bool = False,
) -> Lauffortschritt:
    """Baut den ganzen Stand aus den vorhandenen Tabellen.

    Hier faellt zusammen, was oben behauptet wurde: Es wird **gelesen**. Kein
    Zaehler wird fortgeschrieben, nichts muss nach einem Abbruch geradegerueckt
    werden. Die einzigen gespeicherten Angaben sind die eingefrorene
    Kampagnenliste und die Erschoepfung - alles andere ergibt sich.

    ``gruppen`` bringt die Namen mit (``group_id -> Group``); sie kommen aus
    dem Gruppenbestand und nicht aus dem Marketingspeicher, der sie nicht
    kennt. Fehlt eine Gruppe dort, wird ihre Kennung angezeigt - eine Zeile
    ohne Namen ist besser als eine fehlende Zeile.

    ``aktionen`` kommt von aussen, weil es ueber **alle** Kampagnen gilt und
    aus ``settings.yaml`` stammt - dieses Modul liest keine Konfiguration, so
    wie es kein Netz kennt. Fehlt es, ist nichts eingeschraenkt.

    ``bezuege`` (``group_id -> bezug.Gruppenbezuege``) kommt aus demselben
    Grund von aussen: Die Bezuege stehen im Marketingspeicher und werden dort
    gesammelt. Fehlt eine Gruppe darin, sind ihre Bezuege ``[]`` - ohne
    Folge, solange die Behandlung dafuer nicht festgelegt ist.

    ``heute_je_gruppe`` und ``gruppenlimit`` tragen die Tagesmenge **je
    Gruppe** herein (``limits.comments.je_gruppe_taeglich``). Sie stehen
    neben ``aktionen`` und nicht darin, weil ihre Folge eine andere ist: Eine
    erschoepfte Tagesmenge pausiert die Aktion ueberall, eine volle Gruppe
    laesst die naechste sofort drankommen.

    **Eine Kampagne, die sich nicht lesen laesst, haelt den Lauf nicht auf.**
    Sie kommt als leere, gescheiterte Kampagne in die Liste; der Lauf geht
    zur naechsten weiter. Ein Fehler in einer Zuordnung darf nicht
    dreihundert Beitraege in anderen Kampagnen verhindern.
    """
    from fbgroups.marketing.models import MarketingStatus


    # Wo ist das Konto Mitglied? Alles ab ``mitglied`` zaehlt - wer die
    # Zusammenarbeit angebahnt oder abgeschlossen hat, ist erst recht drin.
    # ``beitritt_angefragt`` zaehlt ausdruecklich **nicht**: Eine offene
    # Anfrage ist keine Mitgliedschaft, und Facebook laesst oft wochenlang
    # offen. Wer sie mitzaehlte, liefe genau in die Fehlversuche, die dieser
    # Wert verhindern soll.
    mitgliedschaft = {
        MarketingStatus.MEMBER,
        MarketingStatus.CONTACTED,
        MarketingStatus.INTERESTED,
        MarketingStatus.APPROVED,
        MarketingStatus.ACTIVE,
    }
    staende = store.load_all_marketing()
    ist_mitglied = {
        gid
        for gid, stand in staende.items()
        if stand.marketing_status in mitgliedschaft
    }

    kopf = store.lauf(lauf_id)
    if kopf is None:
        return Lauffortschritt(lauf_id=lauf_id, status=LaufStatus.GESCHEITERT, kampagnen=[])

    ziel = int(kopf["ziel_je_gruppe"])
    # Was in **diesem** Lauf schon schiefgegangen ist. Die Gruppen darin
    # werden uebersprungen, nicht verurteilt - mit dem naechsten Lauf ist der
    # Vermerk weg.
    uebersprungen = store.uebersprungene_gruppen(lauf_id)
    # **Wer ruht, kommt wieder** - und haelt damit den Platz seiner Kampagne.
    # Ein aelterer Speicher kennt die Frage nicht; dann ruht eben niemand,
    # und es gilt das Verhalten von vorher.
    ruhend = (
        store.ruhende_gruppen(lauf_id)
        if hasattr(store, "ruhende_gruppen")
        else set()
    )
    kampagnen: list[Kampagnenfortschritt] = []

    for zeile in store.lauf_kampagnen(lauf_id):
        campaign_id = zeile["campaign_id"]
        try:
            kampagnen.append(
                _lies_kampagne(
                    store,
                    zeile,
                    gruppen,
                    ziel=ziel,
                    ist_mitglied=ist_mitglied,
                    uebersprungen=uebersprungen,
                    ruhend=ruhend,
                    mitgliedschaft_pflicht=mitgliedschaft_pflicht,
                    bezuege=bezuege or {},
                    heute_je_gruppe=heute_je_gruppe or {},
                    gruppenlimit=gruppenlimit,
                    ziel_kommentare=ziel_kommentare,
                )
            )
        except Exception:  # noqa: BLE001 - eine Kampagne, nicht der Lauf
            # Fehlerisolierung je Kampagne: Sie kommt leer und als
            # gescheitert in die Liste. ``leer`` haelt den Lauf nicht auf
            # (siehe ``Kampagnenfortschritt.leer``), und die naechste
            # Kampagne wird bearbeitet, als waere nichts gewesen.
            kampagne = None
            with contextlib.suppress(Exception):  # dann eben ohne Namen
                kampagne = store.load_campaign(campaign_id)
            kampagnen.append(
                Kampagnenfortschritt(
                    campaign_id=campaign_id,
                    name=(kampagne.name if kampagne else campaign_id),
                    gruppen=[],
                    status=KampagnenLaufStatus.GESCHEITERT,
                )
            )

    return Lauffortschritt(
        lauf_id=lauf_id,
        status=LaufStatus(kopf["status"]),
        kampagnen=kampagnen,
        aktionen=dict(aktionen or {}),
        kommentare_zuerst=kommentare_zuerst,
    )


def _lies_kampagne(
    store,
    zeile,
    gruppen: dict,
    *,
    ziel: int,
    ist_mitglied: set,
    uebersprungen: dict,
    ruhend: set,
    mitgliedschaft_pflicht: bool,
    bezuege: dict,
    heute_je_gruppe: dict,
    gruppenlimit: int,
    ziel_kommentare: int = 0,
) -> Kampagnenfortschritt:
    """Den Stand **einer** Kampagne lesen - herausgeloest, damit sie fuer sich scheitern kann.

    Der ganze Zweck der Aufteilung steht in ``lies_fortschritt``: Ein Fehler
    in einer Zuordnung soll die anderen Kampagnen nicht mitnehmen.
    """
    from fbgroups.scoring import sort_by_rank

    campaign_id = zeile["campaign_id"]
    kampagne = store.load_campaign(campaign_id)
    stand = store.kommentarstand(campaign_id)
    erschoepft = {
        gid: grund
        for gid, grund in store.erschoepfte_gruppen(campaign_id).items()
        if grund.strip() != OHNE_KOMMENTARTEXT
    }
    gescheitert = store.gescheiterte_kommentarfassungen(campaign_id, MAX_VERSUCHE_JE_FASSUNG)

    # Der Beitragsstand kommt aus der Zuordnung selbst - dieselbe Spalte, die
    # auch die Arbeitsseite fuellt. Ein von Hand abgesetzter Beitrag wird
    # deshalb nicht ein zweites Mal abgesetzt.
    post_texte = store.fassungen_mit_text(campaign_id, Texttyp.POST)
    links = store.links_for_campaign(campaign_id)
    je_gruppe = {link.group_id: link for link in links}
    # Schritt 2 des Ablaufs: An welche Gruppen **dieser** Kampagne muss noch
    # eine Beitrittsanfrage gehen? Die Regel dafuer steht im Speicher
    # (Mitgliedschaft, bearbeiten, Adresse) und nicht hier zum zweiten Mal.
    beitritt_noetig = set(store.beitrittskandidaten(campaign_id))
    # Dieselbe Reihenfolge wie in der Arbeitsliste: die besten zuerst. Wird
    # ein Lauf nie zu Ende gefahren, sollen es die richtigen Gruppen gewesen
    # sein. Gruppen ohne Datensatz wandern ans Ende, statt zu verschwinden -
    # eine Gruppe, die nicht in der Liste steht, bekommt nie einen Kommentar.
    bekannt = [link for link in links if link.group_id in gruppen]
    unbekannt = [link for link in links if link.group_id not in gruppen]
    geordnet = sort_by_rank([gruppen[link.group_id] for link in bekannt])
    reihenfolge = [g.group_id for g in geordnet] + [link.group_id for link in unbekannt]

    eintraege = [
        Gruppenfortschritt(
            campaign_id=campaign_id,
            group_id=gid,
            name=(gruppen[gid].name if gid in gruppen and gruppen[gid].name else gid),
            veroeffentlicht=stand.get(gid, 0),
            ziel=ziel,
            erschoepft=gid in erschoepft,
            erschoepft_grund=erschoepft.get(gid, ""),
            gescheiterte_fassungen=frozenset(gescheitert.get(gid, set())),
            mitglied=gid in ist_mitglied,
            mitgliedschaft_noetig=mitgliedschaft_pflicht,
            post_status=_post_status(je_gruppe.get(gid)),
            post_fassungen=frozenset(post_texte.get(gid, set())),
            beitritt_noetig=gid in beitritt_noetig,
            uebersprungen=(campaign_id, gid) in uebersprungen,
            uebersprungen_grund=uebersprungen.get((campaign_id, gid), ""),
            ruht=(campaign_id, gid) in ruhend,
            bezuege=(
                tuple(b.value for b in bezuege[gid].bezuege) if gid in bezuege else ()
            ),
            heute_in_gruppe=int(heute_je_gruppe.get(gid, 0)),
            gruppenlimit=gruppenlimit,
        )
        for gid in reihenfolge
    ]

    return Kampagnenfortschritt(
        campaign_id=campaign_id,
        name=(kampagne.name if kampagne else campaign_id),
        gruppen=eintraege,
        status=KampagnenLaufStatus(zeile["status"]),
        ziel_kommentare=ziel_kommentare,
    )


# --- Anzeige ---------------------------------------------------------------
#: Wie die Abschnitte des Ablaufs heissen. Neben der Aufzaehlung und nicht
#: darin: Der Wert ist fuer die Datenbank, der Text fuer den Menschen.
PHASENTEXT: dict[Phase, str] = {
    Phase.BEITRITT: "Beitrittsanfragen",
    Phase.ARBEIT: "Beitraege und Kommentare",
    Phase.FERTIG: "fertig",
}


def fortschrittstext(fortschritt: Lauffortschritt) -> str:
    """Der laufende Stand als Text - fuer Kommandozeile und Weboberflaeche.

    Eine Quelle, damit beide dasselbe sagen. Die Zahl steht vorn: "3 von 20"
    beantwortet die Frage, "laeuft" beantwortet sie nicht.
    """
    zeilen = [
        f"Automatik: {fortschritt.status.upper()}",
        "",
        f"Kampagnen:  {fortschritt.kampagnen_fertig} / {fortschritt.kampagnen_gesamt}",
        f"Gruppen:    {fortschritt.gruppen_fertig} / {fortschritt.gruppen_gesamt}",
        f"Beitraege:  {fortschritt.beitraege_veroeffentlicht} / {fortschritt.beitraege_ziel}",
        f"Kommentare: {fortschritt.kommentare_veroeffentlicht} / {fortschritt.kommentare_ziel}",
    ]
    if fortschritt.gruppen_wartend:
        zeilen += [
            "",
            f"{fortschritt.gruppen_wartend} Gruppe(n) warten auf Mitgliedschaft "
            "- dort wird nichts versucht.",
        ]
    if fortschritt.gruppen_uebersprungen:
        zeilen += [
            f"{fortschritt.gruppen_uebersprungen} Gruppe(n) nach einem Fehlschlag "
            "beiseitegelegt - im naechsten Lauf sind sie wieder dabei.",
        ]
    # Gruppen ohne erkannten Bezug (23.09.2026): gezaehlt, noch nicht
    # behandelt. Die Zahl steht hier, damit sichtbar ist, wie viele die noch
    # festzulegende Sonderbehandlung betreffen wird.
    if fortschritt.gruppen_ohne_bezug:
        zeilen += [
            f"{fortschritt.gruppen_ohne_bezug} Gruppe(n) ohne erkannten Bezug - "
            "ihre Behandlung ist noch nicht festgelegt.",
        ]
    # **Der haeufigste Grund, warum ein Lauf mit offener Arbeit endet - und
    # der einzige, der bis zum 15.09.2026 nirgends stand.**
    #
    # Am 15.09.2026 las sich die Meldung so:
    #
    #     Offen bei: versand / ... (1 / 10 Kommentare)
    #     Heute nicht mehr moeglich:
    #       post: Tagesmenge erreicht (3/3)
    #
    # Also: offene Arbeit da, Kommentare **nicht** als gesperrt gemeldet -
    # und trotzdem hoerte der Lauf auf. Es sah aus, als habe der
    # Beitragstakt die Kommentare mitgerissen. Tatsaechlich hatte jede
    # verbliebene Gruppe ihren einen Kommentar fuer heute schon, und das
    # steht in ``limits.comments.je_gruppe_taeglich`` - einer Zahl, die in
    # "Heute nicht mehr moeglich" nicht vorkommt, weil dort nur die Grenzen
    # **je Aktion** stehen, nicht die je Gruppe.
    if fortschritt.gruppen_am_tageslimit:
        zeilen += [
            "",
            f"{fortschritt.gruppen_am_tageslimit} Gruppe(n) hatten heute schon "
            "ihren Kommentar (limits.comments.je_gruppe_taeglich) - morgen sind "
            "sie unveraendert offen.",
            "Mehr Kommentare am Tag gibt es nur ueber mehr Gruppen "
            "(campaign sync) oder eine hoehere Zahl je Gruppe.",
        ]
    kampagne = fortschritt.naechste_kampagne
    if kampagne is not None:
        phase = kampagne.phase(beitritt_frei=fortschritt.beitritt_frei)
        zeilen += [
            "",
            f"Aktuelle Kampagne: {kampagne.name}",
            f"Abschnitt:         {PHASENTEXT[phase]}",
        ]
        if phase is Phase.BEITRITT:
            zeilen += [
                f"Offene Anfragen:   {len(kampagne.beitritt_offen)} "
                f"(heute noch {fortschritt.beitritt_kontingent} moeglich)"
            ]
            if fortschritt.beitritt_wartezeit:
                zeilen += [f"Abstandsregel:     {fortschritt.beitritt_wartezeit}"]
        gruppe = kampagne.naechste_gruppe
        if gruppe is not None and phase is Phase.ARBEIT:
            zeilen += [
                f"Aktuelle Gruppe:   {gruppe.name}",
                f"Kommentare:        {gruppe.veroeffentlicht} / {gruppe.ziel}",
            ]
    return "\n".join(zeilen)


OHNE_INHALT = """Automatik ohne Inhalt beendet

Dieser Lauf hatte keine einzige Kampagne eingefroren - es gab nichts zu tun.
So ein Lauf entsteht seit dem 10.09.2026 nicht mehr (hole_oder_starte_lauf);
ein alter wird hier geschlossen, statt bei jedem Start wieder aufgenommen zu
werden."""


def abschlusstext(fortschritt: Lauffortschritt) -> str:
    """Die Abschlussmeldung - und sie behauptet keinen Erfolg, den es nicht gab.

    Erst wenn **jede** Gruppe **jeder** Kampagne durch ist, steht hier
    "erfolgreich abgeschlossen". Ist eine Gruppe nur erschoepft, wird das
    genannt statt verschwiegen: Sie zaehlt als erledigt, nicht als Erfolg.
    """
    if not fortschritt.kampagnen:
        # Weder Erfolg noch Fehlschlag - hier war nichts. Ein "erfolgreich
        # abgeschlossen" waere eine Behauptung ueber Beitraege, die es nie
        # gab; ein "NICHT abgeschlossen" liesse den Leser suchen, was fehlt.
        return OHNE_INHALT

    erschoepft = sum(k.gruppen_erschoepft for k in fortschritt.kampagnen)

    if not fortschritt.fertig:
        zeilen = [
            "Automatik NICHT vollstaendig abgeschlossen",
            "",
            f"Kampagnen:  {fortschritt.kampagnen_fertig} / {fortschritt.kampagnen_gesamt}",
            f"Gruppen:    {fortschritt.gruppen_fertig} / {fortschritt.gruppen_gesamt}",
            f"Beitraege:  {fortschritt.beitraege_veroeffentlicht} / "
            f"{fortschritt.beitraege_ziel}",
            f"Kommentare: {fortschritt.kommentare_veroeffentlicht} / "
            f"{fortschritt.kommentare_ziel}",
        ]
        kampagne = fortschritt.naechste_kampagne
        if kampagne is not None and (gruppe := kampagne.naechste_gruppe) is not None:
            # **Und was dort heute noch geht.** Seit die Tagesmenge je Gruppe
            # nur noch den Kommentar sperrt, kann hier eine Gruppe stehen, in
            # der heute kein Kommentar mehr moeglich ist - "1 / 10
            # Kommentare" las sich dann wie liegengebliebene Arbeit.
            offen = (
                "heute kein Kommentar mehr (Tagesmenge je Gruppe)"
                if gruppe.tageslimit_erreicht
                else f"{gruppe.veroeffentlicht} / {gruppe.ziel} Kommentare"
            )
            zeilen += ["", f"Offen bei: {kampagne.name} / {gruppe.name} ({offen})"]

        # **Der haeufigste Grund, warum ein Lauf sofort endet** - und ohne
        # diese Zeilen der am schwersten zu findende: Es ist offene Arbeit da
        # ("Offen bei: ..."), und trotzdem geschieht nichts. Wer das liest,
        # sucht den Fehler in der Technik, obwohl die Zahl aus
        # ``settings.yaml`` stammt und heute schlicht erreicht ist.
        #
        # Die Aktionen stehen einzeln da, weil sie einzeln gelten: "kommentar
        # erschoepft" heisst nicht, dass auch Beitraege ruhen.
        gesperrt = [
            fortschritt.lage(aktion)
            for aktion in Aktion
            if not fortschritt.lage(aktion).moeglich
        ]
        if gesperrt:
            zeilen += ["", "Heute nicht mehr moeglich:"]
            zeilen += [
                f"  {lage.grund}" + (f" - {lage.wartezeit}" if lage.wartezeit else "")
                for lage in gesperrt
            ]
            if all(not fortschritt.lage(a).moeglich for a in Aktion):
                zeilen += [
                    "",
                    "Alle drei Aktionen ruhen - deshalb wurde kein Schritt "
                    "ausgefuehrt. Das ist kein Fehler: Die Tagesmengen stehen "
                    "in config/settings.yaml unter 'limits', die Abstaende "
                    "unter 'delays'.",
                    "Was heute noch frei ist:  fbgroups campaign automatik --status",
                ]

        # **Die Tagesmenge je Gruppe steht nicht in "Heute nicht mehr
        # moeglich"** - dort stehen nur die Grenzen je **Aktion**. Genau
        # deshalb fehlte sie: Am 15.09.2026 las sich die Tafel so
        #
        #     Offen bei: versand / ... (1 / 10 Kommentare)
        #     Heute nicht mehr moeglich:
        #       post: Tagesmenge erreicht (3/3)
        #
        # - offene Arbeit da, Kommentare nicht als gesperrt gemeldet, und der
        # Lauf hoerte trotzdem auf. Es sah aus, als habe der Beitrag die
        # Kommentare mitgerissen. Tatsaechlich hatte jede verbliebene Gruppe
        # ihren einen Kommentar fuer heute schon.
        if fortschritt.gruppen_am_tageslimit:
            zeilen += [
                "",
                f"{fortschritt.gruppen_am_tageslimit} Gruppe(n) hatten heute "
                "schon ihren Kommentar (limits.comments.je_gruppe_taeglich: "
                "1). Das ist kein Urteil - morgen sind sie unveraendert offen.",
                "Mehr Kommentare am Tag gibt es nur ueber mehr Gruppen "
                "(campaign sync) oder eine hoehere Zahl je Gruppe.",
            ]

        if fortschritt.gruppen_wartend:
            # Der haeufigste Grund, warum ein Lauf frueh endet, und ohne
            # diesen Satz sucht man ihn in der Technik statt im Konto.
            zeilen += [
                "",
                f"{fortschritt.gruppen_wartend} Gruppe(n) ohne Mitgliedschaft. "
                "Dort kann nicht kommentiert werden, solange die Beitrittsanfrage "
                "offen ist - Facebook laesst Nichtmitglieder nicht schreiben.",
                "Beitritt von Hand stellen, dann:  fbgroups marketing set <gruppe> "
                "--status mitglied",
            ]
        if fortschritt.gruppen_uebersprungen:
            zeilen += [
                "",
                f"{fortschritt.gruppen_uebersprungen} Gruppe(n) nach einem "
                "Fehlschlag beiseitegelegt. Der Lauf ist deswegen nicht "
                "abgebrochen; der naechste faengt mit ihnen wieder an.",
            ]
        return "\n".join(zeilen)

    zeilen = [
        "Automatik erfolgreich abgeschlossen",
        "",
        f"Kampagnen:  {fortschritt.kampagnen_fertig} / {fortschritt.kampagnen_gesamt}",
        f"Gruppen:    {fortschritt.gruppen_fertig} / {fortschritt.gruppen_gesamt}",
        f"Beitraege:  {fortschritt.beitraege_veroeffentlicht} / {fortschritt.beitraege_ziel}",
        f"Kommentare: {fortschritt.kommentare_veroeffentlicht} / {fortschritt.kommentare_ziel}",
    ]
    if erschoepft:
        zeilen += [
            "",
            f"Davon {erschoepft} Gruppe(n) erschoepft: Sie hatten nicht genug "
            "Beitraege zum Kommentieren.",
        ]
    if fortschritt.kampagnen_leer:
        # Erledigt, aber nicht erfolgreich - und der haeufigste Grund ist eine
        # Kampagne, der niemand Gruppen zugeordnet hat.
        zeilen += [
            "",
            f"{fortschritt.kampagnen_leer} Kampagne(n) ohne zugeordnete Gruppen: "
            "dort war nichts zu tun.  fbgroups campaign sync <kampagne>",
        ]
    return "\n".join(zeilen)


__all__ = [
    "KEINE_VORLAGE",
    "MAX_VERSUCHE_JE_FASSUNG",
    "OHNE_BEITRAGSTEXT",
    "OHNE_INHALT",
    "OHNE_KOMMENTARTEXT",
    "ZIEL_JE_GRUPPE",
    "Gruppenfortschritt",
    "Kampagnenfortschritt",
    "Lauffortschritt",
    "PHASENTEXT",
    "Phase",
    "Schritt",
    "Schrittart",
    "abschlusstext",
    "fortschrittstext",
    "gruppe_ist_erschoepft",
    "lies_fortschritt",
    "naechste_nummer",
    "naechster_schritt",
]
