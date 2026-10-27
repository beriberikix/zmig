from __future__ import annotations

from zmig.analyzers.hops import plan_hops, release_data


def minors(*xs: str) -> list[tuple[int, int]]:
    return [(int(x.split(".")[0]), int(x.split(".")[1])) for x in xs]


CROSSED_36_TO_44 = minors("3.7", "4.0", "4.1", "4.2", "4.3", "4.4")


def test_small_gap_needs_no_hops() -> None:
    assert plan_hops(minors("3.7"), {(3, 7)}, 2) == []
    assert plan_hops(minors("4.3", "4.4"), set(), 2) == []


def test_large_gap_stops_at_lts_then_fills() -> None:
    assert plan_hops(CROSSED_36_TO_44, {(3, 7)}, 2) == minors("3.7", "4.1", "4.3")


def test_no_anchor_fills_evenly() -> None:
    assert plan_hops(minors("4.0", "4.1", "4.2", "4.3", "4.4"), set(), 2) == minors("4.1", "4.3")


def test_release_data_is_well_formed() -> None:
    data = release_data()
    assert "3.7" in data["lts"] and "3.7" in data["boundaries"]
    assert int(data["max_minor_gap"]) >= 1
