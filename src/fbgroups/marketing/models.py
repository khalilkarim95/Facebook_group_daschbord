"""Datenmodell der Marketing-Erweiterung.

Bewusst dieselbe Grenze wie im Bestand: Es gibt keine Felder fuer Mitglieder,
Admins, Profil-URLs oder Nachrichteninhalte. Gespeichert wird der eigene
Arbeitsstand - wen haben *wir* angesprochen, wer hat *uns* eine Erlaubnis
gegeben, welcher Link gehoert zu welcher Gruppe.

Das Modul veroeffentlicht nichts und verschickt nichts. Es verwaltet
ausschliesslich die Vorbereitung; jeder Beitrag und jede Anfrage wird von Hand
geschrieben und von Hand gesendet.
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
    """

    CLICK = "click"
    LANDING_VISIT = "landing_visit"
    REGISTRATION = "registration"
    ACTIVATION = "activation"
    QUALIFIED = "qualified"
    CONVERSION = "conversion"


# Reihenfolge des Trichters. Steht hier und nicht in der Auswertung, damit
# Trichter und Ereignisse nicht auseinanderlaufen koennen.
FUNNEL_ORDER: tuple[EventType, ...] = (
    EventType.CLICK,
    EventType.LANDING_VISIT,
    EventType.REGISTRATION,
    EventType.ACTIVATION,
    EventType.QUALIFIED,
    EventType.CONVERSION,
)


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
