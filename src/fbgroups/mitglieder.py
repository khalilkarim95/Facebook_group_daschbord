"""Die eigene Mitgliederliste einlesen - Gruppen, in denen wir schon drin sind.

Seit dem 20.09.2026 der einzige Weg, Gruppen in den Bestand zu bekommen: Der
Seed-Import ist mit der Entdeckungsschicht entfallen, und gesucht wird nicht
mehr. Was hier hereinkommt, hat ein Mensch von Hand gesammelt.

**Jede Zeile bedeutet "wir sind Mitglied".** Das ist der Zweck der Datei und
der Grund, warum der Import ``MarketingStatus.MEMBER`` setzt: Eine
Beitrittsanfrage an eine Gruppe, in der wir schon stehen, waere ein Handgriff
ohne Zweck - und eine der wenigen Handlungen, die bei Facebook auffallen.

Das Dateiformat ist das einer fremden Tabelle und **nicht** unser Datenmodell.
Drei Spalten bedeuten etwas anderes, als ihr Name verspricht; das ist der
eigentliche Inhalt dieses Moduls:

``category``
    Traegt Anzeigenamen ("Reise & Transport"), der Bestand traegt Kennungen
    (``reise``). Uebersetzt wird ueber ``KATEGORIEN``. Ohne diese Uebersetzung
    griffe ``marketing.zielprioritaet.kategorien`` nie - jede eingelesene
    Gruppe fiele aus Klasse A heraus, und die Kampagne arbeitete wieder in den
    Gemeinschaftsgruppen. Ein Wert, der nicht in der Tabelle steht, wird
    **uebergangen** und nicht geraten.

``city``
    Traegt das *Reiseziel* ("Damaskus", "دمشق"), nicht den Sitz der Gruppe.
    ``Group.city`` ist aber die deutsche Stadt und traegt 15 Score-Punkte,
    und in ``zielgruppe.bestimme_region`` belegt sie ``Region.DE``. Eine
    Gruppe mit "Damaskus" in diesem Feld gaelte damit als in Deutschland
    ansaessig. Deshalb wird die Spalte **nicht** uebernommen; ihr Inhalt
    landet als ausdruecklich benannter Hinweis in ``notes``. Dieselbe
    Zurueckhaltung wie bei einem Beitragstitel, der kein Gruppenname ist.

``activity``
    Traegt den Kopf der Gruppenseite und damit echte Zahlen: Sichtbarkeit,
    Mitgliederzahl und Beitraege je Tag. Sie werden uebernommen - aber nur,
    wo sie ausdruecklich dastehen. "25 ungelesene Beitraege" ist keine
    Beitragszahl je Tag, und "Aktiv (نشط)" ist ueberhaupt keine Zahl.

Reine Funktionen ueber uebergebene Werte: kein Netz, keine Datenbank, kein
Browser. Wer sie prueft, braucht keine Datei und keinen Server.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from fbgroups.config import AppConfig
from fbgroups.models import (
    ActivitySource,
    Group,
    MemberCountSource,
    PrivacyHint,
    Provenance,
    SourceType,
)
from fbgroups.textnorm import parse_member_count
from fbgroups.urls import ParsedGroupUrl, parse_group_url
from fbgroups.validation import validate_group

#: Anzeigename der Quelltabelle -> Kennung im Bestand. Bewusst klein und
#: bewusst unvollstaendig: Was hier nicht steht, wird uebergangen statt
#: geraten. Eine falsche Kategorie verschiebt eine Gruppe in der Rangfolge,
#: nach der entschieden wird, wo die naechsten dreihundert Beitraege hingehen.
KATEGORIEN: dict[str, str] = {
    "reise & transport": "reise",
    "reise und transport": "reise",
    "reise": "reise",
    "transport": "reise",
    "versand": "versand",
    "versand & mitnahme": "versand",
    "versand und mitnahme": "versand",
    "mitnahme": "versand",
    "community": "community",
    "gemeinschaft": "community",
    "jobs": "jobs",
    "arbeit": "jobs",
    "wohnen": "wohnen",
    "beratung": "beratung",
    "kaufen & verkaufen": "kaufen_verkaufen",
    "kaufen und verkaufen": "kaufen_verkaufen",
    "studenten": "studenten",
    "essen": "essen",
    "events": "events",
}

#: Werte der Spalte ``category``, die keine Kategorie sind. Sie werden still
#: uebergangen - eine Meldung je Zeile waere Laerm ueber eine Tabelle, die
#: schlicht keine Kategorie kennt. "Oeffentlich" steht darunter, weil es eine
#: Sichtbarkeit beschreibt und in der Quelldatei in der falschen Spalte landet.
KEINE_KATEGORIE = frozenset({"", "allgemein", "unbekannt", "oeffentlich", "öffentlich", "privat"})

#: Zeichenketten, die kein Gruppenname sind, sondern Beifang der Erfassung.
#: "الإشعارات" heisst "Benachrichtigungen" - die Ueberschrift neben der
#: Gruppe, nicht die Gruppe. Ungefiltert stuende sie als Name im Bestand,
#: wuerde bewertet und stuende ueber einem Beitrag. Dieselbe Falle wie ein
#: Beitragstitel, der frueher als Gruppenname im Export landete.
KEIN_NAME = frozenset({"الإشعارات", "benachrichtigungen", "notifications", "-", "?"})

_MITGLIEDER_RE = re.compile(r"(\d[\d.,\s ]*)\s*Mitglied", re.IGNORECASE)

# "50+ Beitraege pro Tag", "10 Beitraege pro Tag" - aber ausdruecklich **nicht**
# "25 ungelesene Beitraege": Das ist ein Postfachstand und keine Rate. Ohne das
# "pro Tag" entsteht hier nichts.
_BEITRAEGE_RE = re.compile(
    r"(\d[\d.,]*)\s*\+?[\s ]*Beitr[äa]ge?\s+pro\s+Tag", re.IGNORECASE
)

_SICHTBARKEIT = {
    "öffentlich": PrivacyHint.PUBLIC,
    "oeffentlich": PrivacyHint.PUBLIC,
    "public": PrivacyHint.PUBLIC,
    "privat": PrivacyHint.PRIVATE,
    "private": PrivacyHint.PRIVATE,
    "geschlossen": PrivacyHint.PRIVATE,
}


@dataclass(frozen=True)
class Seitenangaben:
    """Was im Kopf der Gruppenseite stand - oder ``None``, wo nichts stand.

    Kein Ersatzwert: "Aktiv (نشط)" sagt nichts ueber die Mitgliederzahl, und
    eine geratene Zahl stuende hinterher in der Rangliste, ohne dass man ihr
    ansaehe, dass sie geraten ist.
    """

    privacy_hint: PrivacyHint = PrivacyHint.UNKNOWN
    member_count: int | None = None
    posts_per_day: float | None = None

    @property
    def leer(self) -> bool:
        return (
            self.privacy_hint is PrivacyHint.UNKNOWN
            and self.member_count is None
            and self.posts_per_day is None
        )


@dataclass(frozen=True)
class Zeilenfehler:
    """Eine Zeile, aus der keine Gruppe wurde - mit Grund."""

    zeile: int
    wert: str
    grund: str


@dataclass
class Einlesebericht:
    """Was beim Lesen herauskam. Zahlen **und** die Gruende."""

    quelle: str = ""
    zeilen_gesamt: int = 0
    gruppen: list[Group] = field(default_factory=list)
    fehler: list[Zeilenfehler] = field(default_factory=list)
    #: Zeilen, deren Name uebergangen wurde (siehe ``KEIN_NAME``).
    ohne_namen: list[str] = field(default_factory=list)
    #: Kategoriewerte, die ``KATEGORIEN`` nicht kennt - ein Tippfehler in der
    #: Quelltabelle faellt sonst erst auf, wenn die Gruppe nie in Klasse A
    #: auftaucht.
    unbekannte_kategorien: list[str] = field(default_factory=list)
    #: Zeilen, deren ``city`` verworfen wurde, weil sie ein Reiseziel nennt.
    verworfene_staedte: list[str] = field(default_factory=list)


def lies_seitenangaben(text: str | None) -> Seitenangaben:
    """Zerlegt den Kopf einer Gruppenseite in Sichtbarkeit, Groesse und Takt.

    Beispiele aus der Quelldatei::

        "Öffentlich · 5.366 Mitglieder · 50+ Beiträge pro Tag"
        "Privat · 17.421 Mitglieder · 10 Beiträge pro Tag"
        "Öffentlich · 942 Mitglieder"
        "Öffentlich · 174.725 Mitglieder · 25 ungelesene Beiträge · ..."
        "Sehr Aktiv (نشط جداً)"

    Die vierte Zeile ist der Grund fuer die Strenge: "25 ungelesene Beitraege"
    saehe wie eine Beitragszahl aus und waere in Wahrheit unser eigener
    Postfachstand. Die fuenfte ergibt gar nichts - und das ist ein Ergebnis,
    keine Luecke.
    """
    roh = (text or "").strip()
    if not roh:
        return Seitenangaben()

    sichtbarkeit = PrivacyHint.UNKNOWN
    for teil in roh.split("·"):
        treffer = _SICHTBARKEIT.get(teil.strip().lower())
        if treffer is not None:
            sichtbarkeit = treffer
            break

    mitglieder = None
    if (m := _MITGLIEDER_RE.search(roh)) is not None:
        mitglieder = parse_member_count(m.group(1))

    beitraege = None
    if (b := _BEITRAEGE_RE.search(roh)) is not None:
        zahl = parse_member_count(b.group(1))
        beitraege = float(zahl) if zahl is not None else None

    return Seitenangaben(
        privacy_hint=sichtbarkeit, member_count=mitglieder, posts_per_day=beitraege
    )


def faktor_aus_posts_pro_tag(posts_per_day: float, config: AppConfig) -> float:
    """Beitraege je Tag -> Faktor 0-1 laut ``scoring.aktivitaet_buckets``.

    Abgestuft und nicht linear, aus demselben Grund wie bei der
    Mitgliederzahl: Der Unterschied zwischen 0 und 2 Beitraegen am Tag ist
    fuer die Frage "lohnt sich hier ein Beitrag?" gewaltig, der zwischen 20
    und 40 fast bedeutungslos - in beiden Faellen geht der eigene Beitrag im
    Strom unter oder eben nicht.
    """
    buckets = config.get("scoring", "aktivitaet_buckets", default=[]) or []
    # Absteigend und nicht in Dateireihenfolge: Stuende die unterste Stufe
    # versehentlich zuerst, bekaeme jede Gruppe deren Faktor.
    for bucket in sorted(buckets, key=lambda b: float(b.get("min", 0)), reverse=True):
        if posts_per_day >= float(bucket.get("min", 0)):
            return float(bucket.get("factor", 0.0))
    return 0.0


def kategorie_aus(rohwert: str | None) -> tuple[str | None, str]:
    """Anzeigename -> Kennung. Liefert ``(kennung, unbekannter_wert)``.

    Der zweite Rueckgabewert ist gefuellt, wenn der Wert weder bekannt noch
    ausdruecklich als "keine Kategorie" hinterlegt ist. Er wird gemeldet und
    nicht geraten: Eine erfundene Kategorie verschoebe die Gruppe in einer
    Rangfolge, nach der entschieden wird, wo gepostet wird.
    """
    roh = (rohwert or "").strip()
    schluessel = roh.lower()
    if schluessel in KEINE_KATEGORIE:
        return None, ""
    if (kennung := KATEGORIEN.get(schluessel)) is not None:
        return kennung, ""
    return None, roh


def _name_aus(rohwert: str | None) -> str:
    name = (rohwert or "").strip()
    return "" if name.lower() in KEIN_NAME or name in KEIN_NAME else name


def _hinweis(zeile: dict[str, str]) -> str:
    """Was nicht ins Modell passt, aber ein Mensch wissen will.

    Ausdruecklich benannt und nicht als Wert getarnt: "Reiseziel laut Liste:
    Damaskus" ist eine Auskunft, ein "Damaskus" in der Stadtspalte waere eine
    Behauptung ueber den Sitz der Gruppe.
    """
    teile = []
    if (ziel := (zeile.get("city") or "").strip()):
        teile.append(f"Reiseziel laut Liste: {ziel}")
    if (land := (zeile.get("country") or "").strip()):
        teile.append(f"Raum: {land}")
    if (note := (zeile.get("rating") or "").strip()):
        teile.append(f"Eigene Note: {note}")
    if (frei := (zeile.get("notes") or "").strip()):
        teile.append(frei)
    return " · ".join(teile)


def zeile_zu_gruppe(
    zeile: dict[str, str],
    zeilennummer: int,
    quelle: str,
    config: AppConfig,
    bericht: Einlesebericht,
) -> Group | None:
    """Macht aus einer Tabellenzeile eine Gruppe - oder vermerkt den Grund."""
    roh_url = (zeile.get("url") or "").strip()
    if not roh_url:
        bericht.fehler.append(Zeilenfehler(zeilennummer, "", "keine URL"))
        return None

    parsed = parse_group_url(roh_url)
    if not isinstance(parsed, ParsedGroupUrl):
        bericht.fehler.append(Zeilenfehler(zeilennummer, roh_url, parsed.reason))
        return None

    name = _name_aus(zeile.get("name"))
    if not name and (zeile.get("name") or "").strip():
        bericht.ohne_namen.append((zeile.get("name") or "").strip())

    kategorie, unbekannt = kategorie_aus(zeile.get("category"))
    if unbekannt:
        bericht.unbekannte_kategorien.append(unbekannt)

    if (stadt := (zeile.get("city") or "").strip()):
        bericht.verworfene_staedte.append(stadt)

    angaben = lies_seitenangaben(zeile.get("activity"))

    group = Group(
        group_id=parsed.group_id,
        url_canonical=parsed.canonical_url,
        url_variants=(
            [parsed.original_url] if parsed.original_url != parsed.canonical_url else []
        ),
        name=name,
        privacy_hint=angaben.privacy_hint,
        member_count=angaben.member_count,
        member_count_source=(
            MemberCountSource.FACEBOOK if angaben.member_count is not None else None
        ),
        posts_per_day=angaben.posts_per_day,
        category=kategorie,
        # ``country`` ist das eine Feld der Quelltabelle, das genau das
        # bedeutet, was es heisst: der Raum, in dem die Gruppe arbeitet
        # ("Deutschland / Europa", "Deutschland, Syrien, Tuerkei"). Es traegt
        # die unterste Ortsstufe (0,20 von 15 Punkten) - "Deutschland
        # allgemein" ist eine schwaechere Passung als "Bonn", aber immer noch
        # eine. Anders als ``city`` wird es deshalb uebernommen.
        country=(zeile.get("country") or "").strip() or None,
        notes=_hinweis(zeile),
        sources=[
            Provenance(
                source_type=SourceType.MANUAL_SEED,
                source_ref=quelle,
                source_line=zeilennummer,
            )
        ],
    )

    # Die Aktivitaet ist belegt, wenn eine Beitragszahl dastand - dieselbe
    # Quelle und dieselbe Konfidenz wie beim Abruf der Gruppenseite, denn es
    # ist dieselbe Zeile: einmal live gelesen, einmal aus der Tabelle.
    if angaben.posts_per_day is not None:
        group.activity_factor = faktor_aus_posts_pro_tag(angaben.posts_per_day, config)
        group.activity_confidence = 1.0
        group.activity_source = ActivitySource.FACEBOOK
        group.activity_checked_at = datetime.now(UTC)

    if angaben.member_count is not None:
        group.member_count_checked_at = datetime.now(UTC)

    # Strukturelle Pruefung der Kennung. Was nicht in der Datei stand, bleibt
    # leer - ergaenzt wird hier nichts.
    group.validation_status = validate_group(group)
    return group


def lies_mitgliederdatei(pfad: Path, config: AppConfig) -> Einlesebericht:
    """Liest eine Mitgliederliste vollstaendig ein.

    ``utf-8-sig``: Die Datei entsteht auf einem Windows-Rechner, und Notepad
    wie ``Out-File`` schreiben ein BOM. Ohne diese Kodierung wuerde es Teil der
    Spaltenueberschrift ``url``, die Spalte waere unauffindbar und die erste
    Gruppe ginge stillschweigend verloren.
    """
    bericht = Einlesebericht(quelle=pfad.name)

    with pfad.open("r", encoding="utf-8-sig", newline="") as fh:
        leser = csv.DictReader(fh)
        if not leser.fieldnames or "url" not in {
            (f or "").strip().lower() for f in leser.fieldnames
        }:
            bericht.fehler.append(
                Zeilenfehler(0, pfad.name, f"Spalte 'url' fehlt (gefunden: {leser.fieldnames})")
            )
            return bericht

        for nummer, roh in enumerate(leser, start=2):  # 1 ist die Kopfzeile
            bericht.zeilen_gesamt += 1
            zeile = {(k or "").strip().lower(): (v or "") for k, v in roh.items()}
            gruppe = zeile_zu_gruppe(zeile, nummer, pfad.name, config, bericht)
            if gruppe is not None:
                bericht.gruppen.append(gruppe)

    return bericht


__all__ = [
    "KATEGORIEN",
    "KEINE_KATEGORIE",
    "KEIN_NAME",
    "Einlesebericht",
    "Seitenangaben",
    "Zeilenfehler",
    "faktor_aus_posts_pro_tag",
    "kategorie_aus",
    "lies_mitgliederdatei",
    "lies_seitenangaben",
    "zeile_zu_gruppe",
]
