"""Querschnittsfunktionen: Anfrageschluessel und Taktbremse.

Der Zwischenspeicher liegt bewusst nicht mehr hier, sondern in
``storage/query_cache.py``: Er ist dauerhaft und gehoert damit zur Persistenz.
"""

from fbgroups.utils.keys import query_key
from fbgroups.utils.rate_limit import RateLimiter

__all__ = ["RateLimiter", "query_key"]
