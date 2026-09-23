"""Antworten oder nicht? - und wenn ja, in welcher Form.

## Was dieses Modul ist

Die Stelle, an der aus einem gelesenen Beitrag (``inhalt.py``) und dem, was
eine Gruppe erlaubt (``qualifikation.py``), **eine** Handlung wird. Es fuehrt
nichts aus und speichert nichts; es urteilt.

Rein wie seine beiden Nachbarn: kein Netz, keine Datenbank, kein Playwright.
Jede Regel ist ohne Browser pruefbar, und das ist der Zweck der Aufteilung -
die Entscheidung, ob unter einem fremden Beitrag etwas von uns steht, ist die
folgenreichste im ganzen Programm.

## ``NO_REPLY`` ist ein Ergebnis, kein Fehlschlag

Der wichtigste Satz der Anforderung vom 12.09.2026: *Nicht jede gefundene
Gelegenheit muss genutzt werden.* Ein Runner, der unter jeden Beitrag etwas
schreibt, ist ein Spam-Automat - unabhaengig davon, wie gut die Saetze
formuliert sind. Deshalb ist ``NO_REPLY`` hier ein gleichwertiger Ausgang mit
eigenem Grund und wird als solcher gezaehlt, nicht als Ausfall.

## Vier Stufen der Naehe, und sie sind nicht austauschbar

``HELPFUL_REPLY`` traegt gar nichts von uns. ``PRIVATE_CONTACT_SUGGESTION``
verlagert das Gespraech, ohne zu werben. ``CONTEXTUAL_APP_MENTION`` nennt die
App, weil sie zur Frage gehoert. ``DIRECT_APP_RECOMMENDATION`` empfiehlt sie
samt Link. Je naeher an der Werbung, desto mehr muss dafuer sprechen - und
desto mehr muss die Gruppe erlauben.

## Unbekannt heisst nicht erlaubt

Solange die Regeln einer Gruppe ungelesen sind, wird die **vorsichtigere**
Handlung gewaehlt. Das ist Punkt 4 der Anforderung und derselbe Gedanke, der
im Projekt schon dreimal steht: ``Regelbefund.gelesen`` trennt "nichts
verboten" von "nicht nachgesehen", ``Group.score is None`` heisst nicht
bewertbar, ``Seitenbefund.erreichbar`` nicht erreicht. Die Abwesenheit einer
Regel ist keine Erlaubnis, die jemand erteilt hat.

## Keine erfundenen Angebote

Was hier entschieden werden kann, ist **wie nah** eine Antwort an unser
Angebot heranreicht - nie, ob wir etwas haben, das wir nicht haben. Eine
Wohnungssuche fuehrt deshalb nie zu einer Antwort, die eine Wohnung in
Aussicht stellt: Die App vermittelt Reisende mit Platz im Koffer, und alles
andere waere eine Behauptung. Der Vorrat an Formulierungen
(``config/textvorlagen.yaml``) enthaelt aus demselben Grund keinen Satz, der
ein Angebot verspricht.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from fbgroups.marketing.inhalt import Absicht, Anlass, Inhaltsbefund, Relevanz, Thema
from fbgroups.marketing.qualifikation import Qualifikation, Regelbefund


class Antwortart(StrEnum):
    """Was unter einem Beitrag stehen soll - oder dass nichts dort steht.

    Die Reihenfolge ist die Naehe zur Werbung, von keiner bis zur
    ausdruecklichen Empfehlung. Sie ordnet die Anzeige und beschreibt keinen
    Weg: Ein Beitrag springt nicht von Stufe zu Stufe, er bekommt eine.
    """

    NO_REPLY = "no_reply"
    HELPFUL_REPLY = "helpful_reply"
    PRIVATE_CONTACT_SUGGESTION = "private_contact_suggestion"
    CONTEXTUAL_APP_MENTION = "contextual_app_mention"
    DIRECT_APP_RECOMMENDATION = "direct_app_recommendation"


#: Welche Arten ueberhaupt etwas von uns tragen. ``HELPFUL_REPLY`` steht
#: bewusst nicht darin: Eine Antwort, die nur hilft, ist keine Werbeaktion und
#: faellt damit auch nicht unter ein Werbeverbot der Gruppe.
WIRBT: frozenset[Antwortart] = frozenset(
    {
        Antwortart.CONTEXTUAL_APP_MENTION,
        Antwortart.DIRECT_APP_RECOMMENDATION,
    }
)


class Linkmodus(StrEnum):
    """Wie viel von uns im Text stehen darf - vom Namen bis zum Link.

    Die Anforderung vom 13.09.2026 nennt die drei Stufen ausdruecklich und
    setzt die Vorgabe auf **kein Link**: Viele Gruppen lehnen einen Link im
    Kommentar automatisch ab, und eine Ablehnung kostet mehr als ein Klick
    einbringt - sie steht danach als Urteil ueber die Gruppe im Protokoll.

    Es ist **keine eigene Entscheidung**, sondern die Kehrseite von
    ``Antwortart``: Wer die App nicht nennt, traegt auch keinen Link, und wer
    sie mit Link empfiehlt, nennt sie erst recht. Eine zweite Entscheidung
    daneben koennte von der ersten abweichen - und dann stuende ein Link in
    einer Antwort, die gar nichts von uns tragen sollte.

    * ``NO_LINK`` - nicht einmal der Name. Fuer die blosse Hilfe und fuer den
      Hinweis, sich privat zu melden.
    * ``APP_NAME_ONLY`` - "بطريقك" wird genannt, ohne Adresse. Der Regelfall.
    * ``TRACKING_LINK`` - mit Link. Nur wo die Gruppe Links erlaubt **und**
      der Beitrag ihn traegt.
    """

    NO_LINK = "no_link"
    APP_NAME_ONLY = "app_name_only"
    TRACKING_LINK = "tracking_link"


#: Die Zuordnung. Neben der Aufzaehlung und nicht darin - dieselbe Trennung
#: wie bei ``qualifikation.BESCHRIFTUNG``.
LINKMODUS: dict[Antwortart, Linkmodus] = {
    Antwortart.NO_REPLY: Linkmodus.NO_LINK,
    Antwortart.HELPFUL_REPLY: Linkmodus.NO_LINK,
    Antwortart.PRIVATE_CONTACT_SUGGESTION: Linkmodus.NO_LINK,
    Antwortart.CONTEXTUAL_APP_MENTION: Linkmodus.APP_NAME_ONLY,
    Antwortart.DIRECT_APP_RECOMMENDATION: Linkmodus.TRACKING_LINK,
}


#: Wie stark eine Relevanzstufe wiegt - fuer den Vergleich mit dem Anspruch.
#: Eine Zahl neben der Aufzaehlung und nicht darin: ``Relevanz`` ist eine
#: Aussage ueber den Beitrag, die Rangfolge eine Entscheidung von uns.
_RELEVANZRANG: dict[Relevanz, int] = {
    Relevanz.KEINE: 0,
    Relevanz.MITTEL: 1,
    Relevanz.HOCH: 2,
}


@dataclass(frozen=True)
class Anspruch:
    """Wie viel ein Beitrag hergeben muss, damit hier geantwortet wird.

    **Der Unterschied zwischen den Gruppenklassen** (13.09.2026). In einer
    Reisegruppe ist die Frage nach einem Koffer das Thema des Hauses; in
    einer Gemeinschaftsgruppe ist sie eine unter fuenfzig, und in einer
    allgemeinen Gruppe ist sie ein Zufall. Dieselbe Antwort ist an der einen
    Stelle hilfreich und an der anderen eingeworfene Werbung - und der
    Unterschied liegt nicht am Satz, sondern am Ort.

    Die Werte kommen aus ``zielgruppe.anspruch_aus_config``, also aus
    ``settings.yaml``. Die Vorgaben hier sind die des bisherigen Verhaltens:
    ``MITTEL`` genuegt, die Strecke wird nicht verlangt - so bleibt ein
    Aufrufer, der nichts angibt, bei dem, was vorher galt.
    """

    mindestrelevanz: Relevanz = Relevanz.MITTEL
    verlangt_strecke: bool = False
    """Muss der Beitrag Herkunft **und** Ziel nennen?

    Fuer die allgemeinen Gruppen (Klasse C): Dort genuegt "hoch" nicht - ein
    Beitrag ueber ein Paket kann dort alles moegliche meinen. Verlangt wird
    der ausgeschriebene Weg, also genau der Fall, den die Anforderung "klarer
    und konkreter Reise- oder Versandbedarf" nennt.
    """

    anlass_pflicht: bool = True
    """Braucht die App-Nennung einen **erkannten Anlass**?

    Eingeschaltet (Vorgabe) gilt die Regel vom 13.09.2026: Genannt wird die
    App nur, wo der Bezug **belegt** ist - durch ``HOCH`` oder durch einen
    Halbsatz, den ``inhalt.erkenne_anlass`` findet ("مساحة بالشنطة", "wer
    nimmt mit", "Medikamente").

    Ausgeschaltet (``marketing.anlass_pflicht: false``) genuegt die Schwelle
    der Gruppe, die eine Zeile darueber steht - und das ist genau das, was
    der Schalter immer versprochen hat: *"Der vorbereitete Text der Fassung
    geht hinaus, sobald die Relevanz reicht."* Bis zum 21.09.2026 stand er
    nur in ``automatik.text_zur_gelegenheit`` und wirkte deshalb nicht: Die
    Entscheidung war vorher schon bei ``NO_LINK`` gelandet, und dort gibt es
    keinen Vorrat. Im Betrieb sah das so aus - eine Reisegruppe, in der zehn
    Kommentare hingehoeren, und der Lauf ging weiter:

        keine passende Form (reise/unbekannt (سفر))

    **Der Ort entscheidet weiterhin.** Der Schalter hebt die Schwelle nicht
    auf: In einer Gemeinschaftsgruppe bleibt ``HOCH`` noetig, in einer
    allgemeinen dazu die ausgeschriebene Strecke. Was entfaellt, ist allein
    die Forderung nach dem Halbsatz **zusaetzlich** zur Schwelle.
    """


@dataclass(frozen=True)
class Erlaubnis:
    """Was eine Gruppe zulaesst - aus ihren Regeln und aus der Beobachtung.

    **``werbung`` gibt es seit dem 21.09.2026 nicht mehr** (Anweisung des
    Nutzers). Das Feld war der Anfang einer geschlossenen Kette: ungelesene
    Regeln -> ``werbung=False`` -> ``soll_app_nennen`` faellt aus ->
    ``private_contact_suggestion`` -> ``Linkmodus.NO_LINK`` -> kein
    Textvorrat -> kein Kommentar. In einer Kampagne aus Gruppen, die ein
    Mensch ausgesucht und mit "A++" eingestuft hat, hat es damit nichts
    anderes bewirkt als Stille.

    Was die Gruppe zulaesst, wird weiterhin gelesen: ``kommentare``,
    ``beitraege`` und ``links`` bleiben - der Link im Kommentar ist der
    haeufigste Grund einer Ablehnung, und das ist eine Aussage ueber die
    Annahme, nicht ueber die Erlaubnis zu werben.

    **Die Vorgabe fuer ``links`` bleibt die vorsichtige** (``False``): Wer
    dieses Objekt ohne Angaben baut, hat nichts ueber die Gruppe gelesen.
    Ohne Link wird trotzdem kommentiert - die App wird dann genannt, nicht
    verlinkt (``APP_NAME_ONLY``).
    """

    kommentare: bool = True
    beitraege: bool = True
    links: bool = False
    privatkontakt: bool = True
    regeln_gelesen: bool = False

    @classmethod
    def aus_regeln(
        cls, regeln: Regelbefund | None, qualifikation: Qualifikation
    ) -> Erlaubnis:
        """Die Erlaubnis aus dem, was wir ueber die Gruppe wissen.

        Zwei Quellen, und die Rangfolge ist die des Projekts: Die **Regeln der
        Gruppe** binden, die **Beobachtung** kann nur enger machen
        (``qualifikation.beurteile``). Deshalb wird hier nichts neu
        entschieden - es wird uebersetzt.

        ``links`` ist der einzige Wert, der eine gelesene Regel **braucht**:
        Ohne gelesene Regeln bleibt er aus. Dass in einer Gruppe schon einmal
        ein Link durchging, heisst nicht, dass er erlaubt war. Kommentiert
        wird dort trotzdem, nur ohne Adresse.
        """
        regeln = regeln or Regelbefund()
        gelesen = regeln.gelesen
        # ``UNGEEIGNET`` schliesst **beides** aus, nicht nur eines: Es
        # entsteht daraus, dass sowohl Beitraege als auch Kommentare
        # wiederholt abgelehnt wurden. Das Werbeverbot der Gruppe gehoert
        # seit dem 21.09.2026 nicht mehr dazu (siehe ``qualifikation``).
        nichts = qualifikation is Qualifikation.UNGEEIGNET
        return cls(
            kommentare=not nichts and qualifikation is not Qualifikation.OHNE_KOMMENTARE,
            beitraege=not nichts and qualifikation is not Qualifikation.OHNE_BEITRAEGE,
            links=(
                gelesen
                and not regeln.verbietet_links
                and qualifikation is not Qualifikation.OHNE_LINKS
            ),
            # Eine Regel gegen private Kontaktaufnahme lesen wir nicht eigens
            # aus. Bis zum 21.09.2026 galt sie als im Werbeverbot
            # eingeschlossen - mit dem Werbeverbot faellt auch diese
            # Ableitung weg.
            privatkontakt=True,
            regeln_gelesen=gelesen,
        )


@dataclass(frozen=True)
class Entscheidung:
    """Die Handlung und ihr Grund - nie das eine ohne das andere.

    Dieselbe Regel wie bei ``Group.score_reason`` und
    ``qualifikation.Befund.grund``. Hier wiegt sie schwerer als sonst: Der
    Grund ist das Einzige, woran sich spaeter nachvollziehen laesst, warum
    unter einem fremden Beitrag etwas steht - der Beitragstext selbst wird
    nicht gespeichert.
    """

    art: Antwortart = Antwortart.NO_REPLY
    grund: str = ""
    mit_link: bool = False

    @property
    def antwortet(self) -> bool:
        return self.art is not Antwortart.NO_REPLY

    @property
    def linkmodus(self) -> Linkmodus:
        """Wie viel von uns der Text tragen darf - abgeleitet, nicht gewaehlt.

        ``mit_link`` und der Modus koennen nicht auseinanderlaufen: Beide
        haengen an derselben ``Antwortart``, und nur sie wird entschieden.
        """
        return LINKMODUS[self.art]


# --- Die Teilfragen, jede fuer sich pruefbar -------------------------------
#
# Punkt 40 der Anforderung: nicht alles in einen grossen if/else-Block. Jede
# dieser Funktionen beantwortet genau eine Frage und laesst sich einzeln
# pruefen - und jede von ihnen kann "nein" sagen, ohne die anderen zu kennen.


def darf_kommentieren(erlaubnis: Erlaubnis) -> bool:
    """Nimmt diese Gruppe ueberhaupt Kommentare?"""
    return erlaubnis.kommentare


def soll_antworten(befund: Inhaltsbefund, anspruch: Anspruch | None = None) -> bool:
    """Gibt der Beitrag ueberhaupt genug her, worauf man antworten koennte?

    Drei Gruende dagegen, und sie bedeuten Verschiedenes: Der Text ist nicht
    lesbar (eine Aussage ueber uns), er hat mit unserem Angebot nichts zu tun
    (eine Aussage ueber den Beitrag), oder er reicht **an dieser Stelle**
    nicht aus (eine Aussage ueber die Gruppe). Alle drei fuehren zu
    ``NO_REPLY``, aber nur der zweite ist ein Urteil ueber den Beitrag.

    ``anspruch`` fehlt zu lassen heisst: die bisherige Regel, jeder Bezug
    genuegt. Das ist die Vorgabe fuer Aufrufer, die keine Gruppenklasse
    kennen - und nicht die Behauptung, sie sei ueberall richtig.
    """
    anspruch = anspruch or Anspruch()
    if not befund.lesbar:
        return False
    if _RELEVANZRANG[befund.relevanz] < _RELEVANZRANG[anspruch.mindestrelevanz]:
        return False
    return not (anspruch.verlangt_strecke and not befund.strecke)


def soll_privat_anbieten(befund: Inhaltsbefund, erlaubnis: Erlaubnis) -> bool:
    """Passt hier ein "schreib mir privat"?

    Nur, wo jemand etwas **sucht oder fragt**. Wer selbst anbietet ("bin
    Dienstag in Damaskus, hab Platz"), braucht keine Nachricht von uns - er
    ist die andere Haelfte des Marktplatzes, und eine private Anfrage an ihn
    waere ein Gespraech ueber sein Angebot, nicht ueber unseres.
    """
    if not erlaubnis.privatkontakt:
        return False
    return befund.absicht in (Absicht.SUCHT, Absicht.FRAGT)


def soll_app_nennen(
    befund: Inhaltsbefund,
    erlaubnis: Erlaubnis,
    anspruch: Anspruch | None = None,
) -> bool:
    """Gehoert die App zu **dieser** Frage - oder waere sie eingeworfen?

    Verlangt zweierlei: ein Thema, das die App betrifft (Versand oder
    Reise), und einen **belegten** Bezug. Fehlt eines davon, wird die App
    nicht genannt - auch dann nicht, wenn der Beitrag "fast" passt. "Fast"
    ist der Anfang von Spam.

    Die dritte Bedingung war bis zum 21.09.2026 ``erlaubnis.werbung``. Sie
    ist entfallen: Die Gruppen einer Kampagne hat ein Mensch ausgesucht, und
    ob dort geworben werden darf, ist damit beantwortet.

    **Belegt heisst ``HOCH`` oder ein erkannter Anlass** (seit 13.09.2026).
    Der Anlass ist der engere Beleg von beiden: ``HOCH`` verlangt Thema und
    ein genanntes Ziel, der Anlass Thema und einen konkreten Halbsatz
    ("مساحة بالشنطة", "wer nimmt mit", "Medikamente"). Ein Beitrag mit
    "عندي مساحة بالشنطة" nennt das Ziel oft nicht - er steht in einer
    Reisegruppe, dort ist es selbstverstaendlich. Ohne diese Zeile fiele
    genau der Fall aus, fuer den es die App gibt.
    """
    anspruch = anspruch or Anspruch()
    if (
        anspruch.anlass_pflicht
        and befund.relevanz is not Relevanz.HOCH
        and befund.anlass is Anlass.KEINER
    ):
        return False
    return befund.thema in (Thema.VERSAND, Thema.REISE)


def soll_link_nutzen(
    befund: Inhaltsbefund,
    erlaubnis: Erlaubnis,
    anspruch: Anspruch | None = None,
) -> bool:
    """Braucht diese Antwort einen Link - und darf sie einen tragen?

    Zwei Fragen in einer, und beide muessen mit ja beantwortet sein. Ein Link
    ist kein Beiwerk: Er macht aus einem Hinweis eine Werbung, und in einer
    Gruppe, die Links nicht ausdruecklich erlaubt, ist er der haeufigste Grund
    fuer eine Ablehnung.
    """
    return erlaubnis.links and soll_app_nennen(befund, erlaubnis, anspruch)


# --- Die Zusammenfuehrung --------------------------------------------------
def entscheide(
    befund: Inhaltsbefund,
    erlaubnis: Erlaubnis,
    anspruch: Anspruch | None = None,
) -> Entscheidung:
    """Eine Handlung fuer **einen** Beitrag. Die Reihenfolge ist begruendet.

    1. **Nimmt die Gruppe Kommentare?** Sonst eruebrigt sich alles Weitere.
    2. **Ist der Text lesbar und reicht sein Bezug hier aus?** Sonst
       ``NO_REPLY`` - und der Grund nennt, welches von beidem fehlte. Was
       "ausreicht", haengt an der Gruppenklasse (``anspruch``): In einer
       Reisegruppe genuegt eine Beruehrung, in einer allgemeinen Gruppe muss
       der Weg ausgeschrieben dastehen.
    3. **Von innen nach aussen**: erst die naheliegendste Form, die die Gruppe
       zulaesst. Wo Werbung erlaubt und der Bezug belegt ist, darf die App
       genannt werden; wo ausserdem Links erlaubt sind, mit Link. Wo nicht,
       bleibt die private Kontaktaufnahme - und wo auch die nicht passt, die
       blosse Hilfe.
    4. **Und wenn nichts davon passt, steht dort nichts.** Das ist der
       Normalfall und kein Mangel.
    """
    if not darf_kommentieren(erlaubnis):
        return Entscheidung(grund="Gruppe nimmt keine Kommentare")

    anspruch = anspruch or Anspruch()

    if not befund.lesbar:
        return Entscheidung(grund="kein lesbarer Text")

    if not soll_antworten(befund, anspruch):
        # Zwei verschiedene Auskuenfte, und die Unterscheidung ist die halbe
        # Anforderung: "kein Bezug" ist ein Urteil ueber den Beitrag, "zu
        # schwach fuer diese Gruppe" eines ueber den Ort. Wer beides gleich
        # benennt, sieht spaeter im Protokoll nicht, ob die Gruppe nichts
        # hergibt oder ob wir dort nur strenger sind.
        if befund.relevanz is Relevanz.KEINE:
            return Entscheidung(grund=f"kein Bezug zum Angebot ({befund.thema.value})")
        return Entscheidung(
            grund=(
                f"Bezug zu schwach fuer diese Gruppe "
                f"({befund.relevanz.value}, verlangt {anspruch.mindestrelevanz.value}"
                + (" + Strecke" if anspruch.verlangt_strecke else "")
                + ")"
            )
        )

    if soll_app_nennen(befund, erlaubnis, anspruch):
        mit_link = soll_link_nutzen(befund, erlaubnis, anspruch)
        art = (
            Antwortart.DIRECT_APP_RECOMMENDATION
            if mit_link
            else Antwortart.CONTEXTUAL_APP_MENTION
        )
        return Entscheidung(
            art=art,
            grund=f"{befund.grund}, Bezug belegt" + ("" if mit_link else ", ohne Link"),
            mit_link=mit_link,
        )

    if soll_privat_anbieten(befund, erlaubnis):
        # Der haeufigste Fall bei ``MITTEL``: Es gibt eine Beruehrung, aber
        # keinen Beleg - dann wird nicht geworben, sondern ein Gespraech
        # angeboten. Und bei ungelesenen Regeln ist es die vorsichtigere
        # Handlung, die uebrigbleibt.
        grund = befund.grund
        if not erlaubnis.regeln_gelesen:
            grund += ", Regeln ungelesen - vorsichtig"
        return Entscheidung(
            art=Antwortart.PRIVATE_CONTACT_SUGGESTION, grund=grund, mit_link=False
        )

    return Entscheidung(grund=f"keine passende Form ({befund.grund})")


__all__ = [
    "LINKMODUS",
    "WIRBT",
    "Anspruch",
    "Antwortart",
    "Entscheidung",
    "Erlaubnis",
    "Linkmodus",
    "darf_kommentieren",
    "entscheide",
    "soll_antworten",
    "soll_app_nennen",
    "soll_link_nutzen",
    "soll_privat_anbieten",
]
