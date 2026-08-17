"""Gemeinsame Spaltendefinition fuer CSV und Excel."""

from __future__ import annotations

from fbgroups.models import Group

COLUMNS: list[tuple[str, str]] = [
    ("score", "Score"),
    # Ohne die erreichbare Hoechstpunktzahl laesst sich der Score nicht
    # einordnen: 75 von 75 heisst "alles belegt, was vorlag", 75 von 100 heisst
    # "die Mitgliederzahl fehlt".
    ("score_max", "Score max."),
    ("score_reason", "Score Reason"),
    ("status", "Status"),
    ("validation_status", "Validation Status"),
    ("data_quality", "Data Quality"),
    ("name", "Gruppenname"),
    ("url_canonical", "URL"),
    ("audience_tags", "Zielgruppen"),
    ("city", "Stadt"),
    ("bundesland", "Bundesland"),
    ("category", "Kategorie"),
    ("member_count_hint", "Mitglieder (ca.)"),
    ("privacy_hint", "Sichtbarkeit"),
    ("notes", "Notizen"),
    ("audience_confidence", "Konf. Zielgruppe"),
    ("city_confidence", "Konf. Stadt"),
    ("category_confidence", "Konf. Kategorie"),
    ("times_seen", "Funde"),
    ("group_id", "Group-ID"),
]

# Felder, die bei fehlendem Wert ausdruecklich "unknown" anzeigen, statt leer
# zu bleiben. Eine leere Zelle laesst offen, ob geprueft wurde; "unknown" sagt,
# dass geprueft wurde und nichts Belastbares vorlag.
_UNKNOWN_IF_EMPTY = {"name", "city", "bundesland", "category", "member_count_hint"}


def row_values(group: Group) -> list:
    """Wandelt eine Gruppe in eine Exportzeile.

    Der Score bleibt leer, wenn er ``None`` ist - kein Ersatzwert, keine Null.
    """
    values = []
    for field_name, _ in COLUMNS:
        value = getattr(group, field_name, None)

        if isinstance(value, list):
            value = ", ".join(str(v) for v in value) if value else "unknown"
        elif value is None or value == "":
            value = "unknown" if field_name in _UNKNOWN_IF_EMPTY else ""
        elif hasattr(value, "value"):  # StrEnum
            value = value.value

        values.append(value)
    return values


def headers() -> list[str]:
    return [label for _, label in COLUMNS]
