from __future__ import annotations

from fbgroups.dedupe import deduplicate_exact, find_duplicate_suspects
from fbgroups.models import Group, Provenance, SourceType


def make_group(group_id: str, name: str = "", url: str | None = None) -> Group:
    return Group(
        group_id=group_id,
        url_canonical=url or f"https://www.facebook.com/groups/{group_id}",
        name=name,
        sources=[Provenance(source_type=SourceType.MANUAL_SEED, source_ref="test.csv")],
    )


def test_exakte_dublette_wird_zusammengefuehrt() -> None:
    groups = [make_group("111", "Syrer Berlin"), make_group("111", "Syrer Berlin")]
    unique, duplicates = deduplicate_exact(groups)
    assert len(unique) == 1
    assert duplicates == 1
    assert unique[0].times_seen == 2


def test_verschiedene_gruppen_bleiben_getrennt() -> None:
    groups = [make_group("111", "A"), make_group("222", "B"), make_group("333", "C")]
    unique, duplicates = deduplicate_exact(groups)
    assert len(unique) == 3
    assert duplicates == 0


def test_merge_ergaenzt_fehlende_angaben() -> None:
    first = make_group("111", "")
    second = make_group("111", "Syrer Berlin")
    second.member_count_hint = 5000

    unique, _ = deduplicate_exact([first, second])
    assert unique[0].name == "Syrer Berlin"
    assert unique[0].member_count_hint == 5000


def test_merge_sammelt_url_varianten() -> None:
    first = make_group("111", "A", "https://www.facebook.com/groups/111")
    second = make_group("111", "A", "https://m.facebook.com/groups/111")

    unique, _ = deduplicate_exact([first, second])
    assert "https://m.facebook.com/groups/111" in unique[0].url_variants


def test_merge_ueberschreibt_vorhandene_werte_nicht() -> None:
    first = make_group("111", "Originalname")
    second = make_group("111", "Anderer Name")

    unique, _ = deduplicate_exact([first, second])
    assert unique[0].name == "Originalname"


def test_aehnliche_namen_werden_nur_gemeldet() -> None:
    """Aehnlichkeit ist ein Verdacht, keine automatische Zusammenfuehrung."""
    groups = [
        make_group("111", "Syrer in Berlin Community"),
        make_group("222", "Syrer in Berlin Community 2"),
        make_group("333", "Jobs in Hamburg fuer alle"),
    ]
    suspects = find_duplicate_suspects(groups, threshold=85)

    assert len(suspects) == 1
    assert {suspects[0].group_id_a, suspects[0].group_id_b} == {"111", "222"}
    # Beide Gruppen bleiben eigenstaendig erhalten
    unique, duplicates = deduplicate_exact(groups)
    assert len(unique) == 3
    assert duplicates == 0


def test_kurze_namen_erzeugen_keinen_verdacht() -> None:
    groups = [make_group("111", "Berlin"), make_group("222", "Berlin")]
    assert find_duplicate_suspects(groups, threshold=85, min_name_length=8) == []
