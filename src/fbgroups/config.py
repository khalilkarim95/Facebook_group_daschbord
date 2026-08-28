"""Laden und Validieren der YAML-Konfiguration.

Alle Dateien werden explizit als UTF-8 gelesen. Das ist unter Windows nicht die
Voreinstellung und wuerde die arabischen Begriffe sonst zerstoeren.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

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
class Audience:
    id: str
    label_de: str
    priority: int
    phase: int
    terms: dict[str, list[str]]
    # Optionales Kuerzel fuer Tracking-Codes (z. B. "SYR"). Ohne Angabe
    # entstehen die ersten drei Buchstaben der Kennung.
    code: str = ""
    # Anrede fuer {zielgruppe} in Beitragsvorlagen - je Sprache eine eigene.
    # ``label_de`` taugt dafuer nicht: Es lautet "Syrer in Deutschland" und
    # ergaebe in einer Stadtvorlage "Syrer in Deutschland in Bonn".
    label_ar: str = ""
    label_kurz_de: str = ""

    def anrede(self, sprache: str) -> str:
        """Die Anrede in dieser Sprache, mit Rueckfall auf das lange Label.

        Kein Erfinden: Fehlt ``label_ar``, steht dort das deutsche Label - gut
        sichtbar falsch, statt still leer. Ein leerer Platzhalter mitten im
        Satz faellt erst auf, wenn der Beitrag in der Gruppe steht.
        """
        if sprache == "ar":
            return self.label_ar or self.label_kurz_de or self.label_de
        return self.label_kurz_de or self.label_de

    def all_terms(self) -> list[str]:
        return [t for terms in self.terms.values() for t in terms]


@dataclass(frozen=True)
class City:
    id: str
    name_de: str
    name_ar: str
    bundesland: str
    population: int
    phase: int
    aliases: list[str] = field(default_factory=list)
    code: str = ""      # optionales Kuerzel fuer Tracking-Codes, z. B. "BER"

    def anzeige(self, sprache: str) -> str:
        """Der Stadtname in dieser Sprache, mit Rueckfall auf den deutschen."""
        if sprache == "ar":
            return self.name_ar or self.name_de
        return self.name_de

    def all_names(self) -> list[str]:
        return [self.name_de, self.name_ar, *self.aliases]


@dataclass(frozen=True)
class Category:
    id: str
    label_de: str
    terms: dict[str, list[str]]

    def all_terms(self) -> list[str]:
        return [t for terms in self.terms.values() for t in terms]


@dataclass(frozen=True)
class AppConfig:
    root: Path
    settings: dict[str, Any]
    audiences: dict[str, Audience]
    cities: dict[str, City]
    categories: list[Category]
    queries: dict[str, Any]
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

    # -- Gefilterte Sichten --------------------------------------------
    def audiences_for_phase(self, phase: int = 1) -> list[Audience]:
        return sorted(
            (a for a in self.audiences.values() if a.phase <= phase),
            key=lambda a: (a.priority, a.id),
        )

    def cities_for_phase(self, phase: int = 1) -> list[City]:
        return [c for c in self.cities.values() if c.phase <= phase]

    # -- Einzelwerte ----------------------------------------------------
    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.settings
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


def lade_umgebung(root: Path) -> None:
    """Uebernimmt ``.env`` in die Prozessumgebung.

    ``override=False``: Eine echte Umgebungsvariable schlaegt die Datei. Im
    Betrieb wird die Umgebung gesetzt, ``.env`` ist die Bequemlichkeit fuer den
    eigenen Rechner - und darf den Betrieb nicht ueberstimmen.

    Der Aufruf steht hier und nicht nur bei den Suchanbietern: Zuvor las
    ausschliesslich die Schluesselabfrage in ``providers/factory.py`` die
    Datei. Kein ``campaign``-Befehl kam dort vorbei, und ein dort eingetragenes
    ``APP_BASE_URL`` blieb wirkungslos - die Tracking-Links zeigten still
    weiter auf ``localhost``, obwohl ``.env.example`` genau diesen Eintrag
    vorschlaegt. Ein Link, der auf den eigenen Rechner zeigt, ist in einem
    Beitrag wertlos, und man sieht es ihm nicht an.
    """
    load_dotenv(root / ".env", override=False)


def load_config(root: Path | None = None) -> AppConfig:
    """Liest alle Konfigurationsdateien ein."""
    base = Path(root) if root else project_root()
    cfg_dir = base / CONFIG_DIRNAME

    # Vor den YAML-Dateien: Werte aus .env sollen beim Lesen der Konfiguration
    # bereits zur Verfuegung stehen (z. B. APP_BASE_URL).
    lade_umgebung(base)

    settings = _read_yaml(cfg_dir / "settings.yaml")

    raw_audiences = _read_yaml(cfg_dir / "audiences.yaml").get("audiences", {})
    audiences = {
        aid: Audience(
            id=aid,
            label_de=data.get("label_de", aid),
            priority=int(data.get("priority", 99)),
            phase=int(data.get("phase", 99)),
            terms={k: list(v) for k, v in (data.get("terms") or {}).items()},
            code=str(data.get("code", "")),
            label_ar=str(data.get("label_ar", "")),
            label_kurz_de=str(data.get("label_kurz_de", "")),
        )
        for aid, data in raw_audiences.items()
    }

    raw_cities = _read_yaml(cfg_dir / "cities.yaml").get("cities", [])
    cities = {
        data["id"]: City(
            id=data["id"],
            name_de=data.get("name_de", data["id"]),
            name_ar=data.get("name_ar", ""),
            bundesland=data.get("bundesland", ""),
            population=int(data.get("population", 0)),
            phase=int(data.get("phase", 99)),
            aliases=list(data.get("aliases") or []),
            code=str(data.get("code", "")),
        )
        for data in raw_cities
    }

    raw_categories = _read_yaml(cfg_dir / "categories.yaml").get("categories", [])
    categories = [
        Category(
            id=data["id"],
            label_de=data.get("label_de", data["id"]),
            terms={k: list(v) for k, v in (data.get("terms") or {}).items()},
        )
        for data in raw_categories
    ]

    queries = _read_yaml(cfg_dir / "queries.yaml")

    # Fehlt die Datei, bleibt es beim leeren Mapping: ``_read_yaml`` wirft
    # nicht, und ``vorlagen.waehle_vorlage`` meldet den Mangel dort, wo er
    # jemanden interessiert - beim Fuellen, mit Namen der Sprache.
    vorlagen_datei = cfg_dir / "textvorlagen.yaml"
    textvorlagen = _read_yaml(vorlagen_datei) if vorlagen_datei.exists() else {}

    return AppConfig(
        root=base,
        settings=settings,
        audiences=audiences,
        cities=cities,
        categories=categories,
        queries=queries,
        textvorlagen=textvorlagen,
    )


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Zwischengespeicherte Standardkonfiguration."""
    return load_config()
