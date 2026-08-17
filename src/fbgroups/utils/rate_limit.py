"""Taktbremse zwischen zwei Anfragen an denselben Dienst.

Der kostenlose Brave-Tarif erlaubt etwa eine Anfrage pro Sekunde. Statt in ein
429 zu laufen und nachtraeglich zu warten, halten wir den Abstand von vornherein
ein - das ist gegenueber dem Anbieter das korrekte Verhalten.
"""

from __future__ import annotations

import time


class RateLimiter:
    def __init__(self, min_interval_seconds: float = 0.0) -> None:
        self.min_interval = max(float(min_interval_seconds), 0.0)
        self._last_call: float | None = None

    def wait(self) -> float:
        """Wartet, bis der Mindestabstand eingehalten ist. Liefert die Wartezeit."""
        if self.min_interval <= 0:
            self._last_call = time.monotonic()
            return 0.0

        now = time.monotonic()
        if self._last_call is None:
            self._last_call = now
            return 0.0

        elapsed = now - self._last_call
        remaining = self.min_interval - elapsed
        if remaining > 0:
            time.sleep(remaining)
            self._last_call = time.monotonic()
            return remaining

        self._last_call = now
        return 0.0
