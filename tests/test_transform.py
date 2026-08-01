"""Tests for the keyword-to-share pipeline.

This is where a bug does the most damage: a silently doubled brand or a
missing month treated as zero produces numbers that look entirely plausible
and are entirely wrong.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sos.config import Brand, Config, Market
from sos.transform import (
    add_rolling_averages,
    aggregate_to_brands,
    build_brand_frame,
    category_set_warnings,
    compute_shares,
    detect_grouped_keywords,
    dropped_keywords,
    missing_brand_months,
    rows_to_frame,
)


def _rows(keyword: str, volumes: list, start_month: int = 1, year: int = 2025) -> list:
    return [
        {"keyword": keyword, "year": year, "month": start_month + i, "search_volume": v}
        for i, v in enumerate(volumes)
    ]


def _config(*brands: Brand) -> Config:
    return Config(
        market=Market(name="US", location_code=2840, language_code="en"),
        brands=list(brands),
        smoothing_windows=[3],
    )


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def test_rows_to_frame_builds_month_start_dates(sample_rows):
    frame = rows_to_frame(sample_rows)

    assert set(frame.columns) == {"keyword", "date", "search_volume"}
    assert frame["date"].min() == pd.Timestamp("2025-01-01")
    assert frame["date"].max() == pd.Timestamp("2025-04-01")
    assert (frame["date"].dt.day == 1).all()


def test_rows_to_frame_preserves_nulls_as_nan(sample_rows):
    """A missing volume is unknown, not zero — the two mean different things."""
    frame = rows_to_frame(sample_rows)
    initech_april = frame[(frame["keyword"] == "initech") & (frame["date"] == "2025-04-01")]

    assert len(initech_april) == 1
    assert pd.isna(initech_april.iloc[0]["search_volume"])


def test_rows_to_frame_handles_empty_input():
    frame = rows_to_frame([])
    assert frame.empty
    assert list(frame.columns) == ["keyword", "date", "search_volume"]


def test_rows_to_frame_deduplicates_repeated_keyword_months():
    rows = _rows("acme", [100, 200]) + _rows("acme", [999], start_month=1)
    frame = rows_to_frame(rows)

    assert len(frame) == 2
    assert frame[frame["date"] == "2025-01-01"].iloc[0]["search_volume"] == 999


# --------------------------------------------------------------------------
# Grouped-keyword guard
# --------------------------------------------------------------------------


def test_detects_grouped_keywords_within_a_brand(sample_rows, sample_config):
    frame = rows_to_frame(sample_rows)
    groups = detect_grouped_keywords(frame, sample_config)

    assert "Acme" in groups
    assert groups["Acme"][0]["kept"] == "acme"
    assert groups["Acme"][0]["dropped"] == ["acme app"]


def test_grouped_keyword_is_counted_once_not_twice(sample_rows, sample_config):
    """The whole point: summing a grouped pair doubles the brand's volume."""
    frame = rows_to_frame(sample_rows)
    groups = detect_grouped_keywords(frame, sample_config)
    brands = aggregate_to_brands(frame, sample_config, exclude_keywords=dropped_keywords(groups))

    january = brands[(brands["brand"] == "Acme") & (brands["date"] == "2025-01-01")]
    assert january.iloc[0]["raw_volume"] == 1000  # not 2000


def test_identical_volumes_across_different_brands_are_not_grouped():
    """Two brands with the same volume is a coincidence, not a merge."""
    config = _config(
        Brand(name="Acme", keywords=["acme"], is_own_brand=True),
        Brand(name="Globex", keywords=["globex"]),
    )
    frame = rows_to_frame(_rows("acme", [100, 200, 300]) + _rows("globex", [100, 200, 300]))

    assert detect_grouped_keywords(frame, config) == {}


def test_single_overlapping_month_is_not_enough_evidence():
    config = _config(
        Brand(name="Acme", keywords=["acme", "acme app"], is_own_brand=True),
        Brand(name="Globex", keywords=["globex"]),
    )
    frame = rows_to_frame(
        _rows("acme", [100, 200, 300])
        + _rows("acme app", [100, None, None])
        + _rows("globex", [50, 50, 50])
    )

    assert detect_grouped_keywords(frame, config) == {}


def test_all_zero_keywords_are_dead_not_grouped():
    config = _config(
        Brand(name="Acme", keywords=["acme xyz", "acme qrs"], is_own_brand=True),
        Brand(name="Globex", keywords=["globex"]),
    )
    frame = rows_to_frame(
        _rows("acme xyz", [0, 0, 0]) + _rows("acme qrs", [0, 0, 0]) + _rows("globex", [50, 60, 70])
    )

    assert detect_grouped_keywords(frame, config) == {}


def test_three_way_group_keeps_only_the_first_keyword():
    config = _config(
        Brand(name="Acme", keywords=["acme", "acme app", "acme login"], is_own_brand=True),
        Brand(name="Globex", keywords=["globex"]),
    )
    frame = rows_to_frame(
        _rows("acme", [100, 200, 300])
        + _rows("acme app", [100, 200, 300])
        + _rows("acme login", [100, 200, 300])
        + _rows("globex", [10, 20, 30])
    )

    groups = detect_grouped_keywords(frame, config)
    assert groups["Acme"] == [{"kept": "acme", "dropped": ["acme app", "acme login"]}]
    assert dropped_keywords(groups) == {"acme app", "acme login"}


def test_distinct_keywords_are_left_alone():
    config = _config(
        Brand(name="Acme", keywords=["acme", "acme app"], is_own_brand=True),
        Brand(name="Globex", keywords=["globex"]),
    )
    frame = rows_to_frame(
        _rows("acme", [100, 200, 300]) + _rows("acme app", [10, 25, 40]) + _rows("globex", [50, 60, 70])
    )

    assert detect_grouped_keywords(frame, config) == {}

    brands = aggregate_to_brands(frame, config)
    january = brands[(brands["brand"] == "Acme") & (brands["date"] == "2025-01-01")]
    assert january.iloc[0]["raw_volume"] == 110


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------


def test_aggregate_sums_a_brands_keywords():
    config = _config(
        Brand(name="Acme", keywords=["acme", "acme app"], is_own_brand=True),
        Brand(name="Globex", keywords=["globex"]),
    )
    frame = rows_to_frame(
        _rows("acme", [100, 200]) + _rows("acme app", [10, 20]) + _rows("globex", [5, 5])
    )
    brands = aggregate_to_brands(frame, config)

    acme = brands[brands["brand"] == "Acme"].sort_values("date")
    assert list(acme["raw_volume"]) == [110, 220]


def test_partial_keyword_gap_still_sums_what_is_there():
    config = _config(
        Brand(name="Acme", keywords=["acme", "acme app"], is_own_brand=True),
        Brand(name="Globex", keywords=["globex"]),
    )
    frame = rows_to_frame(
        _rows("acme", [100, 200]) + _rows("acme app", [None, 20]) + _rows("globex", [5, 5])
    )
    brands = aggregate_to_brands(frame, config)

    acme = brands[brands["brand"] == "Acme"].sort_values("date")
    assert list(acme["raw_volume"]) == [100, 220]


def test_whole_brand_gap_is_null_not_zero(sample_rows, sample_config):
    """An API gap is not zero demand, and must not be recorded as a zero share."""
    frame = rows_to_frame(sample_rows)
    brands = aggregate_to_brands(frame, sample_config)

    april = brands[(brands["brand"] == "Initech") & (brands["date"] == "2025-04-01")]
    assert pd.isna(april.iloc[0]["raw_volume"])

    gaps = missing_brand_months(brands)
    assert list(gaps["brand"]) == ["Initech"]


def test_brands_absent_from_the_response_still_appear_as_gaps():
    config = _config(
        Brand(name="Acme", keywords=["acme"], is_own_brand=True),
        Brand(name="Ghost", keywords=["ghost"]),
    )
    frame = rows_to_frame(_rows("acme", [100, 200]))
    brands = aggregate_to_brands(frame, config)

    ghost = brands[brands["brand"] == "Ghost"]
    assert len(ghost) == 2
    assert ghost["raw_volume"].isna().all()


def test_keywords_column_records_what_was_actually_counted(sample_rows, sample_config):
    frame = rows_to_frame(sample_rows)
    groups = detect_grouped_keywords(frame, sample_config)
    brands = aggregate_to_brands(frame, sample_config, exclude_keywords=dropped_keywords(groups))

    acme = brands[brands["brand"] == "Acme"].iloc[0]
    assert acme["keywords"] == "acme"


# --------------------------------------------------------------------------
# Share
# --------------------------------------------------------------------------


def test_shares_sum_to_one_hundred_per_month(sample_rows, sample_config):
    frame = rows_to_frame(sample_rows)
    groups = detect_grouped_keywords(frame, sample_config)
    brands = aggregate_to_brands(frame, sample_config, exclude_keywords=dropped_keywords(groups))
    shares = compute_shares(brands)

    per_month = shares.groupby("date")["sos_pct"].sum()
    assert np.allclose(per_month.to_numpy(), 100.0)


def test_share_uses_the_category_set_as_denominator(sample_rows, sample_config):
    frame = rows_to_frame(sample_rows)
    groups = detect_grouped_keywords(frame, sample_config)
    shares = compute_shares(
        aggregate_to_brands(frame, sample_config, exclude_keywords=dropped_keywords(groups))
    )

    january = shares[shares["date"] == "2025-01-01"].set_index("brand")
    # 1000 + 500 + 500 = 2000
    assert january.loc["Acme", "category_total_volume"] == 2000
    assert january.loc["Acme", "sos_pct"] == pytest.approx(50.0)
    assert january.loc["Globex", "sos_pct"] == pytest.approx(25.0)
    assert january.loc["Initech", "sos_pct"] == pytest.approx(25.0)


def test_missing_brand_gets_null_share_and_is_excluded_from_the_total(sample_rows, sample_config):
    frame = rows_to_frame(sample_rows)
    groups = detect_grouped_keywords(frame, sample_config)
    shares = compute_shares(
        aggregate_to_brands(frame, sample_config, exclude_keywords=dropped_keywords(groups))
    )

    april = shares[shares["date"] == "2025-04-01"].set_index("brand")
    assert pd.isna(april.loc["Initech", "sos_pct"])
    assert april.loc["Acme", "category_total_volume"] == 2100  # 1300 + 800, Initech excluded
    assert april.loc["Acme", "sos_pct"] == pytest.approx(1300 / 2100 * 100)


def test_zero_category_total_does_not_divide_by_zero():
    config = _config(
        Brand(name="Acme", keywords=["acme"], is_own_brand=True),
        Brand(name="Globex", keywords=["globex"]),
    )
    frame = rows_to_frame(_rows("acme", [0, 100]) + _rows("globex", [0, 100]))
    shares = compute_shares(aggregate_to_brands(frame, config))

    january = shares[shares["date"] == "2025-01-01"]
    assert january["sos_pct"].isna().all()


# --------------------------------------------------------------------------
# Rolling averages
# --------------------------------------------------------------------------


def test_rolling_window_stays_null_until_it_fills():
    config = _config(
        Brand(name="Acme", keywords=["acme"], is_own_brand=True),
        Brand(name="Globex", keywords=["globex"]),
    )
    frame = rows_to_frame(_rows("acme", [100, 200, 300, 400]) + _rows("globex", [100, 200, 300, 400]))
    rolled = add_rolling_averages(compute_shares(aggregate_to_brands(frame, config)), [3])

    acme = rolled[rolled["brand"] == "Acme"].sort_values("date")
    assert list(acme["sos_pct_3mo"].isna()) == [True, True, False, False]


def test_rolling_average_is_the_trailing_mean():
    config = _config(
        Brand(name="Acme", keywords=["acme"], is_own_brand=True),
        Brand(name="Globex", keywords=["globex"]),
    )
    # Shares: 50, 60, 70, 80 -> 3-month trailing mean at month 3 is 60.
    frame = rows_to_frame(
        _rows("acme", [50, 60, 70, 80]) + _rows("globex", [50, 40, 30, 20])
    )
    rolled = add_rolling_averages(compute_shares(aggregate_to_brands(frame, config)), [3])

    acme = rolled[rolled["brand"] == "Acme"].sort_values("date")
    assert acme.iloc[2]["sos_pct_3mo"] == pytest.approx(60.0)
    assert acme.iloc[3]["sos_pct_3mo"] == pytest.approx(70.0)


def test_a_gap_inside_the_window_produces_no_average():
    """A missing month must shorten coverage, not silently slide the window."""
    config = _config(
        Brand(name="Acme", keywords=["acme"], is_own_brand=True),
        Brand(name="Globex", keywords=["globex"]),
    )
    frame = rows_to_frame(
        _rows("acme", [50, None, 70, 80, 90]) + _rows("globex", [50, 50, 30, 20, 10])
    )
    rolled = add_rolling_averages(compute_shares(aggregate_to_brands(frame, config)), [3])

    acme = rolled[rolled["brand"] == "Acme"].sort_values("date")
    assert pd.isna(acme.iloc[2]["sos_pct_3mo"])  # window spans the gap
    assert pd.isna(acme.iloc[3]["sos_pct_3mo"])  # still spans it
    assert not pd.isna(acme.iloc[4]["sos_pct_3mo"])  # clear of it


def test_rolling_columns_are_named_for_their_window():
    config = _config(
        Brand(name="Acme", keywords=["acme"], is_own_brand=True),
        Brand(name="Globex", keywords=["globex"]),
    )
    frame = rows_to_frame(_rows("acme", [1, 2, 3]) + _rows("globex", [1, 2, 3]))
    rolled = add_rolling_averages(compute_shares(aggregate_to_brands(frame, config)), [3, 12])

    assert "sos_pct_3mo" in rolled.columns
    assert "sos_pct_12mo" in rolled.columns
    # Volume smoothing defaults to the shortest window only, matching the schema.
    assert "volume_3mo" in rolled.columns
    assert "volume_12mo" not in rolled.columns


def test_rolling_averages_do_not_bleed_between_brands():
    config = _config(
        Brand(name="Acme", keywords=["acme"], is_own_brand=True),
        Brand(name="Globex", keywords=["globex"]),
    )
    frame = rows_to_frame(_rows("acme", [90, 90, 90]) + _rows("globex", [10, 10, 10]))
    rolled = add_rolling_averages(compute_shares(aggregate_to_brands(frame, config)), [3])

    assert rolled[rolled["brand"] == "Acme"]["sos_pct_3mo"].dropna().iloc[0] == pytest.approx(90.0)
    assert rolled[rolled["brand"] == "Globex"]["sos_pct_3mo"].dropna().iloc[0] == pytest.approx(10.0)


# --------------------------------------------------------------------------
# End to end + warnings
# --------------------------------------------------------------------------


def test_build_brand_frame_warns_about_grouping_and_gaps(sample_rows, sample_config):
    brands, warnings = build_brand_frame(sample_rows, sample_config)

    assert not brands.empty
    assert any("grouped" in w for w in warnings)
    assert any("Initech" in w for w in warnings)


def test_build_brand_frame_survives_an_empty_response(sample_config):
    brands, warnings = build_brand_frame([], sample_config)

    assert brands.empty
    assert warnings


def test_dominant_own_brand_warns_about_an_incomplete_set():
    config = _config(
        Brand(name="Acme", keywords=["acme"], is_own_brand=True),
        Brand(name="Globex", keywords=["globex"]),
    )
    frame = rows_to_frame(_rows("acme", [9000, 9000]) + _rows("globex", [100, 100]))
    shares = compute_shares(aggregate_to_brands(frame, config))

    warnings = category_set_warnings(shares, "Acme")
    assert warnings and "missing someone" in warnings[0]


def test_balanced_category_produces_no_warning():
    config = _config(
        Brand(name="Acme", keywords=["acme"], is_own_brand=True),
        Brand(name="Globex", keywords=["globex"]),
    )
    frame = rows_to_frame(_rows("acme", [500, 500]) + _rows("globex", [500, 500]))
    shares = compute_shares(aggregate_to_brands(frame, config))

    assert category_set_warnings(shares, "Acme") == []
