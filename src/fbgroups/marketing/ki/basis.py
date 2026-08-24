"""Beitragsvorschlaege - der anbieterunabhaengige Teil.

Hier steht die Fachlichkeit: was ein Modell gefragt wird, wie die Antwort
geprueft wird, was daraus ein Entwurf werden darf. **Kein Anbieter kommt
darin vor.** Ollama und Anthropic sind zwei Umsetzungen des Protokolls
``Modell``; dieses Modul kennt keine von beiden, und deshalb bleibt es
gleich, wenn eine dritte dazukommt oder eine wegfaellt.

Die KI ist ein **Dienst**, kein Ersatz fuer irgendetwas: Sie schreibt
Textvorschlaege und sonst nichts. Sie waehlt keine Gruppe aus, gibt nichts
frei, reiht nichts ein und veroeffentlicht nichts. Alles, was sie liefert,
landet als Entwurf in ``post_entwuerfe`` und wartet auf einen Menschen.

**Der Tracking-Code wird dem Modell nie gezeigt.**

Das ist die wichtigste Entscheidung in dieser Datei, und sie gilt fuer jeden
Anbieter gleichermassen. Das Modell schreibt den Platzhalter ``{link}``, und
erst danach setzt ``beitrag.beitragstext`` den Link **dieser** Gruppe ein -
dieselbe Stelle, an der das seit jeher geschieht. Ein Sprachmodell, das eine
Zeichenkette wie einen Tracking-Code sieht, kann sie verwechseln, abschreiben
oder eine plausibel aussehende danebenlegen, und der Fehler faellt erst auf,
wenn der Beitrag in der Gruppe steht - zurueckholen laesst er sich dann nicht
mehr. Was das Modell nie bekommt, kann es nicht verfaelschen.
``pruefe_platzhalter`` setzt das durch, statt darauf zu bauen.

Mehrere Fassungen sind kein Luxus: Bekaeme jede Gruppe den ersten Vorschlag,
klaengen 310 Beitraege gleich - und gleichlautende Beitraege in 310 Gruppen
sind genau das Muster, nach dem Facebooks Spam-Erkennung sucht.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, Field

from fbgroups.config import AppConfig
from fbgroups.marketing.models import Campaign, PostEntwurf, TextQuelle
from fbgroups.models import Group

# Der Platzhalter, den das Modell setzen soll. Genau derselbe, den die Vorlagen
# der Kampagnen seit jeher benutzen - es gibt nur eine Ersetzungsstelle im
# ganzen Projekt, und die liegt in ``beitrag.beitragstext``.
PLATZHALTER = "{link}"

DEFAULT_VARIANTEN = 3

# Woran ein durchgerutschter Tracking-Code zu erkennen waere: das Kuerzelmuster
# der eigenen Codes. Absichtlich weit gefasst - lieber eine Fassung zu viel
# abgewiesen als eine mit erfundenem Code veroeffentlicht.
_CODE_MUSTER = re.compile(r"\b[A-Z]{2,4}(?:-[A-Z0-9]{2,4}){1,3}-\d{2,4}\b")
# Eine ausgeschriebene URL im Text waere ebenfalls ein Link an der Ersetzung
# vorbei. http(s) und die blosse Domainform.
_URL_MUSTER = re.compile(r"(?:https?://|\bwww\.)\S+", re.IGNORECASE)


class KINichtVerfuegbar(RuntimeError):
    """Der Dienst ist nicht erreichbar oder nicht eingerichtet.

    Traegt bewusst eine ausfuehrliche Meldung: Wer sie zu sehen bekommt, sitzt
    vor einem Rechner, auf dem gerade etwas fehlt, und die naechste Frage ist
    immer "was denn?".
    """


class UngueltigerVorschlag(ValueError):
    """Der Vorschlag haelt sich nicht an den Platzhalter."""


class Variante(BaseModel):
    """Eine Textfassung, wie ein Modell sie liefern soll."""

    text: str = Field(description="Der fertige Beitragstext mit dem Platzhalter {link}.")
    stil: str = Field(
        default="",
        description="Ein bis drei Woerter, was diese Fassung anders macht "
        "(z. B. 'persoenlich', 'sachlich', 'kurz').",
    )


class Vorschlaege(BaseModel):
    """Die Antwort eines Modells - mehrere Fassungen."""

    varianten: list[Variante]


class Modell(Protocol):
    """Was ein KI-Anbieter koennen muss - mehr nicht.

    Ein Protokoll statt einer Basisklasse: Der entscheidende Teil dieses
    Moduls ist die Pruefung des Ergebnisses, und die muss ohne Netz, ohne
    Schluessel und ohne Kosten laufen - sonst wird sie nicht ausgefuehrt. Die
    Tests setzen deshalb eine eigene Fassung ein.
    """

    #: Fuer die Anzeige und fuers Protokoll am Entwurf, z. B. "qwen3.5:4b".
    name: str

    def erzeuge(self, system: str, auftrag: str, *, varianten: int) -> Vorschlaege:
        """Liefert Fassungen. Wirft ``KINichtVerfuegbar``, wenn nichts geht."""
        ...

    def status(self) -> Status:
        """Ist der Dienst erreichbar? Fuer die Anzeige, nie fuer die Auswahl."""
        ...


@dataclass
class Status:
    """Was ueber einen Anbieter zu sagen ist, ohne ihn zu benutzen.

    Wird von der Uebersicht bei jedem Aufruf gelesen und muss deshalb billig
    und ungefaehrlich sein: Es wird nichts erzeugt, nichts bezahlt und nichts
    gespeichert - nur nachgesehen, ob jemand antwortet.
    """

    anbieter: str
    erreichbar: bool
    modell: str = ""
    adresse: str = ""
    meldung: str = ""
    # Die Modelle, die dort tatsaechlich liegen. Leer heisst "nicht gefragt"
    # oder "nicht erreichbar" - nicht "keine da".
    verfuegbare_modelle: list[str] = field(default_factory=list)

    @property
    def modell_vorhanden(self) -> bool:
        """Liegt das eingestellte Modell dort auch wirklich?

        Der haeufigste Fehler nach der Einrichtung: Ollama laeuft, aber das
        Modell wurde nie geholt. Die Meldung "verbunden" allein waere dann
        irrefuehrend - die naechste Erzeugung schluege trotzdem fehl.
        """
        if not self.erreichbar or not self.verfuegbare_modelle:
            return False
        return any(
            m == self.modell or m.split(":")[0] == self.modell.split(":")[0]
            for m in self.verfuegbare_modelle
        )


@dataclass
class Auftrag:
    """Alles, was in den Prompt eingeht - ohne den Tracking-Code.

    Bewusst ein eigener Datensatz und nicht ``Group`` selbst: Was an ein
    Modell geht, soll an einer Stelle sichtbar sein und nicht davon abhaengen,
    welche Felder ``Group`` gerade hat. Bei einem lokalen Modell verlaesst
    nichts davon den Rechner; bei einem entfernten ist genau diese Liste das,
    was uebertragen wird.
    """

    gruppenname: str
    beschreibung: str = ""
    zielgruppe: str = ""
    stadt: str = ""
    kategorie: str = ""
    mitglieder: int | None = None
    sprache: str = "arabisch"
    kampagne: str = ""
    produkt: str = ""
    landing_page: str = ""
    varianten: int = DEFAULT_VARIANTEN
    hinweise: str = ""
    # Was bisher geschrieben wurde, damit die naechste Fassung nicht dasselbe
    # sagt. Nur der eigene Text - nie etwas aus der Gruppe.
    bisherige_texte: list[str] = field(default_factory=list)


SYSTEM_PROMPT = """\
Du schreibst kurze Beitraege fuer Facebook-Gruppen. Ein Mensch liest jeden
Vorschlag, bevor er irgendwo erscheint - schreib so, wie er es selbst
schreiben wuerde.

Regeln:

1. Schreibe in der angegebenen Sprache. Bei Arabisch: modernes Hocharabisch
   mit natuerlichem, alltagsnahem Ton, keine steife Werbesprache.
2. Setze **genau einmal** den Platzhalter {link} an die Stelle, an der der Link
   stehen soll. Schreibe niemals eine Adresse, eine Domain oder eine
   Kennung aus Grossbuchstaben und Ziffern aus - der Link wird nach dir
   eingesetzt, und ein ausgeschriebener waere falsch.

   (Hier steht mit Absicht kein Beispiel fuer eine solche Kennung: Ein
   Beispiel im Prompt ist eine Zeichenfolge, die abgeschrieben werden kann,
   und ein abgeschriebener Link zeigt auf eine fremde Gruppe.)
3. Keine Werbefloskeln, keine Ausrufezeichenketten, keine Emoji-Reihen. Hoechstens
   ein bis zwei Emoji, wenn sie zur Gruppe passen.
4. Kurz: drei bis sechs Saetze. Ein knapper Aufruf am Ende, keine Aufzaehlung
   von Funktionen.
5. Sprich die Gruppe an, nicht "alle". Wenn Stadt oder Zielgruppe genannt sind,
   soll man dem Text anmerken, dass er fuer diese Gruppe geschrieben wurde.
6. Jede Fassung muss sich von den anderen erkennbar unterscheiden - in Einstieg,
   Blickwinkel und Laenge, nicht nur in einzelnen Woertern.
7. Keine Versprechen, die niemand einloesen kann (kein "garantiert", keine
   Preise, keine erfundenen Zahlen).
"""

# Nachfassen, wenn die erste Antwort den Platzhalter verfehlt hat.
#
# Genau ein Versuch, und er wiederholt nur die eine Regel, an der es lag. Ein
# kleines Modell verfehlt den Platzhalter oefter als ein grosses; ohne diesen
# Versuch waere die Haelfte der Fassungen unbrauchbar, mit mehr als einem
# wuerde aus jeder Erzeugung eine Schleife, die niemand mehr ueberblickt.
REPARATUR_PROMPT = """\
Deine letzte Antwort war unbrauchbar: {grund}

Schreibe den Beitrag neu. Halte dich an genau diese eine Sache: Der Text
enthaelt **einmal** die Zeichenfolge {link} - genau so, mit geschweiften
Klammern - und sonst keine Adresse, keinen Link und keine Kennung aus
Grossbuchstaben und Ziffern. Alles Uebrige wie zuvor.
"""


def _auftragstext(auftrag: Auftrag) -> str:
    """Der Nutzerteil des Prompts - als Feldliste, nicht als Fliesstext.

    Eine Liste laesst sich lesen und vergleichen; ein Absatz Prosa haette bei
    fehlenden Angaben Luecken, die das Modell zu fuellen versucht. Gerade ein
    kleines Modell erfindet dann Einzelheiten ueber die Gruppe.
    """
    zeilen = [
        f"Gruppenname: {auftrag.gruppenname}",
        f"Sprache: {auftrag.sprache}",
    ]
    if auftrag.mitglieder:
        zeilen.append(f"Mitglieder: {auftrag.mitglieder:,}".replace(",", "."))
    for beschriftung, wert in (
        ("Beschreibung der Gruppe", auftrag.beschreibung),
        ("Zielgruppe", auftrag.zielgruppe),
        ("Stadt", auftrag.stadt),
        ("Thema der Gruppe", auftrag.kategorie),
        ("Kampagne", auftrag.kampagne),
        ("Produkt", auftrag.produkt),
        ("Landingpage", auftrag.landing_page),
        ("Zusaetzliche Hinweise", auftrag.hinweise),
    ):
        if wert:
            zeilen.append(f"{beschriftung}: {wert}")

    if auftrag.bisherige_texte:
        zeilen.append("")
        zeilen.append(
            "Diese Fassungen gibt es schon - schreib etwas anderes, "
            "keine Umformulierung:"
        )
        zeilen.extend(f"- {text.strip()[:400]}" for text in auftrag.bisherige_texte)

    zeilen.append("")
    zeilen.append(f"Schreibe {auftrag.varianten} verschiedene Fassungen.")
    return "\n".join(zeilen)


def auftrag_aus_gruppe(
    group: Group, campaign: Campaign, config: AppConfig, *, varianten: int = DEFAULT_VARIANTEN
) -> Auftrag:
    """Baut den Auftrag aus dem, was ueber die Gruppe bekannt ist.

    Nimmt ausschliesslich Felder, die ohnehin im Bestand stehen. Es wird nichts
    nachgeschlagen und nichts erfunden: Was leer ist, bleibt leer und faellt
    aus dem Prompt heraus, statt als Vermutung hineinzugehen.
    """
    zielgruppen = [
        config.audiences[tag].label_de if tag in config.audiences else tag
        for tag in group.audience_tags
    ]
    return Auftrag(
        gruppenname=group.name or group.group_id,
        beschreibung=group.description_snippet or "",
        zielgruppe=", ".join(zielgruppen),
        stadt=group.city or "",
        kategorie=group.category or "",
        mitglieder=group.member_count_hint,
        sprache=str(config.get("marketing", "posting", "sprache", default="arabisch")),
        kampagne=campaign.name,
        produkt=str(config.get("marketing", "posting", "produkt", default="")),
        landing_page=campaign.landing_page,
        varianten=varianten,
    )


def pruefe_platzhalter(text: str) -> str:
    """Weist alles zurueck, was den Link nicht der Ersetzung ueberlaesst.

    Drei Gruende fuer eine Zurueckweisung, und alle drei enden gleich - mit
    einem Beitrag, dessen Link ins Leere oder auf eine fremde Gruppe zeigt:

    * kein Platzhalter: der Beitrag haette gar keinen Link,
    * mehrere: die Gruppe bekaeme denselben Link doppelt,
    * eine ausgeschriebene URL oder ein codeaehnliches Muster: das Modell hat
      sich einen Link ausgedacht.

    Zurueckgegeben wird der unveraenderte Text. Es wird ausdruecklich **nicht**
    repariert: Eine stillschweigend geflickte Fassung sieht aus wie eine
    gepruefte, und der naechste Fehler faellt dann gar nicht mehr auf. Wenn
    nachgebessert wird, dann durch ein zweites Fragen (``REPARATUR_PROMPT``) -
    das Ergebnis geht wieder durch dieselbe Pruefung.
    """
    anzahl = text.count(PLATZHALTER)
    if anzahl == 0:
        raise UngueltigerVorschlag(f"Kein {PLATZHALTER} im Text - der Beitrag haette keinen Link.")
    if anzahl > 1:
        raise UngueltigerVorschlag(f"{anzahl}x {PLATZHALTER} im Text - erwartet wird genau einer.")

    ohne_platzhalter = text.replace(PLATZHALTER, " ")
    if treffer := _URL_MUSTER.search(ohne_platzhalter):
        raise UngueltigerVorschlag(
            f"Ausgeschriebene Adresse im Text: {treffer.group(0)[:60]} - "
            f"der Link gehoert an {PLATZHALTER}."
        )
    if treffer := _CODE_MUSTER.search(ohne_platzhalter):
        raise UngueltigerVorschlag(
            f"Codeaehnliche Zeichenfolge im Text: {treffer.group(0)} - "
            f"Tracking-Codes werden nicht geschrieben, sondern eingesetzt."
        )
    return text


def erzeuge_entwuerfe(
    modell: Modell,
    auftrag: Auftrag,
    *,
    campaign_id: str,
    group_id: str,
) -> tuple[list[PostEntwurf], list[str]]:
    """Holt Vorschlaege und macht Entwuerfe daraus.

    Returns: die brauchbaren Entwuerfe und die Gruende der verworfenen.

    Beides zurueckzugeben ist Absicht. Ein Lauf, der zwei von drei Fassungen
    verwirft, ist ein Hinweis auf Prompt oder Modell - er darf nicht als "zwei
    Entwuerfe erzeugt" durchgehen. Und ein Lauf ganz ohne brauchbare Fassung
    ist ein Fehler, der beim Menschen ankommen muss und nicht in einem
    Protokoll endet.

    **Genau ein Reparaturversuch je Fassung.** Ein kleines Modell verfehlt den
    Platzhalter deutlich oefter als ein grosses; ohne das Nachfassen waere ein
    guter Teil der Fassungen unbrauchbar. Mehr als einen Versuch gibt es
    nicht - daraus wuerde eine Schleife, deren Kosten (bei Anthropic) und
    Dauer (bei Ollama) niemand mehr ueberblickt.
    """
    antwort = modell.erzeuge(SYSTEM_PROMPT, _auftragstext(auftrag), varianten=auftrag.varianten)

    entwuerfe: list[PostEntwurf] = []
    verworfen: list[str] = []
    for variante in antwort.varianten:
        text = variante.text.strip()
        try:
            pruefe_platzhalter(text)
        except UngueltigerVorschlag as exc:
            repariert = _repariere(modell, auftrag, str(exc))
            if repariert is None:
                verworfen.append(str(exc))
                continue
            text = repariert
        entwuerfe.append(
            PostEntwurf(
                campaign_id=campaign_id,
                group_id=group_id,
                variante=0,           # der Speicher zaehlt hoch
                text=text,
                quelle=TextQuelle.KI,
                modell=modell.name,
            )
        )
    return entwuerfe, verworfen


def _repariere(modell: Modell, auftrag: Auftrag, grund: str) -> str | None:
    """Fragt genau einmal nach - mit der Regel, an der es lag.

    Das Ergebnis geht durch dieselbe Pruefung wie das erste. Waere es anders,
    haette das Nachfassen eine Hintertuer aufgemacht, und genau die Fassungen,
    die schon einmal falsch waren, kaemen ungeprueft durch.
    """
    nachfrage = REPARATUR_PROMPT.format(grund=grund, link=PLATZHALTER)
    try:
        antwort = modell.erzeuge(
            SYSTEM_PROMPT,
            f"{_auftragstext(auftrag)}\n\n{nachfrage}",
            varianten=1,
        )
    except KINichtVerfuegbar:
        return None

    for variante in antwort.varianten:
        text = variante.text.strip()
        try:
            return pruefe_platzhalter(text)
        except UngueltigerVorschlag:
            continue
    return None
