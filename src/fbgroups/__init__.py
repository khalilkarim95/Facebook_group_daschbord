"""Facebook Groups Finder - Germany.

Phase 1: Import oeffentlich auffindbarer Gruppen-URLs aus manuellen Seeds,
Normalisierung, Deduplizierung, Klassifikation, Scoring und Export.

Grenzen des Projekts (bewusst im Code verankert, siehe models.Group):
Es werden keine Mitglieder- oder Admindaten, keine Profil-URLs, keine
Beitragsinhalte und keine Kontaktdaten erfasst. Es findet kein automatisches
Posten und kein automatisches Messaging statt.
"""

__version__ = "0.1.0"
