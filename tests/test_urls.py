from __future__ import annotations

import pytest

from fbgroups.urls import (
    ParsedGroupUrl,
    UrlParseError,
    UrlRejectReason,
    beitragslinks,
    canonical_post_url,
    is_group_url,
    parse_group_url,
)

VALID_CASES = [
    # (Eingabe, erwartete group_id)
    ("https://www.facebook.com/groups/123456789", "123456789"),
    ("https://www.facebook.com/groups/123456789/", "123456789"),
    ("https://facebook.com/groups/123456789", "123456789"),
    ("http://facebook.com/groups/123456789", "123456789"),
    ("facebook.com/groups/123456789", "123456789"),
    ("www.facebook.com/groups/123456789", "123456789"),
    ("https://m.facebook.com/groups/123456789/?ref=share", "123456789"),
    ("https://web.facebook.com/groups/123456789/permalink/987654321/", "123456789"),
    ("https://mbasic.facebook.com/groups/123456789", "123456789"),
    ("https://de-de.facebook.com/groups/123456789", "123456789"),
    ("https://ar-ar.facebook.com/groups/123456789", "123456789"),
    ("https://fb.com/groups/123456789", "123456789"),
    ("https://www.facebook.com/groups/syrer.berlin", "syrer.berlin"),
    ("https://www.facebook.com/groups/Syrer.Berlin", "syrer.berlin"),
    ("https://www.facebook.com/groups/syrer-berlin-2024", "syrer-berlin-2024"),
    ("https://www.facebook.com/groups/arab_stuttgart/about", "arab_stuttgart"),
    ("https://www.facebook.com/groups/123456789?ref=bookmarks&sk=about", "123456789"),
    ("  https://www.facebook.com/groups/123456789  ", "123456789"),
    ("https://www.facebook.com/groups/123456789#anchor", "123456789"),
]

INVALID_CASES = [
    ("", UrlRejectReason.EMPTY),
    ("   ", UrlRejectReason.EMPTY),
    ("https://example.com/groups/123", UrlRejectReason.WRONG_HOST),
    ("https://facebook.evil.com/groups/123", UrlRejectReason.WRONG_HOST),
    ("https://www.facebook.com/pages/etwas", UrlRejectReason.NOT_A_GROUP),
    ("https://www.facebook.com/profile.php?id=123", UrlRejectReason.NOT_A_GROUP),
    ("https://www.facebook.com/groups", UrlRejectReason.NOT_A_GROUP),
    ("https://www.facebook.com/groups/", UrlRejectReason.NOT_A_GROUP),
    ("https://www.facebook.com/groups/feed", UrlRejectReason.RESERVED),
    ("https://www.facebook.com/groups/search", UrlRejectReason.RESERVED),
    ("nur ein text", UrlRejectReason.WRONG_HOST),
]


@pytest.mark.parametrize(("raw", "expected_id"), VALID_CASES)
def test_parse_valid_urls(raw: str, expected_id: str) -> None:
    result = parse_group_url(raw)
    assert isinstance(result, ParsedGroupUrl), f"{raw!r} sollte gueltig sein"
    assert result.group_id == expected_id
    assert result.canonical_url == f"https://www.facebook.com/groups/{expected_id}"


@pytest.mark.parametrize(("raw", "reason"), INVALID_CASES)
def test_parse_invalid_urls(raw: str, reason: str) -> None:
    result = parse_group_url(raw)
    assert isinstance(result, UrlParseError), f"{raw!r} sollte verworfen werden"
    assert result.reason == reason


def test_varianten_ergeben_dieselbe_kanonische_url() -> None:
    """Der Kern der Deduplizierung: verschiedene Schreibweisen, eine Identitaet."""
    variants = [
        "https://www.facebook.com/groups/123456789",
        "https://m.facebook.com/groups/123456789/",
        "facebook.com/groups/123456789?ref=share",
        "https://web.facebook.com/groups/123456789/about",
    ]
    canonical = {parse_group_url(v).canonical_url for v in variants}
    assert len(canonical) == 1


def test_is_group_url() -> None:
    assert is_group_url("https://www.facebook.com/groups/123")
    assert not is_group_url("https://www.facebook.com/pages/123")


# --- Beitragsadressen aus einer Gruppenseite -------------------------------
def test_beitragslinks_erkennt_die_ueblichen_formen() -> None:
    """Facebook schreibt dieselbe Stelle je nach Ansicht verschieden."""
    hrefs = [
        "#",
        "/groups/123/user/9/",
        "/groups/123/posts/987/?__cft__[0]=AZx&__tn__=R",
        "/groups/123/?multi_permalinks=555&notif_id=1",
        "https://www.facebook.com/groups/123/permalink/777/",
        "/groups/123/posts/pfbid02abcXYZ/",
        None,
    ]

    assert beitragslinks(hrefs, "123") == [
        "https://www.facebook.com/groups/123/posts/987/",
        "https://www.facebook.com/groups/123/posts/555/",
        "https://www.facebook.com/groups/123/posts/777/",
        "https://www.facebook.com/groups/123/posts/pfbid02abcXYZ/",
    ]


def test_derselbe_beitrag_nur_einmal() -> None:
    """Die wechselnden Parameter machen aus einem Beitrag keine zwei.

    ``__cft__`` und ``__tn__`` aendern sich bei jedem Laden. Blieben sie
    stehen, saehe derselbe Beitrag bei jedem Durchgang neu aus - und ein
    bereits kommentierter galte als unkommentiert.
    """
    hrefs = [
        "/groups/123/posts/987/?__cft__[0]=AZx",
        "/groups/123/posts/987/?__cft__[0]=BYq&__tn__=R",
        "https://www.facebook.com/groups/123/posts/987/",
    ]

    assert beitragslinks(hrefs, "123") == ["https://www.facebook.com/groups/123/posts/987/"]


def test_eine_pfbid_kennung_wird_nicht_verworfen() -> None:
    """Seit 2022 vergibt Facebook auch nicht-numerische Beitragskennungen."""
    assert (
        canonical_post_url("/groups/123/posts/pfbid02abcXYZ/", "123")
        == "https://www.facebook.com/groups/123/posts/pfbid02abcXYZ/"
    )


def test_ohne_beitragskennung_bleiben_die_parameter_weg() -> None:
    """Was nicht als Beitrag zu erkennen ist, wird wenigstens stabil."""
    assert (
        canonical_post_url("https://www.facebook.com/groups/123/?ref=x&notif=2", "123")
        == "https://www.facebook.com/groups/123/"
    )
