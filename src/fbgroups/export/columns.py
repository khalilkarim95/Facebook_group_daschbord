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
    # Die fuenf Bestandteile einzeln. Ohne sie steht im Export eine Zahl, die
    # sich nicht nachrechnen laesst - und die Frage "warum 84?" ist genau die,
    # die vor einer Kooperationsentscheidung gestellt wird.
    ("punkte_members", "Punkte Mitglieder"),
    ("punkte_activity", "Punkte Aktivitaet"),
    ("punkte_location", "Punkte Ort"),
    ("punkte_category", "Punkte Kategorie"),
    ("punkte_target_audience", "Punkte Zielgruppe"),
    # Neben dem Score, nie darin: "wie sicher?" und "wie gut?" sind zwei Fragen.
    ("data_confidence", "Datenqualitaet"),
    ("status", "Status"),
    ("validation_status", "Validation Status"),
    ("data_quality", "Data Quality"),
    ("name", "Gruppenname"),
    ("url_canonical", "URL"),
    ("audience_tags", "Zielgruppen"),
    ("city", "Stadt"),
    ("bundesland", "Bundesland"),
    ("country", "Land"),
    ("category", "Kategorie"),
    ("secondary_categories", "Nebenkategorien"),
    ("member_count", "Mitglieder (ca.)"),
    # Die Herkunft gehoert zur Zahl: Dieselbe Zahl aus einem Suchtreffer ist
    # weniger wert als eine von der Gruppenseite.
    ("member_count_source", "Quelle Mitglieder"),
    ("posts_per_day", "Beitraege/Tag"),
    ("activity_source", "Quelle Aktivitaet"),
    ("last_post_at", "Letzter Beitrag"),
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
_UNKNOWN_IF_EMPTY = {
    "name", "city", "bundesland", "country", "category", "member_count",
    # Auch hier ausdruecklich "unknown" statt leer: Eine leere Zelle laesst
    # offen, ob geprueft wurde. Bei der Aktivitaet ist genau das die Frage -
    # "0 Beitraege am Tag" und "nie nachgesehen" sind zwei Welten.
    "posts_per_day", "member_count_source", "activity_source", "last_post_at",
}

#: Die Bestandteile des Scores kommen nicht aus ``Group``, sondern aus
#: ``score_breakdown``. Sie hier aufzuloesen ist der Preis dafuer, dass die
#: Aufschluesselung ein eigenes Objekt ist - und der ist geringer als der
#: einer flachen Kopie, die auseinanderlaufen kann.
_PUNKTE_PRAEFIX = "punkte_"


def row_values(group: Group) -> list:
    """Wandelt eine Gruppe in eine Exportzeile.

    Der Score bleibt leer, wenn er ``None`` ist - kein Ersatzwert, keine Null.
    """
    values = []
    for field_name, _ in COLUMNS:
        if field_name.startswith(_PUNKTE_PRAEFIX):
            teil = field_name.removeprefix(_PUNKTE_PRAEFIX)
            # Leer statt 0, wenn die Gruppe gar nicht bewertet wurde: Eine 0
            # neben einem leeren Score waere eine Aussage ueber eine Gruppe,
            # ueber die keine gemacht wurde.
            values.append(
                getattr(group.score_breakdown, teil, 0.0) if group.score is not None else ""
            )
            continue

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
