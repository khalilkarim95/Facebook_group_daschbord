from __future__ import annotations

from fbgroups.textnorm import contains_term, has_arabic, normalize, normalize_arabic


def test_umlaute_werden_aufgeloest() -> None:
    assert normalize("München") == "muenchen"
    assert normalize("Köln") == "koeln"
    assert normalize("Düsseldorf") == "duesseldorf"


def test_arabische_varianten_werden_vereinheitlicht() -> None:
    # Alef mit Hamza und blankes Alef muessen gleich behandelt werden
    assert normalize_arabic("ألمانيا") == normalize_arabic("المانيا")


def test_bestimmter_artikel_wird_getroffen() -> None:
    """Im Arabischen haengt der Artikel am Wort - Teilstring ist hier korrekt."""
    assert contains_term(normalize("مجموعة السوريين في برلين"), "سوريين")
    assert contains_term(normalize("تجمع العرب في هامبورغ"), "عرب")


def test_lateinische_wortgrenze_wird_beachtet() -> None:
    assert contains_term(normalize("Araber in Berlin"), "Arab")
    assert contains_term(normalize("Arabische Community"), "arabisch")
    # "rab" steht mitten im Wort und darf nicht anschlagen
    assert not contains_term(normalize("Araber in Berlin"), "rab")


def test_has_arabic() -> None:
    assert has_arabic("سوريين")
    assert not has_arabic("Syrer Berlin")
    assert has_arabic("Syrer سوريين")


def test_leerer_text() -> None:
    assert normalize("") == ""
    assert not contains_term("", "Berlin")
    assert not contains_term(normalize("Berlin"), "")
