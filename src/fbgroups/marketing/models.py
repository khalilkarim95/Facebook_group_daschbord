"""Datenmodell der Marketing-Erweiterung.

Bewusst dieselbe Grenze wie im Bestand: Es gibt keine Felder fuer Mitglieder,
Admins, Profil-URLs oder Nachrichteninhalte. Gespeichert wird der eigene
Arbeitsstand - wen haben *wir* angesprochen, wer hat *uns* eine Erlaubnis
gegeben, welcher Link gehoert zu welcher Gruppe.

Das Modul veroeffentlicht nichts und verschickt nichts. Es verwaltet
ausschliesslich die Vorbereitung; jeder Beitrag und jede Anfrage wird von Hand
geschrieben und von Hand gesendet. ``PostStatus`` haelt fest, was dabei
herauskam - es ist ein Protokoll ueber die eigene Arbeit, keine Steuerung
eines Automaten.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MarketingStatus(StrEnum):
    """Wo im Kooperationsablauf eine Gruppe steht.

    Die Reihenfolge folgt dem tatsaechlichen Ablauf bei Facebook: Erst muss man
    **aufgenommen** sein, danach kann man die Gruppenleitung ueberhaupt
    ansprechen. Frueher sprang das Modell von ``not_contacted`` direkt auf
    ``contacted`` - fuer die Beitrittsanfrage gab es keinen Zustand, obwohl sie
    der haeufigste Schritt ist: Sie betrifft jede Gruppe im Bestand, das
    Ansprechen der Leitung nur eine Handvoll.

    ``rejected`` bleibt der Kooperation vorbehalten (die Leitung will keine
    Werbung); eine abgelehnte **Aufnahme** ist ``join_rejected`` - dort ist man
    nicht einmal Mitglied und kann es spaeter erneut versuchen.
    """

    NOT_CONTACTED = "not_contacted"
    JOIN_REQUESTED = "beitritt_angefragt"   # Beitrittsanfrage gestellt
    MEMBER = "mitglied"                     # aufgenommen, Posten moeglich
    JOIN_REJECTED = "beitritt_abgelehnt"    # Aufnahme abgelehnt
    CONTACTED = "contacted"                 # Leitung wegen Werbung angesprochen
    INTERESTED = "interested"
    APPROVED = "approved"
    REJECTED = "rejected"
    ACTIVE = "active"
    INACTIVE = "inactive"


# Reihenfolge des Ablaufs - allein fuer die Frage "ist die Gruppe schon
# weiter?". Ein Sammelbefehl darf einen erreichten Stand nicht zurueckdrehen:
# Wer bei einer Gruppe bereits Mitglied ist oder die Erlaubnis hat, verliert
# das nicht dadurch, dass eine Liste erneut abgearbeitet wird. Die abgelehnten
# und beendeten Zustaende stehen bewusst nicht darin - sie wieder aufzunehmen
# ist eine Entscheidung von Hand, kein Nebeneffekt.
MARKETING_FORTSCHRITT: tuple[MarketingStatus, ...] = (
    MarketingStatus.NOT_CONTACTED,
    MarketingStatus.JOIN_REQUESTED,
    MarketingStatus.MEMBER,
    MarketingStatus.CONTACTED,
    MarketingStatus.INTERESTED,
    MarketingStatus.APPROVED,
    MarketingStatus.ACTIVE,
)


class ContactStatus(StrEnum):
    """Stand der Ansprache - unabhaengig vom Ergebnis.

    ``no_channel`` haelt den haeufigen Fall fest, dass eine oeffentliche Gruppe
    keine offene Kontaktmoeglichkeit bietet. Das ist eine Feststellung ueber
    die Gruppe, keine gespeicherte Kontaktangabe.
    """

    NONE = "none"
    ATTEMPTED = "attempted"
    REACHED = "reached"
    REPLIED = "replied"
    NO_CHANNEL = "no_channel"


class PermissionStatus(StrEnum):
    """Erlaubnis der Gruppenleitung, dort zu werben."""

    UNKNOWN = "unknown"
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"


class CampaignParticipation(StrEnum):
    """Beteiligung der Gruppe an laufenden Kampagnen."""

    NONE = "none"
    PLANNED = "planned"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class GroupMarketing(BaseModel):
    """Arbeitsstand zu einer bereits gefundenen Gruppe.

    Liegt in einer eigenen Tabelle, nicht in ``groups``: Ein Suchlauf schreibt
    jeden gefundenen Datensatz neu. Der von Hand gepflegte Stand darf davon
    nicht beruehrt werden - und umgekehrt.
    """

    group_id: str
    marketing_status: MarketingStatus = MarketingStatus.NOT_CONTACTED
    contact_status: ContactStatus = ContactStatus.NONE
    permission_status: PermissionStatus = PermissionStatus.UNKNOWN
    campaign_status: CampaignParticipation = CampaignParticipation.NONE
    # Wann die Beitrittsanfrage gestellt wurde. Eigenes Feld, nicht
    # last_contacted_at: Eine Anfrage an die Gruppe ist keine Ansprache der
    # Leitung, und beide Zeitpunkte liegen oft Wochen auseinander - Facebook
    # laesst Beitrittsanfragen lange offen.
    join_requested_at: datetime | None = None
    last_contacted_at: datetime | None = None
    last_posted_at: datetime | None = None
    # Eigene Achse, ausdruecklich NICHT ueber marketing_status geloest: Dort
    # bedeuten ACTIVE/INACTIVE das Ende des Kooperationswegs (Zusammenarbeit
    # laeuft / ist beendet). Wer damit auch "nicht bearbeiten" ausdrueckte,
    # loeschte beim Ausschliessen die Information, dass er in der Gruppe
    # bereits Mitglied war - und beim Wiederaufnehmen faengt er von vorn an.
    #
    # Der Tracking-Code bleibt bei einem Ausschluss unberuehrt gueltig: Er
    # steht moeglicherweise in einem veroeffentlichten Beitrag. Ausschliessen
    # ist eine Entscheidung ueber die eigene Arbeit, kein Widerruf des Codes.
    bearbeiten: bool = True
    ausschlussgrund: str = ""
    notes: str = ""
    updated_at: datetime = Field(default_factory=_utcnow)


class Campaign(BaseModel):
    """Eine Marketing-Kampagne.

    ``audiences`` und ``cities`` verweisen auf die Kennungen aus
    ``config/audiences.yaml`` und ``config/cities.yaml`` - dieselbe fachliche
    Wahrheit wie im Rest des Projekts, keine zweite Liste.

    **Beschreibung und Auswahlregel sind zwei verschiedene Dinge.** ``audiences``
    und ``cities`` sagen, *wen* die Kampagne bewirbt; die ``target_*``-Felder
    sagen, *welche Gruppen* einen Tracking-Code bekommen. Frueher war beides
    dasselbe Feld. Das faellt erst auf, wenn man es auseinanderziehen will: Eine
    Kampagne darf "Batreeq Syrian Germany" heissen, syrische Zielgruppen
    bewerben und trotzdem den gesamten Bestand abdecken.

    Bei jedem ``target_*``-Feld heisst **leer: keine Einschraenkung**. Eine
    Kampagne ohne Angaben erfasst damit den ganzen Bestand - "alle Gruppen" ist
    ein Normalfall der Regel und kein Sonderweg.
    """

    campaign_id: str                       # Slug, z. B. "batreeq-syrian-germany"
    name: str
    description: str = ""
    audiences: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    language: str = ""
    message_template: str = ""             # Vorlage zum Selberposten, kein Automat
    landing_page: str = ""
    # Wohin ein Klick fuehrt: "store" (Play Store, Code ueberlebt ueber den
    # Install Referrer) oder "landing" (die eigene Seite, Code in der Adresse).
    # **Leer heisst: die Vorgabe aus der Konfiguration.** Nicht "landing" -
    # sonst muesste jede bestehende Kampagne einzeln umgestellt werden, und
    # eine neue Vorgabe waere wirkungslos.
    ziel: str = ""
    status: CampaignStatus = CampaignStatus.DRAFT
    starts_on: date | None = None
    ends_on: date | None = None
    # -- Auswahlregel: welche Gruppen die Kampagne erfasst ------------------
    target_audiences: list[str] = Field(default_factory=list)
    target_cities: list[str] = Field(default_factory=list)
    target_categories: list[str] = Field(default_factory=list)
    target_statuses: list[str] = Field(default_factory=list)
    target_min_score: float | None = None
    # Gruppen ohne Score sind solche, von denen oft nur die URL bekannt ist.
    # Sie bekommen einen Code ohne Zielgruppe und ohne Stadt (FB-GEN-DE-...),
    # der nichts ueber die Gruppe aussagt. Deshalb ist das eine ausdrueckliche
    # Entscheidung und keine Vorgabe.
    target_include_unscored: bool = False
    # Uebernimmt ein Import- oder Suchlauf neu gefundene Gruppen von selbst?
    # Vorgabe aus: Ein Lauf soll melden, was er gefunden hat, und nicht
    # nebenbei eine Kampagne veraendern. Anders als bei der Suche kostet das
    # Einschalten aber nichts - es wird nichts abgerufen und nichts bezahlt.
    auto_assign: bool = False
    # Kein Feld fuer "fuehrt diese Kampagne Kommentare?" mehr (28.08.2026).
    # Beitrag **und** Kommentar entstehen fuer jede Kampagne. Die Frage stand
    # vorher in jeder Kampagnenzeile und musste in keiner beantwortet werden:
    # Wer in einer Gruppe postet, kommentiert dort auch - und ein Kommentar,
    # den niemand braucht, kostet einen Blick, waehrend ein fehlender einen
    # Handgriff kostet. Die Spalte ``campaigns.kommentare`` bleibt im Schema
    # (Migrationen sind hier additiv), wird aber nicht mehr gelesen.
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class EventType(StrEnum):
    """Stationen auf dem Weg vom Klick zum Kunden.

    ``CLICK`` entsteht im eigenen Redirect-Dienst. Alles danach meldet die
    Anwendung, in der sich die Leute registrieren - dieses Projekt kann es
    nicht wissen.

    **Jede Stufe steht fuer sich.** Sie sind keine Kette, die man der Reihe
    nach durchlaufen muss: Wer sich registriert und die App nie herunterlaedt,
    ist ein gueltiger Zustand; wer herunterlaedt, ohne sich je registriert zu
    haben, ebenso. Die Reihenfolge in ``FUNNEL_ORDER`` ist die der Anzeige,
    keine Bedingung - kein Ereignis setzt ein anderes voraus, und keines
    erzeugt ein anderes mit.

    ``DOWNLOAD`` heisst: Der Bezug der App wurde ausgeloest. Was genau der
    ausloesende Moment ist, entscheidet die meldende Anwendung; sie soll ihn
    so spaet wie moeglich setzen (siehe ``docs/events-api.html``). Der Beweis,
    dass die App wirklich auf einem Geraet liegt, ist ``ACTIVATION`` - nur die
    App selbst kann ihn erbringen.

    **``STORE_VISIT`` ist keine Installation.** Es heisst genau: Wir haben
    diesen Menschen zum Play Store geschickt. Ob er dort auf "Installieren"
    drueckt, ob die Installation gelingt, ob er die App je oeffnet - davon
    weiss dieser Dienst nichts, denn der Play Store meldet uns nichts. Die
    Stufe waere als "Installation" bezeichnet eine Behauptung ueber etwas,
    das auf einem fremden Geraet geschieht.

    Damit gibt es drei verschiedene Dinge und drei verschiedene Namen:
    ``STORE_VISIT`` (wir haben weitergeleitet, gemessen von uns),
    ``DOWNLOAD`` (der Bezug wurde ausgeloest, gemessen von der ausliefernden
    Stelle) und ``ACTIVATION`` (die App lief wirklich, gemessen von der App).
    Nur die letzte beweist, dass die App angekommen ist.
    """

    CLICK = "click"
    LANDING_VISIT = "landing_visit"
    STORE_VISIT = "store_visit"
    REGISTRATION = "registration"
    DOWNLOAD = "download"
    ACTIVATION = "activation"
    QUALIFIED = "qualified"
    CONVERSION = "conversion"


# Reihenfolge des Trichters. Steht hier und nicht in der Auswertung, damit
# Trichter und Ereignisse nicht auseinanderlaufen koennen.
#
# Sie ordnet die *Anzeige*, nicht den Ablauf: Die Zahlen einer Stufe werden
# unabhaengig von jeder anderen gezaehlt. "Registrierungen 10, Downloads 3"
# bedeutet nicht, dass sieben Registrierungen fehlerhaft sind - es bedeutet,
# dass drei der Leute die App geholt haben.
FUNNEL_ORDER: tuple[EventType, ...] = (
    EventType.CLICK,
    EventType.LANDING_VISIT,
    EventType.STORE_VISIT,
    EventType.REGISTRATION,
    EventType.DOWNLOAD,
    EventType.ACTIVATION,
    EventType.QUALIFIED,
    EventType.CONVERSION,
)

# Ereignisse, die je Mensch hoechstens einmal zaehlen.
#
# Ein Download ist von Natur aus wiederholbar: neu laden, zweites Geraet,
# Neuinstallation. Ohne diese Schranke ueberholte ein einzelner Mensch mit
# fuenf Versuchen eine Gruppe, die vier Menschen gebracht hat - und genau
# diese Rangfolge ist der Zweck des ganzen Projekts.
#
# ``registration``, ``qualified`` und ``conversion`` stehen bewusst **nicht**
# darin: Sie sind seit dem 18.08.2026 im Betrieb, und ihre Bedeutung
# stillschweigend zu aendern machte alte und neue Zahlen unvergleichbar.
# ``activation`` ebenso - die meldende App bestimmt selbst, was "erstmals
# geoeffnet" heisst, und meldet es einmal.
EINMAL_JE_MENSCH: frozenset[EventType] = frozenset({EventType.DOWNLOAD})


class TrackingEvent(BaseModel):
    """Ein einzelnes Ereignis.

    ``user_ref`` ist die undurchsichtige Kennung aus der Zielanwendung - nie
    ein Name, nie eine Adresse, nie eine Telefonnummer. Sie steht nur dort,
    wo es sie schon gibt; ein Klick hat keine.

    ``visitor_hash`` ist kein Personenbezug, sondern ein taeglich wechselnder
    Pruefwert zum Aussortieren doppelter Klicks. Er ist nicht zurueckrechenbar
    und wird nie ausgegeben.
    """

    event_id: int | None = None
    tracking_code: str = ""
    campaign_id: str = ""
    group_id: str = ""
    user_ref: str = ""
    event_type: EventType
    occurred_at: datetime = Field(default_factory=_utcnow)
    visitor_hash: str = ""
    source: str = ""            # "redirect" oder "api"


class ReferralStatus(StrEnum):
    PENDING = "pending"
    REGISTERED = "registered"
    QUALIFIED = "qualified"
    CONVERTED = "converted"
    REJECTED = "rejected"
    REVIEW = "review"


class Referral(BaseModel):
    """Eine Empfehlung: ``referrer_user_ref`` hat ``referred_user_ref`` gebracht."""

    referral_id: int | None = None
    referral_code: str
    referrer_user_ref: str
    referred_user_ref: str
    status: ReferralStatus = ReferralStatus.PENDING
    campaign_id: str = ""
    group_id: str = ""
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    note: str = ""


class RewardType(StrEnum):
    FEATURE_UNLOCK = "feature_unlock"
    PREMIUM_DAYS = "premium_days"
    CREDITS = "credits"
    DISCOUNT = "discount"
    CUSTOM = "custom"


class RewardStatus(StrEnum):
    LOCKED = "locked"
    EARNED = "earned"
    CLAIMED = "claimed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class RewardRule(BaseModel):
    """Eine Praemienregel aus ``config/rewards.yaml``.

    Die Schwellen stehen in der Konfiguration, nicht im Code - eine geaenderte
    Zahl ist eine fachliche Entscheidung und darf keine Codeaenderung kosten.
    """

    rule_id: str
    label_de: str = ""
    threshold: int = 1
    metric: str = "qualified"        # zaehlt Referrals in diesem Status
    reward_type: RewardType = RewardType.CUSTOM
    value: str = ""
    active: bool = True


class Reward(BaseModel):
    """Eine erreichte Praemie eines Benutzers."""

    reward_id: int | None = None
    user_ref: str
    rule_id: str
    reward_type: RewardType = RewardType.CUSTOM
    value: str = ""
    status: RewardStatus = RewardStatus.EARNED
    earned_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    note: str = ""


class PostStatus(StrEnum):
    """Ob der Beitrag dieser Kampagne in dieser Gruppe erledigt ist.

    Gehoert zum **Paar** aus Kampagne und Gruppe, nicht zur Gruppe: Dieselbe
    Gruppe kann in zwei Kampagnen stehen und traegt dann zwei Beitraege mit
    zwei verschiedenen Tracking-Codes. In ``GroupMarketing`` gespeichert
    verdeckte der zweite Beitrag den ersten, und die Arbeitsliste haette eine
    offene Aufgabe als erledigt gemeldet.

    ``UEBERSPRUNGEN`` ist kein Fehlschlag: Die Gruppe passt gerade nicht (falsche
    Sprache, Thema daneben, Leitung verbietet Werbung). Sie erscheint deshalb
    nicht mehr in der Arbeitsliste, aber ``campaign retry`` holt allein die
    fehlgeschlagenen zurueck - eine bewusste Entscheidung wird nicht durch einen
    Sammelbefehl rueckgaengig gemacht.
    """

    OFFEN = "offen"
    VEROEFFENTLICHT = "veroeffentlicht"
    FEHLGESCHLAGEN = "fehlgeschlagen"
    UEBERSPRUNGEN = "uebersprungen"


class JobStatus(StrEnum):
    """Wo der Beitrag dieser Gruppe in der Vorbereitung steht.

    Zweite Achse neben ``PostStatus``, nicht dessen Ersatz. ``PostStatus``
    beantwortet "was kam in der Gruppe dabei heraus?" und wird seit Bestehen
    von ``campaign queue``, ``campaign retry``, ``post_counts`` und der
    Uebersicht gelesen; ``JobStatus`` beantwortet "wie weit ist der Text?".
    Beides in ein Feld zu zwaengen hiesse, entweder die Vorbereitung oder das
    Ergebnis zu verlieren.

    Damit die beiden nie auseinanderlaufen, wird ``PostStatus`` aus dem
    ``JobStatus`` **abgeleitet** (``POST_STATUS_ZU_JOB``) und an genau einer
    Stelle mitgeschrieben. Jeder aeltere Leser sieht dadurch unveraendert das,
    was er immer gesehen hat.

    ``PROCESSING`` ist der einzige Zustand, den nicht ein Mensch setzt,
    sondern der Arbeiter, der den Beitrag gerade in der Hand hat. Er ist
    deshalb auch der einzige, der nach einem Absturz haengenbleiben kann -
    ``queue.verwaiste_jobs`` findet genau diese wieder.
    """

    DRAFT = "draft"                    # Job angelegt, noch kein Text
    # Bestandsdaten: Bis zum Ausbau der KI-Schicht setzte ein Sprachmodell
    # diesen Stand. Er wird nicht mehr vergeben, bleibt aber lesbar - sonst
    # waeren Zuordnungen von damals nicht mehr ladbar, und die
    # Uebergangstabelle wuesste nicht, wie sie von dort weiterkommt.
    AI_GENERATED = "ai_generated"
    PENDING_REVIEW = "pending_review"  # wartet auf einen Menschen
    APPROVED = "approved"              # freigegeben, aber noch nicht eingereiht
    QUEUED = "queued"                  # in der Warteschlange
    PROCESSING = "processing"          # ein Arbeiter hat ihn gerade
    PUBLISHED = "published"            # steht in der Gruppe
    FAILED = "failed"                  # Versuch fehlgeschlagen
    CANCELLED = "cancelled"            # abgebrochen oder "passt nicht"


# Welcher Ergebnisstand zu welchem Vorbereitungsstand gehoert.
#
# Die Richtung ist Absicht: Der Job fuehrt, das Ergebnis folgt. Umgekehrt
# waere es nicht eindeutig - "offen" kann Entwurf, Freigabe oder Warteschlange
# heissen, und aus einem einzigen Wert liesse sich das nicht zurueckholen.
POST_STATUS_ZU_JOB: dict[JobStatus, PostStatus] = {
    JobStatus.DRAFT: PostStatus.OFFEN,
    JobStatus.AI_GENERATED: PostStatus.OFFEN,
    JobStatus.PENDING_REVIEW: PostStatus.OFFEN,
    JobStatus.APPROVED: PostStatus.OFFEN,
    JobStatus.QUEUED: PostStatus.OFFEN,
    JobStatus.PROCESSING: PostStatus.OFFEN,
    JobStatus.PUBLISHED: PostStatus.VEROEFFENTLICHT,
    JobStatus.FAILED: PostStatus.FEHLGESCHLAGEN,
    JobStatus.CANCELLED: PostStatus.UEBERSPRUNGEN,
}


class Texttyp(StrEnum):
    """Wofuer ein Text gedacht ist - eigener Beitrag oder Kommentar.

    Zwei Einsatzzwecke mit verschiedenen sprachlichen Anforderungen. Ein Post
    eroeffnet: Er darf begruessen, erklaeren und mit einem Aufruf enden. Ein
    Kommentar steht unter einem fremden Beitrag, der seinen Anlass schon
    mitbringt - er reagiert kurz und hilfreich, und derselbe Text als
    Kommentar gelesen waere eingeworfene Werbung.

    Sie teilen sich deshalb **keine** Vorlage, keinen Prompt und kein Feld am
    Datensatz. Was sie teilen, ist das Paar aus Kampagne und Gruppe: Beide
    Texte gehoeren zu *dieser* Gruppe in *dieser* Kampagne.

    Der Beitrag traegt weiterhin allein den Arbeitsablauf (``JobStatus``,
    Warteschlange, Versuche). Ein Kommentar wird kopiert und eingefuegt, er
    wird nicht eingereiht - eine zweite Warteschlange daneben waere eine
    zweite Zaehlweise fuer dieselben Beitraege.
    """

    POST = "post"
    KOMMENTAR = "kommentar"


class TextQuelle(StrEnum):
    """Woher der Beitragstext stammt.

    Steht am Datensatz, weil die Frage spaeter niemand mehr beantworten kann:
    Ein Text, den Claude geschrieben und ein Mensch ueberarbeitet hat, sieht
    aus wie ein Text, den ein Mensch geschrieben hat. Fuer die Beurteilung der
    Ergebnisse ist der Unterschied aber der ganze Punkt.
    """

    VORLAGE = "vorlage"    # aus config/textvorlagen.yaml oder campaign.message_template
    HAND = "hand"          # von Hand geschrieben oder ueberarbeitet
    # Bestandsdaten: Bis zum Ausbau der KI-Schicht schrieb ein Sprachmodell
    # Textfassungen, und die Spalte traegt fuer diese Zuordnungen weiterhin
    # "ki". Der Wert wird nicht mehr vergeben - entfernen liesse ihn aber
    # nicht lesen, und ein Datensatz von damals waere nicht mehr ladbar.
    KI = "ki"


#: Wie viele Fassungen je Gruppe und Einsatzzweck bereitstehen.
#:
#: Fuenf, weil der Vorrat in ``config/textvorlagen.yaml`` fuenf Fassungen je
#: Sprache, Zweck und Topf haelt: Die Zahl ist also keine willkuerliche
#: Obergrenze, sondern die Groesse des Topfes. Steht dort mehr oder weniger,
#: entstehen entsprechend mehr oder weniger Vorschlaege - diese Zahl ist die
#: Schranke, nicht die Vorgabe.
MAX_VORSCHLAEGE = 5


class VorschlagStatus(StrEnum):
    """Was aus **einer** Fassung geworden ist - je Vorschlag, nicht je Gruppe.

    Der Unterschied zu ``PostStatus`` ist der ganze Punkt der
    Vorschlagstabelle: ``PostStatus`` beantwortet "hat diese Gruppe ihren
    Beitrag bekommen?", ``VorschlagStatus`` beantwortet "was ist mit *dieser*
    Fassung geschehen?". Eine Gruppe kann Fassung 1 veroeffentlicht haben,
    Fassung 2 als fehlgeschlagen fuehren und Fassung 3 noch als Entwurf -
    ohne das waere jede der fuenf Fassungen nur eine Vorschau.

    ``GESPEICHERT`` steht zwischen Entwurf und Ausgang: Der Text wurde
    angefasst, ist aber noch nirgends hingegangen. Ohne diesen Wert liesse
    sich "aus der Vorlage gefallen" nicht von "durchgelesen und behalten"
    unterscheiden, und beim Blaettern durch fuenf Fassungen ist genau das die
    Frage.
    """

    ENTWURF = "entwurf"
    GESPEICHERT = "gespeichert"
    VEROEFFENTLICHT = "veroeffentlicht"
    FEHLGESCHLAGEN = "fehlgeschlagen"


class LaufStatus(StrEnum):
    """Der Gesamtzustand der Kommentarautomatik - ueber alle Kampagnen.

    Zweite Ebene ueber ``CampaignStatus``: Jene sagt, ob eine Kampagne
    betrieben wird; diese sagt, ob **gerade gearbeitet** wird. Eine Kampagne
    bleibt ``active``, auch wenn niemand einen Lauf gestartet hat.

    ``GESCHEITERT`` ist ein Lauf, der nicht zu Ende kam - nicht eine Kampagne,
    die nichts erreicht hat. Der Unterschied entscheidet, ob ein Neustart
    fortsetzt (``LAEUFT``, ``ANGEHALTEN``, ``GESCHEITERT``) oder eine neue
    Liste einfriert (``FERTIG``).
    """

    LAEUFT = "laeuft"
    ANGEHALTEN = "angehalten"
    GESCHEITERT = "gescheitert"
    FERTIG = "fertig"


class KampagnenLaufStatus(StrEnum):
    """Wo eine einzelne Kampagne innerhalb eines Laufs steht.

    Sie steht **neben** ``CampaignStatus``, nicht darin: Jene beschreibt die
    Kampagne auf Dauer (Entwurf, aktiv, pausiert), diese ihre Stellung in
    genau diesem Lauf. In ein Feld gezwungen verloere man beim zweiten Lauf
    die Auskunft, ob die Kampagne ueberhaupt noch betrieben wird.
    """

    WARTET = "wartet"
    LAEUFT = "laeuft"
    FERTIG = "fertig"
    GESCHEITERT = "gescheitert"


class QueueZustand(StrEnum):
    """Ob die Warteschlange einer Kampagne gerade laufen darf.

    ``PAUSIERT`` haelt nur die Ausgabe an - ein Beitrag, der gerade
    geschrieben wird, wird zu Ende gebracht. ``GESTOPPT`` raeumt zusaetzlich
    die Warteschlange: Alles, was noch nicht angefangen wurde, geht auf
    ``approved`` zurueck. Der Unterschied ist der zwischen "kurz warten" und
    "heute nicht mehr" - und er muss im Zustand stehen, nicht im Kopf des
    Bedienenden.
    """

    LAUFEND = "laufend"
    PAUSIERT = "pausiert"
    GESTOPPT = "gestoppt"


class PostVersuch(BaseModel):
    """Ein einzelner Veroeffentlichungsversuch - das Protokoll.

    Bisher hielt ``CampaignGroup`` nur einen Zaehler und den **letzten**
    Fehler. Damit liess sich nicht beantworten, ob eine Gruppe dreimal am
    selben Fehler scheiterte oder an drei verschiedenen - und das ist der
    Unterschied zwischen "die Gruppe erlaubt keine Links" und "das Netz war
    weg". Jeder Versuch bekommt deshalb seine eigene Zeile.

    ``browser_session`` benennt die Sitzung, in der es geschah - nie ein
    Passwort, nie ein Cookie, nur ein Name wie ``standard``. Der Unterschied
    ist wichtig genug, dass das Feld nicht ``credentials`` heisst.
    """

    versuch_id: int | None = None
    campaign_id: str
    group_id: str
    texttyp: str = "post"
    nummer: int = 1
    tracking_code: str = ""
    job_status: JobStatus = JobStatus.PROCESSING
    erfolg: bool = False
    post_url: str = ""             # falls Facebook eine Beitrags-URL zeigt
    fehler: str = ""
    browser_session: str = ""
    ausgeloest_von: str = ""       # "cli", "uebersicht", "worker"
    begonnen_am: datetime = Field(default_factory=_utcnow)
    beendet_am: datetime | None = None


class CampaignGroup(BaseModel):
    """Zuordnung Kampagne <-> Gruppe samt ihrem Tracking-Code.

    Der Code ist ab der Vergabe unveraenderlich: Er steht in veroeffentlichten
    Beitraegen und in fremden Statistiken. Wuerde er neu berechnet, zeigten
    alte Links ins Leere oder - schlimmer - auf eine andere Gruppe.
    """

    campaign_id: str
    group_id: str
    tracking_code: str
    tracking_url: str = ""

    # Der zweite Code desselben Paares - derselbe Weg, anderes Ziel.
    #
    # ``tracking_code`` fuehrt zum Play Store und **bleibt dabei**: Er steht in
    # veroeffentlichten Beitraegen, und sein Ziel nachtraeglich umzustellen
    # aenderte, wohin alte Beitraege fuehren. Dieser hier fuehrt in den
    # Browser, auf die Landingpage.
    #
    # Beide werden vollstaendig gezaehlt. Der Unterschied ist allein das Ziel
    # **nach** der Weiterleitung - und genau dadurch laesst sich im Trichter
    # unterscheiden, ob ein Mensch ueber den Store oder den Browser kam.
    #
    # Leer heisst "noch nicht vergeben": Ein Code ist endgueltig, und keiner
    # entsteht auf Vorrat.
    tracking_code_browser: str = ""
    tracking_url_browser: str = ""

    def code_fuer(self, ziel: str) -> str:
        """Der Code fuer ein Ziel - ``browser`` oder ``store``.

        Faellt auf den Store-Code zurueck, wenn der Browser-Code fehlt: Ein
        Beitrag ohne Link waere schlimmer als einer mit dem anderen Ziel.
        """
        if ziel == "browser" and self.tracking_code_browser:
            return self.tracking_code_browser
        return self.tracking_code

    def url_fuer(self, ziel: str) -> str:
        """Die Weiterleitungsadresse fuer ein Ziel. Siehe ``code_fuer``."""
        if ziel == "browser" and self.tracking_url_browser:
            return self.tracking_url_browser
        return self.tracking_url
    added_at: datetime = Field(default_factory=_utcnow)
    # -- Protokoll des Beitrags -------------------------------------------
    # Getrennt von ``added_at``: Eine Zuordnung entsteht durch die Regel, ein
    # Beitrag durch einen Menschen. Zwischen beidem liegen oft Wochen.
    post_status: PostStatus = PostStatus.OFFEN
    # Wann er veroeffentlicht wurde. Nur bei Erfolg gesetzt und danach nie
    # ueberschrieben - der erste Beitrag ist der, auf den die Klicks zurueckgehen.
    posted_at: datetime | None = None
    # Wann zuletzt etwas versucht wurde, gleich mit welchem Ausgang. Trennt
    # "noch nie angefasst" von "gestern gescheitert".
    last_attempt_at: datetime | None = None
    post_attempts: int = 0
    # Warum es nicht ging - im Klartext, wie ein Mensch es aufschreibt
    # ("Gruppe erlaubt keine Links", "Beitrag wartet auf Freigabe").
    post_error: str = ""
    # -- Der Beitrag selbst -----------------------------------------------
    # Bisher entstand der Text jedes Mal neu aus der Vorlage der Kampagne und
    # war nach der Ausgabe wieder weg. Sobald ein Mensch ihn ueberarbeitet
    # oder Claude ihn je Gruppe verschieden schreibt, ist das nicht mehr
    # haltbar: Freigegeben wird ein bestimmter Text, und veroeffentlicht muss
    # genau dieser werden - nicht einer, der sich beim naechsten Aufruf neu
    # zusammensetzt.
    job_status: JobStatus = JobStatus.DRAFT
    # Der Text, der wirklich hinausgeht - das "current_text" des Aufbaus.
    # Beginnt als Abschrift von ``generated_text`` und veraendert sich danach
    # nur durch einen Menschen oder einen uebernommenen KI-Vorschlag.
    post_text: str = ""
    # Was die deterministische Herstellung aus Vorlage und Gruppendaten
    # ergeben hat - unveraendert, auch wenn ``post_text`` laengst ueberarbeitet
    # ist. Zwei Felder statt einem, weil es zwei verschiedene Fragen sind:
    # "was hat die Maschine gebaut?" und "was steht jetzt da?". Ohne das erste
    # gaebe es nach einer KI-Ueberarbeitung keinen Weg zurueck, und niemand
    # koennte mehr sagen, wie viel von dem Text aus der Vorlage stammt.
    generated_text: str = ""
    # Welche Vorlage es war, z. B. "ar/mit_stadt/3". Gespeichert und nicht neu
    # berechnet: Nur so bekommt dieselbe Gruppe beim naechsten Fuellen wieder
    # dieselbe Fassung - etwa nachdem eine Stadt nachgetragen wurde. Wird die
    # Vorlagenliste umsortiert, zeigt der Schluessel woanders hin; das ist der
    # Preis dafuer, ihn lesbar zu halten, und steht in textvorlagen.yaml.
    vorlage_key: str = ""
    text_quelle: TextQuelle = TextQuelle.VORLAGE
    generiert_am: datetime | None = None
    freigegeben_am: datetime | None = None
    freigegeben_von: str = ""
    # -- Der Kommentar: dieselben drei Fragen, eigene Antworten -------------
    # Vier Spalten neben den Beitragsfeldern statt einer zweiten Tabelle: Der
    # Kommentar gehoert zum **selben** Paar aus Kampagne und Gruppe, hat
    # dieselbe Lebensdauer und verschwindet mit derselben Zuordnung. Eine
    # eigene Tabelle haette denselben Schluessel und dieselbe Lebensdauer -
    # eine Kopie mit dem Risiko, auseinanderzulaufen (dieselbe Ueberlegung
    # wie bei den Job-Feldern, die auch hier stehen und nicht daneben).
    #
    # Kein ``job_status`` fuer den Kommentar und keine ``kommentar_versuche``:
    # Der Beitrag traegt den Ablauf, der Kommentar wird kopiert.
    kommentar_text: str = ""
    kommentar_generated: str = ""
    kommentar_vorlage_key: str = ""
    kommentar_quelle: TextQuelle = TextQuelle.VORLAGE
    kommentar_generiert_am: datetime | None = None

    def text_fuer(self, texttyp: Texttyp) -> str:
        """Der laufende Text dieses Einsatzzwecks."""
        return self.kommentar_text if texttyp is Texttyp.KOMMENTAR else self.post_text

    def vorlage_fuer(self, texttyp: Texttyp) -> str:
        """Der Vorlagenschluessel dieses Einsatzzwecks."""
        return (
            self.kommentar_vorlage_key
            if texttyp is Texttyp.KOMMENTAR
            else self.vorlage_key
        )

class Textvorschlag(BaseModel):
    """Eine von mehreren Fassungen fuer *eine* Gruppe und *einen* Zweck.

    Bis hierher trug das Paar aus Kampagne und Gruppe genau **einen**
    Beitragstext und **einen** Kommentartext. Das war die stillschweigende
    Behauptung, die Wahl der Vorlage sei bereits getroffen - dabei ist sie das
    einzige, was ein Mensch beim Durchsehen wirklich entscheidet. Wer eine
    andere Fassung wollte, musste die vorhandene ueberschreiben, und die
    verworfene war weg.

    Der Schluessel ist deshalb vierteilig: Kampagne, Gruppe, Zweck, Nummer.
    Die Nummer ist eine **Position im Vorrat** und keine Rangfolge - Fassung 1
    ist nicht besser als Fassung 4, sie steht nur vorn. Welche Fassung auf
    Platz 1 liegt, entscheidet ``vorlagen.reihenfolge_fuer`` aus der Kennung
    der Gruppe; deshalb bekommt Platz 1 genau den Text, den die Gruppe auch
    vorher bekommen haette.

    ``generated_text`` steht wie am Paar neben ``text``: das erste ist, was
    die Vorlage ergeben hat, das zweite, was wirklich hinausgeht. Ohne beides
    gaebe es nach einer Ueberarbeitung keinen Weg zurueck.
    """

    campaign_id: str
    group_id: str
    texttyp: Texttyp = Texttyp.POST
    nummer: int = 1
    # Der Text, der wirklich hinausgeht - mit ``{link}`` darin. Aufgeloest
    # wird der Platzhalter erst in ``beitrag.beitragstext``, wie bisher.
    text: str = ""
    generated_text: str = ""
    vorlage_key: str = ""
    quelle: TextQuelle = TextQuelle.VORLAGE
    status: VorschlagStatus = VorschlagStatus.ENTWURF
    generiert_am: datetime | None = None
    # Nur beim **ersten** Erfolg gesetzt - dieselbe Regel wie ``posted_at``
    # am Paar: Die Klicks gehen auf den Beitrag zurueck, der zuerst stand.
    veroeffentlicht_am: datetime | None = None
    # Jeder gemeldete Ausgang zaehlt mit, auch der Erfolg. Beantwortet "wie
    # oft angefasst?", nicht "wie oft schiefgegangen?".
    versuche: int = 0
    fehler: str = ""

    @property
    def hat_text(self) -> bool:
        return bool(self.text.strip())

    @property
    def erledigt(self) -> bool:
        """Ist diese Fassung hinausgegangen?"""
        return self.status is VorschlagStatus.VEROEFFENTLICHT
