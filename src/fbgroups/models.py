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
    TEST_DATA = "test_data"     # offensichtlicher Platzhalter, kein echter Fund
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

    NONE = "none"          # nur die URL
    MINIMAL = "minimal"    # 1-2 Felder
    PARTIAL = "partial"    # 3-4 Felder
    COMPLETE = "complete"  # 5 und mehr Felder


class PrivacyHint(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    UNKNOWN = "unknown"


class Provenance(BaseModel):
    """Woher stammt ein Datensatz - fuer die Qualitaetsauswertung."""

    source_type: SourceType
    source_ref: str = ""          # Dateiname (Seed) bzw. query_id (Suche)
    source_line: int | None = None
    discovered_at: datetime = Field(default_factory=_utcnow)


class ScoreBreakdown(BaseModel):
    """Nachvollziehbare Einzelteile des Scores statt einer nackten Zahl."""

    audience_match: float = 0.0
    city_match: float = 0.0
    category_match: float = 0.0
    member_count: float = 0.0
    name_quality: float = 0.0
    # -- Gemessene Resonanz ------------------------------------------------
    # Was die Gruppe tatsaechlich gebracht hat, nicht was sie verspricht. Die
    # Zahlen stammen aus den eigenen Tracking-Ereignissen - dem einzigen
    # Aktivitaetsmass, das dieses Projekt ohne facebook.com erheben kann.
    resonanz_engagement: float = 0.0
    resonanz_reichweite: float = 0.0
    resonanz_aktualitaet: float = 0.0

    def total(self) -> float:
        return round(
            self.audience_match
            + self.city_match
            + self.category_match
            + self.member_count
            + self.name_quality
            + self.resonanz_engagement
            + self.resonanz_reichweite
            + self.resonanz_aktualitaet,
            2,
        )


class Group(BaseModel):
    """Eine oeffentlich auffindbare Facebook-Gruppe."""

    # Identitaet
    group_id: str
    url_canonical: str
    url_variants: list[str] = Field(default_factory=list)

    # Oeffentliche Angaben
    name: str = ""
    description_snippet: str | None = None
    member_count_hint: int | None = None
    privacy_hint: PrivacyHint = PrivacyHint.UNKNOWN
    language_hint: str | None = None

    # Klassifikation
    audience_tags: list[str] = Field(default_factory=list)
    audience_confidence: float = 0.0
    city: str | None = None
    bundesland: str | None = None
    city_confidence: float = 0.0
    category: str | None = None
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
        if self.member_count_hint is None and other.member_count_hint is not None:
            self.member_count_hint = other.member_count_hint
        if not self.description_snippet and other.description_snippet:
            self.description_snippet = other.description_snippet

        self.sources.extend(other.sources)
        self.last_seen_at = max(self.last_seen_at, other.last_seen_at)
        self.times_seen += other.times_seen


class QueryStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"                # Antwort ohne Treffer
    CACHED = "cached"              # aus dem Zwischenspeicher, kein Guthaben verbraucht
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    ERROR = "error"
    SKIPPED = "skipped"            # wegen Obergrenze nicht ausgefuehrt


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
    n_results: int = 0             # Treffer insgesamt
    n_group_urls: int = 0          # davon Facebook-Gruppen
    n_groups_new: int = 0          # davon in diesem Lauf erstmals gesehen
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
    rows_valid: int = 0          # Zeilen mit formal parsbarer Gruppen-URL
    rows_rejected: int = 0       # Zeilen ohne verwertbare URL
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
