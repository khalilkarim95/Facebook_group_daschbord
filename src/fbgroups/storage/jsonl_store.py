"""Rohprotokoll eines Laufs als JSONL/JSON.

Jeder Lauf erhaelt ein eigenes Verzeichnis unter ``data/runs/<run_id>/``.
Damit bleibt jeder Import reproduzierbar nachvollziehbar, auch nachdem die
Gruppen in SQLite zusammengefuehrt wurden.
"""

from __future__ import annotations

from pathlib import Path

from fbgroups.models import Group, ImportRun


def _write_jsonl(path: Path, items: list) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(item.model_dump_json() + "\n")


def save_run_artifacts(runs_dir: Path, run: ImportRun, groups: list[Group]) -> Path:
    """Schreibt Laufprotokoll, Gruppen und Ausschuss in ein Laufverzeichnis."""
    run_dir = runs_dir / run.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    with (run_dir / "run.json").open("w", encoding="utf-8") as fh:
        fh.write(run.model_dump_json(indent=2))

    _write_jsonl(run_dir / "groups.jsonl", groups)
    _write_jsonl(run_dir / "rejected.jsonl", run.rejected)
    _write_jsonl(run_dir / "duplicate_suspects.jsonl", run.duplicate_suspects)

    return run_dir
