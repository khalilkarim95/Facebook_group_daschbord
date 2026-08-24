"""Adapter fuers Absetzen eines Beitrags.

``basis`` haelt den Vertrag, jedes weitere Modul eine Umsetzung. Die Importe
unten sind das, was die Adapter in die Registry eintraegt - ohne sie kennte
``baue_veroeffentlicher`` keinen einzigen Namen.

Ein neuer Adapter (etwa einer, der einen Browser steuert) besteht aus:

  1. einer Datei in diesem Paket,
  2. einer Klasse mit ``name``, ``beschreibung`` und ``veroeffentliche``,
  3. ``@register_veroeffentlicher("...")`` darueber,
  4. einer Zeile hier.

Weder ``worker.py`` noch die Kommandozeile noch die Uebersicht aendern sich
dabei. Genau dafuer ist der Vertrag da.
"""

from __future__ import annotations

from fbgroups.marketing.veroeffentlicher.assistiert import AssistierterVeroeffentlicher
from fbgroups.marketing.veroeffentlicher.basis import (
    Ergebnis,
    UnbekannterVeroeffentlicher,
    Veroeffentlicher,
    baue_veroeffentlicher,
    register_veroeffentlicher,
    verfuegbare,
)

__all__ = [
    "AssistierterVeroeffentlicher",
    "Ergebnis",
    "UnbekannterVeroeffentlicher",
    "Veroeffentlicher",
    "baue_veroeffentlicher",
    "register_veroeffentlicher",
    "verfuegbare",
]
