"""CSV-Export.

Geschrieben mit BOM (``utf-8-sig``) und Semikolon als Trenner, damit Excel
unter Windows die arabischen Namen korrekt anzeigt und Spalten richtig trennt.
"""

from __future__ import annotations

import csv
from pathlib import Path

from fbgroups.export.columns import headers, row_values
from fbgroups.models import Group


def export_csv(groups: list[Group], path: Path, delimiter: str = ";") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=delimiter, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(headers())
        for group in groups:
            writer.writerow(row_values(group))

    return path
