"""Regressionstests fuer Windows-Encoding-Fallen.

Unter Windows erzeugen Notepad und ``Out-File`` UTF-8-Dateien mit BOM. Ohne
``utf-8-sig`` wird das BOM Teil der ersten Zeile - die erste URL einer
Seed-Liste ging dadurch stillschweigend verloren.
"""

from __future__ import annotations

from pathlib import Path

from fbgroups.importers.manual_seed import import_seeds

REAL_ID_A = "482910573829104"
REAL_ID_B = "739201847362915"


def test_txt_mit_bom_verliert_keine_zeile(config, tmp_path: Path) -> None:
    path = tmp_path / "seeds.txt"
    path.write_text(
        f"https://www.facebook.com/groups/{REAL_ID_A}\n"
        f"https://www.facebook.com/groups/{REAL_ID_B}\n",
        encoding="utf-8-sig",
    )
    groups, run = import_seeds(config, paths=[path])

    assert run.rows_valid == 2
    assert run.rows_rejected == 0
    assert {g.group_id for g in groups} == {REAL_ID_A, REAL_ID_B}


def test_csv_mit_bom_verliert_keine_zeile(config, tmp_path: Path) -> None:
    path = tmp_path / "seeds.csv"
    path.write_text(
        f"url;name\nhttps://www.facebook.com/groups/{REAL_ID_A};Syrer in Berlin\n",
        encoding="utf-8-sig",
    )
    groups, run = import_seeds(config, paths=[path])

    assert run.rows_valid == 1
    assert groups[0].name == "Syrer in Berlin"


def test_txt_ohne_bom_funktioniert_weiterhin(config, tmp_path: Path) -> None:
    path = tmp_path / "seeds.txt"
    path.write_text(f"https://www.facebook.com/groups/{REAL_ID_A}\n", encoding="utf-8")
    _, run = import_seeds(config, paths=[path])
    assert run.rows_valid == 1
