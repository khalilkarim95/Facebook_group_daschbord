"""Aus einer Vorlage und einer Gruppe wird ein fertiger Text.

Die **deterministische** Haelfte der Textherstellung. Was hier entsteht, haengt
allein von der Gruppe, der Kampagne und ``config/textvorlagen.yaml`` ab -
dieselbe Gruppe ergibt heute und in einem Jahr denselben Text. Die andere
Haelfte ist ``marketing/ki/``, und die ist ausdruecklich nicht deterministisch;
sie setzt hier auf, statt hier einzugreifen. **Die Vielfalt kommt aus dem
Vorrat, nicht aus dem Modell** - ein Sprachmodell, das aus dem Nichts schreiben
soll, erfindet Produkt, Anlass und Zahlen.

**Zwei Einsatzzwecke, zwei Vorraete.** Ein Beitrag eroeffnet, ein Kommentar
antwortet unter einem fremden Beitrag; sprachlich haben sie wenig gemeinsam,
und ein gekuerzter Post als Kommentar liest sich wie eingeworfene Werbung.
``Texttyp`` waehlt deshalb den Topf, den Prompt und die Spalte - nie wird ein
Posttext als Kommentar wiederverwendet.

**Der Tracking-Link wird in diesem Modul nicht ersetzt.** ``{link}`` geht
unveraendert durch und wird erst in ``beitrag.beitragstext`` aufgeloest - der
einzigen Stelle im Projekt, an der ein Tracking-Code in einen Text kommt. Das
ist keine Bequemlichkeit, sondern die Voraussetzung dafuer, dass ein
Sprachmodell den gespeicherten Text ueberarbeiten darf, ohne je einen Code zu
sehen. Was das Modell nie bekommt, kann es nicht verfaelschen.

Ersetzt werden ausschliesslich ``{zielgruppe}``, ``{stadt}``, ``{ziel}``,
``{gegenstand}`` und ``{gruppe}``: Angaben, die zu *dieser* Gruppe gehoeren,
sich nicht mehr aendern und deshalb mitgespeichert werden duerfen. So steht in
``generated_text`` genau das, was hinausgeht - abgesehen von dem einen Link.

``{datum}`` gehoert ausdruecklich **nicht** dazu, obwohl es kein Link ist. Es
traegt den laufenden Monat, und der aendert sich; eingesetzt und gespeichert
stuende in einem Beitrag, der drei Wochen nach dem Erzeugen hinausgeht, der
Monat von damals. Deshalb geht es wie ``{link}`` durch und wird erst in
``beitrag.mit_link`` aufgeloest.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fbgroups.config import AppConfig, Audience, City
from fbgroups.marketing.models import Campaign, Texttyp
from fbgroups.models import Group

# Der Platzhalter, der dieses Modul unangetastet verlaesst. Denselben Namen
# benutzen die Kampagnenvorlagen seit jeher und ``ki.basis.PLATZHALTER``.
PLATZHALTER_LINK = "{link}"

#: Was in einer Vorlage stehen darf. Alles andere in geschweiften Klammern ist
#: ein Fehler und keine Freiheit: Es bliebe unersetzt im Beitrag stehen, und
#: das faellt erst auf, wenn er in der Gruppe steht.
#:
#: Die ersten fuenf loest dieses Modul auf, die letzten vier
#: ``beitrag.mit_link`` beim Lesen.
#:
#: ``datum`` steht bei den spaeten: Ein beim Erzeugen eingesetzter Monatsname
#: bliebe im gespeicherten Text stehen, und der Beitrag ginge drei Wochen
#: spaeter mit dem Monat von damals hinaus. ``zielgruppe``, ``stadt``,
#: ``ziel`` und ``gegenstand`` sind dagegen Angaben ueber *diese* Gruppe, die
#: sich nicht mehr aendern - sie duerfen mitgespeichert werden.
ERLAUBTE_PLATZHALTER = frozenset(
    {
        "zielgruppe",
        "stadt",
        "ziel",
        "gegenstand",
        "gruppe",
        "link",
        "tracking_code",
        "landing_page",
        "datum",
    }
)

_PLATZHALTER_MUSTER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# Woran ein durchgerutschter Tracking-Code zu erkennen waere: das Kuerzelmuster
# der eigenen Codes. Absichtlich weit gefasst - lieber ein Text zu viel
# abgewiesen als einer mit erfundenem Code veroeffentlicht.
_CODE_MUSTER = re.compile(r"\b[A-Z]{2,4}(?:-[A-Z0-9]{2,4}){1,3}-\d{2,4}\b")
# Eine ausgeschriebene URL waere ebenfalls ein Link an der Ersetzung vorbei.
# http(s) und die blosse Domainform.
_URL_MUSTER = re.compile(r"(?:https?://|\bwww\.)\S+", re.IGNORECASE)

#: Die beiden Toepfe je Sprache und Zweck. Getrennt statt eines Platzhalters,
#: der manchmal leer bleibt: 152 von 313 Gruppen im Bestand haben keine
#: erkannte Stadt, und "in " mit anschliessendem Nichts steht im Beitrag.
MIT_STADT = "mit_stadt"
OHNE_STADT = "ohne_stadt"

#: Was in ``settings.yaml`` und im Kampagnenformular an Sprachen vorkommt.
#: ``marketing.posting.sprache`` steht dort ausgeschrieben ("arabisch"), das
#: Formular schickt den Kuerzel-Code ("ar") - beide muessen hier ankommen.
_SPRACHEN: dict[str, str] = {
    "ar": "ar",
    "arabisch": "ar",
    "arabic": "ar",
    "de": "de",
    "deutsch": "de",
    "german": "de",
}

VORGABE_SPRACHE = "ar"

#: Schluessel fuer "der Text kam aus der eigenen Vorlage der Kampagne".
#: Kein Verweis in ``textvorlagen.yaml`` - er zeigt auf ``campaign.message_template``.
KAMPAGNENVORLAGE = "kampagne"


class VorlageFehlt(LookupError):
    """Zu dieser Sprache und diesem Topf steht keine Vorlage bereit.

    Eigene Ausnahme statt eines leeren Textes: Ein leerer Beitragstext faellt
    erst auf, wenn er in der Gruppe steht, und dann ist er nicht mehr
    zurueckzuholen. Die Meldung nennt Sprache, Zweck und Topf, weil das die
    Angaben sind, mit denen sich die Luecke in der Konfiguration schliessen
    laesst.
    """


class UnbekannterPlatzhalter(VorlageFehlt):
    """In der Vorlage steht ein Platzhalter, den niemand ersetzt.

    Erbt von ``VorlageFehlt``, damit jeder Aufrufer, der eine lueckenhafte
    Konfiguration schon behandelt, auch diesen Fall behandelt: Die Folge ist
    dieselbe - fuer *diese* Gruppe entsteht kein Text, und der Grund steht im
    Bericht. Ein Beitrag mit ``{beruf}`` mittendrin waere schlimmer als keiner.
    """


class UngueltigerText(ValueError):
    """Der Text traegt seinen Link nicht dort, wo er ersetzt wird.

    Frueher hiess das ``ki.basis.UngueltigerVorschlag`` und galt fuer
    Modellantworten. Die Zusicherung gilt aber fuer **jeden** Text: einen aus
    der Vorlage, einen von Hand geschriebenen, einen aus der Zwischenablage.
    Mit der KI hatte sie nur zufaellig zusammengewohnt.
    """


def pruefe_platzhalter(text: str, *, texttyp: Texttyp = Texttyp.POST) -> str:
    """Weist alles zurueck, was den Link nicht der Ersetzung ueberlaesst.

    Drei Gruende fuer eine Zurueckweisung, und alle drei enden gleich - mit
    einem Beitrag, dessen Link ins Leere oder auf eine fremde Gruppe zeigt:

    * kein Platzhalter: der Beitrag haette gar keinen Link,
    * mehrere: die Gruppe bekaeme denselben Link doppelt,
    * eine ausgeschriebene URL oder ein codeaehnliches Muster: jemand hat
      einen Link von Hand hineingeschrieben.

    **Der erste Grund gilt nicht fuer den Kommentar** (seit 11.09.2026). Ein
    Kommentar *darf* ohne Link auskommen - ob er einen traegt, entscheidet
    der Vorrat und nicht diese Pruefung. Der Anlass: Facebook zeigte am
    11.09.2026 "Dein Kommentar wurde abgelehnt" mit dem Kriterium *"Link in
    Kommentar"*. Das ist eine **Regel der Gruppe**, die deren Leitung
    eingeschaltet hat - sie gilt in manchen Gruppen und in anderen nicht, und
    deshalb ist es eine Entscheidung und keine Zusicherung. Ohne Link im
    Kommentar zaehlt allein der eigene Beitrag daneben.

    **Mehrfach und ausgeschrieben bleiben auch dort verboten.** Ein Kommentar
    ohne Link ist eine Entscheidung, ein Kommentar mit einer von Hand
    getippten Adresse ist derselbe alte Fehler: Er sieht richtig aus, und
    seine Gruppe bekommt nie einen Klick gutgeschrieben.

    Zurueckgegeben wird der unveraenderte Text. Es wird ausdruecklich **nicht**
    repariert: Eine stillschweigend geflickte Fassung sieht aus wie eine
    gepruefte, und der naechste Fehler faellt dann gar nicht mehr auf.
    """
    anzahl = text.count(PLATZHALTER_LINK)
    if anzahl == 0 and texttyp is not Texttyp.KOMMENTAR:
        raise UngueltigerText(
            f"Kein {PLATZHALTER_LINK} im Text - der Beitrag haette keinen Link."
        )
    if anzahl > 1:
        raise UngueltigerText(
            f"{anzahl}x {PLATZHALTER_LINK} im Text - erwartet wird genau einer."
        )

    ohne_platzhalter = text.replace(PLATZHALTER_LINK, " ")
    if treffer := _URL_MUSTER.search(ohne_platzhalter):
        raise UngueltigerText(
            f"Ausgeschriebene Adresse im Text: {treffer.group(0)[:60]} - "
            f"der Link gehoert an {PLATZHALTER_LINK}."
        )
    if treffer := _CODE_MUSTER.search(ohne_platzhalter):
        raise UngueltigerText(
            f"Codeaehnliche Zeichenfolge im Text: {treffer.group(0)} - "
            f"Tracking-Codes werden nicht geschrieben, sondern eingesetzt."
        )
    return text


@dataclass(frozen=True)
class Vorlage:
    """Eine Fassung aus dem Vorrat: ihre Kennung und ihr Text.

    Die Kennung ("direkt", "hilfreich") ist Teil des gespeicherten
    Schluessels. Frueher stand dort eine laufende Nummer - dann verschob eine
    in der Mitte eingefuegte Vorlage alle folgenden, und eine Gruppe trug
    einen Schluessel, hinter dem ein anderer Text stand. Eine Kennung
    ueberlebt das Umsortieren; sie zu **aendern** ist dasselbe wie die Vorlage
    zu loeschen, und das ist die ehrlichere Beschreibung des Vorgangs.
    """

    kennung: str
    text: str


@dataclass(frozen=True)
class Personalisierung:
    """Was aus Gruppe und Kampagne in die Vorlage einfliesst.

    Bewusst nur drei Angaben. Jede weitere waere eine Behauptung ueber die
    Gruppe, die aus dem Bestand nicht belegt ist - und ein Beitrag, der eine
    Gruppe falsch beschreibt, richtet mehr Schaden an als einer, der sie gar
    nicht beschreibt.
    """

    zielgruppe: str
    stadt: str
    gruppe: str = ""
    # Wohin die Reise geht ("سوريا") und was verschickt wird ("غرض"). Beides
    # steht in der Konfiguration und nicht im Text: Eine Vorlage, die "سوريا"
    # ausschreibt, ist fuer eine irakische Kampagne falsch, und man sieht es
    # ihr nicht an.
    ziel: str = ""
    gegenstand: str = ""

    @property
    def topf(self) -> str:
        """Aus welchem Topf die Vorlage kommen muss."""
        return MIT_STADT if self.stadt else OHNE_STADT


def sprache_der_kampagne(campaign: Campaign, config: AppConfig) -> str:
    """Die Sprache, in der diese Kampagne schreibt.

    Reihenfolge: das Feld der Kampagne, dann die Vorgabe aus ``settings.yaml``,
    dann Arabisch. Ein **unbekannter** Wert faellt auf die Vorgabe zurueck
    statt eine Ausnahme zu werfen - dieselbe Ueberlegung wie bei ``AI_PROVIDER``:
    ein Tippfehler im Formular soll die Textherstellung nicht anhalten.

    Kein Sprachmodell entscheidet das. Die Sprache steht an der Kampagne, und
    ein Modell, das sie selbst waehlte, schriebe irgendwann arabisch in eine
    deutsche Gruppe.
    """
    aus_kampagne = _SPRACHEN.get((campaign.language or "").strip().lower())
    if aus_kampagne:
        return aus_kampagne
    aus_settings = str(config.get("marketing", "posting", "sprache", default=""))
    return _SPRACHEN.get(aus_settings.strip().lower(), VORGABE_SPRACHE)


def _stadt_der_gruppe(group: Group, config: AppConfig) -> City | None:
    """Findet den Stadt-Datensatz zu ``Group.city``.

    ``Group.city`` haelt den **deutschen Anzeigenamen** ("Duesseldorf"), nicht
    die Kennung - der Bestand ist so gewachsen. Fuer ``name_ar`` braucht es
    aber den Datensatz, also wird ueber alle Namen der Stadt gesucht
    (deutsch, arabisch, Aliasse). Findet sich nichts, gilt die Gruppe als ohne
    Stadt: lieber die allgemeine Vorlage als ein Beitrag, der eine Stadt nennt,
    die es in der Konfiguration nicht gibt.
    """
    roh = (group.city or "").strip()
    if not roh:
        return None
    for city in config.cities.values():
        if roh == city.name_de or roh == city.name_ar or roh in city.aliases:
            return city
    return None


def _zielgruppe_der_gruppe(
    group: Group, campaign: Campaign, config: AppConfig
) -> Audience | None:
    """Welche Zielgruppe angesprochen wird.

    Drei Stufen, und die Reihenfolge ist der Punkt:

    1. Ein Tag der Gruppe, den **auch die Kampagne** bewirbt. Steht eine
       Gruppe unter ``syrians`` und ``arabs`` und die Kampagne wirbt fuer
       Syrer, ist "السوريين" die richtige Anrede und nicht "العرب".
    2. Sonst der erste Tag der Gruppe.
    3. Sonst die erste Zielgruppe der **Kampagne**. Das ist kein Erfinden:
       115 von 313 Gruppen tragen keinen Tag, und die Kampagne sagt selbst,
       wen sie bewirbt.
    """
    tags = list(group.audience_tags or [])
    gemeinsam = [t for t in tags if t in campaign.audiences]
    for kandidat in (*gemeinsam, *tags, *campaign.audiences):
        treffer = config.audiences.get(kandidat)
        if treffer is not None:
            return treffer
    return None


def _allgemeine_anrede(config: AppConfig, sprache: str) -> str:
    """Die Anrede, wenn weder Gruppe noch Kampagne eine Zielgruppe nennen.

    Steht in ``textvorlagen.yaml`` und nicht hier: "Freunde" statt "الأصدقاء"
    ist eine Entscheidung ueber den Tonfall eines Beitrags, und die gehoert
    zum Text.
    """
    anreden = config.textvorlagen.get("anrede_allgemein") or {}
    return str(anreden.get(sprache, "")).strip()


def _aus_textvorlagen(config: AppConfig, block: str, sprache: str) -> str:
    """Ein sprachabhaengiger Rueckfallwert aus ``textvorlagen.yaml``.

    Dieselbe Form wie ``anrede_allgemein``: ein Block, darunter je Sprache ein
    Wort. Sie stehen dort und nicht hier, weil "الوطن" statt "بلدك" und "غرض"
    statt "طرد" Entscheidungen ueber den Tonfall eines Beitrags sind - und
    die gehoeren zum Text.
    """
    werte = config.textvorlagen.get(block) or {}
    return str(werte.get(sprache, "")).strip()


def monat_jetzt(config: AppConfig, sprache: str, *, jetzt: datetime | None = None) -> str:
    """Der Name des laufenden Monats fuer ``{datum}``.

    ``jetzt`` gibt es allein fuer den Test: Ein Test, der den echten Kalender
    liest, prueft im August etwas anderes als im September.

    Fehlt die Liste oder ist sie unvollstaendig, kommt eine leere Zeichenkette
    zurueck. ``config-check`` meldet das vorher; hier still auf eine
    lateinische Zahl auszuweichen hiesse, mitten in einen arabischen Satz "8"
    zu schreiben.
    """
    monate = (config.textvorlagen.get("monate") or {}).get(sprache) or []
    nummer = (jetzt or datetime.now()).month
    if len(monate) < 12:
        return ""
    return str(monate[nummer - 1]).strip()


def personalisierung(group: Group, campaign: Campaign, config: AppConfig) -> Personalisierung:
    """Sammelt die Angaben fuer *diese* Gruppe."""
    sprache = sprache_der_kampagne(campaign, config)

    city = _stadt_der_gruppe(group, config)
    audience = _zielgruppe_der_gruppe(group, campaign, config)

    return Personalisierung(
        zielgruppe=(
            audience.anrede(sprache) if audience else _allgemeine_anrede(config, sprache)
        ),
        stadt=city.anzeige(sprache) if city else "",
        gruppe=group.name or "",
        # Das Ziel kommt von der Zielgruppe, nicht von der Gruppe: Wer syrische
        # Gruppen bewirbt, meint Syrien - auch wenn die Gruppe selbst in Bonn
        # sitzt. Ohne hinterlegtes Land ("arabs") faellt es auf "الوطن"
        # zurueck, statt eines zu erfinden.
        ziel=(
            (audience.ziel(sprache) if audience else "")
            or _aus_textvorlagen(config, "ziel_allgemein", sprache)
        ),
        gegenstand=_aus_textvorlagen(config, "gegenstand_allgemein", sprache),
    )


def _toepfe_der_sprache(config: AppConfig, sprache: str, texttyp: Texttyp) -> dict[str, Any]:
    """Die beiden Toepfe zu Sprache und Zweck - auch aus einer alten Datei.

    Die aktuelle Form ist ``vorlagen: <sprache>: <zweck>: <topf>``. Eine
    Datei aus der Zeit vor den Kommentaren hat die Zweck-Ebene nicht; ihre
    Toepfe stehen direkt unter der Sprache und sind Beitragsvorlagen. Sie
    werden deshalb als ``post`` gelesen, statt den Dienst mit "keine Vorlage"
    anzuhalten: Code und Konfiguration werden zusammen ausgerollt, aber nicht
    zwingend in derselben Sekunde.
    """
    alle: dict[str, Any] = config.textvorlagen.get("vorlagen") or {}
    ebene: dict[str, Any] = alle.get(sprache) or {}
    if MIT_STADT in ebene or OHNE_STADT in ebene:
        # Alte Form: nur Beitragsvorlagen, kein Kommentar.
        return ebene if texttyp is Texttyp.POST else {}
    return ebene.get(texttyp.value) or {}


def _topf(config: AppConfig, sprache: str, texttyp: Texttyp, topf: str) -> list[Vorlage]:
    """Die Vorlagen eines Topfes. Wirft, wenn er leer ist.

    Ein Eintrag ist ``{id, text}``; eine blanke Zeichenkette aus der alten
    Form bekommt ihre Position als Kennung ("1" ... "5"). Damit loest ein
    gespeicherter Schluessel aus der Zeit davor ("ar/mit_stadt/3") weiterhin
    genau dieselbe Vorlage auf.
    """
    liste = _toepfe_der_sprache(config, sprache, texttyp).get(topf) or []
    vorlagen: list[Vorlage] = []
    for nummer, eintrag in enumerate(liste, 1):
        if isinstance(eintrag, dict):
            kennung = str(eintrag.get("id") or nummer).strip()
            text = str(eintrag.get("text") or "")
        else:
            kennung, text = str(nummer), str(eintrag)
        vorlagen.append(Vorlage(kennung=kennung, text=text))

    if not vorlagen:
        raise VorlageFehlt(
            f"config/textvorlagen.yaml hat keine Vorlage fuer "
            f"'{sprache}/{texttyp.value}/{topf}'."
        )
    return vorlagen


def _nummer(schluessel: str, anzahl: int) -> int:
    """Welche Vorlage dieser Schluessel bekommt - stabil ueber Laeufe hinweg.

    ``blake2b`` statt des eingebauten ``hash``: Der ist fuer Zeichenketten je
    Prozess gesalzen (PYTHONHASHSEED), und dieselbe Gruppe bekaeme nach jedem
    Neustart eine andere Vorlage. Der Text wuerde sich damit unter demjenigen
    aendern, der ihn gerade freigegeben hat.

    Aus demselben Grund nicht ``random`` und nicht die Reihenfolge der
    Zuordnung: Die Nummer haengt allein am uebergebenen Schluessel - dieselbe
    Ueberlegung wie bei der Codevergabe, die ``first_seen_at`` folgt und nicht
    dem Score.
    """
    roh = hashlib.blake2b(schluessel.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(roh, "big") % anzahl


def _wahlschluessel(group_id: str, texttyp: Texttyp) -> str:
    """Woraus die Wahl gezogen wird: Gruppe **und** Einsatzzweck.

    Der Zweck gehoert hinein, sonst faende dieselbe Gruppe in beiden Toepfen
    immer die gleiche Stelle - und wer "direkt" als Beitrag bekommt, bekaeme
    "direkt" auch als Kommentar. Beide sind ohnehin verschieden formuliert,
    aber die Haeufung waere eine unnoetige Regelmaessigkeit in etwas, das
    gerade nicht regelmaessig aussehen soll.
    """
    return f"{group_id}|{texttyp.value}"


def schluessel_fuer(
    config: AppConfig,
    *,
    sprache: str,
    topf: str,
    group_id: str,
    texttyp: Texttyp = Texttyp.POST,
) -> str:
    """Der Vorlagenschluessel dieser Gruppe, z. B. ``"ar/post/mit_stadt/alltag"``.

    Vier Teile: Sprache, Zweck, Topf, Kennung. Die Kennung statt einer Nummer
    ist der Unterschied zu frueher - siehe ``Vorlage``.
    """
    liste = _topf(config, sprache, texttyp, topf)
    gewaehlt = liste[_nummer(_wahlschluessel(group_id, texttyp), len(liste))]
    return f"{sprache}/{texttyp.value}/{topf}/{gewaehlt.kennung}"


def _zerlege(schluessel: str) -> tuple[str, Texttyp, str, str]:
    """``"ar/post/mit_stadt/alltag"`` -> Sprache, Zweck, Topf, Kennung.

    Ein Schluessel aus drei Teilen ("ar/mit_stadt/3") stammt aus der Zeit vor
    den Kommentaren und meint einen Beitrag. Ihn weiterhin zu lesen ist keine
    Nachsicht, sondern der Unterschied zwischen "der Bestand behaelt seine
    Texte" und "310 Gruppen bekommen beim naechsten Fuellen eine andere
    Vorlage".
    """
    teile = schluessel.split("/")
    if len(teile) == 4:
        sprache, zweck, topf, kennung = teile
        try:
            return sprache, Texttyp(zweck), topf, kennung
        except ValueError as exc:
            raise VorlageFehlt(f"Unbekannter Texttyp in '{schluessel}'.") from exc
    if len(teile) == 3:
        sprache, topf, kennung = teile
        return sprache, Texttyp.POST, topf, kennung
    raise VorlageFehlt(f"Kein gueltiger Vorlagenschluessel: '{schluessel}'.")


def vorlage_zu(config: AppConfig, schluessel: str) -> str:
    """Holt den Text zu einem gespeicherten Schluessel.

    Wirft ``VorlageFehlt``, wenn der Schluessel ins Leere zeigt - etwa weil
    jemand eine Vorlage entfernt oder ihre Kennung geaendert hat. Der Aufrufer
    entscheidet dann, ob er neu waehlt; still auf die erste Vorlage
    auszuweichen wuerde den Unterschied verschweigen.

    Eine rein numerische Kennung wird zusaetzlich als **Position** gelesen,
    wenn sie unter den Kennungen nicht vorkommt. Das ist der Weg fuer die
    Schluessel, die vor der Umstellung vergeben wurden.
    """
    sprache, texttyp, topf, kennung = _zerlege(schluessel)
    liste = _topf(config, sprache, texttyp, topf)

    for vorlage in liste:
        if vorlage.kennung == kennung:
            return vorlage.text

    if kennung.isdigit():
        index = int(kennung) - 1
        if 0 <= index < len(liste):
            return liste[index].text

    raise VorlageFehlt(
        f"'{schluessel}' zeigt auf keine Vorlage in "
        f"'{sprache}/{texttyp.value}/{topf}' "
        f"({', '.join(v.kennung for v in liste)})."
    )


def unbekannte_platzhalter(text: str) -> list[str]:
    """Welche Platzhalter in diesem Text niemand ersetzen wird."""
    return sorted(
        {
            name
            for name in _PLATZHALTER_MUSTER.findall(text)
            if name not in ERLAUBTE_PLATZHALTER
        }
    )


def fuelle(text: str, daten: Personalisierung) -> str:
    """Setzt die Angaben der Gruppe ein - und **nur** diese.

    ``{link}``, ``{tracking_code}``, ``{landing_page}`` und ``{datum}`` bleiben
    stehen. Sie gehoeren ``beitrag.mit_link``, und dass sie hier durchgehen,
    ist der Grund, warum der gespeicherte Text einem Sprachmodell vorgelegt
    werden darf.

    Ein Platzhalter, den weder dieses Modul noch ``beitragstext`` kennt, wirft
    ``UnbekannterPlatzhalter``. Er bliebe sonst in geschweiften Klammern im
    Beitrag stehen, und das faellt erst in der Gruppe auf.
    """
    gefuellt = (
        text.replace("{zielgruppe}", daten.zielgruppe)
        .replace("{stadt}", daten.stadt)
        .replace("{ziel}", daten.ziel)
        .replace("{gegenstand}", daten.gegenstand)
        .replace("{gruppe}", daten.gruppe)
    )
    if offen := unbekannte_platzhalter(gefuellt):
        raise UnbekannterPlatzhalter(
            "Platzhalter, die niemand ersetzt: " + ", ".join(f"{{{n}}}" for n in offen)
        )
    return gefuellt


# --- Anlassbezogene Kommentare ---------------------------------------------
#: Wo die anlassbezogenen Fassungen stehen. Eigener Zweig neben ``vorlagen:``
#: und nicht darin: Jene gehoeren einer **Gruppe** und stehen fest, bevor ein
#: Beitrag gelesen wurde; diese gehoeren einem **Anlass** und antworten auf
#: das, was in dem Beitrag steht. Ein gemeinsamer Topf haette zwei Dinge
#: vermischt, die zu verschiedenen Zeitpunkten gewaehlt werden.
ANLASSZWEIG = "anlaesse"


def anlassvorrat(config: AppConfig, sprache: str, anlass: str) -> list[Vorlage]:
    """Die Fassungen zu **einem** Anlass - leer, wenn es keine gibt.

    Leer und keine Ausnahme: Ein Anlass ohne Vorrat ist kein Fehler der
    Konfiguration, sondern die Auskunft "dazu haben wir nichts vorbereitet".
    Der Aufrufer schweigt dann, statt etwas anderes zu sagen - dieselbe
    Haltung wie bei ``Relevanz.KEINE``.
    """
    zweig = config.textvorlagen.get(ANLASSZWEIG) or {}
    liste = (zweig.get(sprache) or {}).get(anlass) or []
    return [
        Vorlage(kennung=str(e.get("id", i)), text=str(e.get("text", "")).strip())
        for i, e in enumerate(liste)
        if str(e.get("text", "")).strip()
    ]


def anlasstext(
    config: AppConfig,
    *,
    sprache: str,
    anlass: str,
    group_id: str,
    daten: Personalisierung,
    mit_link: bool = False,
    bisherige: frozenset[str] = frozenset(),
) -> tuple[str, str] | None:
    """Der Kommentartext zu einem Anlass. Returns: ``(schluessel, text)``.

    ``None`` heisst: Zu diesem Anlass steht in ``textvorlagen.yaml`` nichts -
    dann wird nicht kommentiert. Eine Ersatzfassung aus einem anderen Topf
    waere eine Antwort auf eine andere Frage.

    **Die Wahl ist deterministisch** (``blake2b`` ueber Gruppe und Anlass),
    aus demselben Grund wie ueberall im Projekt: Dieselbe Gruppe bekommt zum
    selben Anlass dieselbe Fassung, auch nach einem Neustart. ``bisherige``
    nimmt die in dieser Gruppe bereits verwendeten Schluessel entgegen und
    geht dann der Reihe nach weiter - damit steht nicht zweimal derselbe Satz
    in derselben Gruppe, und das ist die Duplikatkontrolle auf Textebene.
    Sind alle verbraucht, wird die berechnete genommen: Ein Kommentar zu viel
    in derselben Gruppe faellt weniger auf als gar keine Antwort - und die
    Tagesmenge je Gruppe begrenzt das ohnehin.

    **Der Link kommt mechanisch dazu, nicht aus der Vorlage.** Die Fassungen
    tragen keine Adresse; wo die Gruppe Links erlaubt und der Beitrag ihn
    traegt, wird ``{link}`` in einer eigenen Zeile angehaengt. Aufgeloest wird
    er weiterhin erst in ``beitrag.mit_link`` - der gespeicherte Text traegt
    den Platzhalter, nie den Code.
    """
    vorrat = anlassvorrat(config, sprache, anlass)
    if not vorrat:
        return None

    start = _nummer(f"{group_id}|{anlass}", len(vorrat))
    reihe = [vorrat[(start + i) % len(vorrat)] for i in range(len(vorrat))]
    gewaehlt = next(
        (
            v
            for v in reihe
            if f"{sprache}/{ANLASSZWEIG}/{anlass}/{v.kennung}" not in bisherige
        ),
        reihe[0],
    )

    text = fuelle(gewaehlt.text, daten)
    if mit_link:
        text = f"{text}\n{PLATZHALTER_LINK}"
    pruefe_platzhalter(text, texttyp=Texttyp.KOMMENTAR)
    return f"{sprache}/{ANLASSZWEIG}/{anlass}/{gewaehlt.kennung}", text


def anlasstext_zu(config: AppConfig, schluessel: str, *, mit_link: bool) -> str:
    """Der Anlasstext zu einem gespeicherten Schluessel - oder ``""``.

    Die Umkehrung von ``anlasstext`` und der Grund, warum kein Text vom
    Arbeitsrechner zurueckreisen muss (14.09.2026): Der Fernbetrieb waehlt
    seinen Kommentar seit demselben Tag selbst, und der Server muss trotzdem
    festhalten koennen, **welcher** Satz in der Gruppe steht. Er bekommt
    dafuer die **Kennung** und baut den Text daraus neu - beide fahren
    dieselbe ``textvorlagen.yaml``.

    Dieselbe Sparsamkeit wie beim Regelbefund: Hinueber geht das Urteil, nie
    die Seite. Und dieselbe Zusicherung wie ueberall: Der Text traegt
    ``{link}``, nie den Code - aufgeloest wird er erst in
    ``beitrag.mit_link``.

    ``""`` heisst "nicht aufloesbar" und ist kein Fehler: Wer eine Vorlage
    entfernt, soll damit keine Buchung verhindern - festgehalten wird dann
    eben nur der Ausgang.
    """
    teile = schluessel.split("/")
    if len(teile) != 4 or teile[1] != ANLASSZWEIG:
        return ""
    sprache, _, anlass, kennung = teile

    for vorlage in anlassvorrat(config, sprache, anlass):
        if vorlage.kennung == kennung:
            text = vorlage.text
            if mit_link:
                text = f"{text}\n{PLATZHALTER_LINK}"
            return text
    return ""


def erzeuge(
    group: Group,
    campaign: Campaign,
    config: AppConfig,
    *,
    schluessel: str = "",
    texttyp: Texttyp = Texttyp.POST,
) -> tuple[str, str]:
    """Der fertige Text fuer eine Gruppe. Returns: ``(schluessel, text)``.

    Ist ein ``schluessel`` gegeben und noch aufloesbar, wird **er** benutzt.
    Damit bleibt der Text derselbe, wenn die Personalisierung erneut laeuft -
    etwa nachdem eine Stadt nachgetragen wurde. Zeigt er ins Leere, wird neu
    gewaehlt; das ist besser als abzubrechen, denn der Grund liegt dann in der
    Konfiguration und nicht bei dieser Gruppe.

    Ein gespeicherter Schluessel des **falschen** Zwecks wird uebergangen: Ein
    Kommentar, der die Beitragsvorlage traegt, waere genau die Vermischung,
    gegen die es die beiden Toepfe gibt.
    """
    daten = personalisierung(group, campaign, config)

    if schluessel:
        try:
            if _zerlege(schluessel)[1] is texttyp:
                return schluessel, fuelle(vorlage_zu(config, schluessel), daten)
        except UnbekannterPlatzhalter:
            raise
        except VorlageFehlt:
            pass

    sprache = sprache_der_kampagne(campaign, config)
    neuer = schluessel_fuer(
        config,
        sprache=sprache,
        topf=daten.topf,
        group_id=group.group_id,
        texttyp=texttyp,
    )
    return neuer, fuelle(vorlage_zu(config, neuer), daten)


def text_fuer_gruppe(
    group: Group,
    campaign: Campaign,
    config: AppConfig,
    *,
    schluessel: str = "",
    texttyp: Texttyp = Texttyp.POST,
) -> tuple[str, str]:
    """Der Text dieser Gruppe. Returns: ``(schluessel, text)``.

    Die eine Stelle, an der entschieden wird, **welche** Vorlage gilt -
    Kommandozeile und Uebersicht rufen beide hier an. Zwei Fassungen dieser
    Entscheidung koennten auseinanderlaufen, und der Unterschied fiele erst in
    einem veroeffentlichten Beitrag auf.

    Hat die Kampagne eine eigene Vorlage, gilt **sie** - aber nur fuer den
    Beitrag: Ein Mensch hat sie ausdruecklich als Beitrag hingeschrieben, und
    sie als Kommentar unter einen fremden Beitrag zu setzen waere eine
    Behauptung, sie tauge dafuer. Sie geht durch dieselbe Personalisierung -
    ``{zielgruppe}``, ``{stadt}`` und ``{gruppe}`` wirken darin genauso wie in
    einer Vorlage aus dem Topf.

    Ist das Feld leer, kommt die Vorlage aus ``textvorlagen.yaml``. Leer ist
    hier der Normalfall und kein Mangel: Ein Vorrat von fuenf Fassungen je
    Topf ist der Grund, warum 310 Beitraege nicht gleich klingen.
    """
    eigene = campaign.message_template.strip()
    if eigene and texttyp is Texttyp.POST:
        return KAMPAGNENVORLAGE, fuelle(eigene, personalisierung(group, campaign, config))
    return erzeuge(group, campaign, config, schluessel=schluessel, texttyp=texttyp)


def reihenfolge_fuer(
    config: AppConfig,
    *,
    sprache: str,
    topf: str,
    group_id: str,
    texttyp: Texttyp = Texttyp.POST,
) -> list[Vorlage]:
    """Der ganze Topf, gedreht - die Fassung dieser Gruppe steht vorn.

    Die Arbeitsseite zeigt nicht mehr *eine* Vorlage je Gruppe, sondern alle
    fuenf; ein Mensch waehlt aus. Damit stellt sich eine Frage, die es vorher
    nicht gab: In welcher Reihenfolge?

    Nicht in der Reihenfolge der Datei. Stuende ueberall dieselbe Fassung auf
    Platz 1, waere sie die, die jeder nimmt, der nicht blaettert - und 310
    Beitraege klaengen wieder gleich. Genau davor schuetzt der Vorrat.

    Gedreht wird deshalb um dieselbe Zahl, die vorher die *eine* Fassung
    bestimmt hat (``_nummer`` ueber ``blake2b``). Zwei Dinge folgen daraus,
    und beide sind der Grund fuer diese Bauart: Platz 1 traegt exakt den
    Text, den die Gruppe auch vor den Vorschlaegen bekommen haette, und die
    Reihenfolge ueberlebt jeden Neustart des Dienstes. Ein ``random.shuffle``
    haette bei jedem Aufruf eine andere Nummer neben denselben Text gestellt.
    """
    liste = _topf(config, sprache, texttyp, topf)
    start = _nummer(_wahlschluessel(group_id, texttyp), len(liste))
    return [liste[(start + versatz) % len(liste)] for versatz in range(len(liste))]


def alle_texte_fuer_gruppe(
    group: Group,
    campaign: Campaign,
    config: AppConfig,
    *,
    texttyp: Texttyp = Texttyp.POST,
    hoechstens: int = 0,
) -> list[tuple[str, str]]:
    """Alle Fassungen fuer *diese* Gruppe. Returns: ``[(schluessel, text), ...]``.

    Die Mehrzahl-Fassung von ``text_fuer_gruppe`` und aus demselben Grund die
    **eine** Stelle, an der entschieden wird, welche Vorlagen eine Gruppe
    bekommt: Kommandozeile und Arbeitsseite rufen beide hier an.

    Hat die Kampagne eine eigene Vorlage, gibt es genau **eine** Fassung und
    keine fuenf - sie ist ein Text, den ein Mensch hingeschrieben hat, und
    fuenf Kopien davon waeren fuenfmal dasselbe. Das ist zugleich die
    sichtbare Kehrseite der eigenen Vorlage: Sie kostet die Abwechslung, fuer
    die es den Vorrat gibt. Leer ist deshalb der Normalfall.

    ``hoechstens`` schneidet ab, wenn im Topf mehr steht, als die Oberflaeche
    zeigen kann (``models.MAX_VORSCHLAEGE``). Null heisst: alles nehmen.

    Ein Platzhalter, den niemand ersetzt, wirft weiterhin - fuer *diese*
    Gruppe entsteht dann kein Text, und der Grund steht im Bericht. Das ist
    dieselbe Regel wie bei einer einzelnen Vorlage: Ein Beitrag mit ``{beruf}``
    mittendrin waere schlimmer als keiner.
    """
    daten = personalisierung(group, campaign, config)

    eigene = campaign.message_template.strip()
    if eigene and texttyp is Texttyp.POST:
        return [(KAMPAGNENVORLAGE, fuelle(eigene, daten))]

    sprache = sprache_der_kampagne(campaign, config)
    gedreht = reihenfolge_fuer(
        config,
        sprache=sprache,
        topf=daten.topf,
        group_id=group.group_id,
        texttyp=texttyp,
    )
    if hoechstens > 0:
        if hoechstens > len(gedreht) and gedreht:
            # Mehr Fassungen verlangt als Texte im Topf: Der Topf wird
            # gedreht. Fassung 6 traegt wieder Vorlage 1, Fassung 7 wieder
            # Vorlage 2 - genau die Rotation, die ``lauf.vorlage_zu_nummer``
            # beschreibt. Zwei gleiche Texte gehen dabei nie unter denselben
            # Beitrag: ``bisherige_post_urls`` gibt jedem Kommentar einen
            # anderen.
            gedreht = [gedreht[i % len(gedreht)] for i in range(hoechstens)]
        else:
            gedreht = gedreht[:hoechstens]
    return [
        (
            f"{sprache}/{texttyp.value}/{daten.topf}/{vorlage.kennung}",
            fuelle(vorlage.text, daten),
        )
        for vorlage in gedreht
    ]


def pruefe(config: AppConfig) -> list[str]:
    """Was an ``textvorlagen.yaml`` nicht stimmt - fuer ``config-check``.

    Geprueft wird, was sich still auswirkt und erst im veroeffentlichten
    Beitrag auffiele: eine Vorlage ohne ``{link}`` (die Gruppe bekaeme nie
    einen Klick gutgeschrieben), ein ``{stadt}`` im Topf ``ohne_stadt`` (der
    Platzhalter bliebe unersetzt), ein erfundener Platzhalter, eine doppelt
    vergebene Kennung (der Schluessel waere mehrdeutig), eine Zielgruppe
    ohne Beschriftung fuer die Sprache, in der sie angesprochen wird, und die
    drei Rueckfaelle (``ziel_allgemein``, ``gegenstand_allgemein``,
    ``monate``): Fehlt einer, wird der Platzhalter durch nichts ersetzt - ein
    Loch mitten im Satz sieht man dem Beitrag nicht an.
    """
    fehler: list[str] = []
    alle: dict[str, Any] = config.textvorlagen.get("vorlagen") or {}

    if not alle:
        return ["config/textvorlagen.yaml fehlt oder enthaelt keine Vorlagen."]

    for sprache in alle:
        for texttyp in Texttyp:
            for topf in (MIT_STADT, OHNE_STADT):
                try:
                    liste = _topf(config, str(sprache), texttyp, topf)
                except VorlageFehlt as exc:
                    fehler.append(str(exc))
                    continue

                gesehen: set[str] = set()
                for vorlage in liste:
                    ort = f"{sprache}/{texttyp.value}/{topf}/{vorlage.kennung}"
                    if vorlage.kennung in gesehen:
                        fehler.append(f"{ort}: die Kennung kommt zweimal vor.")
                    gesehen.add(vorlage.kennung)
                    # Der Beitrag **muss** seinen Link tragen, der Kommentar
                    # **darf**. Beides ist eine Entscheidung des Nutzers und
                    # keine technische Grenze: Manche Gruppen lehnen einen
                    # Kommentar mit Link ab ("Link in Kommentar", 11.09.2026),
                    # andere nicht - und ohne Link im Kommentar zaehlt allein
                    # der eigene Beitrag daneben.
                    if texttyp is not Texttyp.KOMMENTAR and PLATZHALTER_LINK not in vorlage.text:
                        fehler.append(f"{ort}: enthaelt kein {PLATZHALTER_LINK}.")
                    try:
                        pruefe_platzhalter(vorlage.text, texttyp=texttyp)
                    except UngueltigerText as exc:
                        fehler.append(f"{ort}: {exc}")
                    if topf == OHNE_STADT and "{stadt}" in vorlage.text:
                        fehler.append(
                            f"{ort}: enthaelt {{stadt}}, steht aber in '{OHNE_STADT}'."
                        )
                    for name in unbekannte_platzhalter(vorlage.text):
                        fehler.append(f"{ort}: unbekannter Platzhalter {{{name}}}.")

        if not _allgemeine_anrede(config, str(sprache)):
            fehler.append(f"anrede_allgemein fehlt fuer '{sprache}'.")

        # Die beiden Rueckfaelle und die Monatsliste. Fehlt einer, wird der
        # Platzhalter durch nichts ersetzt - im Beitrag steht dann ein Loch
        # mitten im Satz, und das faellt erst in der Gruppe auf.
        if not _aus_textvorlagen(config, "ziel_allgemein", str(sprache)):
            fehler.append(f"ziel_allgemein fehlt fuer '{sprache}' - {{ziel}} bliebe leer.")
        if not _aus_textvorlagen(config, "gegenstand_allgemein", str(sprache)):
            fehler.append(
                f"gegenstand_allgemein fehlt fuer '{sprache}' - "
                f"{{gegenstand}} bliebe leer."
            )
        monate = (config.textvorlagen.get("monate") or {}).get(str(sprache)) or []
        if len(monate) != 12:
            fehler.append(
                f"monate['{sprache}'] hat {len(monate)} Eintraege statt 12 - "
                f"{{datum}} bliebe leer."
            )

        # Die Anrede ist eine Angabe ueber die Zielgruppe, kein Suchbegriff.
        # Fehlt sie, faellt in der Vorlage entweder das deutsche Label in
        # einen arabischen Satz oder "Syrer in Deutschland" in "... in Bonn".
        for kennung, audience in config.audiences.items():
            if not audience.anrede(str(sprache)).strip():
                fehler.append(
                    f"audiences.yaml: '{kennung}' hat keine Anrede fuer '{sprache}'."
                )

    fehler.extend(pruefe_anlaesse(config))
    return fehler


def pruefe_anlaesse(config: AppConfig) -> list[str]:
    """Was am Anlassvorrat nicht stimmt - fuer ``config-check`` (13.09.2026).

    Drei Dinge, und jedes wuerde erst in einer fremden Gruppe auffallen:

    * **Ein Anlass, den es nicht gibt.** ``inhalt.Anlass`` ist die Liste der
      Faelle, die erkannt werden koennen; ein Schluessel daneben ist ein
      Tippfehler, und der Text daneben wird nie verwendet. Dieselbe Regel wie
      bei einem Gewicht fuer einen Bestandteil, den es nicht gibt.
    * **Ein Link in der Vorlage.** Der Vorrat traegt keinen - ``anlasstext``
      haengt ihn an, wo die Gruppe ihn erlaubt. Eine Vorlage mit
      ausgeschriebener Adresse ergaebe einen Kommentar, der richtig aussieht
      und dessen Gruppe nie einen Klick gutgeschrieben bekommt.
    * **Ein erfundener Platzhalter.** Er bliebe in geschweiften Klammern im
      Kommentar stehen.

    Ein **fehlender** Vorrat ist kein Fehler: Eine Sprache ohne Anlasstexte
    faellt auf die Fassungen der Kampagne zurueck. Gemeldet wird es trotzdem
    als Hinweis - wer ``anlass_pflicht`` eingeschaltet hat und keinen Vorrat
    pflegt, bekommt sonst still gar keine Kommentare mehr.
    """
    from fbgroups.marketing.inhalt import Anlass

    fehler: list[str] = []
    zweig: dict[str, Any] = config.textvorlagen.get(ANLASSZWEIG) or {}
    if not zweig:
        return [
            f"config/textvorlagen.yaml hat keinen Block '{ANLASSZWEIG}' - ohne ihn "
            "kommentiert die Automatik nichts, solange marketing.anlass_pflicht an ist."
        ]

    bekannt = {a.value for a in Anlass} - {Anlass.KEINER.value}
    for sprache, anlaesse in zweig.items():
        for anlass in anlaesse or {}:
            ort = f"{ANLASSZWEIG}/{sprache}/{anlass}"
            if anlass not in bekannt:
                fehler.append(
                    f"{ort}: unbekannter Anlass. Bekannt sind: "
                    f"{', '.join(sorted(bekannt))}."
                )
                continue
            vorrat = anlassvorrat(config, str(sprache), str(anlass))
            if not vorrat:
                fehler.append(f"{ort}: kein einziger Text.")
            gesehen: set[str] = set()
            for vorlage in vorrat:
                stelle = f"{ort}/{vorlage.kennung}"
                if vorlage.kennung in gesehen:
                    fehler.append(f"{stelle}: die Kennung kommt zweimal vor.")
                gesehen.add(vorlage.kennung)
                if PLATZHALTER_LINK in vorlage.text:
                    fehler.append(
                        f"{stelle}: enthaelt {PLATZHALTER_LINK}. Der Vorrat traegt "
                        f"keinen Link - anlasstext haengt ihn an, wo die Gruppe ihn "
                        f"erlaubt."
                    )
                try:
                    pruefe_platzhalter(vorlage.text, texttyp=Texttyp.KOMMENTAR)
                except UngueltigerText as exc:
                    fehler.append(f"{stelle}: {exc}")
                for name in unbekannte_platzhalter(vorlage.text):
                    fehler.append(f"{stelle}: unbekannter Platzhalter {{{name}}}.")
    return fehler
