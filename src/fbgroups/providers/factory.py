"""Aufloesung eines Providernamens aus der Konfiguration zu einer Instanz.

Nur dieses Modul kennt den Zusammenhang zwischen Konfigurationseintrag,
Umgebungsvariable und Implementierung. Der Rest der Anwendung arbeitet
ausschliesslich mit dem Protokoll ``SearchProvider``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# Import der Implementierungen, damit der Registry-Dekorator ausgefuehrt wird.
from fbgroups.providers import brave, fixture, serper  # noqa: F401
from fbgroups.providers.base import (
    ProviderState,
    ProviderStatus,
    SearchProvider,
    get_provider_class,
)


def load_provider_config(root: Path) -> dict[str, Any]:
    path = root / "config" / "providers.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Konfigurationsdatei fehlt: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _api_key_for(settings: dict[str, Any], root: Path) -> str | None:
    env_name = settings.get("api_key_env")
    if not env_name:
        return None
    load_dotenv(root / ".env", override=False)
    return os.environ.get(env_name) or None


def build_provider(
    name: str,
    provider_config: dict[str, Any],
    root: Path,
) -> SearchProvider:
    """Erzeugt eine Providerinstanz nach Namen."""
    providers = provider_config.get("providers", {}) or {}
    if name not in providers:
        raise KeyError(
            f"Provider {name!r} steht nicht in config/providers.yaml. "
            f"Vorhanden: {sorted(providers)}"
        )

    settings = providers[name] or {}
    if not settings.get("enabled", True):
        raise ValueError(f"Provider {name!r} ist in der Konfiguration deaktiviert.")

    # Relative Pfade beziehen sich auf die Projektwurzel.
    if "fixtures_dir" in settings:
        settings = {**settings, "fixtures_dir": root / settings["fixtures_dir"]}

    provider_class = get_provider_class(name)
    return provider_class(settings, api_key=_api_key_for(settings, root))


def check_all_providers(
    provider_config: dict[str, Any],
    root: Path,
) -> list[tuple[str, ProviderStatus]]:
    """Preflight ueber alle konfigurierten Provider - ohne Kontingentverbrauch."""
    results: list[tuple[str, ProviderStatus]] = []

    for name, settings in (provider_config.get("providers", {}) or {}).items():
        if not (settings or {}).get("enabled", True):
            results.append(
                (
                    name,
                    ProviderStatus(
                        available=False,
                        state=ProviderState.UNAVAILABLE,
                        message="in der Konfiguration deaktiviert",
                    ),
                )
            )
            continue

        try:
            provider = build_provider(name, provider_config, root)
            results.append((name, provider.check_availability()))
        except Exception as exc:  # noqa: BLE001 - Preflight darf nie abbrechen
            results.append(
                (
                    name,
                    ProviderStatus(
                        available=False,
                        state=ProviderState.UNAVAILABLE,
                        message=str(exc),
                    ),
                )
            )

    return results


def resolve_active_provider(
    provider_config: dict[str, Any],
    root: Path,
    override: str | None = None,
) -> SearchProvider:
    """Waehlt den aktiven Provider; bei Ausfall greift die Fallback-Kette."""
    candidates = [override] if override else []
    if not candidates:
        candidates = [provider_config.get("active", "fixture")]
        candidates.extend(provider_config.get("fallback_chain", []) or [])

    errors: list[str] = []
    for name in candidates:
        try:
            provider = build_provider(name, provider_config, root)
        except (KeyError, ValueError) as exc:
            errors.append(f"{name}: {exc}")
            continue

        status = provider.check_availability()
        if status.available:
            return provider
        errors.append(f"{name}: {status.message}")

    raise RuntimeError(
        "Kein verwendbarer Search-Provider gefunden.\n  " + "\n  ".join(errors)
    )
