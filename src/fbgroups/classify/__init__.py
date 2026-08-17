"""Regelbasierte Klassifikation von Gruppen (Zielgruppe, Stadt, Kategorie)."""

from fbgroups.classify.audience import classify_audience
from fbgroups.classify.category import classify_category
from fbgroups.classify.city import classify_city

__all__ = ["classify_audience", "classify_category", "classify_city"]
