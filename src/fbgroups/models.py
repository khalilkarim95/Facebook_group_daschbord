"""Datenmodell.

Bewusste Einschraenkung: Es gibt keine Felder fuer Mitglieder- oder Adminnamen,
Profil-URLs, Beitragsinhalte, E-Mail-Adressen oder Telefonnummern. Gespeichert
werden ausschliesslich oeffentliche Organisationsdaten der Gruppe selbst.
Wer das Modell erweitert, soll diese Grenze bewusst und sichtbar aendern muessen.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SourceType(StrEnum):
    MANUAL_SEED = "manual_seed"
    SEARCH = "search"


class ValidationStatus(StrEnum):
    """Ergebnis der Pruefung der Gruppen-URL."""

    VALID = "valid"
    INVALID = "invalid"
    TEST_DATA = "test_data"  # offensichtlicher Platzhalter, kein echter Fund
    # Vom Menschen im Browser geprueft und nicht erreichbar (geloescht, privat,
    # umbenannt). Dieses Urteil kann das Programm nicht selbst faellen - es
    # ruft facebook.com nicht auf - und darf es deshalb auch nie ueberschreiben.
    UNREACHABLE = "unreachable"


class RecordStatus(StrEnum):
    """Status eines Datensatzes im Verarbeitungsablauf."""

    NEW = "new"
    VALIDATED = "validated"
    INVALID = "invalid"
    DUPLICATE = "duplicate"
    INSUFFICIENT_DATA = "insufficient_data"


class DataQuality(StrEnum):
    """Wie viele belastbare Metadatenfelder tatsaechlich vorliegen."""

    NONE = "none"  # nur die URL
    MINIMAL = "minimal"  # 1-2 Felder
    PARTIAL = "partial"  # 3-4 Felder
    COMPLETE = "complete"  # 5 und mehr Felder


class PrivacyHint(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    UNKNOWN = "unknown"


class MemberCountSource(StrEnum):
    """Woher die Mitgliederzahl stammt - und damit, wie belastbar sie ist.

    Die Angabe ist kein Beiwerk. Dieselbe Zahl bedeutet etwas anderes, je
    nachdem, ob sie auf der Gruppenseite stand oder aus einem Suchtreffer
    geschaetzt wurde; ``data_confidence`` liest genau das hier ab.
    """

    FACEBOOK = "facebook"  # oeffentlich erreichbare Gruppenseite
    SEARCH = "search"  # ausdruecklich benannt im Suchtreffer
    MANUAL = "manual"  # von Hand gepflegt (fbgroups pruefliste)


class ActivitySource(StrEnum):
    """Woher das Aktivitaetsmass stammt.

    Drei Quellen mit deutlich verschiedener Aussagekraft, deshalb getrennt
    benannt: Die Beitragsliste der Gruppe misst die Gruppe, die eigene
    Resonanz misst, was **uns** von dort erreicht, und die Datumsangaben der
    Suchtreffer belegen nur, dass irgendwann ein Beitrag indexiert wurde.
    """

    FACEBOOK = "facebook"  # Beitragsliste der Gruppenseite
    RESONANZ = "resonanz"  # eigene Tracking-Ereignisse
    SEARCH_DATES = "search_dates"  # Datumsangaben indexierter Beitraege


class Provenance(BaseModel):
    """Woher stammt ein Datensatz - fuer die Qualitaetsauswertung."""

    source_type: SourceType
    source_ref: str = ""  # Dateiname (Seed) bzw. query_id (Suche)
    source_line: int | None = None
    discovered_at: datetime = Field(default_factory=_utcnow)


class ScoreBreakdown(BaseModel):
    """Nachvollziehbare Einzelteile des Scores statt einer nackten Zahl.

    Die Feldnamen sind die Namen der Bestandteile aus ``scoring.BESTANDTEILE``;
    ein neuer Bestandteil braucht hier ein Feld und in ``settings.yaml`` ein
    Gewicht, sonst nichts. ``model_config`` laesst unbekannte Namen deshalb
    **nicht** durchgehen: Ein Tippfehler im Bestandteilsnamen soll auffallen
    und nicht still null Punkte ergeben.

    Ein Bestandteil mit Gewicht 0 und einer, dessen Grundlage fehlt, stehen
    hier beide auf 0,0 - unterscheiden lassen sie sich an ``score_max`` und
    ``score_reason``. Das ist Absicht: Die Aufschluesselung nennt Punkte, die
    Begruendung nennt Gruende.
    """

    # -- Die fuenf Bestandteile (Summe der Gewichte: 100) ------------------
    members: float = 0.0  # 25 - Groesse, logarithmisch gestuft
    activity: float = 0.0  # 25 - Betrieb in der Gruppe
    location: float = 0.0  # 15 - geografische Passung
    category: float = 0.0  # 20 - Themenpassung
    target_audience: float = 0.0  # 15 - Zielgruppenpassung

    # -- Abschaltbare Zusatzbestandteile (Vorgabegewicht 0) ----------------
    # Sie zaehlen nicht mit, solange ihr Gewicht 0 ist, und erscheinen dann
    # auch nicht als "unbekannt". Erhalten bleiben sie, weil das Abschalten
    # eines Bestandteils eine Zahlenaenderung sein soll und keine Codeaenderung.
    name_quality: float = 0.0

    def total(self) -> float:
        """Summe aller Bestandteile - unabhaengig davon, welche es gerade gibt.

        Ueber die Felder statt ueber eine Aufzaehlung im Code: Wer einen
        Bestandteil ergaenzt, soll ihn nicht an zwei Stellen eintragen
        muessen - die zweite wird sonst vergessen.
        """
        return round(sum(float(w) for w in self.model_dump().values()), 2)


class Group(BaseModel):
    """Eine oeffentlich auffindbare Facebook-Gruppe."""

    # Identitaet
    group_id: str
    url_canonical: str
    url_variants: list[str] = Field(default_factory=list)

    # Oeffentliche Angaben
    name: str = ""
    description_snippet: str | None = None
    privacy_hint: PrivacyHint = PrivacyHint.UNKNOWN
    language_hint: str | None = None

    # -- Mitgliederzahl ----------------------------------------------------
    # Drei Felder, nicht eines. ``None`` heisst unbekannt und ist etwas
    # anderes als 0: "0 Mitglieder" waere eine Aussage ueber die Gruppe,
    # "unbekannt" eine ueber unsere Daten. Die Herkunft steht daneben, weil
    # dieselbe Zahl je nach Quelle verschieden viel wert ist, und der
    # Zeitpunkt, weil eine Mitgliederzahl von vor einem Jahr keine
    # Mitgliederzahl von heute ist. ``checked_at`` wird auch dann gesetzt,
    # wenn nichts gefunden wurde - sonst liefe der Abruf jedes Mal erneut.
    member_count: int | None = None
    member_count_source: MemberCountSource | None = None
    member_count_checked_at: datetime | None = None

    # -- Aktivitaet --------------------------------------------------------
    # Dieselbe Trennung. ``posts_per_day`` bleibt ``None``, solange keine
    # Zahl belegt ist; ``activity_factor`` (0-1) ist die daraus abgeleitete
    # Bewertung und existiert getrennt, weil es Quellen gibt, die einen
    # Betriebseindruck liefern, ohne eine Beitragszahl zu nennen - die
    # Datumsangaben der Suchtreffer etwa.
    posts_per_day: float | None = None
    last_post_at: datetime | None = None
    activity_factor: float | None = None
    activity_confidence: float = 0.0
    activity_source: ActivitySource | None = None
    activity_checked_at: datetime | None = None

    # Klassifikation
    audience_tags: list[str] = Field(default_factory=list)
    audience_confidence: float = 0.0
    city: str | None = None
    bundesland: str | None = None
    # Das Land ist fast immer Deutschland - aber "fast immer" ist kein
    # "immer", und eine Gruppe ohne erkennbare Stadt kann trotzdem
    # geografisch passen. Ohne das Feld waere "Deutschland allgemein" von
    # "geografisch unbekannt" nicht zu unterscheiden.
    country: str | None = None
    city_confidence: float = 0.0
    category: str | None = None
    # Mehrere Themen sind der Normalfall: "Syrer in Bonn" ist zugleich
    # Community, Lokales und Integration. Die Hauptkategorie entscheidet die
    # Punkte, die Nebenkategorien heben sie an - eine Gruppe, die drei
    # gesuchte Themen trifft, ist besser als eine, die eines trifft.
    secondary_categories: list[str] = Field(default_factory=list)
    category_confidence: float = 0.0

    # Bewertung
    # None bedeutet: nicht bewertbar. Ein Ersatzwert waere eine Behauptung
    # ueber Daten, die nicht vorliegen - der Grund steht in score_reason.
    score: float | None = None
    # Hoechstpunktzahl, die bei DIESER Datenlage ueberhaupt erreichbar war.
    # Ohne diesen Wert liesse sich ein Score nicht einordnen: 75 aus 75
    # moeglichen Punkten ist etwas anderes als 75 aus 100.
    score_max: float | None = None
    score_reason: str = ""
    score_breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    # Wie belastbar die Grundlage des Scores ist (0-1) - **neben** dem Score,
    # nicht darin. Ein schlechter Score aus guten Daten und ein guter Score
    # aus duennen Daten sind zwei verschiedene Aussagen; sie zu verrechnen
    # machte beide unlesbar. Die Confidence darf deshalb nie in den Score
    # einfliessen, sie wird daneben angezeigt.
    data_confidence: float = 0.0
    # Wann zuletzt versucht wurde, die Angaben aufzufrischen.
    last_checked_at: datetime | None = None

    # Prozess
    validation_status: ValidationStatus = ValidationStatus.VALID
    data_quality: DataQuality = DataQuality.NONE
    status: RecordStatus = RecordStatus.NEW
    notes: str = ""

    # Herkunft
    sources: list[Provenance] = Field(default_factory=list)
    first_seen_at: datetime = Field(default_factory=_utcnow)
    last_seen_at: datetime = Field(default_factory=_utcnow)
    times_seen: int = 1

    def merge_duplicate(self, other: Group) -> None:
        """Fuehrt einen erneuten Fund derselben Gruppe in diesen Datensatz."""
        for variant in [other.url_canonical, *other.url_variants]:
            if variant not in self.url_variants and variant != self.url_canonical:
                self.url_variants.append(variant)

        # Fehlende Angaben aus dem Duplikat ergaenzen, vorhandene nicht ueberschreiben.
        if not self.name and other.name:
            self.name = other.name
        # Die Mitgliederzahl kommt mit ihrer Herkunft oder gar nicht: Eine
        # Zahl ohne Quellenangabe liesse sich spaeter nicht mehr einordnen.
        if self.member_count is None and other.member_count is not None:
            self.member_count = other.member_count
            self.member_count_source = other.member_count_source
            self.member_count_checked_at = other.member_count_checked_at
        if not self.description_snippet and other.description_snippet:
            self.description_snippet = other.description_snippet

        self.sources.extend(other.sources)
        self.last_seen_at = max(self.last_seen_at, other.last_seen_at)
        self.times_seen += other.times_seen


class GroupPost(BaseModel):
    """Metriken eines einzelnen Gruppenbeitrags.

    ACHTUNG: Dieses Modell darf keine Felder für Beitragstexte oder
    Autorennamen enthalten (harte Projektgrenze). Es dient ausschließlich
    der Auswahl des besten Beitrags für automatisierte Kommentare.
    """

    group_id: str
    post_url: str
    interactions: int = 0
    comments: int = 0
    published_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=_utcnow)


class QueryStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"  # Antwort ohne Treffer
    CACHED = "cached"  # aus dem Zwischenspeicher, kein Guthaben verbraucht
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    ERROR = "error"
    SKIPPED = "skipped"  # wegen Obergrenze nicht ausgefuehrt


class QueryRecord(BaseModel):
    """Protokoll einer einzelnen Suchanfrage - Grundlage der Qualitaetsmessung."""

    query_id: str
    text: str
    lang: str = ""
    scope: str = ""
    template_id: str = ""
    city_id: str | None = None
    audiences: list[str] = Field(default_factory=list)

    provider: str = ""
    status: QueryStatus = QueryStatus.SKIPPED
    from_cache: bool = False
    n_results: int = 0  # Treffer insgesamt
    n_group_urls: int = 0  # davon Facebook-Gruppen
    n_groups_new: int = 0  # davon in diesem Lauf erstmals gesehen
    error_type: str = ""
    error_message: str = ""
    duration_ms: int = 0
    executed_at: datetime = Field(default_factory=_utcnow)

    @property
    def precision(self) -> float:
        """Anteil der Treffer, die tatsaechlich Gruppen-URLs sind."""
        if self.n_results == 0:
            return 0.0
        return round(self.n_group_urls / self.n_results * 100, 1)


class SearchRun(BaseModel):
    """Ergebnis eines automatischen Suchlaufs."""

    run_id: str
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime | None = None
    provider: str = ""
    dry_run: bool = False

    queries_planned: int = 0
    queries_executed: int = 0
    queries_ok: int = 0
    queries_cached: int = 0
    queries_failed: int = 0

    hits_total: int = 0
    group_urls_found: int = 0
    groups_unique: int = 0
    groups_new: int = 0
    groups_known: int = 0

    # Vom Dienst gemeldeter Verbrauch dieses Laufs. Das Restguthaben meldet
    # Serper nicht - es steht nur im Konto unter serper.dev/dashboard.
    credits_used: int = 0
    quota_remaining: int | None = None
    records: list[QueryRecord] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def precision(self) -> float:
        """Wie treffsicher die Suchstrategie insgesamt war."""
        if self.hits_total == 0:
            return 0.0
        return round(self.group_urls_found / self.hits_total * 100, 1)

    @property
    def yield_per_query(self) -> float:
        """Neue Gruppen je ausgefuehrter Anfrage."""
        if self.queries_executed == 0:
            return 0.0
        return round(self.groups_unique / self.queries_executed, 2)


class RejectedRow(BaseModel):
    """Eine Eingabezeile, die keine gueltige Gruppen-URL enthielt."""

    raw_value: str
    reason: str
    source_ref: str = ""
    source_line: int | None = None


class DuplicateSuspect(BaseModel):
    """Zwei verschiedene Gruppen mit auffaellig aehnlichem Namen."""

    group_id_a: str
    group_id_b: str
    name_a: str
    name_b: str
    similarity: float


class ImportRun(BaseModel):
    """Ergebnis eines Importlaufs - Grundlage des Qualitaetsreports."""

    run_id: str
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime | None = None
    source_type: SourceType = SourceType.MANUAL_SEED
    source_files: list[str] = Field(default_factory=list)

    rows_total: int = 0
    rows_valid: int = 0  # Zeilen mit formal parsbarer Gruppen-URL
    rows_rejected: int = 0  # Zeilen ohne verwertbare URL
    groups_new: int = 0
    groups_duplicate: int = 0

    # Ergebnis der inhaltlichen Pruefung
    groups_validated: int = 0
    groups_test_data: int = 0
    groups_invalid: int = 0
    groups_insufficient_data: int = 0
    groups_scored: int = 0

    rejected: list[RejectedRow] = Field(default_factory=list)
    duplicate_suspects: list[DuplicateSuspect] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def duplicate_rate(self) -> float:
        if self.rows_valid == 0:
            return 0.0
        return round(self.groups_duplicate / self.rows_valid * 100, 1)
