"""Kampagne A, Runde um Runde - bis sie erreicht ist. Erst dann B.

Die Zusicherung aus der Anforderung vom 21.09.2026:

    Kampagne A
    → Gruppe 1 → Gruppe 2 → ... → Gruppe 13
    → Runde 2 wieder Gruppe 1 → ...
    → ERST wenn das Ziel erreicht ist: Kampagne B

**Ein vollstaendiger Gruppendurchlauf loest niemals den Wechsel aus.** Genau
das konnte er bis zum 21.09.2026: Liegen am Ende einer Runde alle Gruppen
fuer zwei Minuten beiseite (``automatik.ruhe_minuten``), gibt die Kampagne
in diesem Augenblick nichts her - und ``naechste_kampagne`` nahm die
naechste, die etwas hergab. Die Unterscheidung dazu ist
``Gruppenfortschritt.ruht``: Wer ruht, kommt von selbst zurueck und haelt
den Platz seiner Kampagne.

**Was "erreicht" heisst.** Nicht "hundert Kommentare" - das ist die
Tagesmenge (``limits.comments.daily``) und gilt ueber alle Kampagnen.
Erreicht ist eine Kampagne, wenn **jede ihrer Gruppen** ihre zehn Fassungen
veroeffentlicht hat (``lauf.ZIEL_JE_GRUPPE``). Bei dreizehn Gruppen sind das
130 Kommentare; die letzte fehlende Fassung haelt die Kampagne aktiv, genau
wie in der Anforderung "99 → aktiv, 100 → abgeschlossen".
"""

from __future__ import annotations

from fbgroups.marketing import lauf
from fbgroups.marketing.lauf import (
    Gruppenfortschritt,
    Kampagnenfortschritt,
    Lauffortschritt,
    LaufStatus,
)
from fbgroups.marketing.models import PostStatus

ZIEL = lauf.ZIEL_JE_GRUPPE


def _gruppe(
    campaign_id: str,
    gid: str,
    *,
    veroeffentlicht: int = 0,
    ruht: bool = False,
) -> Gruppenfortschritt:
    return Gruppenfortschritt(
        campaign_id=campaign_id,
        group_id=gid,
        name=f"Gruppe {gid}",
        veroeffentlicht=veroeffentlicht,
        ziel=ZIEL,
        mitglied=True,
        # Der Beitrag ist hier nicht die Frage: Er stuende sonst als Schritt
        # vor jedem Kommentar und verdeckte, worum es geht.
        post_status=PostStatus.VEROEFFENTLICHT,
        uebersprungen=ruht,
        uebersprungen_grund="kein Anlass" if ruht else "",
        ruht=ruht,
    )


def _kampagne(campaign_id: str, gruppen: list[Gruppenfortschritt]) -> Kampagnenfortschritt:
    return Kampagnenfortschritt(
        campaign_id=campaign_id, name=campaign_id, gruppen=gruppen
    )


def _lauf(*kampagnen: Kampagnenfortschritt) -> Lauffortschritt:
    return Lauffortschritt(
        lauf_id=1, status=LaufStatus.LAEUFT, kampagnen=list(kampagnen)
    )


# --- 1. Eine volle Runde wechselt die Kampagne nicht ---------------------

def test_eine_volle_runde_loest_keinen_kampagnenwechsel_aus() -> None:
    """**Der Kern.** Dreizehn Gruppen ruhen - A bleibt trotzdem an der Reihe.

    Ohne ``ruht`` haette hier B uebernommen: A gibt in diesem Augenblick
    nichts her, und genau das war der Fehler - "gerade nichts" ist nicht
    "fertig".
    """
    a = _kampagne("a", [_gruppe("a", str(i), veroeffentlicht=3, ruht=True) for i in range(13)])
    b = _kampagne("b", [_gruppe("b", "b1")])

    fortschritt = _lauf(a, b)

    assert fortschritt.naechste_kampagne is a, "A behaelt ihren Platz"
    assert lauf.naechster_schritt(fortschritt) is None, "und der Lauf wartet"


def test_eine_einzige_ruhende_gruppe_haelt_die_kampagne_schon() -> None:
    """Es braucht keine volle Runde - eine Gruppe, die wiederkommt, genuegt."""
    a = _kampagne(
        "a",
        [
            _gruppe("a", "1", veroeffentlicht=ZIEL),  # fertig
            _gruppe("a", "2", veroeffentlicht=1, ruht=True),  # ruht
        ],
    )
    b = _kampagne("b", [_gruppe("b", "b1")])

    assert _lauf(a, b).naechste_kampagne is a


# --- 2. Erst das erreichte Ziel gibt den Platz frei ----------------------

def test_die_vorletzte_fassung_haelt_die_kampagne_aktiv() -> None:
    """"99 → aktiv": Eine einzige fehlende Fassung, und A bleibt dran."""
    fehlt_eine = [
        _gruppe("a", str(i), veroeffentlicht=ZIEL) for i in range(12)
    ] + [_gruppe("a", "12", veroeffentlicht=ZIEL - 1)]
    a = _kampagne("a", fehlt_eine)
    b = _kampagne("b", [_gruppe("b", "b1")])

    fortschritt = _lauf(a, b)

    assert not a.fertig, "eine Fassung fehlt noch"
    assert not a.abgeschlossen
    assert fortschritt.naechste_kampagne is a
    schritt = lauf.naechster_schritt(fortschritt)
    assert schritt is not None
    assert schritt.campaign_id == "a", "gearbeitet wird weiter in A"
    assert schritt.group_id == "12", "und zwar in der Gruppe, der etwas fehlt"


def test_erst_mit_der_letzten_fassung_kommt_kampagne_b() -> None:
    """"100 → abgeschlossen": Jede Gruppe voll, damit ist A durch."""
    a = _kampagne("a", [_gruppe("a", str(i), veroeffentlicht=ZIEL) for i in range(13)])
    b = _kampagne("b", [_gruppe("b", "b1")])

    fortschritt = _lauf(a, b)

    assert a.fertig and a.abgeschlossen, "erreicht, nicht nur beendet"
    assert fortschritt.naechste_kampagne is b
    schritt = lauf.naechster_schritt(fortschritt)
    assert schritt is not None
    assert schritt.campaign_id == "b"


def test_zwischen_den_runden_bleibt_die_reihenfolge_der_gruppen() -> None:
    """Runde 2 faengt wieder bei der besten Gruppe an, nicht bei der naechsten.

    Die Arbeitsliste ist bei jedem Durchgang dieselbe Rechnung
    (Zielprioritaet → Region → Vorrang → Score); was sie veraendert, ist
    allein der Stand der Gruppen. Eine Gruppe, die gerade ruht, faellt
    heraus - kommt sie zurueck, steht sie wieder an ihrem Platz.
    """
    gruppen = [
        _gruppe("a", "1", veroeffentlicht=1, ruht=True),
        _gruppe("a", "2", veroeffentlicht=1),
    ]
    a = _kampagne("a", gruppen)

    erste_wahl = a.naechste_kommentargruppe
    assert erste_wahl is not None
    assert erste_wahl.group_id == "2", "die ruhende faellt aus der Runde"

    # Zurueck aus der Ruhe - und wieder dabei.
    zurueck = _kampagne(
        "a",
        [_gruppe("a", "1", veroeffentlicht=1), _gruppe("a", "2", veroeffentlicht=1)],
    )
    assert {g.group_id for g in zurueck.arbeitsliste} == {"1", "2"}


# --- 3. Eine Kampagne ohne Rueckkehr haelt den Lauf nicht auf -----------

def test_eine_leere_kampagne_gibt_ihren_platz_frei() -> None:
    """Der Gegenfall, und er bleibt wie er war.

    Eine Kampagne **ohne** Gruppen (geloescht, nie zugeordnet) kommt nie
    wieder - sie zu halten hiesse, den Lauf an ihr aufzuhaengen. Nur wer
    ruht, kommt zurueck.
    """
    leer = _kampagne("leer", [])
    b = _kampagne("b", [_gruppe("b", "b1")])

    assert _lauf(leer, b).naechste_kampagne is b
