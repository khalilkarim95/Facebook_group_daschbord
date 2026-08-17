from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.importers.manual_seed import import_seeds, parse_member_count
from fbgroups.pipeline import run_seed_import


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12500", 12500),
        ("12.500", 12500),
        ("12,500", 12500),
        ("12,5k", 12500),
        ("12k", 12000),
        ("1.5k", 1500),
        ("3 Mio", 3_000_000),
        ("ca. 4.200 Mitglieder", 4200),
        ("", None),
        (None, None),
        ("keine Angabe", None),
    ],
)
def test_parse_member_count(raw, expected) -> None:
    assert parse_member_count(raw) == expected


def _write_csv(tmp_path: Path, content: str, name: str = "seeds.csv") -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_import_mit_semikolon(config, tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path,
        "url;name;member_count\n"
        "https://www.facebook.com/groups/111;Syrer in Berlin;12.000\n",
    )
    groups, run = import_seeds(config, paths=[path])

    assert run.rows_total == 1
    assert run.rows_valid == 1
    assert groups[0].name == "Syrer in Berlin"
    assert groups[0].member_count_hint == 12000


def test_import_mit_komma(config, tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path,
        "url,name\nhttps://www.facebook.com/groups/222,Araber Hamburg\n",
    )
    groups, run = import_seeds(config, paths=[path])
    assert run.rows_valid == 1
    assert groups[0].name == "Araber Hamburg"


def test_import_deutsche_spaltennamen(config, tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path,
        "Link;Gruppenname;Mitglieder\n"
        "https://www.facebook.com/groups/333;Test Gruppe;500\n",
    )
    groups, run = import_seeds(config, paths=[path])
    assert run.rows_valid == 1
    assert groups[0].name == "Test Gruppe"
    assert groups[0].member_count_hint == 500


def test_import_txt_mit_kommentaren(config, tmp_path: Path) -> None:
    path = tmp_path / "seeds.txt"
    path.write_text(
        "# Kommentarzeile\n"
        "https://www.facebook.com/groups/444\n"
        "\n"
        "https://www.facebook.com/groups/555  # nachgestellter Kommentar\n",
        encoding="utf-8",
    )
    groups, run = import_seeds(config, paths=[path])
    assert run.rows_total == 2
    assert run.rows_valid == 2
    assert {g.group_id for g in groups} == {"444", "555"}


def test_ungueltige_zeilen_werden_protokolliert(config, tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path,
        "url;name\n"
        "https://www.facebook.com/groups/111;Gueltig\n"
        "https://example.com/groups/999;Falsche Domain\n"
        "https://www.facebook.com/pages/abc;Keine Gruppe\n",
    )
    groups, run = import_seeds(config, paths=[path])

    assert run.rows_valid == 1
    assert run.rows_rejected == 2
    assert len(groups) == 1
    reasons = {row.reason for row in run.rejected}
    assert reasons == {"not_a_facebook_host", "not_a_group_url"}
    # Die Herkunft jeder verworfenen Zeile bleibt nachvollziehbar
    assert all(row.source_line is not None for row in run.rejected)


def test_fehlende_pflichtspalte_wird_gemeldet(config, tmp_path: Path) -> None:
    path = _write_csv(tmp_path, "name;mitglieder\nOhne URL;100\n")
    groups, run = import_seeds(config, paths=[path])

    assert groups == []
    assert run.errors
    assert "url" in run.errors[0].lower()


def test_arabischer_inhalt_bleibt_erhalten(config, tmp_path: Path) -> None:
    """Regressionstest fuer die Encoding-Falle unter Windows."""
    path = _write_csv(
        tmp_path,
        "url;name;description\n"
        "https://www.facebook.com/groups/666;سوريين في برلين;مجموعة للسوريين\n",
    )
    groups, _ = import_seeds(config, paths=[path])
    assert groups[0].name == "سوريين في برلين"


def test_kompletter_lauf_ueber_die_pipeline(config, tmp_path: Path) -> None:
    id_a, id_b = "482910573829104", "739201847362915"
    path = _write_csv(
        tmp_path,
        "url;name;member_count\n"
        f"https://www.facebook.com/groups/{id_a};Syrer in Berlin;20000\n"
        f"https://m.facebook.com/groups/{id_a}/?ref=share;Syrer in Berlin;20000\n"
        f"https://www.facebook.com/groups/{id_b};عرب في هامبورغ;5000\n"
        "https://example.com/kaputt;Ungueltig;\n",
    )
    groups, run = run_seed_import(config, paths=[path])

    assert run.rows_total == 4
    assert run.rows_valid == 3
    assert run.rows_rejected == 1
    assert run.groups_duplicate == 1      # die mobile Variante derselben Gruppe
    assert run.groups_new == 2
    assert run.finished_at is not None

    by_id = {g.group_id: g for g in groups}
    assert by_id[id_a].city == "Berlin"
    assert "syrians" in by_id[id_a].audience_tags
    assert by_id[id_b].city == "Hamburg"
    assert "arabs" in by_id[id_b].audience_tags
    # Scoring hat gegriffen und sortiert
    assert all(g.score is not None and g.score > 0 for g in groups)
    assert groups == sorted(groups, key=lambda g: -g.score)


def test_leeres_verzeichnis(config, tmp_path: Path) -> None:
    groups, run = import_seeds(config, paths=[])
    assert groups == []
    assert run.errors
