"""Tests for the CSV store: idempotency, upsert semantics, atomic writes."""

from __future__ import annotations

import pandas as pd
import pytest

from sos.config import Brand, Config, ConfigError, Market
from sos.store import (
    STORE_FILENAME,
    build_rows,
    config_from_store,
    counted_keywords,
    existing_months,
    is_empty,
    load_store,
    order_columns,
    recompute,
    stale_brands,
    store_path,
    upsert,
    write_store,
)
from sos.transform import build_brand_frame


@pytest.fixture
def rows(sample_rows, sample_config):
    brand_frame, _ = build_brand_frame(sample_rows, sample_config)
    return build_rows(brand_frame, sample_config, data_source="dataforseo", pulled_at="2025-05-01T00:00:00+00:00")


# --------------------------------------------------------------------------
# Basics
# --------------------------------------------------------------------------


def test_empty_store_reports_empty(tmp_path):
    assert is_empty(tmp_path)
    assert load_store(tmp_path).empty


def test_upsert_creates_the_store(tmp_path, rows, sample_config):
    upsert(tmp_path, rows, sample_config.smoothing_windows)

    assert store_path(tmp_path).exists()
    assert store_path(tmp_path).name == STORE_FILENAME
    assert not is_empty(tmp_path)


def test_stored_columns_match_the_documented_schema(tmp_path, rows, sample_config):
    upsert(tmp_path, rows, sample_config.smoothing_windows)
    stored = load_store(tmp_path)

    for column in [
        "date", "year", "month", "brand", "is_own_brand", "market", "location_code",
        "language_code", "keywords", "raw_volume", "category_total_volume", "sos_pct",
        "sos_pct_3mo", "sos_pct_12mo", "volume_3mo", "data_source", "pulled_at",
    ]:
        assert column in stored.columns, column


def test_column_order_is_stable(tmp_path, rows, sample_config):
    upsert(tmp_path, rows, sample_config.smoothing_windows)
    ordered = list(pd.read_csv(store_path(tmp_path)).columns)

    assert ordered[:4] == ["date", "year", "month", "brand"]
    assert ordered[-2:] == ["data_source", "pulled_at"]
    assert ordered.index("sos_pct_3mo") < ordered.index("sos_pct_12mo")


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


def test_rerunning_the_same_pull_does_not_duplicate_rows(tmp_path, rows, sample_config):
    first = upsert(tmp_path, rows, sample_config.smoothing_windows)
    second = upsert(tmp_path, rows, sample_config.smoothing_windows)

    assert len(first) == len(second)
    assert not second.duplicated(subset=["date", "brand", "market"]).any()


def test_rerunning_produces_an_identical_file_apart_from_the_timestamp(tmp_path, rows, sample_config):
    upsert(tmp_path, rows, sample_config.smoothing_windows)
    first = store_path(tmp_path).read_text()

    later = rows.copy()
    later["pulled_at"] = "2025-06-01T00:00:00+00:00"
    upsert(tmp_path, later, sample_config.smoothing_windows)
    second = store_path(tmp_path).read_text()

    assert first.replace("2025-05-01T00:00:00+00:00", "") == second.replace(
        "2025-06-01T00:00:00+00:00", ""
    )


def test_upsert_replaces_matching_keys_rather_than_appending(tmp_path, rows, sample_config):
    upsert(tmp_path, rows, sample_config.smoothing_windows)

    # Google revises a historical month upward.
    revised = rows.copy()
    mask = (revised["date"] == pd.Timestamp("2025-01-01")) & (revised["brand"] == "Acme")
    revised.loc[mask, "raw_volume"] = 5000

    combined = upsert(tmp_path, revised, sample_config.smoothing_windows)

    january_acme = combined[(combined["date"] == "2025-01-01") & (combined["brand"] == "Acme")]
    assert len(january_acme) == 1
    assert january_acme.iloc[0]["raw_volume"] == 5000


def test_partial_refresh_leaves_older_months_alone(tmp_path, rows, sample_config):
    upsert(tmp_path, rows, sample_config.smoothing_windows)

    trailing = rows[rows["date"] >= pd.Timestamp("2025-03-01")].copy()
    combined = upsert(tmp_path, trailing, sample_config.smoothing_windows)

    assert sorted(pd.to_datetime(combined["date"]).dt.strftime("%Y-%m").unique()) == [
        f"2025-0{m}" for m in range(1, 9)
    ]
    assert len(combined) == len(rows)


# --------------------------------------------------------------------------
# Recomputation
# --------------------------------------------------------------------------


def test_derived_columns_are_rebuilt_from_raw_volume(tmp_path, rows, sample_config):
    """raw_volume is the only source of truth; everything else is regenerated.

    This is what makes Google's revisions to historical months harmless.
    """
    upsert(tmp_path, rows, sample_config.smoothing_windows)

    revised = rows.copy()
    mask = revised["date"] == pd.Timestamp("2025-01-01")
    revised.loc[mask & (revised["brand"] == "Acme"), "raw_volume"] = 3000
    combined = upsert(tmp_path, revised, sample_config.smoothing_windows)

    january = combined[combined["date"] == "2025-01-01"].set_index("brand")
    # 3000 + 500 + 500 = 4000
    assert january.loc["Acme", "category_total_volume"] == 4000
    assert january.loc["Acme", "sos_pct"] == pytest.approx(75.0)
    assert january.loc["Globex", "sos_pct"] == pytest.approx(12.5)


def test_stale_derived_columns_do_not_survive_a_recompute(tmp_path, rows, sample_config):
    poisoned = rows.copy()
    poisoned["sos_pct"] = 999.0
    poisoned["category_total_volume"] = 1.0

    combined = upsert(tmp_path, poisoned, sample_config.smoothing_windows)

    assert (combined["sos_pct"].dropna() != 999.0).all()


def test_markets_are_recomputed_independently(tmp_path, rows):
    us = rows.copy()
    uk = rows.copy()
    uk["market"] = "UK"
    uk["location_code"] = 2826
    uk["raw_volume"] = uk["raw_volume"] * 10

    combined = recompute(pd.concat([us, uk], ignore_index=True), [3])

    january_us = combined[(combined["date"] == "2025-01-01") & (combined["market"] == "US")]
    january_uk = combined[(combined["date"] == "2025-01-01") & (combined["market"] == "UK")]

    assert january_us["category_total_volume"].iloc[0] == 2000
    assert january_uk["category_total_volume"].iloc[0] == 20000
    # Shares are identical because the UK figures are a clean multiple.
    assert january_us["sos_pct"].sum() == pytest.approx(100.0)
    assert january_uk["sos_pct"].sum() == pytest.approx(100.0)


def test_changing_the_smoothing_window_regenerates_the_columns(tmp_path, rows):
    upsert(tmp_path, rows, [3])
    assert "sos_pct_3mo" in load_store(tmp_path).columns

    upsert(tmp_path, rows, [2])
    columns = load_store(tmp_path).columns
    assert "sos_pct_2mo" in columns
    assert "sos_pct_3mo" not in columns


# --------------------------------------------------------------------------
# Atomic writes
# --------------------------------------------------------------------------


def test_write_leaves_no_temp_files_behind(tmp_path, rows, sample_config):
    upsert(tmp_path, rows, sample_config.smoothing_windows)

    leftovers = [p.name for p in tmp_path.iterdir() if p.name != STORE_FILENAME]
    assert leftovers == []


def test_a_failed_write_leaves_the_previous_store_intact(tmp_path, rows, sample_config, monkeypatch):
    upsert(tmp_path, rows, sample_config.smoothing_windows)
    before = store_path(tmp_path).read_text()

    def explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("sos.store.os.replace", explode)

    with pytest.raises(OSError):
        write_store(tmp_path, rows)

    assert store_path(tmp_path).read_text() == before
    assert [p.name for p in tmp_path.iterdir()] == [STORE_FILENAME]


def test_dates_are_written_as_iso_month_starts(tmp_path, rows, sample_config):
    upsert(tmp_path, rows, sample_config.smoothing_windows)
    raw = pd.read_csv(store_path(tmp_path))

    assert set(raw["date"]) == {f"2025-0{m}-01" for m in range(1, 9)}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def test_existing_months_is_scoped_to_one_market(tmp_path, rows, sample_config):
    uk = rows.copy()
    uk["market"] = "UK"
    uk = uk[uk["date"] == pd.Timestamp("2025-01-01")]

    upsert(tmp_path, pd.concat([rows, uk], ignore_index=True), sample_config.smoothing_windows)

    assert len(existing_months(tmp_path, "US")) == 8
    assert len(existing_months(tmp_path, "UK")) == 1
    assert existing_months(tmp_path, "DE") == []


# --------------------------------------------------------------------------
# Changing the category set
# --------------------------------------------------------------------------


def _swap_competitor(rows_frame, old: str, new: str):
    """The rows a run would produce after renaming a competitor in the config."""
    swapped = rows_frame.copy()
    swapped["brand"] = swapped["brand"].replace({old: new})
    swapped["keywords"] = swapped["keywords"].replace({old.lower(): new.lower()})
    return swapped


def test_dropping_a_competitor_removes_it_from_the_category_total(tmp_path, rows, sample_config):
    """A stale brand left in the store keeps inflating the denominator.

    Nothing replaces it on a trailing refresh — the incoming rows never share
    its key — so without pruning it silently understates everyone else.
    """
    upsert(tmp_path, rows, sample_config.smoothing_windows)

    without_initech = rows[rows["brand"] != "Initech"]
    combined = upsert(
        tmp_path,
        without_initech,
        sample_config.smoothing_windows,
        active_brands=["Acme", "Globex"],
    )

    assert set(combined["brand"]) == {"Acme", "Globex"}
    january = combined[combined["date"] == "2025-01-01"].set_index("brand")
    assert january.loc["Acme", "category_total_volume"] == 1500  # 1000 + 500, no Initech
    assert january.loc["Acme", "sos_pct"] == pytest.approx(1000 / 1500 * 100)


def test_a_renamed_competitor_does_not_linger_after_a_partial_refresh(tmp_path, rows, sample_config):
    upsert(tmp_path, rows, sample_config.smoothing_windows)

    renamed = _swap_competitor(rows, "Initech", "NewCo")
    trailing = renamed[renamed["date"] >= pd.Timestamp("2025-03-01")]

    combined = upsert(
        tmp_path,
        trailing,
        sample_config.smoothing_windows,
        active_brands=["Acme", "Globex", "NewCo"],
    )

    assert "Initech" not in set(combined["brand"])
    march = combined[combined["date"] == "2025-03-01"].set_index("brand")
    assert march.loc["Acme", "category_total_volume"] == 2000  # 1200 + 700 + 100


def test_pruning_leaves_other_markets_alone(tmp_path, rows, sample_config):
    uk = rows.copy()
    uk["market"] = "UK"
    upsert(tmp_path, pd.concat([rows, uk], ignore_index=True), sample_config.smoothing_windows)

    us_only = rows[rows["brand"] != "Initech"]
    combined = upsert(
        tmp_path, us_only, sample_config.smoothing_windows, active_brands=["Acme", "Globex"]
    )

    assert set(combined[combined["market"] == "US"]["brand"]) == {"Acme", "Globex"}
    assert "Initech" in set(combined[combined["market"] == "UK"]["brand"])


def test_omitting_active_brands_prunes_nothing(tmp_path, rows, sample_config):
    """Callers that don't declare a category set get the old behaviour."""
    upsert(tmp_path, rows, sample_config.smoothing_windows)
    combined = upsert(tmp_path, rows[rows["brand"] != "Initech"], sample_config.smoothing_windows)

    assert "Initech" in set(combined["brand"])


def test_stale_brands_reports_what_would_be_dropped(tmp_path, rows, sample_config):
    upsert(tmp_path, rows, sample_config.smoothing_windows)
    stored = load_store(tmp_path)

    assert stale_brands(stored, "US", ["Acme", "Globex"]) == ["Initech"]
    assert stale_brands(stored, "US", ["Acme", "Globex", "Initech"]) == []
    assert stale_brands(stored, "DE", ["Acme"]) == []


# --------------------------------------------------------------------------
# Reading a previous run's decisions back
# --------------------------------------------------------------------------


def test_counted_keywords_recovers_the_grouping_decision(tmp_path, rows, sample_config):
    """'acme app' was dropped as a grouped duplicate; the store remembers."""
    upsert(tmp_path, rows, sample_config.smoothing_windows)

    counted = counted_keywords(load_store(tmp_path), "US")
    assert counted["Acme"] == ["acme"]
    assert counted["Globex"] == ["globex"]


def test_counted_keywords_is_none_when_nothing_is_stored():
    assert counted_keywords(pd.DataFrame(), "US") is None


def test_counted_keywords_is_scoped_to_one_market(tmp_path, rows, sample_config):
    upsert(tmp_path, rows, sample_config.smoothing_windows)
    assert counted_keywords(load_store(tmp_path), "DE") is None


def test_config_can_be_reconstructed_from_the_store(tmp_path, rows, sample_config):
    """`sos dashboard` after an ad-hoc `sos run` has no config file to read."""
    upsert(tmp_path, rows, sample_config.smoothing_windows)

    rebuilt = config_from_store(load_store(tmp_path))

    assert rebuilt.own_brand.name == "Acme"
    assert rebuilt.brands[0].name == "Acme"  # own brand first, keeps colour slot 1
    assert {b.name for b in rebuilt.brands} == {"Acme", "Globex", "Initech"}
    assert rebuilt.market.name == "US"
    assert rebuilt.market.location_code == 2840
    assert rebuilt.smoothing_windows == [3, 12]
    # The grouped 'acme app' was dropped before storage, so it must not return.
    assert rebuilt.brands[0].keywords == ["acme"]


def test_reconstructing_a_market_that_was_never_stored_is_a_clear_error(tmp_path, rows, sample_config):
    upsert(tmp_path, rows, sample_config.smoothing_windows)

    with pytest.raises(ConfigError, match="No stored data for market 'DE'"):
        config_from_store(load_store(tmp_path), "DE")


def test_reconstructing_from_an_empty_store_is_a_clear_error():
    with pytest.raises(ConfigError, match="empty"):
        config_from_store(pd.DataFrame())


def test_order_columns_tolerates_unexpected_extras(rows):
    extra = rows.copy()
    extra["future_column"] = 1
    assert "future_column" in order_columns(extra).columns


def test_build_rows_marks_the_own_brand(rows):
    assert set(rows[rows["is_own_brand"]]["brand"]) == {"Acme"}
    assert set(rows[~rows["is_own_brand"]]["brand"]) == {"Globex", "Initech"}
