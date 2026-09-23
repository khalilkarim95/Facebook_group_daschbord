"""Darf in dieser Gruppe ueberhaupt etwas stehen? - die Frage **vor** dem Text.

## Was dieses Modul ist

Der fehlende Schritt zwischen "wir kennen 314 Gruppen" und "wir posten in 314
Gruppen". Es beantwortet je Gruppe: Sind wir Mitglied? Was sagen ihre Regeln?
Und was ist bei den bisherigen Versuchen tatsaechlich herausgekommen?

Rein wie ``kaltmodus.py`` und ``beitritt.py``: kein Netz, keine Datenbank,
kein Playwright. Der Aufrufer reicht herein, was er weiss, und bekommt ein
Urteil mit Begruendung zurueck. Das ist dieselbe Aufteilung wie ueberall im
Projekt - die Regel steht hier, die Handlung woanders.

## Warum die Regeln der Gruppe binden und die Beobachtung nur einschraenkt

Eine Gruppe, deren Regeln "keine Links" sagen, bleibt ``OHNE_LINKS``, auch
wenn einmal ein Kommentar mit Link durchgegangen ist. Dass etwas moeglich
war, heisst nicht, dass es erlaubt war - und die Regeln der Gruppe zu umgehen
ist ausdruecklich nicht das Ziel. Umgekehrt gilt es sehr wohl: Eine Gruppe
ohne verbietende Regel, die Kommentare mit Link wiederholt ablehnt, wird
``OHNE_LINKS``. **Beobachtungen koennen nur enger machen, nie weiter.**

## Warum "nicht gelesen" etwas anderes ist als "erlaubt"

``Regelbefund.gelesen`` unterscheidet "die Gruppe verbietet nichts" von "wir
haben nicht nachgesehen". Derselbe Gedanke wie bei ``Seitenbefund.erreichbar``
und bei ``Group.score is None``: Eine fehlende Angabe ist eine Aussage ueber
**uns**, keine ueber die Gruppe. Solange die Regeln ungelesen sind, steht eine
Gruppe auf ``BEWERTUNG`` - versuchen darf man dort, aber es ist ein Versuch
und kein Urteil.

## Warum ein Fehlschlag klassifiziert wird

Am 11.09.2026 hat ein geschlossener Browserfenster dreissig Fassungen
scheitern lassen; danach galten 45 Gruppen als erschoepft. Ein Abbruch der
Technik und eine Ablehnung der Gruppe sehen im Protokoll gleich aus, bedeuten
aber das Gegenteil: Das eine sagt nichts ueber die Gruppe, das andere alles.
``klassifiziere`` trennt sie, und im Zweifel gilt ``TECHNISCH`` - eine
geratene Ablehnung verurteilte eine Gruppe, gegen die nichts vorliegt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from fbgroups.marketing.models import Texttyp
from fbgroups.textnorm import normalize

# Wie viele gleichlautende Ablehnungen ein Urteil tragen. Eine einzelne kann
# alles sein - ein Ausrutscher der Seite, ein Moderator in schlechter Laune.
# Zwei in derselben Richtung sind ein Muster.
MINDEST_BEOBACHTUNGEN = 2


class Ausgangsart(StrEnum):
    """Woran ein Versuch gescheitert ist - und ob das die Gruppe betrifft.

    ``MODERATION`` ist ein Urteil der Gruppe ueber unseren Inhalt.
    ``TECHNISCH`` ist ein Urteil ueber gar nichts: ein geschlossener Browser,
    ein Zeitablauf, ein Feld, das nicht geladen hat.
    """

    ERFOLG = "erfolg"
    MODERATION = "moderation"
    TECHNISCH = "technisch"
    GRUPPENLIMIT = "gruppenlimit"
    """Die **Gruppe** nimmt gerade nichts mehr an - nicht das Konto.

    "Du hast das Limit fuer freizugebende Inhalte in dieser Gruppe erreicht"
    (12.09.2026). Es heisst: Dort warten schon genug Beitraege von uns auf
    die Freigabe eines Moderators. Weder ein Urteil ueber den Text noch eine
    Sperre des Kontos - in der naechsten Gruppe geht es sofort weiter.

    Getrennt von ``RATE_LIMIT``, weil die Folge eine andere ist: Eine Bremse
    des Kontos pausiert die **Aktion** (keine Kommentare mehr, ueberall),
    diese Meldung nur **diese Gruppe**. Wer beides gleich behandelt, haelt
    wegen einer vollen Warteschlange sechs Kampagnen an.
    """

    RATE_LIMIT = "rate_limit"
    """Die Gegenseite bremst - und das ist weder das eine noch das andere.

    Kein Urteil ueber die Gruppe (die Gruppe hat nichts getan) und kein
    technischer Fehler (der Browser arbeitet einwandfrei). Was folgt, ist
    ausschliesslich eine Pause fuer **diese** Aktion: ``grenzen.sperre_bis``.

    Getrennt seit dem 12.09.2026. Vorher fiel eine Bremsung unter
    ``TECHNISCH`` - richtig in der Wirkung (sie zaehlt nicht gegen die
    Gruppe), aber falsch in der Folge: Der Lauf machte sofort weiter und
    bestaetigte der Gegenseite genau das Muster, wegen dessen sie gebremst
    hatte.
    """


class Qualifikation(StrEnum):
    """Wie weit eine Gruppe auf dem Weg zur Arbeitsliste ist.

    Die Reihenfolge ist der Trichter: entdeckt, Beitritt noetig, angefragt,
    in Bewertung, dann eines der Urteile. Die drei mittleren Urteile sind
    **Einschraenkungen**, kein Ausschluss: In einer Gruppe ohne Links darf
    weiterhin der Beitrag stehen, in einer ohne Kommentare der Beitrag.
    """

    UNBEKANNT = "unbekannt"
    BEITRITT_NOETIG = "beitritt_noetig"
    BEITRITT_ANGEFRAGT = "beitritt_angefragt"
    BEWERTUNG = "bewertung"
    GEEIGNET = "geeignet"
    OHNE_LINKS = "ohne_links"
    OHNE_KOMMENTARE = "ohne_kommentare"
    OHNE_BEITRAEGE = "ohne_beitraege"
    UNGEEIGNET = "ungeeignet"


#: Die Stufen des Trichters in ihrer Reihenfolge - fuer die Anzeige, nicht
#: fuer den Ablauf. Dieselbe Trennung wie bei ``FUNNEL_ORDER``: Die Reihenfolge
#: ordnet die Darstellung und behauptet keinen Zwangsweg.
TRICHTER: tuple[Qualifikation, ...] = (
    Qualifikation.UNBEKANNT,
    Qualifikation.BEITRITT_NOETIG,
    Qualifikation.BEITRITT_ANGEFRAGT,
    Qualifikation.BEWERTUNG,
    Qualifikation.GEEIGNET,
    Qualifikation.OHNE_LINKS,
    Qualifikation.OHNE_KOMMENTARE,
    Qualifikation.OHNE_BEITRAEGE,
    Qualifikation.UNGEEIGNET,
)

BESCHRIFTUNG: dict[Qualifikation, str] = {
    Qualifikation.UNBEKANNT: "unbekannt",
    Qualifikation.BEITRITT_NOETIG: "Beitritt noetig",
    Qualifikation.BEITRITT_ANGEFRAGT: "Beitritt angefragt",
    Qualifikation.BEWERTUNG: "in Bewertung",
    Qualifikation.GEEIGNET: "geeignet",
    Qualifikation.OHNE_LINKS: "ohne Links",
    Qualifikation.OHNE_KOMMENTARE: "ohne Kommentare",
    Qualifikation.OHNE_BEITRAEGE: "ohne Beitraege",
    Qualifikation.UNGEEIGNET: "ungeeignet",
}


# --- Was die Gruppe selbst sagt --------------------------------------------
#
# Die Formulierungen stehen in drei Sprachen nebeneinander, weil die Gruppen
# des Bestands sie so schreiben: deutsche Gruppen deutsch, arabische arabisch,
# und Facebooks eigene Bausteine oft englisch. Absichtlich weit gefasst und
# ohne Wortgrenze im Arabischen - dieselbe Ueberlegung wie in ``textnorm.py``:
# Im Arabischen haengen Artikel und Praepositionen am Wort.
_KEINE_LINKS = (
    "keine links", "keine verlinkung", "links sind nicht", "links verboten",
    "no links", "no external links",
    "ممنوع الروابط", "ممنوع وضع روابط", "بدون روابط", "لا روابط",
)
_KEINE_WERBUNG = (
    "keine werbung", "werbung verboten", "werbung ist nicht", "kein verkauf",
    "no advertising", "no ads", "no promotion", "no self-promotion",
    "ممنوع الاعلان", "ممنوع الإعلان", "ممنوع الاعلانات", "ممنوع الإعلانات",
    "ممنوع الترويج",
)
_FREIGABE = (
    "muessen genehmigt", "müssen genehmigt", "von admins genehmigt",
    "beitraege werden gepr", "beiträge werden gepr", "vor der veroeffentlichung gepr",
    "post approval", "approved by an admin", "admin approval", "pending approval",
    "المنشورات تحتاج موافقة", "بموافقة المشرف", "موافقة الادارة", "موافقة الإدارة",
)
_NEUE_OHNE_LINKS = (
    "neue mitglieder duerfen keine", "neue mitglieder dürfen keine",
    "new members can't post links", "new members cannot post links",
    "الاعضاء الجدد ممنوع", "الأعضاء الجدد ممنوع",
)

# Facebooks eigene Ablehnung, wie sie im Dialog steht (11.09.2026):
# "Dein Kommentar wurde abgelehnt ... Link in Kommentar".
_LINK_IN_KOMMENTAR = ("link in kommentar", "link in comment", "رابط في التعليق")

# Die Bremse der Gegenseite. Sie klingt wie eine Ablehnung ("gesperrt",
# "blockiert") und ist doch das Gegenteil: kein Urteil ueber den Inhalt,
# sondern ueber die Geschwindigkeit. Deshalb wird sie **vor** beiden anderen
# geprueft - "voruebergehend gesperrt" enthaelt "gesperrt", und wer das als
# Moderation liest, verurteilt eine Gruppe fuer unsere Eile.
# Die Meldung der **Gruppe**, nicht des Kontos. Sie wird vor allen anderen
# geprueft: Sie enthaelt "Limit" (wie die Bremse) und "erreicht", und beide
# anderen Listen wuerden sie falsch einordnen.
_GRUPPENLIMIT = (
    "limit fuer freizugebende", "limit für freizugebende",
    "freizugebende inhalte", "limit fuer inhalte", "limit für inhalte",
    "limit for content to be approved", "limit of pending",
    "pending posts limit", "too many pending",
    "الحد الاقصى للمحتوى", "الحد الأقصى للمحتوى", "وصلت الى الحد", "وصلت إلى الحد",
)

_RATE_LIMIT = (
    "zu oft", "zu schnell", "zu viele", "voruebergehend gesperrt",
    "vorübergehend gesperrt", "voruebergehend blockiert", "spaeter erneut",
    "später erneut", "versuche es spaeter", "versuche es später",
    "warte ein", "eingeschraenkt", "eingeschränkt",
    "temporarily blocked", "temporarily restricted", "action blocked",
    "try again later", "too many", "too fast", "slow down", "rate limit",
    "you're going too fast", "limit reached",
    "تم حظرك مؤقتا", "محظور مؤقتا", "حاول مرة اخرى لاحقا", "حاول لاحقا",
    "تم حظر هذا الاجراء", "بسرعة كبيرة", "عدد كبير من",
)


@dataclass(frozen=True)
class Regelbefund:
    """Was in den Regeln der Gruppe steht - und ob wir ueberhaupt nachsahen.

    ``gelesen`` ist der wichtigste Wert: Ohne ihn liesse sich "die Gruppe
    verbietet nichts" nicht von "wir haben nicht nachgesehen" unterscheiden,
    und die Abwesenheit einer Regel waere eine Erlaubnis, die niemand erteilt
    hat.
    """

    gelesen: bool = False
    keine_links: bool = False
    keine_werbung: bool = False
    freigabe_noetig: bool = False
    neue_ohne_links: bool = False

    @property
    def verbietet_links(self) -> bool:
        return self.keine_links or self.neue_ohne_links

    def zusammenfassung(self) -> str:
        """Die geltenden Regeln als Text - leer, wenn keine greift."""
        if not self.gelesen:
            return "Regeln nicht gelesen"
        teile = [
            wort
            for wort, gilt in (
                ("keine Werbung", self.keine_werbung),
                ("keine Links", self.keine_links),
                ("neue Mitglieder ohne Links", self.neue_ohne_links),
                ("Freigabe noetig", self.freigabe_noetig),
            )
            if gilt
        ]
        return ", ".join(teile)


def lies_regeln(text: str) -> Regelbefund:
    """Liest die Regeln aus dem Text einer Gruppenseite. Rein, ohne Netz.

    Getrennt vom Abruf wie ``gruppenseite.lies_seite``, und aus demselben
    Grund: Die Auswertung ist die Stelle, an der eine erfundene Aussage
    entstuende, und genau die muss ein Test ohne facebook.com festhalten
    koennen.

    Sie steht hier und nicht in ``extract/``: Was eine Gruppe erlaubt, ist
    eine Frage des Marketing-Kanals und nicht der Anreicherung des Bestands -
    ``extract`` dient dem Suchweg und soll von Kampagnen nichts wissen.

    Ein **leerer** Text ergibt ``gelesen=False``. Aus nichts abzulesen, dass
    nichts verboten ist, waere die Erlaubnis aus dem Nichts.
    """
    if not text or not text.strip():
        return Regelbefund()

    klein = re.sub(r"\s+", " ", text.lower())

    def trifft(muster: tuple[str, ...]) -> bool:
        return any(wort in klein for wort in muster)

    return Regelbefund(
        gelesen=True,
        keine_links=trifft(_KEINE_LINKS),
        keine_werbung=trifft(_KEINE_WERBUNG),
        freigabe_noetig=trifft(_FREIGABE),
        neue_ohne_links=trifft(_NEUE_OHNE_LINKS),
    )


# --- Was tatsaechlich herauskam --------------------------------------------
_TECHNISCH = (
    "browsercontext", "target page", "context or browser", "has been closed",
    "timeout", "zeitablauf", "net::", "err_", "nicht gefunden", "not found",
    "kein textfeld", "kein schreibfeld", "keine beitraege", "keine gruppen-url",
    "connection", "verbindung",
)
_MODERATION = (
    "abgelehnt", "rejected", "declined", "genehmigung", "approval",
    "pending", "wartet auf", "zur pruefung", "zur prüfung", "spam",
    "verstoesst", "verstößt", "violat", "blockiert", "gesperrt",
    "مرفوض", "تم رفض", "بانتظار الموافقة", "مخالف",
    *_LINK_IN_KOMMENTAR,
)


#: Dieselben Listen in der Normalform, in der auch der Fehlertext verglichen
#: wird. ``normalize`` loest Umlaute auf und entfernt arabische Diakritika -
#: sonst entschiede die Schreibweise ("vorübergehend" gegen
#: "voruebergehend") darueber, ob eine Bremse erkannt wird.
_NORMAL_GRUPPENLIMIT = tuple(normalize(wort) for wort in _GRUPPENLIMIT)
_NORMAL_RATE_LIMIT = tuple(normalize(wort) for wort in _RATE_LIMIT)
_NORMAL_TECHNISCH = tuple(normalize(wort) for wort in _TECHNISCH)
_NORMAL_MODERATION = tuple(normalize(wort) for wort in _MODERATION)


def klassifiziere(fehler: str) -> Ausgangsart:
    """Ablehnung der Gruppe oder Panne der Technik? Rein, ueber den Fehlertext.

    **Im Zweifel ``TECHNISCH``.** Eine geratene Ablehnung verurteilte eine
    Gruppe, gegen die nichts vorliegt - und sie wuerde aus der Arbeitsliste
    fallen, ohne dass jemand das entschieden haette. Ein uebersehener
    Moderationsfall kostet dagegen einen weiteren Versuch; er faellt beim
    naechsten Mal auf.

    Die Reihenfolge ist begruendet:

    1. ``RATE_LIMIT`` zuerst, weil die Bremse wie eine Ablehnung klingt:
       "voruebergehend gesperrt" enthaelt "gesperrt". Wer das als Moderation
       liest, verurteilt eine Gruppe fuer **unsere** Eile.
    2. ``TECHNISCH`` davor: "Target page ... has been closed" enthaelt kein
       Moderationswort, aber manche Playwright-Meldung traegt "blockiert" und
       "timeout" nebeneinander, und dann ist die Technik der naeherliegende
       Grund.
    3. ``MODERATION`` zuletzt - nur was uebrigbleibt, ist ein Urteil der
       Gruppe.
    """
    if not fehler or not fehler.strip():
        return Ausgangsart.TECHNISCH
    klein = normalize(fehler)
    if any(wort in klein for wort in _NORMAL_GRUPPENLIMIT):
        return Ausgangsart.GRUPPENLIMIT
    if any(wort in klein for wort in _NORMAL_RATE_LIMIT):
        return Ausgangsart.RATE_LIMIT
    if any(wort in klein for wort in _NORMAL_TECHNISCH):
        return Ausgangsart.TECHNISCH
    if any(wort in klein for wort in _NORMAL_MODERATION):
        return Ausgangsart.MODERATION
    return Ausgangsart.TECHNISCH


class Ablehnungsgrund(StrEnum):
    """**Warum** Facebook einen Versuch nicht angenommen hat.

    ``Ausgangsart`` beantwortet die Frage, die der **Lauf** stellt: Zaehlt das
    gegen die Gruppe, gegen das Konto oder gegen gar nichts? Diese Aufzaehlung
    beantwortet die Frage, die ein **Mensch** stellt, wenn er hinterher in die
    Uebersicht sieht: Was ist da eigentlich passiert?

    Zwei Aufzaehlungen fuer zwei Fragen, und nicht eine fuer beide: Der Lauf
    darf nicht feiner unterscheiden, als er handeln kann - sonst haette jede
    neue Meldung von Facebook eine neue Verzweigung zur Folge. Der Bericht
    darf und soll es. ``fuer`` bildet die eine auf die andere ab, damit sie
    nicht auseinanderlaufen koennen.

    Gespeichert wird sie in ``post_versuche.grund`` (Migrationsschritt 22) -
    neben dem Fehlertext, nicht statt seiner: Der Text ist der Beleg, die
    Einstufung die Auskunft.
    """

    OK = "ok"
    LINK_REJECTED = "link_rejected"
    COMMENT_REQUIRES_REVIEW = "comment_requires_review"
    GROUP_COMMENT_RESTRICTED = "group_comment_restricted"
    GROUP_PENDING_LIMIT = "group_pending_limit"
    CONTENT_REJECTED = "content_rejected"
    TEMPORARY_PLATFORM_RESTRICTION = "temporary_platform_restriction"
    TECHNICAL_ERROR = "technical_error"
    UNKNOWN_FACEBOOK_RESPONSE = "unknown_facebook_response"


GRUND_BESCHRIFTUNG: dict[Ablehnungsgrund, str] = {
    Ablehnungsgrund.OK: "angenommen",
    Ablehnungsgrund.LINK_REJECTED: "Link abgelehnt",
    Ablehnungsgrund.COMMENT_REQUIRES_REVIEW: "wartet auf Freigabe",
    Ablehnungsgrund.GROUP_COMMENT_RESTRICTED: "Gruppe laesst nicht kommentieren",
    Ablehnungsgrund.GROUP_PENDING_LIMIT: "Freigabe-Warteschlange der Gruppe voll",
    Ablehnungsgrund.CONTENT_REJECTED: "Inhalt abgelehnt",
    Ablehnungsgrund.TEMPORARY_PLATFORM_RESTRICTION: "Konto voruebergehend gebremst",
    Ablehnungsgrund.TECHNICAL_ERROR: "technischer Fehler",
    Ablehnungsgrund.UNKNOWN_FACEBOOK_RESPONSE: "unklare Antwort",
}

#: Sie warten nur auf einen Moderator. Ein Grund fuer sich, weil der
#: Tracking-Link damit **heraus** ist - er wird bloss noch nicht geklickt, und
#: eine Null bei den Klicks ist dann kein Urteil ueber die Gruppe.
_WARTET = (
    "wartet auf", "zur pruefung", "zur prüfung", "ausstehend", "pending",
    "genehmigung", "approval", "wird geprueft", "wird geprüft",
    "بانتظار الموافقة", "قيد المراجعة", "بانتظار المراجعة",
)

#: Die Gruppe hat Kommentare fuer uns abgeschaltet - eine Eigenschaft der
#: Gruppe und keine Aussage ueber unseren Text.
_KOMMENTARE_AUS = (
    "kommentare sind deaktiviert", "kommentarfunktion", "kommentare wurden deaktiviert",
    "comments are turned off", "comments have been limited", "commenting is limited",
    "التعليقات معطلة", "تم ايقاف التعليقات", "تم إيقاف التعليقات",
)

# "Kein Kommentarfeld gefunden" steht bewusst **nicht** darin, obwohl es
# dasselbe bedeuten kann: Es ist eine Aussage ueber **unsere Suche**, nicht
# ueber die Gruppe - das Feld kann fehlen, weil die Seite nicht fertig geladen
# hat. Belegt wird es erst, wenn die Seite selbst etwas sagt; danach fragt
# ``actions.comment_on_post``, bevor es aufgibt.

#: Dieselben Listen in der Normalform - wie bei den Listen darueber: Ohne sie
#: entschiede die Schreibweise ("vorübergehend" gegen "voruebergehend"), ob
#: ein Grund erkannt wird.
_NORMAL_WARTET = tuple(normalize(wort) for wort in _WARTET)
_NORMAL_KOMMENTARE_AUS = tuple(normalize(wort) for wort in _KOMMENTARE_AUS)



def grund(fehler: str, *, erfolg: bool = False) -> Ablehnungsgrund:
    """Die feine Einstufung **einer** Antwort von Facebook. Rein, ohne Netz.

    Sie baut auf ``klassifiziere`` auf und laeuft ihr nicht davon: Was dort
    ``RATE_LIMIT`` heisst, heisst hier ``TEMPORARY_PLATFORM_RESTRICTION`` -
    derselbe Sachverhalt, nur ausgeschrieben. Nur innerhalb von
    ``MODERATION`` wird weiter unterschieden, denn nur dort gibt es etwas zu
    unterscheiden: Ein abgelehnter Link, ein Kommentar in der Warteschlange
    und eine Gruppe ohne Kommentarfunktion sind drei verschiedene Dinge, und
    sie verlangen drei verschiedene Handgriffe von einem Menschen.

    **Ein technischer Fehler wird nie zu einer Gruppenregel.** Das ist Punkt
    9 der Anforderung und die teuerste Verwechslung, die dieses Projekt
    gemacht hat: Am 11.09.2026 liess ein geschlossenes Browserfenster
    dreissig Fassungen scheitern, und danach galten 45 Gruppen als erschoepft.

    ``UNKNOWN_FACEBOOK_RESPONSE`` ist der ehrliche Rest: Es kam etwas zurueck,
    das keine der Listen kennt. Es als technischen Fehler zu buchen waere
    eine Behauptung ueber unseren Rechner, es als Ablehnung eine ueber die
    Gruppe.
    """
    if erfolg:
        return Ablehnungsgrund.OK
    if not fehler or not fehler.strip():
        return Ablehnungsgrund.TECHNICAL_ERROR

    art = klassifiziere(fehler)
    if art is Ausgangsart.GRUPPENLIMIT:
        return Ablehnungsgrund.GROUP_PENDING_LIMIT
    if art is Ausgangsart.RATE_LIMIT:
        return Ablehnungsgrund.TEMPORARY_PLATFORM_RESTRICTION

    klein = normalize(fehler)
    if ist_linkablehnung(fehler):
        return Ablehnungsgrund.LINK_REJECTED
    if any(wort in klein for wort in _NORMAL_WARTET):
        return Ablehnungsgrund.COMMENT_REQUIRES_REVIEW
    if any(wort in klein for wort in _NORMAL_KOMMENTARE_AUS):
        return Ablehnungsgrund.GROUP_COMMENT_RESTRICTED
    if art is Ausgangsart.MODERATION:
        return Ablehnungsgrund.CONTENT_REJECTED

    # Hier endet ``klassifiziere`` mit ``TECHNISCH``, und das hat zwei
    # verschiedene Gruende: Entweder stand ein technisches Wort darin
    # ("timeout", "has been closed") - oder gar keines, und ``TECHNISCH`` war
    # die vorsichtige Vorgabe. Fuer die **Handlung** ist das dasselbe (es
    # zaehlt nicht gegen die Gruppe), fuer den **Bericht** nicht: Das eine ist
    # eine Auskunft, das andere ein Eingestaendnis.
    if any(wort in klein for wort in _NORMAL_TECHNISCH):
        return Ablehnungsgrund.TECHNICAL_ERROR
    return Ablehnungsgrund.UNKNOWN_FACEBOOK_RESPONSE


def ist_linkablehnung(fehler: str) -> bool:
    """Nennt die Ablehnung ausdruecklich den Link? Dann ist es kein Raten mehr.

    Facebook schreibt den Grund hin ("Link in Kommentar - Der Kommentar
    enthaelt einen Link"). Wo das dasteht, braucht es keine zwei
    Beobachtungen: Die Gruppe hat es selbst gesagt.
    """
    klein = (fehler or "").lower()
    return any(wort in klein for wort in _LINK_IN_KOMMENTAR)


@dataclass(frozen=True)
class Beobachtung:
    """Was bei den bisherigen Versuchen in **dieser** Gruppe herauskam.

    Kommentare sind nach Link getrennt gezaehlt, und darin liegt der ganze
    Zweck: "mit Link abgelehnt, ohne Link durchgegangen" ist das Muster, das
    ``OHNE_LINKS`` traegt. In einer gemeinsamen Zahl waere es unsichtbar.

    Technische Fehlschlaege stehen bewusst in **keinem** Feld. Sie sagen
    nichts ueber die Gruppe, und mitgezaehlt machten sie aus einem geschlossenen
    Browser ein Urteil.
    """

    beitrag_erfolg: int = 0
    beitrag_moderation: int = 0
    mit_link_erfolg: int = 0
    mit_link_moderation: int = 0
    ohne_link_erfolg: int = 0
    ohne_link_moderation: int = 0
    #: Eine Ablehnung, die den Link ausdruecklich benennt. Zaehlt einzeln,
    #: weil sie allein schon genuegt.
    link_ausdruecklich: int = 0

    @property
    def kommentar_erfolg(self) -> int:
        return self.mit_link_erfolg + self.ohne_link_erfolg

    @property
    def kommentar_moderation(self) -> int:
        return self.mit_link_moderation + self.ohne_link_moderation

    @property
    def leer(self) -> bool:
        """Noch nichts beobachtet - weder Erfolg noch Ablehnung."""
        return (
            self.beitrag_erfolg
            + self.beitrag_moderation
            + self.kommentar_erfolg
            + self.kommentar_moderation
        ) == 0


@dataclass(frozen=True)
class Befund:
    """Das Urteil und sein Grund - nie das eine ohne das andere.

    Dieselbe Regel wie bei ``Group.score`` und ``score_reason``: Eine
    Einstufung, deren Begruendung man nachschlagen muss, wird nicht
    nachgeschlagen, sondern geglaubt.
    """

    qualifikation: Qualifikation
    grund: str = ""

    @property
    def beschriftung(self) -> str:
        return BESCHRIFTUNG.get(self.qualifikation, self.qualifikation.value)


def beurteile(
    *,
    mitglied: bool,
    beitritt_angefragt: bool = False,
    regeln: Regelbefund | None = None,
    beobachtung: Beobachtung | None = None,
) -> Befund:
    """Das Urteil ueber **eine** Gruppe. Rein, aus uebergebenen Werten.

    Die Reihenfolge der Pruefungen ist nicht beliebig:

    1. **Mitgliedschaft zuerst.** Ohne sie laesst Facebook in den meisten
       Gruppen weder posten noch kommentieren; jede Bewertung des Inhalts
       waere verfrueht.
    2. **Dann die Regeln der Gruppe.** Sie binden, und zwar auch dann, wenn
       die Beobachtung etwas anderes nahelegt: Dass ein Link einmal
       durchging, heisst nicht, dass er erlaubt war.
    3. **Zuletzt die Beobachtung.** Sie kann nur **enger** machen. Eine
       Gruppe, deren Regeln Links verbieten, wird durch einen geglueckten
       Versuch nicht wieder ``GEEIGNET``.
    """
    regeln = regeln or Regelbefund()
    beobachtung = beobachtung or Beobachtung()

    if not mitglied:
        if beitritt_angefragt:
            return Befund(
                Qualifikation.BEITRITT_ANGEFRAGT,
                "Beitrittsanfrage laeuft - Facebook laesst oft wochenlang offen",
            )
        return Befund(
            Qualifikation.BEITRITT_NOETIG,
            "noch kein Mitglied - zuerst die Beitrittsanfrage",
        )

    # --- Die Regeln der Gruppe --------------------------------------------
    #
    # **Das Werbeverbot sperrt seit dem 21.09.2026 nicht mehr** (Anweisung
    # des Nutzers). Es stand hier und machte aus einer Gruppe ``UNGEEIGNET``
    # - damit fielen Beitrag und Kommentar zugleich aus, und im Protokoll
    # stand rundenlang "keine Werbung erlaubt". Die Gruppen einer Kampagne
    # hat ein Mensch ausgesucht und eingestuft; ob dort geworben werden
    # darf, ist damit beantwortet.
    #
    # ``keine_werbung`` wird weiterhin **gelesen** und steht in der
    # Zusammenfassung: Es ist eine Auskunft ueber die Gruppe, nur keine
    # Sperre mehr. Was die Annahme betrifft, bindet unveraendert - die
    # Linkregeln unten und jede Beobachtung.

    # --- Die Beobachtung: kann nur einschraenken ---------------------------
    kommentare_tot = (
        beobachtung.kommentar_erfolg == 0
        and beobachtung.kommentar_moderation >= MINDEST_BEOBACHTUNGEN
        and beobachtung.ohne_link_moderation > 0
    )
    beitraege_tot = (
        beobachtung.beitrag_erfolg == 0
        and beobachtung.beitrag_moderation >= MINDEST_BEOBACHTUNGEN
    )
    # "mit Link abgelehnt, ohne Link durchgegangen" - oder die Ablehnung hat
    # den Link selbst benannt, dann genuegt sie allein.
    links_tot = beobachtung.link_ausdruecklich > 0 or (
        beobachtung.mit_link_erfolg == 0
        and beobachtung.mit_link_moderation >= MINDEST_BEOBACHTUNGEN
    )

    if kommentare_tot and beitraege_tot:
        return Befund(
            Qualifikation.UNGEEIGNET,
            f"{beobachtung.kommentar_moderation} Kommentare und "
            f"{beobachtung.beitrag_moderation} Beitraege abgelehnt",
        )
    if kommentare_tot:
        return Befund(
            Qualifikation.OHNE_KOMMENTARE,
            f"{beobachtung.kommentar_moderation} Kommentare abgelehnt, keiner durchgegangen",
        )
    if beitraege_tot:
        return Befund(
            Qualifikation.OHNE_BEITRAEGE,
            f"{beobachtung.beitrag_moderation} Beitraege abgelehnt, keiner durchgegangen",
        )
    if regeln.verbietet_links:
        return Befund(
            Qualifikation.OHNE_LINKS,
            f"Regeln der Gruppe: {regeln.zusammenfassung()}",
        )
    if links_tot:
        grund = (
            "Facebook nennt den Link als Ablehnungsgrund"
            if beobachtung.link_ausdruecklich
            else f"{beobachtung.mit_link_moderation}x mit Link abgelehnt"
        )
        if beobachtung.ohne_link_erfolg:
            grund += f", {beobachtung.ohne_link_erfolg}x ohne Link durchgegangen"
        return Befund(Qualifikation.OHNE_LINKS, grund)

    if not regeln.gelesen:
        return Befund(
            Qualifikation.BEWERTUNG,
            "Mitglied, aber die Regeln der Gruppe sind ungelesen",
        )
    if beobachtung.leer:
        return Befund(
            Qualifikation.BEWERTUNG,
            f"Mitglied, Regeln gelesen ({regeln.zusammenfassung() or 'nichts verboten'}), "
            "noch kein Versuch",
        )
    return Befund(
        Qualifikation.GEEIGNET,
        f"{beobachtung.kommentar_erfolg} Kommentare und "
        f"{beobachtung.beitrag_erfolg} Beitraege sind durchgegangen",
    )


#: Die Stufen, die von der **Mitgliedschaft** handeln und nicht vom Inhalt.
#:
#: Sie stehen getrennt, weil ueber sie ein anderer Schalter entscheidet:
#: ``automatik.mitgliedschaft_pflicht`` sagt, ob der Vermerk "kein Mitglied"
#: eine Sperre ist. Der Nutzer hat ihn am 01.09.2026 abgeschaltet, weil der
#: Vermerk unseren Arbeitsstand beschreibt und nicht, ob Facebook dort
#: schreiben laesst - in ``Betaraqiq-Test Syrer in Berlin`` standen drei
#: veroeffentlichte Kommentare, waehrend er auf ``beitritt_angefragt`` stand.
BEITRITTSSTUFEN: frozenset[Qualifikation] = frozenset(
    {
        Qualifikation.UNBEKANNT,
        Qualifikation.BEITRITT_NOETIG,
        Qualifikation.BEITRITT_ANGEFRAGT,
    }
)


def darf(qualifikation: Qualifikation, texttyp: Texttyp, *, mit_link: bool) -> bool:
    """Darf dieser Text in diese Gruppe? Die **eine** Stelle, die das sagt.

    ``BEWERTUNG`` erlaubt ausdruecklich: Aus nichts entsteht keine
    Beobachtung, und eine Gruppe, in der nie etwas versucht wird, bliebe fuer
    immer unbewertet. Der erste Versuch ist die Bewertung.

    Die drei Einschraenkungen lassen jeweils das andere zu - eine Gruppe ohne
    Links nimmt denselben Kommentar ohne Link, eine ohne Kommentare nimmt den
    Beitrag. Nur ``UNGEEIGNET`` und die Beitrittsstufen lassen gar nichts.
    """
    if qualifikation in BEITRITTSSTUFEN:
        return False
    return darf_nach_regeln(qualifikation, texttyp, mit_link=mit_link)


def darf_nach_regeln(
    qualifikation: Qualifikation, texttyp: Texttyp, *, mit_link: bool
) -> bool:
    """Dasselbe Urteil **ohne** die Beitrittsstufen - und es bindet immer.

    Der Unterschied zu ``darf`` ist die Antwort auf zwei verschiedene Fragen:

    * "Sind wir drin?" - das ist eine Angabe ueber **unseren** Stand, und ob
      sie sperrt, entscheidet ``automatik.mitgliedschaft_pflicht``.
    * "Was erlaubt die Gruppe?" - das ist eine Angabe ueber **die Gruppe**,
      und sie bindet ohne Schalter. Eine Gruppe, deren Regeln Links
      verbieten, bekommt keinen Link; eine, die Kommentare wiederholt
      abgelehnt hat, bekommt keinen Kommentar. Die Regeln der Gruppe zu
      umgehen ist ausdruecklich nicht das Ziel, und ein Schalter, der es
      erlaubte, waere ein Schalter zum Regelbruch.

    ``qualifikation.pflicht`` entscheidet deshalb seit dem 12.09.2026 nur
    noch ueber die Beitrittsstufen. Was hier steht, gilt immer.

    Technische Fehlschlaege fuehren zu keinem dieser Urteile: ``beurteile``
    sieht sie gar nicht erst (``Beobachtung`` zaehlt nur ``MODERATION``), ein
    abgestuerzter Browser ist also weder Verbot noch Erlaubnis.
    """
    if qualifikation is Qualifikation.UNGEEIGNET:
        return False
    if qualifikation is Qualifikation.OHNE_LINKS and mit_link:
        return False
    if qualifikation is Qualifikation.OHNE_KOMMENTARE and texttyp is Texttyp.KOMMENTAR:
        return False
    return not (
        qualifikation is Qualifikation.OHNE_BEITRAEGE and texttyp is Texttyp.POST
    )


def pflicht(config) -> bool:  # noqa: ANN001 - AppConfig, ohne Import fuer die Reinheit
    """Sperren auch die **Beitrittsstufen** - oder nur die Regeln der Gruppe?

    Seit dem 12.09.2026 ist das die ganze Frage, die dieser Schalter noch
    stellt. Was die Gruppe selbst erlaubt, bindet ohne ihn: kein Link, wo
    Links verboten sind, kein Kommentar, wo Kommentare abgelehnt werden
    (``darf_nach_regeln``). Ein Schalter, der das aufhoebe, waere ein
    Schalter zum Regelbruch.

    Uebrig bleibt die Frage nach der Mitgliedschaft, und dort ist die Vorgabe
    weiterhin **aus**: Am 11.09.2026 stand keine einzige Gruppe auf
    ``GEEIGNET``, weil noch nie jemand ihre Regeln gelesen hat. Sofort scharf
    geschaltet haette der Schalter jede laufende Kampagne angehalten, bis
    eine vollstaendige Runde aus Beitritt und Bewertung durch ist - bei 50
    Anfragen am Tag also tagelang.

    Er ueberschneidet sich darin mit ``automatik.mitgliedschaft_pflicht``;
    beide beschreiben dieselbe Vorsicht aus verschiedenen Richtungen, und es
    genuegt, einen von beiden einzuschalten.
    """
    return bool(config.get("qualifikation", "pflicht", default=False))


__all__ = [
    "BEITRITTSSTUFEN",
    "BESCHRIFTUNG",
    "GRUND_BESCHRIFTUNG",
    "MINDEST_BEOBACHTUNGEN",
    "TRICHTER",
    "Ablehnungsgrund",
    "Ausgangsart",
    "Befund",
    "Beobachtung",
    "Qualifikation",
    "Regelbefund",
    "beurteile",
    "darf",
    "darf_nach_regeln",
    "grund",
    "ist_linkablehnung",
    "klassifiziere",
    "lies_regeln",
    "pflicht",
]
