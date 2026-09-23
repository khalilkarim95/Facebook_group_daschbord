"""Wie viel an einem Tag, und wie schnell hintereinander - je Aktion getrennt.

## Was dieses Modul ist

Die Rechnung hinter drei Fragen, die bisher an drei Stellen halb beantwortet
waren:

* **Wie viele** Beitrittsanfragen, Beitraege und Kommentare an einem Tag?
* **Wie lange** muss zwischen zwei davon liegen?
* **Was gilt**, wenn die Gegenseite bremst - und fuer welche Aktion?

Rein wie ``kaltmodus.py`` und ``beitritt.py``: kein
Netz, keine Datenbank, kein Playwright. Der Aufrufer reicht Zaehlerstaende
herein und bekommt ein Urteil.

## Je Aktion, nicht je Konto

Der Kern der Anforderung vom 12.09.2026 (Punkte 7 und 8): Ein Limit fuer
Kommentare ist **kein** Limit fuer Beitraege und erst recht keines fuer den
ganzen Lauf. Wer wegen einer gebremsten Aktion alles anhaelt, verliert die
Arbeit in den Gruppen, in denen nichts dagegen spricht - und lernt nichts
darueber, was tatsaechlich gesperrt war.

Deshalb hat jede Aktion ihre eigene Tagesmenge, ihren eigenen Takt und ihre
eigene Sperre. Was sie teilen, ist das Konto - und das ist genau der Grund,
warum die Zahlen klein sind.

## Eine Antwort zaehlt als Kommentar

``COMMENT_REPLY`` ist keine eigene Menge (Punkt 25). Eine Antwort auf einen
fremden Kommentar ist fuer die Gegenseite dasselbe wie ein neuer Kommentar;
sie getrennt zu zaehlen waere eine Umgehung der eigenen Grenze, und zwar eine,
die man sich selbst erlaubt hat.

## Die Zahlen sind unsere, nicht die von Facebook

``limits`` in ``settings.yaml`` sind **Planungswerte dieses Programms**. Sie
beschreiben, wie vorsichtig wir sein wollen, und nicht, was die Gegenseite
zulaesst - die veroeffentlicht das nicht. Wer sie als garantierte Grenze
liest, liest etwas hinein, das niemand zugesagt hat.

## Verhaeltnis zu den bisherigen Schaltern

``beitritt.einstellungen`` liest seit dem 12.09.2026 **hier**. Zwei Zahlen
fuer dieselbe Frage waeren zwei Wahrheiten: Eine Aenderung an einer von
beiden haette die andere still ueberstimmt, je nachdem, welcher Aufrufer
gerade fragt. Die alten Schluessel (``beitritt.anfragen_pro_tag``,
``beitritt.mindestabstand_minuten``) bleiben als Rueckfall gueltig, damit eine
bestehende Konfiguration sich durch ein Update nicht anders verhaelt.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class Aktion(StrEnum):
    """Was getan wird - und wofuer getrennt gezaehlt wird.

    ``KOMMENTAR`` deckt auch die Antwort auf einen fremden Kommentar ab; sie
    ist fuer die Gegenseite dasselbe. Siehe ``aus_texttyp``.
    """

    BEITRITT = "beitritt"
    POST = "post"
    KOMMENTAR = "kommentar"


#: Die vorsichtigen Vorgaben **im Code**, fuer den Fall, dass niemand etwas
#: gesagt hat. Gesagt wird es in ``config/settings.yaml`` - dieselbe
#: Aufteilung wie bei ``automatik.mitgliedschaft_pflicht``.
#:
#: Sie liegen bewusst am unteren Rand der Anforderung (3-5 / 5-8 / 2-4): Ein
#: zu niedriger Wert kostet einen Tag, ein zu hoher das Konto.
VORGABE: dict[Aktion, tuple[int, int, int]] = {
    #                 pro Tag, Abstand min, Abstand max (Minuten)
    Aktion.BEITRITT: (4, 30, 90),
    Aktion.KOMMENTAR: (6, 30, 90),
    Aktion.POST: (3, 120, 240),
}

#: Wie die Bloecke in ``settings.yaml`` heissen. Englisch, weil die
#: Anforderung sie so benennt, und weil ``limits``/``delays`` in einer
#: Konfigurationsdatei gelaeufiger sind als eine Uebersetzung.
_SCHLUESSEL: dict[Aktion, str] = {
    Aktion.BEITRITT: "join_requests",
    Aktion.KOMMENTAR: "comments",
    Aktion.POST: "posts",
}
_TAKT_SCHLUESSEL: dict[Aktion, str] = {
    Aktion.BEITRITT: "join_request",
    Aktion.KOMMENTAR: "comment",
    Aktion.POST: "post",
}

#: Wie lange eine Sperre mindestens und hoechstens gilt. Verdoppelt sich mit
#: jeder weiteren Bremsung derselben Aktion (``backoff_minuten``).
BACKOFF_BASIS_MINUTEN = 60
BACKOFF_HOECHSTENS_MINUTEN = 24 * 60


def aus_texttyp(texttyp: str) -> Aktion:
    """``post``/``kommentar`` - und eine Antwort zaehlt als Kommentar."""
    return Aktion.POST if str(texttyp) == "post" else Aktion.KOMMENTAR


@dataclass(frozen=True)
class Grenze:
    """Tagesmenge und Takt **einer** Aktion.

    ``pro_tag = 0`` schaltet die Aktion ab - dieselbe Bedeutung wie beim
    Gewicht ``0`` im Scoring: nicht "unbegrenzt", sondern "gar nicht".
    """

    pro_tag: int
    abstand_min: int
    abstand_max: int

    je_gruppe_taeglich: int = 0
    """Hoechstens so viele Handlungen in **derselben** Gruppe an einem Tag.

    ``0`` heisst hier **ohne Schranke**, und das ist die Umkehrung von
    ``pro_tag`` - Absicht, kein Versehen: Eine Aktion abzuschalten ist eine
    Entscheidung ("gar nicht"), eine Schranke wegzulassen heisst nur, dass
    die Tagesmenge allein zaehlt. Wer beides gleich bedeuten liesse, koennte
    die Schranke nicht mehr weglassen, ohne die Aktion zu beenden.

    Die Tagesmenge schuetzt das **Konto**, diese Zahl die **Gruppe**: Fuenf
    Kommentare am Tag, alle in derselben Gruppe, sind fuer deren Leser
    dasselbe Bild wie fuenfzig - und zwei Kommentare unter zwei Beitraegen
    desselben Menschen sind eine Ansprache zu viel. Sie ist damit zugleich
    die einzige Duplikatkontrolle, die ohne Autorennamen auskommt; die harte
    Projektgrenze laesst keine zu (siehe ``CLAUDE.md``).
    """

    @property
    def abgeschaltet(self) -> bool:
        return self.pro_tag <= 0


@dataclass(frozen=True)
class Grenzen:
    """Die Grenzen aller Aktionen zusammen - aus ``settings.yaml`` gelesen."""

    je_aktion: dict[Aktion, Grenze]

    def fuer(self, aktion: Aktion) -> Grenze:
        return self.je_aktion[aktion]


def einstellungen(config) -> Grenzen:  # noqa: ANN001 - AppConfig, ohne Import
    """Die Grenzen aus ``limits`` und ``delays`` - mit den alten Schluesseln als Rueckfall.

    Der Rueckfall ist kein Zierrat: Auf dem Server steht eine
    ``settings.yaml``, die ``beitritt.anfragen_pro_tag: 50`` kennt und die
    neuen Bloecke nicht. Ohne ihn faenden 50 Anfragen am Tag ploetzlich bei 4
    ihr Ende - eine Aenderung, die niemand angeordnet hat.
    """
    je_aktion: dict[Aktion, Grenze] = {}
    for aktion, (pro_tag, klein, gross) in VORGABE.items():
        name = _SCHLUESSEL[aktion]
        takt = _TAKT_SCHLUESSEL[aktion]
        je_aktion[aktion] = Grenze(
            pro_tag=int(config.get("limits", name, "daily", default=pro_tag)),
            abstand_min=int(config.get("delays", takt, "min_minutes", default=klein)),
            abstand_max=int(config.get("delays", takt, "max_minutes", default=gross)),
            je_gruppe_taeglich=int(
                config.get("limits", name, "je_gruppe_taeglich", default=0)
            ),
        )

    # Die alten Schluessel gewinnen, solange die neuen nicht gesetzt sind.
    alt_pro_tag = config.get("beitritt", "anfragen_pro_tag", default=None)
    alt_abstand = config.get("beitritt", "mindestabstand_minuten", default=None)
    neu_pro_tag = config.get("limits", "join_requests", "daily", default=None)
    neu_abstand = config.get("delays", "join_request", "min_minutes", default=None)

    if neu_pro_tag is None and alt_pro_tag is not None:
        vorhanden = je_aktion[Aktion.BEITRITT]
        je_aktion[Aktion.BEITRITT] = Grenze(
            pro_tag=int(alt_pro_tag),
            abstand_min=vorhanden.abstand_min,
            abstand_max=vorhanden.abstand_max,
            je_gruppe_taeglich=vorhanden.je_gruppe_taeglich,
        )
    if neu_abstand is None and alt_abstand is not None:
        vorhanden = je_aktion[Aktion.BEITRITT]
        je_aktion[Aktion.BEITRITT] = Grenze(
            pro_tag=vorhanden.pro_tag,
            abstand_min=int(alt_abstand),
            abstand_max=max(int(alt_abstand), vorhanden.abstand_max),
            je_gruppe_taeglich=vorhanden.je_gruppe_taeglich,
        )
    return Grenzen(je_aktion=je_aktion)


# --- Die Tagesmenge --------------------------------------------------------
def rest(grenze: Grenze, heute: int) -> int:
    """Wie viel von dieser Aktion heute noch hinausdarf."""
    return max(grenze.pro_tag - max(heute, 0), 0)


def darf(grenze: Grenze, heute: int) -> bool:
    """Reicht die Tagesmenge noch fuer eine weitere Handlung?"""
    return rest(grenze, heute) > 0


def darf_in_gruppe(grenze: Grenze, heute_in_gruppe: int) -> bool:
    """Nimmt **diese Gruppe** heute noch eine weitere Handlung?

    Getrennt von ``darf``, weil die Antwort eine andere Folge hat: Ist die
    Tagesmenge erschoepft, pausiert die Aktion ueberall; ist die Gruppe voll,
    geht es in der naechsten sofort weiter. Beides in eine Funktion zu legen
    hiesse, wegen einer Gruppe alle anderen anzuhalten.

    Ohne Schranke (``je_gruppe_taeglich = 0``) immer wahr - siehe die
    Begruendung an ``Grenze.je_gruppe_taeglich``.
    """
    if grenze.je_gruppe_taeglich <= 0:
        return True
    return max(heute_in_gruppe, 0) < grenze.je_gruppe_taeglich


# --- Der Takt --------------------------------------------------------------
def naechster_zeitpunkt(
    letzte: datetime | None, grenze: Grenze, *, jetzt: datetime
) -> datetime | None:
    """Ab wann die naechste Handlung dieser Aktion darf - oder ``None``.

    Gestreut zwischen ``abstand_min`` und ``abstand_max``, und **deterministisch
    geseedet** wie im Kaltmodus: Sonst zeigte eine Anzeige bei jedem Neuladen
    eine andere Wartezeit, und keine davon waere die geltende. Der Seed ist
    der Zeitpunkt der letzten Handlung - derselbe Ausgangspunkt ergibt
    denselben Abstand.
    """
    if letzte is None or grenze.abstand_min <= 0:
        return None
    rng = random.Random(int(letzte.timestamp()))
    minuten = rng.uniform(grenze.abstand_min, max(grenze.abstand_max, grenze.abstand_min))
    frei_ab = letzte + timedelta(minutes=minuten)
    return frei_ab if frei_ab > jetzt else None


def wartezeit_text(frei_ab: datetime | None, *, jetzt: datetime) -> str:
    """``noch 42 Min`` - oder leer, wenn nichts zu warten ist."""
    if frei_ab is None or frei_ab <= jetzt:
        return ""
    sekunden = (frei_ab - jetzt).total_seconds()
    return f"noch {-int(-sekunden // 60)} Min"


# --- Die Bremse der Gegenseite ---------------------------------------------
def backoff_minuten(stufe: int) -> int:
    """Wie lange eine Aktion nach der ``stufe``-ten Bremsung ruht.

    Verdoppelt sich: 60, 120, 240, 480 ... bis zu einem Tag. Das ist der
    Gegenentwurf zum Wiederholen (Punkt 37): Wer nach einer Bremsung sofort
    erneut versucht, bestaetigt der Gegenseite genau das Muster, wegen dessen
    sie gebremst hat.

    ``stufe <= 0`` ergibt die Grundzeit; niemand soll ausrechnen muessen, was
    die nullte Bremsung bedeutet.
    """
    faktor = 2 ** max(stufe - 1, 0)
    return min(BACKOFF_BASIS_MINUTEN * faktor, BACKOFF_HOECHSTENS_MINUTEN)


def sperre_bis(stufe: int, *, jetzt: datetime) -> datetime:
    """Bis wann eine gebremste Aktion ruht."""
    return jetzt + timedelta(minutes=backoff_minuten(stufe))


@dataclass(frozen=True)
class Lage:
    """Ob eine Aktion jetzt moeglich ist - und wenn nicht, warum.

    Drei Gruende, und sie bedeuten Verschiedenes: abgeschaltet (eine
    Entscheidung von uns), Tagesmenge erreicht (heute nicht mehr), Takt oder
    Sperre (jetzt noch nicht). Nur der mittlere ist bis morgen bindend.
    """

    aktion: Aktion
    moeglich: bool
    grund: str = ""
    wartezeit: str = ""
    rest_heute: int = 0

    nur_takt: bool = False
    """Es ist **unser eigener** Abstand, der bremst - keine fremde Sperre.

    Beide tragen eine Wartezeit, und trotzdem sind sie nicht dasselbe
    (14.09.2026):

    * Der Takt (``delays``, 8-20 Min) ist unsere eigene Vorsicht. Ihn
      abzuwarten ist genau das, wofuer er da ist - der Lauf tut zwischen zwei
      Schritten ohnehin nichts anderes.
    * Die Bremse der Gegenseite (``gesperrt_bis``, 60 Min bis 24 h) ist eine
      Ansage von Facebook. Sie zu **verschlafen** hiesse, eine Stunde vor dem
      Bildschirm zu sitzen und danach genau das Muster fortzusetzen, das zur
      Bremsung gefuehrt hat. Der Lauf endet, der Backoff ueberlebt den
      Neustart, der naechste Lauf findet ihn vor.

    Ohne diese Unterscheidung wartete ``Lauffortschritt.wartet_auf_takt``
    auch eine Sperre ab - und der Test
    ``test_eine_bremse_haelt_nur_ihre_eigene_aktion_an`` lief eine Stunde
    lang nicht zu Ende.
    """

    @property
    def wartet(self) -> bool:
        """Nur eine Frage der Zeit - kein Grund, die Aktion aufzugeben."""
        return not self.moeglich and bool(self.wartezeit)


def pruefe(
    aktion: Aktion,
    grenze: Grenze,
    *,
    heute: int,
    letzte: datetime | None,
    gesperrt_bis: datetime | None = None,
    jetzt: datetime,
) -> Lage:
    """Darf diese Aktion jetzt? Die **eine** Stelle, die das beantwortet.

    Die Reihenfolge der Pruefungen ist nicht beliebig: Wer abgeschaltet hat,
    will das lesen und nicht "Tagesmenge erreicht"; wer gebremst wurde, will
    die Sperre sehen und nicht den Takt. Dieselbe Ueberlegung wie bei den
    Sperren der Arbeitsseite.
    """
    if grenze.abgeschaltet:
        return Lage(aktion, False, grund=f"{aktion.value}: abgeschaltet (pro_tag 0)")

    if gesperrt_bis is not None and gesperrt_bis > jetzt:
        return Lage(
            aktion,
            False,
            grund=f"{aktion.value}: von der Gegenseite gebremst",
            wartezeit=wartezeit_text(gesperrt_bis, jetzt=jetzt),
            rest_heute=rest(grenze, heute),
        )

    uebrig = rest(grenze, heute)
    if uebrig <= 0:
        return Lage(
            aktion,
            False,
            grund=f"{aktion.value}: Tagesmenge erreicht ({heute}/{grenze.pro_tag})",
        )

    frei_ab = naechster_zeitpunkt(letzte, grenze, jetzt=jetzt)
    if frei_ab is not None:
        return Lage(
            aktion,
            False,
            grund=f"{aktion.value}: Abstandsregel",
            wartezeit=wartezeit_text(frei_ab, jetzt=jetzt),
            rest_heute=uebrig,
            # Der einzige Fall, in dem ein Lauf die Zeit abwarten darf: Es
            # ist unsere eigene Vorsicht, und nach ihr geht es weiter.
            nur_takt=True,
        )

    return Lage(aktion, True, rest_heute=uebrig)


__all__ = [
    "BACKOFF_BASIS_MINUTEN",
    "BACKOFF_HOECHSTENS_MINUTEN",
    "VORGABE",
    "Aktion",
    "Grenze",
    "Grenzen",
    "Lage",
    "aus_texttyp",
    "backoff_minuten",
    "darf",
    "darf_in_gruppe",
    "einstellungen",
    "naechster_zeitpunkt",
    "pruefe",
    "rest",
    "sperre_bis",
    "wartezeit_text",
]
