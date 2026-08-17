from __future__ import annotations

import pytest

from fbgroups.config import AppConfig, load_config


@pytest.fixture(scope="session")
def config() -> AppConfig:
    """Echte Projektkonfiguration - die Tests pruefen auch deren Inhalt."""
    return load_config()
