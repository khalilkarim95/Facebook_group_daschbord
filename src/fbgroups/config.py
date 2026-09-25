"""Laden und Validieren der YAML-Konfiguration.

Die Konfiguration besteht aus zwei Dateien: ``settings.yaml`` (verpflichtend)
und ``textvorlagen.yaml`` (optional).

Eine ``.env`` wird seit dem 25.09.2026 nicht mehr gelesen: Ihre letzten
Schluessel (``APP_BASE_URL``, ``EVENTS_TOKEN``, ``UEBERSICHT_TOKEN``) gehoerten
zum Tracking, und das ist entfernt.

Bis zum 20.09.2026 lagen hier fuenf weitere Dateien: ``audiences.yaml``,
``cities.yaml``, ``categories.yaml``, ``queries.yaml`` und ``providers.yaml``.
Sie gehoerten zur Entdeckungsschicht - Suche, Klassifikation, Anbieter -, und
die ist entfernt. Zielgruppe, Stadt und Kategorie einer Gruppe stehen seither
allein im Bestand (``groups``); sie werden nicht mehr aus Begriffslisten
abgeleitet, sondern von Hand gepflegt.

Alle Dateien werden explizit als UTF-8 gelesen. Das ist unter Windows nicht die
Voreinstellung und wuerde die arabischen Begriffe sonst zerstoeren.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIRNAME = "config"


def project_root() -> Path:
    """Wurzelverzeichnis des Projekts (drei Ebenen ueber dieser Datei)."""
    return Path(__file__).resolve().parents[2]


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Konfigurationsdatei fehlt: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Konfiguration muss ein Mapping sein: {path}")
    return data


@dataclass(frozen=True)
class AppConfig:
    root: Path
    settings: dict[str, Any]
    # Beitragsvorlagen je Sprache. Voreingestellt leer, damit eine Konfiguration
    # ohne die Datei weiterhin laedt - ohne Vorlagen faellt nur die
    # Personalisierung aus, nicht der Rest des Programms.
    textvorlagen: dict[str, Any] = field(default_factory=dict)

    # -- Pfade ---------------------------------------------------------
    def path(self, key: str) -> Path:
        rel = self.settings.get("paths", {}).get(key)
        if rel is None:
            raise KeyError(f"Unbekannter Pfadschluessel: {key}")
        return self.root / rel

    # -- Einzelwerte ----------------------------------------------------
    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.settings
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


def load_config(root: Path | None = None) -> AppConfig:
    """Liest ``settings.yaml`` und - falls vorhanden - ``textvorlagen.yaml``."""
    base = Path(root) if root else project_root()
    cfg_dir = base / CONFIG_DIRNAME

    settings = _read_yaml(cfg_dir / "settings.yaml")

    # Fehlt die Datei, bleibt es beim leeren Mapping: ``_read_yaml`` wirft
    # nicht, und ``vorlagen.waehle_vorlage`` meldet den Mangel dort, wo er
    # jemanden interessiert - beim Fuellen, mit Namen der Sprache.
    vorlagen_datei = cfg_dir / "textvorlagen.yaml"
    textvorlagen = _read_yaml(vorlagen_datei) if vorlagen_datei.exists() else {}

    return AppConfig(root=base, settings=settings, textvorlagen=textvorlagen)


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Zwischengespeicherte Standardkonfiguration."""
    return load_config()
