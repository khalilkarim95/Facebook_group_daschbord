"""Welche Bezuege stecken in einem Beitrag - und welche in einer Gruppe?

## Was dieses Modul ist

Die Grundlage, auf der seit dem 23.09.2026 eine Gruppe beurteilt wird. Sie
ersetzt die Zielklassen ``A``-``D``, die Region und die Note als
Entscheidungsgrundlage (Anweisung des Nutzers):

```
GRUPPE -> INHALTE ANALYSIEREN -> ALLE ERKANNTEN BEZUEGE SAMMELN
       -> BEZUEGE DER GRUPPE
            []      -> spaeter definierte Sonderbehandlung
            nicht [] -> spaeter normale Behandlung
```

Die Klassen beurteilten eine Gruppe **am Namen**: "Reise" im Namen hiess
Reisegruppe. Hier wird gezaehlt, was in den Beitraegen einer Gruppe
tatsaechlich vorkommt - eine Gemeinschaftsgruppe "Syrer in Deutschland", in
der Reisende ihren freien Koffer anbieten, traegt damit dieselben Bezuege
wie eine Reisegruppe, und eine Reisegruppe, in der nur Werbung steht, keine.

## Ein Bezug ist ein Begriff, kein Wort

Jeder Bezug hat Woerter, aber viele verlangen einen **Zusammenhang**:

* ``REISENDER`` ist ein Bewegungswort nur zusammen mit einem Ziel, einem
  Herkunftsort oder einem Reisewort. "رايح ع الشغل" ist kein Reisender.
* Das Gepaeckgewicht verlangt einen Reise- oder Gepaeckzusammenhang und
  faellt weg, sobald vom Koerper die Rede ist ("وزني 80 وبدي انحف").
* ``TERMIN_DATUM`` zaehlt nur neben einer Reise, einer Mitnahme oder einer
  Uebergabe - "بكرا" steht in jedem zweiten Beitrag.
* ``RICHTUNG`` und ``SENDUNG_MIT_REISENDEM`` entstehen gar nicht aus
  Woertern, sondern aus anderen Bezuegen.

Das ist die Antwort auf "keine zu breiten Keywords" (21.09.2026): Die
Musterliste des Nutzers vom 23.09.2026 liess jedes Wort allein genuegen -
"مكان", "شي", "نقل" -, und damit traegt fast jeder Beitrag einen Bezug.
Solche Woerter stehen hier nicht.

## Rein, und der Text bleibt draussen

Wie ``inhalt.py``: kein Netz, keine Datenbank, kein Playwright. Der Text
geht hinein, heraus kommt eine Menge von Schlagwoertern. Gespeichert werden
darf nur diese Menge (``store.merke_bezuege``) - nie der Satz, nie der
Mensch, der ihn geschrieben hat.

## Kein Sprachmodell

Dieselbe Entscheidung wie ueberall im Projekt ("Keine KI (entfernt)"): Ein
Bezug, der nicht an einem nachlesbaren Wort haengt, ist ein geratener. Jeder
Befund traegt deshalb seine Treffer mit.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from fbgroups.marketing import inhalt
from fbgroups.textnorm import contains_term, normalize


class Bezug(StrEnum):
    """Die feste Liste der Bezuege (23.09.2026).

    Die Reihenfolge ist die der Anzeige: erst Gepaeck, dann was mitgeht,
    dann wie es geht, zuletzt die abgeleiteten.
    """

    GEPAECK_GEWICHT = "gepaeck_gewicht"
    UEBERGEPAECK = "uebergepaeck"
    GEPAECK = "gepaeck"
    GEPAECK_KAPAZITAET = "gepaeck_kapazitaet"
    GEGENSTAND = "gegenstand"
    PERSOENLICHE_GEGENSTAENDE = "persoenliche_gegenstaende"
    PAKET_SENDUNG = "paket_sendung"
    VERSENDEN_ZUSTELLUNG_ABHOLUNG = "versenden_zustellung_abholung"
    AMANAH = "amanah"
    DOKUMENTE = "dokumente"
    REISENDER = "reisender"
    MEDIKAMENTE = "medikamente"
    GESCHENKE = "geschenke"
    ELEKTRONIK = "elektronik"
    TRANSPORT = "transport"
    FLUGHAFEN = "flughafen"
    MITNAHME = "mitnahme"
    UEBERGABE_ABHOLUNG = "uebergabe_abholung"
    TERMIN_DATUM = "termin_datum"
    RICHTUNG = "richtung"
    SENDUNG_MIT_REISENDEM = "sendung_mit_reisendem"


class Richtung(StrEnum):
    """Wohin der Weg geht, wo er sich erkennen laesst."""

    NACH_ZIEL = "nach_ziel"
    """Von hier nach Syrien (oder in ein anderes Ziel): "نازل ع الشام"."""

    AUS_ZIEL = "aus_ziel"
    """Von dort hierher: "جاي من حلب", "يجبلي", "عودة من الشام"."""

    BEIDE = "beide"
    """Hin und zurueck im selben Beitrag."""


#: Was mitgehen kann. Jeder dieser Bezuege macht zusammen mit einem Reisenden
#: oder einer Mitnahme eine ``SENDUNG_MIT_REISENDEM``.
GEGENSTAENDE: frozenset[Bezug] = frozenset(
    {
        Bezug.GEGENSTAND,
        Bezug.PERSOENLICHE_GEGENSTAENDE,
        Bezug.PAKET_SENDUNG,
        Bezug.AMANAH,
        Bezug.DOKUMENTE,
        Bezug.MEDIKAMENTE,
        Bezug.GESCHENKE,
        Bezug.ELEKTRONIK,
    }
)


# --- Woerter ------------------------------------------------------------------
#
# Arabisch als Teilstring, lateinisch mit Wortgrenze (``contains_term``). Die
# Formen stehen in der Schreibweise, wie sie in den Gruppen vorkommt; die
# Normalisierung (أ/إ/آ -> ا, ة -> ه, ى -> ي) erledigt die Varianten.

_GEWICHT = ("وزن", "كيلو", "كيلوات", "كغ", "كغم", "كيلة", "kilo")

_UEBERGEPAECK = (
    "وزن زائد", "وزن زايد", "وزن اضافي", "وزن إضافي", "زيادة وزن", "وزن زيادة",
    "كيلو زيادة", "كيلوات زيادة", "uebergepaeck", "zusatzgepaeck",
    "extra baggage", "excess baggage", "extra kilo",
)

#: Wo vom Koerper die Rede ist, ist "وزن" kein Gepaeck. Ohne diese Liste
#: waere "عندي وزن 80 كيلو وبدي انحف" freier Platz im Koffer - der Fall, an
#: dem die alte Erkennung am 23.09.2026 nachweislich scheiterte.
_KOERPER = (
    "انحف", "تنحيف", "رجيم", "ريجيم", "دايت", "سمنة", "نحافة", "رشاقة",
    "حمية", "وزني", "وزن الطفل", "وزن البيبي", "وزن الولد", "طولي",
    "abnehmen", "zunehmen", "diaet", "idealgewicht", "bmi",
)

_GEPAECK = (
    "شنطة", "شنط", "حقيبة", "حقائب", "حقايب", "جنطة", "جنط",
    "koffer", "gepaeck", "luggage", "baggage", "suitcase",
)

#: Freier Platz. Aus ``inhalt._PLATZ`` - dieselben Wendungen, eine Quelle -
#: plus die Formen mit "معاي" (syrisch fuer "معي").
_KAPAZITAET = (
    *inhalt._PLATZ,
    "معاي وزن", "معاي مجال", "معاي مكان", "معاي مساحة", "معاي كم كيلو",
)

_GEGENSTAND = (
    "غرض", "اغراض", "أغراض", "اشياء", "أشياء", "شغلات",
    # Was aus Syrien und nach Syrien geht - die haeufigsten Dinge in den
    # Beitraegen des Bestands.
    "مونة", "زيت", "مكدوس", "زعتر", "ملابس", "تياب", "اواعي", "حلويات",
    "gegenstand", "gegenstaende", "sachen", "items",
)
#: "لغرض" heisst "zum Zweck" und ist kein Gegenstand.
_GEGENSTAND_AUSBLENDEN = ("لغرض", "بغرض", "الغرض من")

_PERSOENLICH = (
    "اغراضي", "أغراضي", "غرضي", "شغلاتي", "تيابي", "اواعيي",
    "اغراض شخصية", "أغراض شخصية", "اغراض خاصة", "أغراض خاصة",
    "اغراض منزلية", "أغراض منزلية", "اغراض للبيت", "اغراض البيت",
    "persoenliche sachen", "meine sachen",
)

_PAKET = (
    "طرد", "طرود", "شحنة", "كرتونة", "كراتين", "بضاعة", "بضاعه",
    "paket", "pakete", "paeckchen", "sendung", "parcel", "package",
)

_VERSENDEN = (
    "ارسال", "إرسال", "ارسل", "ترسل", "يرسل", "نرسل", "رسلي",
    "ابعت", "بعت", "يبعت", "تبعت", "نبعت", "بعتلي", "ابعتلك",
    "توصيل", "يوصل", "يوصلها", "يوصله",
    "schicken", "verschicken", "versenden", "senden", "zustellen",
    "zustellung", "liefern", "lieferung", "send",
)

_AMANAH = ("امانة", "أمانة", "امانات", "أمانات", "امانتي")

_DOKUMENTE = inhalt._DOKUMENTE

_REISENDER_DIREKT = (
    "مسافر", "مسافرة", "مسافرين", "بسافر", "رح سافر", "ناوي سافر", "طاير",
    "reisender", "reisende", "traveler", "traveller",
)
#: Woran erkennbar ist, dass ein Bewegungswort eine Reise meint und nicht
#: den Weg zur Arbeit.
_REISEWORT = (
    "سفر", "رحلة", "رحلتي", "طيارة", "طيران", "مطار", "حجز", "تذكرة",
    "flug", "flieg", "reise", "ticket", "flight",
)

_MEDIKAMENTE = (
    "دواء", "دوا", "ادوية", "أدوية",
    "medikament", "medikamente", "medizin", "arznei", "tabletten",
    "medicine", "medication",
)
#: "دوام" (Dienst) und "دوائر" (Behoerden) enthalten "دوا".
_MEDIKAMENTE_AUSBLENDEN = ("دوام", "دوائر", "دواير")

_GESCHENKE = ("هدية", "هدايا", "هديه", "geschenk", "geschenke", "gift", "gifts")

_ELEKTRONIK = (
    "لابتوب", "موبايل", "جوال", "ايفون", "آيفون", "تابلت", "ايباد", "سماعات",
    "laptop", "handy", "iphone", "tablet", "smartphone",
)

_TRANSPORT = (
    "شحن", "شركة شحن", "مكتب شحن", "كارغو", "كارجو",
    "cargo", "spedition", "kurier", "versandfirma",
)
#: "شحن رصيد" ist Guthaben aufladen, kein Versand - in syrischen Gruppen
#: haeufiger als jeder Versandbeitrag.
_TRANSPORT_AUSBLENDEN = (
    "شحن رصيد", "شحن وحدات", "شحن جوال", "شحن موبايل", "شحن بطارية",
    "شحن الموبايل", "شحن الجوال",
)

_FLUGHAFEN = (
    "مطار", "بالمطار", "طيارة", "طيران", "ترانزيت",
    "flughafen", "airport", "abflug", "flight", "boarding",
)

_MITNAHME = (
    *inhalt._SUCHT_REISENDEN,
    "ياخدلي", "تاخدلي", "ياخد معه", "ياخد معو", "ياخد معاه", "ياخد شي",
    "ياخد غرض", "ياخد امانة", "حدا ياخد",
    "اخد معي", "اخد معاي", "اخد معو", "اخد معه", "اخد غرض", "اخد امانة",
    "اخد اغراض", "فيني اخد", "بقدر اخد", "بقدر جيب", "فيني جيب",
    "يجبلي", "يجيبلي", "جبلي", "تجبلي", "بجيب معي", "بجبلك", "يجيب معه",
    # Der Reisende bietet an: "اذا حدا بدو يبعت شي".
    "حدا بدو يبعت", "حدا بدو يرسل", "حدا بدو يوصل", "اذا حدا عنده",
    "mitnehmen", "mitbringen", "nimmt mit", "nehme mit", "bring mir",
    "take with", "bring me",
)

_UEBERGABE = (
    "استلام", "تسليم", "بسلمك", "بسلمها", "سلمني", "بستلم", "يستلم", "استلم",
    "abholen", "abholung", "uebergabe", "uebergeben", "pick up", "pickup",
    "hand over",
)

_ZEITNAH = inhalt._ZEITNAH + (
    "يوم الاثنين", "يوم الإثنين", "يوم الثلاثاء", "يوم الاربعاء",
    "يوم الأربعاء", "يوم الخميس", "الشهر الجاي", "اول الشهر", "آخر الشهر",
    "morgen", "naechste woche", "am montag", "am dienstag", "am mittwoch",
    "am donnerstag", "am freitag", "am samstag", "am sonntag",
)
#: "27/9", "27.9", "3-10" - Tag und Monat.
_DATUM_RE = re.compile(r"(?<!\d)\d{1,2}\s*[/.\-]\s*\d{1,2}(?!\d)")
#: "بعد 3 ايام", "بعد اسبوع".
_ABSTAND_RE = re.compile(r"بعد\s*\d*\s*(?:يوم|ايام|اسبوع|اسابيع|شهر)")

#: Rueckwaertswoerter: Wer so schreibt, kommt aus dem Ziel.
_RUECK = (
    "عوده", "عودة", "رجعة", "رجعه", "رجوع", "عائد", "عايد", "راجع", "راجعة",
    "راجعين", "طالع", "طالعة",
    # "جاي" allein nicht: "الاسبوع الجاي" heisst "naechste Woche". Nur mit
    # "من" ist es eine Herkunft.
    "جاي من", "جاية من",
)
#: Hinwoerter: Wer so schreibt, geht ins Ziel.
_HIN = ("نازل", "نازلة", "رايح", "رايحة", "مسافر", "مسافرة", "بسافر", "بنزل")
#: "bring mir" - die Sache kommt aus dem Ziel.
_BRING_MIR = ("يجبلي", "يجيبلي", "جبلي", "تجبلي", "بجبلك")


def _treffer(text: str, begriffe: Iterable[str]) -> list[str]:
    return [b for b in begriffe if contains_term(text, b)]


def _ohne(text: str, ausblenden: Iterable[str]) -> str:
    """Den Text ohne die Wendungen, die einen Begriff nur vortaeuschen."""
    for wendung in ausblenden:
        text = text.replace(normalize(wendung), " ")
    return text


@dataclass(frozen=True)
class Bezugsbefund:
    """Die Bezuege **eines** Beitrags - mit ihren Belegen.

    ``treffer`` haelt je Bezug die Woerter, an denen er haengt. Fuer die
    abgeleiteten Bezuege steht dort, woraus sie abgeleitet sind - ein Urteil
    ohne Begruendung wird geglaubt und nicht nachgeschlagen.
    """

    bezuege: frozenset[Bezug] = frozenset()
    treffer: tuple[tuple[Bezug, tuple[str, ...]], ...] = ()
    richtung: Richtung | None = None

    @property
    def relevant(self) -> bool:
        return bool(self.bezuege)

    @property
    def sortiert(self) -> tuple[Bezug, ...]:
        """In der Reihenfolge der Aufzaehlung - fuer Anzeige und Protokoll."""
        return tuple(b for b in Bezug if b in self.bezuege)

    @property
    def grund(self) -> str:
        if not self.bezuege:
            return "keine Bezuege"
        return ", ".join(b.value for b in self.sortiert)


def _richtung(normal: str) -> Richtung | None:
    """Hin, zurueck, beides - oder nicht erkennbar.

    Zurueck heisst: ein Ziel **nach "من"** ("جاي من حلب"), ein Rueckwort
    neben einem Ziel, oder "bring mir" ("يجبلي"). Hin heisst: ein Hinwort
    neben einem Ziel ("نازل ع الشام") oder "nach <Ziel>". Ohne Ziel gibt es
    keine Richtung - "طالع بكرا" kann jede Fahrt meinen.
    """
    ziele = [normalize(z) for z in inhalt._ZIELE]
    ziel_da = any(z and z in normal for z in ziele)
    aus = any(f"من {z}" in normal for z in ziele if z) or any(
        f"aus {z}" in normal or f"von {z}" in normal for z in ziele if z
    )
    aus = aus or bool(_treffer(normal, _BRING_MIR))
    aus = aus or (ziel_da and bool(_treffer(normal, _RUECK)))
    nach = ziel_da and bool(_treffer(normal, _HIN))
    nach = nach or any(f"nach {z}" in normal for z in ziele if z)
    if aus and nach:
        return Richtung.BEIDE
    if aus:
        return Richtung.AUS_ZIEL
    if nach:
        return Richtung.NACH_ZIEL
    return None


def erkenne(text: str) -> Bezugsbefund:
    """Alle Bezuege eines Beitrags. Rein, ohne Speicher.

    Die Reihenfolge der Schritte ist die der Abhaengigkeiten: erst die
    Bezuege, die an Woertern haengen, dann die, die einen Zusammenhang
    verlangen, zuletzt die abgeleiteten.
    """
    roh = (text or "").strip()
    if len(roh) < 8:
        return Bezugsbefund()
    normal = normalize(roh)

    gefunden: dict[Bezug, list[str]] = {}

    def setze(bezug: Bezug, treffer: list[str]) -> None:
        if treffer:
            gefunden.setdefault(bezug, []).extend(treffer)

    # --- 1. Woerter, die allein einen Bezug tragen ------------------------
    setze(Bezug.GEPAECK, _treffer(normal, _GEPAECK))
    setze(Bezug.GEGENSTAND, _treffer(_ohne(normal, _GEGENSTAND_AUSBLENDEN), _GEGENSTAND))
    setze(Bezug.PERSOENLICHE_GEGENSTAENDE, _treffer(normal, _PERSOENLICH))
    setze(Bezug.PAKET_SENDUNG, _treffer(normal, _PAKET))
    setze(Bezug.VERSENDEN_ZUSTELLUNG_ABHOLUNG, _treffer(normal, _VERSENDEN))
    setze(Bezug.AMANAH, _treffer(normal, _AMANAH))
    setze(Bezug.DOKUMENTE, _treffer(normal, _DOKUMENTE))
    setze(
        Bezug.MEDIKAMENTE,
        _treffer(_ohne(normal, _MEDIKAMENTE_AUSBLENDEN), _MEDIKAMENTE),
    )
    setze(Bezug.GESCHENKE, _treffer(normal, _GESCHENKE))
    setze(Bezug.ELEKTRONIK, _treffer(normal, _ELEKTRONIK))
    setze(Bezug.TRANSPORT, _treffer(_ohne(normal, _TRANSPORT_AUSBLENDEN), _TRANSPORT))
    setze(Bezug.FLUGHAFEN, _treffer(normal, _FLUGHAFEN))
    setze(Bezug.MITNAHME, _treffer(normal, _MITNAHME))
    setze(Bezug.UEBERGABE_ABHOLUNG, _treffer(normal, _UEBERGABE))

    # --- 2. Der Reisende: ein Wort **mit** Zusammenhang --------------------
    reisender = _treffer(normal, _REISENDER_DIREKT)
    bewegung = _treffer(normal, inhalt._BEWEGUNG)
    zusammenhang = (
        _treffer(normal, inhalt._ZIELE)
        + _treffer(normal, inhalt._HERKUNFT)
        + _treffer(normal, _REISEWORT)
    )
    if bewegung and zusammenhang:
        reisender += bewegung
    if _treffer(normal, inhalt._SUCHT_REISENDEN):
        # "مين مسافر ع دمشق" - wer einen Reisenden sucht, spricht von einem.
        reisender += _treffer(normal, inhalt._SUCHT_REISENDEN)
    setze(Bezug.REISENDER, sorted(set(reisender)))

    # --- 3. Gewicht und Platz: nur mit Gepaeck, nie mit dem Koerper -------
    koerper = bool(_treffer(normal, _KOERPER))
    gepaeckzusammenhang = any(
        b in gefunden
        for b in (Bezug.REISENDER, Bezug.GEPAECK, Bezug.FLUGHAFEN, Bezug.MITNAHME)
    )
    kapazitaet = _treffer(normal, _KAPAZITAET)
    if koerper:
        # Eine Wendung ueber Gewicht faellt weg, eine ueber Platz im Koffer
        # nicht: "مساحة بالشنطة" meint nie den Koerper.
        kapazitaet = [k for k in kapazitaet if not _treffer(normalize(k), ("وزن", "كيلو"))]
    setze(Bezug.GEPAECK_KAPAZITAET, kapazitaet)
    if not koerper and (gepaeckzusammenhang or kapazitaet):
        setze(Bezug.GEPAECK_GEWICHT, _treffer(normal, _GEWICHT))
        setze(Bezug.UEBERGEPAECK, _treffer(normal, _UEBERGEPAECK))

    # --- 4. Der Termin: nur neben einer Reise, Mitnahme oder Uebergabe ----
    if any(
        b in gefunden for b in (Bezug.REISENDER, Bezug.MITNAHME, Bezug.UEBERGABE_ABHOLUNG)
    ):
        termin = _treffer(normal, _ZEITNAH)
        termin += _DATUM_RE.findall(roh)
        termin += _ABSTAND_RE.findall(normal)
        setze(Bezug.TERMIN_DATUM, termin)

    # --- 5. Abgeleitet ------------------------------------------------------
    richtung = None
    if any(
        b in gefunden
        for b in (Bezug.REISENDER, Bezug.MITNAHME, Bezug.VERSENDEN_ZUSTELLUNG_ABHOLUNG)
    ):
        richtung = _richtung(normal)
        if richtung is not None:
            gefunden[Bezug.RICHTUNG] = [richtung.value]

    # Sortiert, damit der Grund bei jedem Lauf gleich lautet - ein frozenset
    # hat keine feste Reihenfolge.
    etwas = [b.value for b in Bezug if b in GEGENSTAENDE and b in gefunden]
    platz = Bezug.GEPAECK_KAPAZITAET in gefunden
    mitnahme = Bezug.MITNAHME in gefunden
    if (Bezug.REISENDER in gefunden and (mitnahme or platz or etwas)) or (
        mitnahme and (etwas or platz)
    ):
        grund = ["reisender" if Bezug.REISENDER in gefunden else "mitnahme"]
        grund += etwas or (["mitnahme"] if mitnahme else ["platz"])
        gefunden[Bezug.SENDUNG_MIT_REISENDEM] = grund

    bezuege = frozenset(gefunden)
    return Bezugsbefund(
        bezuege=bezuege,
        treffer=tuple(
            (b, tuple(dict.fromkeys(gefunden[b]))[:4]) for b in Bezug if b in bezuege
        ),
        richtung=richtung,
    )


@dataclass(frozen=True)
class Gruppenbezuege:
    """Die Bezuege einer **Gruppe**: gesammelt aus ihren Beitraegen.

    ``anzahl`` sagt je Bezug, in wie vielen Beitraegen er vorkam. Leer heisst
    ``BEZUEGE = []`` - dafuer gibt es spaeter eine eigene Behandlung, die
    **noch nicht** festgelegt ist.
    """

    anzahl: dict[Bezug, int] = field(default_factory=dict)
    beitraege: int = 0
    """Wie viele Beitraege dieser Gruppe gelesen wurden - auch die ohne Bezug.

    Null heisst: Es wurde noch nichts gelesen. Das ist etwas anderes als
    "gelesen und nichts gefunden", und die spaetere Sonderbehandlung wird
    beides unterscheiden muessen.
    """

    @property
    def bezuege(self) -> tuple[Bezug, ...]:
        return tuple(b for b in Bezug if self.anzahl.get(b))

    @property
    def leer(self) -> bool:
        return not self.bezuege

    @property
    def gelesen(self) -> bool:
        return self.beitraege > 0


def fuer_gruppe(beitraege: Iterable[Iterable[Bezug | str]]) -> Gruppenbezuege:
    """Aus den Bezuegen der einzelnen Beitraege die der Gruppe.

    Gesammelt wird alles, was vorkommt - ein Bezug, der in einem einzigen
    Beitrag steht, gehoert dazu. Ob eine Mindestzahl gelten soll, ist eine
    Frage der spaeteren Behandlung; die Zahlen dafuer stehen in ``anzahl``.
    """
    zaehler: Counter[Bezug] = Counter()
    gelesen = 0
    for bezuege in beitraege:
        gelesen += 1
        for b in set(bezuege):
            try:
                zaehler[Bezug(b)] += 1
            except ValueError:
                # Ein Name aus einer aelteren Fassung der Liste: Er wird nicht
                # geraten, sondern uebergangen.
                continue
    return Gruppenbezuege(anzahl=dict(zaehler), beitraege=gelesen)


__all__ = [
    "GEGENSTAENDE",
    "Bezug",
    "Bezugsbefund",
    "Gruppenbezuege",
    "Richtung",
    "erkenne",
    "fuer_gruppe",
]
