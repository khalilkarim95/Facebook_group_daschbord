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
    """

    CLICK = "click"
    LANDING_VISIT = "landing_visit"
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
    AI_GENERATED = "ai_generated"      # Claude hat einen Text erzeugt
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


class TextQuelle(StrEnum):
    """Woher der Beitragstext stammt.

    Steht am Datensatz, weil die Frage spaeter niemand mehr beantworten kann:
    Ein Text, den Claude geschrieben und ein Mensch ueberarbeitet hat, sieht
    aus wie ein Text, den ein Mensch geschrieben hat. Fuer die Beurteilung der
    Ergebnisse ist der Unterschied aber der ganze Punkt.
    """

    VORLAGE = "vorlage"    # aus campaign.message_template
    KI = "ki"              # von Claude, unveraendert
    HAND = "hand"          # von Hand geschrieben oder ueberarbeitet


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


class PostEntwurf(BaseModel):
    """Eine erzeugte Textfassung fuer eine Gruppe.

    Mehrere je Gruppe: Der Nutzer soll waehlen koennen, statt den ersten
    Vorschlag nehmen zu muessen. Die gewaehlte Fassung wird in
    ``CampaignGroup.post_text`` kopiert - der Entwurf bleibt daneben stehen,
    damit sichtbar ist, wogegen entschieden wurde.
    """

    entwurf_id: int | None = None
    campaign_id: str
    group_id: str
    variante: int = 1
    text: str = ""
    quelle: TextQuelle = TextQuelle.KI
    modell: str = ""               # z. B. "claude-sonnet-5"
    erzeugt_am: datetime = Field(default_factory=_utcnow)
    gewaehlt: bool = False


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
    post_text: str = ""
    text_quelle: TextQuelle = TextQuelle.VORLAGE
    generiert_am: datetime | None = None
    freigegeben_am: datetime | None = None
    freigegeben_von: str = ""
