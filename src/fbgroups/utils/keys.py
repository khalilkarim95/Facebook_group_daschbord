"""Stabiler Schluessel fuer eine Suchanfrage.

Liegt bewusst ausserhalb des Speichers: Der Fixture-Provider bildet damit
Dateinamen, der Anfragespeicher seinen Primaerschluessel. Beide muessen
denselben Schluessel berechnen, sonst greift der Zwischenspeicher nicht.

Der Schluessel umfasst Anbieter, Anfragetext und die aufrufrelevanten
Parameter (z. B. Ergebnisanzahl). Aendert sich einer davon, ist es eine
andere Anfrage - und sie darf erneut Guthaben kosten.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def query_key(provider: str, query_text: str, params: dict[str, Any] | None = None) -> str:
    payload = json.dumps(
        {"provider": provider, "q": query_text, "params": params or {}},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
