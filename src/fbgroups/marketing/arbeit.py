"""Ein Arbeitsschritt an **einer Gruppe** - nicht an einem Beitrag.

Bis hierher war die Einheit der Arbeitsseite der Beitrag: ``hole_auftrag``
nahm den naechsten aus der Warteschlange, markierte ihn als ``processing`` und
gab ihn heraus; wer den Ausgang meldete, bekam den naechsten. Das war ein
Ablauf mit genau einer Richtung - veroeffentlichen, weiter -, und er hat drei
Dinge unterstellt, die nicht stimmen:

* dass es je Gruppe **einen** Text gibt. Tatsaechlich haelt
  ``config/textvorlagen.yaml`` fuenf Fassungen je Sprache, Zweck und Topf, und
  welche davon zu einer Gruppe passt, ist die eigentliche Entscheidung.
* dass Beitrag und Kommentar zusammen erledigt werden. Sie gehen in
  Wirklichkeit getrennt hinaus, oft an verschiedenen Tagen.
* dass nach einem veroeffentlichten Beitrag die naechste Gruppe drankommt.
  Wer das nicht will, hatte keinen Weg: Der einzige Knopf, der weiterfuehrte,
  war zugleich der, der einen Ausgang meldete.

Die Einheit ist deshalb jetzt die **Gruppe**: ``hole_gruppenarbeit`` liefert
sie mit allen ihren Fassungen - fuenf Beitraege, fuenf Kommentare -, und
``melde_vorschlag`` traegt den Ausgang **einer** Fassung ein. Nichts daran
blaettert weiter; wohin es als naechstes geht, entscheidet ein Mensch.

**Was bleibt, sind die Regeln.** Pausiert und gestoppt halten das
Veroeffentlichen an, und jeder Ausgang eines Beitrags schreibt seine Zeile in
``post_versuche``.

**Kein Tageslimit mehr** (entfernt am 27.08.2026 auf ausdruecklichen Wunsch).
Es war der letzte Rest des Arbeiters: zwanzig Beitraege am Tag ueber alle
Kampagnen, gezaehlt in ``store.versuche_heute``. Gegen eine Schleife, die
selbst abschickt, war es eine Bremse; gegen einen Menschen, der jeden Beitrag
von Hand einfuegt, war es nur eine Sperre, die ausgerechnet den traf, der
gerade arbeitet - wer dreissig Gruppen vor sich hatte, stand vor der
einundzwanzigsten. Der Takt entsteht ohnehin von selbst, weil ein Mensch
davorsitzt.

**Der Versuch entsteht jetzt beim Melden, nicht beim Ansehen.** Vorher fing
das blosse Oeffnen der Seite einen Versuch an - das musste so sein, weil sie
einen Beitrag aus der Warteschlange nahm und ihn sonst bei einem geschlossenen
Reiter verloren haette. Eine Seite, die nichts herausnimmt, kann auch nichts
verlieren: Ansehen, Blaettern und Schreiben zaehlen nirgends mit, und erst
"veroeffentlicht" oder "fehlgeschlagen" schreibt eine Zeile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from fbgroups.config import AppConfig
from fbgroups.marketing.beitrag import mit_link
from fbgroups.marketing.models import (
    MAX_VORSCHLAEGE,
    Campaign,
    CampaignGroup,
    JobStatus,
    PostStatus,
    PostVersuch,
    QueueZustand,
    Texttyp,
    Textvorschlag,
    VorschlagStatus,
)
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group


@dataclass(frozen=True)
class Ergebnis:
    """Was aus **einer** Fassung geworden ist.

    Frueher war das der Ausgang eines ganzen Beitrags und kannte deshalb
    ``uebersprungen`` (ein Urteil ueber die Gruppe) und ``abbrechen``
    (Schluss fuer heute). Beides gehoert nicht zu einer Fassung: "Passt
    nicht" ist eine Aussage ueber die Gruppe und wird in der Uebersicht
    getroffen, und wer aufhoert, schliesst die Seite.

    Uebrig bleiben die zwei Ausgaenge, die ein Mensch nach dem Absenden
    wirklich melden kann: Es steht drin, oder es ging nicht.
    """

    erfolg: bool
    fehler: str = ""
    post_url: str = ""


class Grund:
    """Warum gerade nichts hinausgehen darf. Wird dem Menschen angezeigt."""

    KEINE_GRUPPEN = "Keine Gruppen zugeordnet"
    PAUSIERT = "Kampagne pausiert"
    GESTOPPT = "Kampagne gestoppt"


@dataclass(frozen=True)
class Sperre:
    """Es geht gerade nichts hinaus - und warum nicht.

    Eine Sperre haelt **nur das Veroeffentlichen** an, nicht die Seite. Lesen,
    Blaettern, Schreiben und Speichern gehen weiter: Eine pausierte Kampagne
    ist ein Grund, heute nichts abzusenden, und kein Grund, die Texte von
    morgen nicht vorzubereiten.

    Nur noch ein Feld. Solange es das Tageslimit gab, trug sie die Zahlen mit -
    "Tageslimit erreicht" ohne "20 von 20" waere eine Auskunft gewesen, mit der
    niemand etwas anfangen kann. Pausiert und gestoppt brauchen keine Zahl.
    """

    grund: str


@dataclass(frozen=True)
class Fassung:
    """Eine Fassung, wie die Seite sie zeigt: gespeichert und fertig gelesen.

    Zwei Texte in einem Objekt, und der Unterschied ist der wichtigste des
    Moduls: ``vorschlag.text`` traegt ``{link}`` und ist das, was bearbeitet
    und gespeichert wird; ``angezeigt`` traegt den eingesetzten Tracking-Link
    und ist das, was kopiert und in die Gruppe eingefuegt wird.

    Der Mensch bekommt ``{link}`` nur im Textfeld zu sehen - dort **muss** er
    stehen, sonst bekaeme die Gruppe nie einen Klick gutgeschrieben. In die
    Zwischenablage geht ausschliesslich ``angezeigt``.
    """

    vorschlag: Textvorschlag
    angezeigt: str

    @property
    def nummer(self) -> int:
        return self.vorschlag.nummer

    @property
    def status(self) -> VorschlagStatus:
        return self.vorschlag.status


@dataclass(frozen=True)
class Gruppenarbeit:
    """Eine Gruppe mit allem, was an ihr zu tun ist - die Einheit der Seite.

    Beitraege und Kommentare stehen als zwei **unabhaengige** Listen darin.
    Sie teilen sich die Gruppe und sonst nichts: eigene Nummerierung, eigene
    Staende, eigene Ausgaenge. Genau das ist der Unterschied zur frueheren
    Fassung, in der der Kommentar ein Anhaengsel des Beitrags war.
    """

    link: CampaignGroup
    gruppe: Group | None
    posts: list[Fassung] = field(default_factory=list)
    kommentare: list[Fassung] = field(default_factory=list)
    # Wo diese Gruppe in der Liste steht - fuer "Gruppe 12 von 310".
    nummer: int = 1
    gesamt: int = 1
    zustand: QueueZustand = QueueZustand.LAUFEND

    @property
    def name(self) -> str:
        return (self.gruppe.name if self.gruppe else "") or self.link.group_id

    @property
    def url(self) -> str:
        return self.gruppe.url_canonical if self.gruppe else ""

    def sperre(self) -> Sperre | None:
        """Darf gerade etwas hinausgehen? ``None`` heisst ja.

        Seit das Tageslimit weg ist, bleibt nur der Zustand der Kampagne -
        also ein Entschluss eines Menschen und keine gezaehlte Grenze.
        """
        if self.zustand is QueueZustand.PAUSIERT:
            return Sperre(Grund.PAUSIERT)
        if self.zustand is QueueZustand.GESTOPPT:
            return Sperre(Grund.GESTOPPT)
        return None


@dataclass(frozen=True)
class Gruppeneintrag:
    """Eine Zeile der Gruppenauswahl: Name, Platz und ob dort schon etwas steht.

    Absichtlich schmal. Die Auswahlliste traegt bei 310 Gruppen 310 Eintraege;
    jedes Feld darin steht dreihundertmal auf der Seite. Was ein Mensch beim
    Suchen braucht, ist der Name - und die Auskunft, ob er hier schon war.
    """

    nummer: int
    group_id: str
    name: str
    veroeffentlicht: bool = False
    fehlgeschlagen: bool = False


def auswahlliste(
    store: MarketingStore,
    campaign_id: str,
    reihe: list[CampaignGroup],
    gruppen: dict[str, Group],
) -> list[Gruppeneintrag]:
    """Die Gruppen als Auswahl - in derselben Reihenfolge wie die Arbeit.

    ``reihe`` kommt herein, statt hier noch einmal berechnet zu werden: Die
    Nummer in der Auswahl **muss** dieselbe sein wie die in der Adresse, sonst
    fuehrt ein Eintrag auf eine andere Gruppe als die, die daneben steht. Zwei
    Berechnungen derselben Rangfolge koennten auseinanderlaufen - dieselbe
    Ueberlegung wie bei ``search.build_plan``.

    Die Staende kommen mit **einer** Abfrage (``staende_je_gruppe``). Je Gruppe
    einzeln nachzufragen waeren dreihundert Abfragen fuer eine Auswahlliste.
    """
    staende = store.staende_je_gruppe(campaign_id, Texttyp.POST)
    eintraege: list[Gruppeneintrag] = []
    for nummer, link in enumerate(reihe, start=1):
        gefunden = staende.get(link.group_id, set())
        gruppe = gruppen.get(link.group_id)
        eintraege.append(
            Gruppeneintrag(
                nummer=nummer,
                group_id=link.group_id,
                name=(gruppe.name if gruppe else "") or link.group_id,
                veroeffentlicht=VorschlagStatus.VEROEFFENTLICHT.value in gefunden,
                fehlgeschlagen=VorschlagStatus.FEHLGESCHLAGEN.value in gefunden,
            )
        )
    return eintraege


def arbeitsreihenfolge(
    store: MarketingStore, campaign_id: str, gruppen: dict[str, Group]
) -> list[CampaignGroup]:
    """Die Gruppen dieser Kampagne, die besten zuerst.

    Dieselbe Rangfolge wie ueberall sonst (``scoring.sort_by_rank``): Bei 310
    Gruppen bringt niemand die Liste an einem Tag zu Ende, und wer abbricht,
    soll die wertvollsten Beitraege geschrieben haben und nicht die
    alphabetisch ersten.

    Grundlage ist die **Zuordnung**, nicht die Warteschlange. Das ist der
    sichtbarste Teil der Umstellung: Eine Gruppe muss nicht mehr freigegeben
    und eingereiht sein, um bearbeitet werden zu koennen - sie muss der
    Kampagne zugeordnet sein. ``job_status`` bleibt trotzdem gepflegt; er
    beantwortet weiterhin die Frage der Kommandozeile, wie weit ein Text ist.

    Ausgeschlossene Gruppen (``bearbeiten = 0``) stehen nicht darin - sie sind
    bereits als "daran arbeiten wir nicht" beurteilt. Ihr Tracking-Code bleibt
    davon unberuehrt gueltig.

    Eine Gruppe ohne Datensatz im Bestand wandert ans **Ende** statt zu
    verschwinden: Ein Beitrag, der nicht in der Liste steht, wird nie
    geschrieben.
    """
    from fbgroups.scoring import sort_by_rank

    links = store.links_zum_bearbeiten(campaign_id)
    bekannt = [link for link in links if link.group_id in gruppen]
    unbekannt = [link for link in links if link.group_id not in gruppen]

    geordnet = sort_by_rank([gruppen[link.group_id] for link in bekannt])
    rang = {gruppe.group_id: platz for platz, gruppe in enumerate(geordnet)}
    bekannt.sort(key=lambda link: rang.get(link.group_id, len(rang)))
    return [*bekannt, *unbekannt]


def heutige_reihe(
    store: MarketingStore,
    reihe: list[CampaignGroup],
    config: AppConfig,
    *,
    jetzt: datetime | None = None,
) -> tuple[list[CampaignGroup], object | None]:
    """Die Reihe auf die heutige Portion gekuerzt. ``(reihe, portion | None)``.

    Ist der Kaltmodus aus, kommt die Reihe unveraendert zurueck und ``None``
    dazu - dann verhaelt sich alles wie vorher.

    Diese eine Stelle kuerzt, und alle Aufrufer nehmen sie: Die Kommandozeile
    und die Weboberflaeche muessen dieselbe Portion sehen, sonst zeigte die
    eine Gruppen, die die andere fuer morgen haelt. Derselbe Gedanke wie bei
    ``auswahlliste``, die die Reihe uebergeben bekommt statt sie neu zu rechnen.
    """
    from fbgroups.marketing import kaltmodus

    aktiv, pro_tag, _abstand = kaltmodus.einstellungen(config)
    if not aktiv:
        return reihe, None
    jetzt = jetzt or datetime.now(UTC)
    portion = kaltmodus.tagesportion(
        reihe,
        erledigt_heute=store.versuche_heute(jetzt.date().isoformat()),
        grenze=pro_tag,
    )
    return portion.gruppen, portion


def stelle_texte_bereit(
    store: MarketingStore,
    campaign: Campaign,
    gruppe: Group,
    config: AppConfig,
    *,
    ueberschreiben: bool = False,
) -> dict[Texttyp, int]:
    """Fuellt die Fassungen **einer** Gruppe. Returns: wie viele je Zweck neu sind.

    Die **eine** Stelle, an der die Texte einer Gruppe entstehen -
    Kommandozeile, Werkbank und die Arbeitsseite rufen alle hier an. Zwei
    Fassungen dieser Entscheidung koennten auseinanderlaufen, und der
    Unterschied fiele erst in einem veroeffentlichten Beitrag auf; es ist
    dieselbe Ueberlegung wie bei ``vorlagen.text_fuer_gruppe``.

    Je Gruppe und Zweck entsteht der **ganze Topf** in der Reihenfolge aus
    ``vorlagen.reihenfolge_fuer``: Platz 1 traegt die Fassung, die diese
    Gruppe auch vor den Vorschlaegen bekommen haette, dahinter die uebrigen.
    Damit ist die Nummer eines Vorschlags stabil - dieselbe Gruppe findet
    morgen unter "3" denselben Text wie heute.

    **Vorhandenes wird nicht ueberschrieben.** Weder ein von Hand
    geschriebener Text noch der Stand einer Fassung; ``ueberschreiben`` ist
    der ausdrueckliche Weg dafuer und gehoert in den Tag, an dem die Vorlagen
    geaendert wurden, nicht in den taeglichen Ablauf.

    **Platz 1 wird zusaetzlich ans Paar geschrieben.** ``campaign_groups``
    bleibt das Schaufenster fuer alles, was es vor den Vorschlaegen schon gab
    (``campaign message``, ``queue``, ``beitragstext``, die Uebersicht); ohne
    diesen Schritt stuende dort nichts, waehrend die Arbeitsseite fuenf Texte
    zeigt. Zustaendig dafuer ist ``set_generierten_text`` mit seiner
    unveraenderten Regel: Der erzeugte Text wird immer aufgefrischt, der
    laufende nur, wenn dort noch nichts steht.

    **Beide Zwecke entstehen immer** (seit dem 28.08.2026). Vorher hing der
    Kommentar an einem Haken der Kampagne; der stand in jeder Zeile und musste
    in keiner beantwortet werden. Wer in einer Gruppe postet, kommentiert dort
    auch - und ein Kommentar zu viel kostet einen Blick, ein fehlender einen
    Handgriff.
    """
    from fbgroups.marketing import vorlagen

    zwecke = list(Texttyp)

    entstanden: dict[Texttyp, int] = {}
    for texttyp in zwecke:
        fassungen = vorlagen.alle_texte_fuer_gruppe(
            gruppe, campaign, config, texttyp=texttyp, hoechstens=MAX_VORSCHLAEGE
        )
        neu_entstanden = 0
        for nummer, (schluessel, text) in enumerate(fassungen, start=1):
            vorher = store.vorschlag(
                campaign.campaign_id, gruppe.group_id, texttyp, nummer
            )
            store.setze_erzeugten_vorschlag(
                campaign.campaign_id,
                gruppe.group_id,
                texttyp,
                nummer,
                text=text,
                vorlage_key=schluessel,
                ueberschreiben=ueberschreiben,
            )
            if vorher is None or not vorher.text.strip() or ueberschreiben:
                neu_entstanden += 1

        if fassungen:
            link = store.link_for(campaign.campaign_id, gruppe.group_id)
            schluessel, text = fassungen[0]
            store.set_generierten_text(
                campaign.campaign_id,
                gruppe.group_id,
                text=text,
                vorlage_key=schluessel,
                uebernehmen=(
                    ueberschreiben
                    or link is None
                    or not link.text_fuer(texttyp).strip()
                ),
                texttyp=texttyp,
            )
        entstanden[texttyp] = neu_entstanden
    return entstanden


def hole_gruppenarbeit(
    store: MarketingStore,
    campaign: Campaign,
    gruppen: dict[str, Group],
    config: AppConfig,
    *,
    nummer: int = 1,
    group_id: str = "",
    reihe: list[CampaignGroup] | None = None,
) -> Gruppenarbeit | None:
    """Eine Gruppe mit allen ihren Fassungen. ``None``, wenn es sie nicht gibt.

    **Liest nur.** Kein Stand wird gesetzt und keine Protokollzeile
    geschrieben. Das ist der Unterschied zum frueheren ``hole_auftrag``, und er
    ist keine Feinheit: Solange das Ansehen einer Gruppe einen Versuch anfing,
    war Blaettern eine Handlung mit Folgen.

    ``group_id`` geht vor ``nummer``: Wer eine bestimmte Gruppe meint, meint
    sie auch dann noch, wenn sich die Rangfolge zwischendurch geaendert hat.

    ``config`` geht bis in ``beitrag.mit_link`` durch: Dort wird ``{datum}``
    aufgeloest, und dafuer braucht es die Sprache der Kampagne und die
    Monatsnamen aus ``textvorlagen.yaml``.

    ``reihe`` darf mitgegeben werden, wenn der Aufrufer sie ohnehin schon hat
    (die Auswahlliste braucht sie auch). Dieselbe Liste fuer beide ist keine
    Sparsamkeit, sondern eine Zusicherung: Die Nummer auf dem Bildschirm und
    die Nummer in der Adresse meinen dann dieselbe Gruppe.
    """
    if reihe is None:
        reihe = arbeitsreihenfolge(store, campaign.campaign_id, gruppen)
    if not reihe:
        return None

    if group_id:
        treffer = [i for i, link in enumerate(reihe, start=1) if link.group_id == group_id]
        if not treffer:
            return None
        nummer = treffer[0]
    if not 1 <= nummer <= len(reihe):
        return None

    link = reihe[nummer - 1]
    return Gruppenarbeit(
        link=link,
        gruppe=gruppen.get(link.group_id),
        posts=_fassungen(store, campaign, link, Texttyp.POST, config),
        kommentare=_fassungen(store, campaign, link, Texttyp.KOMMENTAR, config),
        nummer=nummer,
        gesamt=len(reihe),
        zustand=store.queue_zustand(campaign.campaign_id),
    )


def melde_vorschlag(
    store: MarketingStore,
    campaign: Campaign,
    link: CampaignGroup,
    texttyp: Texttyp,
    nummer: int,
    ergebnis: Ergebnis,
    *,
    ausgeloest_von: str = "arbeitsseite",
    sitzung: str = "",
    jetzt: datetime | None = None,
) -> Textvorschlag | Sperre:
    """Traegt den Ausgang **einer** Fassung ein. Returns: sie selbst oder die Sperre.

    Die einzige Stelle, an der eine Fassung ihren Ausgang bekommt. Zwei
    Fassungen dieser Regel waeren zwei Zaehlweisen fuer dieselben Beitraege.

    **Es wird nichts weitergeschaltet.** Der Rueckgabewert ist die gemeldete
    Fassung, nicht die naechste: Wer einen Beitrag veroeffentlicht hat,
    entscheidet selbst, was als naechstes kommt.

    **Pausiert und gestoppt halten neue Beitraege an**: Sie sind ein
    Entschluss, in dieser Kampagne gerade nichts hinauszugeben. Ein bereits
    erfolgreich veroeffentlichter Beitrag (erfolg=True) wird aber dennoch 
    gespeichert, damit die Buchfuehrung mit der Wirklichkeit auf Facebook 
    uebereinstimmt.
    """
    jetzt = jetzt or datetime.now(UTC)
    campaign_id = campaign.campaign_id

    zustand = store.queue_zustand(campaign_id)
    if zustand is QueueZustand.PAUSIERT and not ergebnis.erfolg:
        return Sperre(Grund.PAUSIERT)
    if zustand is QueueZustand.GESTOPPT and not ergebnis.erfolg:
        return Sperre(Grund.GESTOPPT)

    stand = (
        VorschlagStatus.VEROEFFENTLICHT if ergebnis.erfolg
        else VorschlagStatus.FEHLGESCHLAGEN
    )
    gemeldet = store.setze_vorschlag_stand(
        campaign_id, link.group_id, texttyp, nummer, stand,
        fehler=ergebnis.fehler or ("" if ergebnis.erfolg else "ohne Angabe"),
    )
    if gemeldet is None:
        return Sperre(Grund.KEINE_GRUPPEN)

    # Die Protokollzeile entsteht **nach** der Handlung und in einem Zug:
    # Es gibt keinen Zeitraum mehr, in dem ein Versuch offen herumliegt -
    # der Mensch hat den Beitrag bereits abgesetzt, wenn er hier meldet.
    versuch_id = store.beginne_versuch(
        PostVersuch(
            campaign_id=campaign_id,
            group_id=link.group_id,
            tracking_code=link.tracking_code,
            job_status=JobStatus.PROCESSING,
            ausgeloest_von=ausgeloest_von,
            browser_session=sitzung,
            begonnen_am=jetzt,
        )
    )
    store.beende_versuch(
        versuch_id,
        erfolg=ergebnis.erfolg,
        post_url=ergebnis.post_url,
        fehler="" if ergebnis.erfolg else (ergebnis.fehler or "ohne Angabe"),
    )
    
    if texttyp is Texttyp.POST:
        _gruppenstand_nachziehen(store, campaign_id, link.group_id)

    return gemeldet


def _gruppenstand_nachziehen(
    store: MarketingStore, campaign_id: str, group_id: str
) -> None:
    """Leitet den Stand des **Paares** aus den Staenden seiner Fassungen ab.

    ``post_status`` und ``job_status`` an ``campaign_groups`` bleiben die
    Wahrheit fuer alles, was es vor den Vorschlaegen schon gab: ``campaign
    queue``, ``retry``, ``fortschritt``, ``post_counts`` und die Uebersicht.
    Sie wuerden stehenbleiben, wenn nur noch die Fassungen gepflegt wuerden -
    und die Uebersicht meldete 310 offene Beitraege, waehrend die Haelfte
    laengst draussen steht.

    Veroeffentlicht gewinnt. Genau der Fall aus der Anforderung - Fassung 1
    steht in der Gruppe, Fassung 2 ist beim Absenden gescheitert - darf das
    Paar nicht auf "fehlgeschlagen" ziehen: Die Gruppe **hat** ihren Beitrag,
    und ``campaign retry`` holte sie sonst zurueck in eine Liste, auf der sie
    nichts mehr zu suchen hat.

    Geschrieben wird ueber ``set_post_status`` und nicht ueber
    ``set_job_status``: Der eine traegt ein Ergebnis ein und pflegt beide
    Achsen, der andere prueft Uebergaenge, die fuer diesen Weg nicht gelten -
    eine Fassung darf gemeldet werden, ohne dass die Gruppe je durch
    "freigegeben" und "eingereiht" gegangen ist.
    """
    staende = {
        v.status for v in store.vorschlaege(campaign_id, group_id, Texttyp.POST)
    }
    if VorschlagStatus.VEROEFFENTLICHT in staende:
        store.set_post_status(campaign_id, group_id, PostStatus.VEROEFFENTLICHT)
    elif VorschlagStatus.FEHLGESCHLAGEN in staende:
        fehler = next(
            (
                v.fehler
                for v in store.vorschlaege(campaign_id, group_id, Texttyp.POST)
                if v.status is VorschlagStatus.FEHLGESCHLAGEN and v.fehler
            ),
            "",
        )
        store.set_post_status(
            campaign_id, group_id, PostStatus.FEHLGESCHLAGEN, fehler
        )


def _fassungen(
    store: MarketingStore,
    campaign: Campaign,
    link: CampaignGroup,
    texttyp: Texttyp,
    config: AppConfig,
) -> list[Fassung]:
    """Die gespeicherten Fassungen eines Zwecks, fertig gelesen.

    ``mit_link`` ist dieselbe Ersetzung, die ``beitragstext`` benutzt - es
    gibt weiterhin genau eine Stelle, an der ein Tracking-Code in einen Text
    kommt.
    """
    return [
        Fassung(
            vorschlag=vorschlag,
            angezeigt=mit_link(campaign, link, vorschlag.text, config=config),
        )
        for vorschlag in store.vorschlaege(campaign.campaign_id, link.group_id, texttyp)
    ]
