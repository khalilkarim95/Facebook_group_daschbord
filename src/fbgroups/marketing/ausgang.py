"""Was Facebook auf einen Versuch geantwortet hat - und was daraus folgt.

Zwei Fragen, zwei Aufzaehlungen:

* ``Ausgangsart`` beantwortet die Frage des **Laufs**: Zaehlt das gegen das
  Konto (``RATE_LIMIT`` - die Aktion pausiert, ``grenzen.sperre_bis``), gegen
  die Gruppe (``GRUPPENLIMIT`` - die naechste Gruppe kommt dran) oder gegen
  gar nichts (``TECHNISCH``)?
* ``Ablehnungsgrund`` beantwortet die Frage des **Menschen** in der
  Uebersicht: Was ist da eigentlich passiert? Gespeichert in
  ``post_versuche.grund``.

Bis zum 23.09.2026 stand beides in ``qualifikation.py``. Die Qualifikation
selbst - Gruppenregeln lesen, Einstufung, Sperre nach wiederholter
Ablehnung - ist auf Anweisung des Nutzers entfernt; was hier steht, braucht
der Lauf weiterhin: Ohne die Erkennung der Bremse der Gegenseite liefe er
nach einer Sperre einfach weiter.

**Im Zweifel ``TECHNISCH``.** Am 11.09.2026 liess ein geschlossenes
Browserfenster dreissig Fassungen scheitern; ein Abbruch der Technik und eine
Ablehnung sehen im Protokoll gleich aus und bedeuten das Gegenteil. Ein
technischer Fehlschlag zaehlt deshalb nicht gegen eine Fassung.
"""

from __future__ import annotations

from enum import StrEnum

from fbgroups.textnorm import normalize


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
