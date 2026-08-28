"""The run pipeline, driven through its interface with no network.

These tests exist because the sequence in :func:`sos.run.refresh` is held
together by ordering constraints that no signature states. Each of the first
two below fails loudly if its constraint is broken, and passed silently before
the pipeline had a module to be tested through.
"""

from __future__ import annotations

from datetime import date

import pytest

from conftest import FakeSource
from sos import run as run_module
from sos.config import Brand, Config, Market
from sos.datasource.base import DataSourceError
from sos.store import load_store, store_path

JAN = date(2025, 1, 1)
DEC = date(2025, 12, 1)
OCT = date(2025, 10, 1)

PULLED_AT = "2026-01-15T00:00:00+00:00"


def _volumes(keyword: str, base: int, step: int, skip_months=()) -> list:
    """Twelve months of steadily rising volume for one keyword."""
    return [
        {"keyword": keyword, "year": 2025, "month": month, "search_volume": base + step * (month - 1)}
        for month in range(1, 13)
        if month not in skip_months
    ]


def _rows(initech_skips=()) -> list:
    """The canned response.

    'acme' and 'acme app' return identical volumes in every month — the shape
    Google Ads produces when it has merged two close variants into one figure.
    """
    return (
        _volumes("acme", 1000, 100)
        + _volumes("acme app", 1000, 100)
        + _volumes("globex", 900, 90)
        + _volumes("initech", 800, 80, skip_months=initech_skips)
    )


def _config(with_initech: bool = True) -> Config:
    brands = [
        Brand(name="Acme", keywords=["acme", "acme app"], is_own_brand=True),
        Brand(name="Globex", keywords=["globex"]),
    ]
    if with_initech:
        brands.append(Brand(name="Initech", keywords=["initech"]))
    return Config(
        market=Market(name="US", location_code=2840, language_code="en"),
        brands=brands,
        smoothing_windows=[3, 12],
    )


def _volume_at(frame, brand: str, month: str):
    row = frame[(frame["brand"] == brand) & (frame["date"].astype(str).str.startswith(month))]
    return float(row.iloc[0]["raw_volume"])


# --------------------------------------------------------------------------
# Ordering constraint 1: the store is read before it is written
# --------------------------------------------------------------------------


def test_a_short_refresh_keeps_the_grouping_an_earlier_run_decided(tmp_path):
    """The constraint: `counted_keywords` must read the store *before* upsert.

    A twelve-month run has enough evidence to see that Google merged 'acme'
    and 'acme app', and counts them once. A later three-month refresh is too
    short to make that call itself, so it has to recover the earlier decision
    from the store — from the very column `upsert` is about to overwrite.

    Lose that and 'acme app' is counted again, doubling Acme's volume and
    putting a step change in its history that looks like real demand.
    """
    source = FakeSource(_rows())

    run_module.refresh(_config(), source, tmp_path, JAN, DEC, pulled_at=PULLED_AT)
    first = load_store(tmp_path)
    assert _volume_at(first, "Acme", "2025-12") == 2100.0

    run_module.refresh(_config(), source, tmp_path, OCT, DEC, pulled_at=PULLED_AT)
    second = load_store(tmp_path)

    assert _volume_at(second, "Acme", "2025-12") == 2100.0, "'acme app' was counted twice"
    acme = second[second["brand"] == "Acme"].iloc[-1]
    assert acme["keywords"] == "acme"


def test_the_fetch_asks_for_the_range_it_was_given(tmp_path):
    source = FakeSource(_rows())
    run_module.refresh(_config(), source, tmp_path, OCT, DEC, pulled_at=PULLED_AT)

    assert source.calls[-1]["date_from"] == "2025-10-01"
    assert source.calls[-1]["date_to"] == "2025-12-01"
    assert source.calls[-1]["location_code"] == 2840


# --------------------------------------------------------------------------
# Ordering constraint 2: the active brand set goes to the write
# --------------------------------------------------------------------------


def test_a_dropped_competitor_stops_inflating_the_category_total(tmp_path):
    """The constraint: `upsert` must receive the active brand set.

    Removing a competitor from the config does not remove its stored rows —
    nothing in an ordinary refresh collides with their key. Left behind, they
    keep contributing to the category total and understate every brand that
    remains.
    """
    source = FakeSource(_rows())
    run_module.refresh(_config(), source, tmp_path, JAN, DEC, pulled_at=PULLED_AT)

    before = load_store(tmp_path)
    assert "Initech" in set(before["brand"])
    assert _volume_at(before, "Acme", "2025-12") == 2100.0
    assert float(before[before["brand"] == "Acme"].iloc[-1]["category_total_volume"]) == 5670.0

    result = run_module.refresh(
        _config(with_initech=False), source, tmp_path, JAN, DEC, pulled_at=PULLED_AT
    )

    assert "Initech" not in set(result.frame["brand"])
    assert result.stale_brands == ["Initech"]
    assert any("Initech" in w for w in result.warnings)

    acme = result.frame[result.frame["brand"] == "Acme"].iloc[-1]
    assert float(acme["category_total_volume"]) == 3990.0, "Initech still in the denominator"
    assert float(acme["sos_pct"]) == pytest.approx(2100 / 3990 * 100)


# --------------------------------------------------------------------------
# The rest of the interface
# --------------------------------------------------------------------------


def test_the_same_run_twice_leaves_the_store_byte_identical(tmp_path):
    source = FakeSource(_rows())

    run_module.refresh(_config(), source, tmp_path, JAN, DEC, pulled_at=PULLED_AT)
    once = store_path(tmp_path).read_bytes()

    run_module.refresh(_config(), source, tmp_path, JAN, DEC, pulled_at=PULLED_AT)
    twice = store_path(tmp_path).read_bytes()

    assert once == twice


def test_an_empty_response_raises_and_leaves_the_store_untouched(tmp_path):
    source = FakeSource(_rows())
    run_module.refresh(_config(), source, tmp_path, JAN, DEC, pulled_at=PULLED_AT)
    before = store_path(tmp_path).read_bytes()

    # A window the canned rows do not cover, so the fake returns nothing.
    with pytest.raises(DataSourceError):
        run_module.refresh(
            _config(), source, tmp_path, date(2024, 1, 1), date(2024, 3, 1), pulled_at=PULLED_AT
        )

    assert store_path(tmp_path).read_bytes() == before


def test_an_empty_response_on_a_first_run_writes_no_store(tmp_path):
    with pytest.raises(DataSourceError):
        run_module.refresh(_config(), FakeSource([]), tmp_path, JAN, DEC)

    assert not store_path(tmp_path).exists()


def test_warnings_are_returned_rather_than_printed(tmp_path, capsys):
    source = FakeSource(_rows(initech_skips=(6,)))
    result = run_module.refresh(_config(), source, tmp_path, JAN, DEC, pulled_at=PULLED_AT)

    assert any("Initech" in w and "no data" in w for w in result.warnings)
    assert all(isinstance(w, str) for w in result.warnings)
    assert capsys.readouterr().out == ""


def test_the_result_reports_what_was_fetched(tmp_path):
    source = FakeSource(_rows())
    result = run_module.refresh(_config(), source, tmp_path, OCT, DEC, pulled_at=PULLED_AT)

    assert result.months_returned == 3
    assert result.rows_fetched == 12  # 4 keywords x 3 months
    assert result.store_path == store_path(tmp_path)
    assert result.stale_brands == []
