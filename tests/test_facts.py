"""Tests for the facts payload and the rule-based commentary.

Every number a reader sees is computed here. If these are wrong, the
commentary is confidently wrong, which is worse than silent.
"""

from __future__ import annotations

import pandas as pd
import pytest

from sos import commentary
from sos.config import Brand, Config, Market
from sos.facts import month_facts
from sos.store import build_rows, recompute
from sos.transform import build_brand_frame


def _config(*brands: Brand, windows=(3,)) -> Config:
    return Config(
        market=Market(name="US", location_code=2840, language_code="en"),
        brands=list(brands),
        smoothing_windows=list(windows),
    )


def _store(config: Config, volumes: dict, months: int) -> pd.DataFrame:
    """Build a recomputed store from ``{keyword: [volume, ...]}``."""
    rows = []
    for keyword, series in volumes.items():
        for index in range(months):
            rows.append(
                {
                    "keyword": keyword,
                    "year": 2025 + (index // 12),
                    "month": (index % 12) + 1,
                    "search_volume": series[index],
                }
            )
    brand_frame, _ = build_brand_frame(rows, config)
    return recompute(
        build_rows(brand_frame, config, data_source="test", pulled_at="2026-01-01T00:00:00+00:00"),
        config.smoothing_windows,
    )


@pytest.fixture
def two_brand_config() -> Config:
    return _config(
        Brand(name="Acme", keywords=["acme"], is_own_brand=True),
        Brand(name="Globex", keywords=["globex"]),
    )


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------


def test_facts_describe_the_latest_month_by_default(two_brand_config):
    store = _store(two_brand_config, {"acme": [500, 600, 700], "globex": [500, 400, 300]}, 3)
    facts = month_facts(store, two_brand_config)

    assert facts["month"] == "2025-03"
    assert facts["market"] == "US"
    assert facts["months_of_history"] == 3
    assert facts["own_brand"] == "Acme"
    assert [b["brand"] for b in facts["brands"]] == ["Acme", "Globex"]


def test_an_explicit_month_can_be_requested(two_brand_config):
    store = _store(two_brand_config, {"acme": [500, 600, 700], "globex": [500, 400, 300]}, 3)
    facts = month_facts(store, two_brand_config, month="2025-01")

    assert facts["month"] == "2025-01"
    assert facts["own_brand_facts"]["sos_pct"] == pytest.approx(50.0)


def test_an_unknown_month_returns_an_empty_payload(two_brand_config):
    store = _store(two_brand_config, {"acme": [500], "globex": [500]}, 1)
    facts = month_facts(store, two_brand_config, month="2030-01")

    assert facts["brands"] == []
    assert facts["own_brand_facts"] is None


def test_an_empty_store_returns_an_empty_payload(two_brand_config):
    facts = month_facts(pd.DataFrame(columns=["market"]), two_brand_config)
    assert facts["brands"] == []


# --------------------------------------------------------------------------
# Deltas and ranks
# --------------------------------------------------------------------------


def test_month_on_month_delta_is_in_percentage_points(two_brand_config):
    # 50% -> 60%: a ten-point move.
    store = _store(two_brand_config, {"acme": [500, 600], "globex": [500, 400]}, 2)
    facts = month_facts(store, two_brand_config)

    assert facts["own_brand_facts"]["sos_pct"] == pytest.approx(60.0)
    assert facts["own_brand_facts"]["mom_delta_pp"] == pytest.approx(10.0)


def test_year_on_year_delta_reaches_back_twelve_months(two_brand_config):
    acme = [500] * 12 + [800]
    globex = [500] * 12 + [200]
    store = _store(two_brand_config, {"acme": acme, "globex": globex}, 13)
    facts = month_facts(store, two_brand_config)

    assert facts["own_brand_facts"]["yoy_delta_pp"] == pytest.approx(30.0)


def test_the_first_month_has_no_deltas(two_brand_config):
    store = _store(two_brand_config, {"acme": [500], "globex": [500]}, 1)
    facts = month_facts(store, two_brand_config)

    assert facts["own_brand_facts"]["mom_delta_pp"] is None
    assert facts["own_brand_facts"]["yoy_delta_pp"] is None


def test_rank_one_is_the_largest_share(two_brand_config):
    store = _store(two_brand_config, {"acme": [300], "globex": [700]}, 1)
    facts = month_facts(store, two_brand_config)

    by_brand = {b["brand"]: b for b in facts["brands"]}
    assert by_brand["Globex"]["rank"] == 1
    assert by_brand["Acme"]["rank"] == 2
    assert facts["leader"] == "Globex"


def test_rank_change_is_positive_when_a_brand_moves_up(two_brand_config):
    store = _store(two_brand_config, {"acme": [300, 700], "globex": [700, 300]}, 2)
    facts = month_facts(store, two_brand_config)

    by_brand = {b["brand"]: b for b in facts["brands"]}
    assert by_brand["Acme"]["rank_change"] == 1
    assert by_brand["Globex"]["rank_change"] == -1


# --------------------------------------------------------------------------
# Noise threshold
# --------------------------------------------------------------------------


def test_a_move_inside_the_usual_variation_is_not_flagged(two_brand_config):
    # Share oscillates around 50% by several points every month, then moves
    # by a comparable amount — that is ordinary for this brand.
    acme = [500, 560, 500, 560, 500, 560, 500, 560, 500, 560, 500, 560, 530]
    globex = [1000 - v for v in acme]
    store = _store(two_brand_config, {"acme": acme, "globex": globex}, 13)
    facts = month_facts(store, two_brand_config)

    assert facts["own_brand_facts"]["exceeds_noise_threshold"] is False


def test_a_move_far_beyond_the_usual_variation_is_flagged(two_brand_config):
    acme = [500, 505, 500, 505, 500, 505, 500, 505, 500, 505, 500, 505, 900]
    globex = [1000 - v for v in acme]
    store = _store(two_brand_config, {"acme": acme, "globex": globex}, 13)
    facts = month_facts(store, two_brand_config)

    assert facts["own_brand_facts"]["exceeds_noise_threshold"] is True


def test_too_little_history_leaves_the_threshold_undecided(two_brand_config):
    store = _store(two_brand_config, {"acme": [500, 600], "globex": [500, 400]}, 2)
    facts = month_facts(store, two_brand_config)

    assert facts["own_brand_facts"]["exceeds_noise_threshold"] is None
    assert facts["own_brand_facts"]["noise_threshold_pp"] is None


def test_a_perfectly_flat_brand_has_no_measurable_threshold(two_brand_config):
    store = _store(two_brand_config, {"acme": [500] * 6, "globex": [500] * 6}, 6)
    facts = month_facts(store, two_brand_config)

    assert facts["own_brand_facts"]["noise_threshold_pp"] is None


# --------------------------------------------------------------------------
# Category-driven detection
# --------------------------------------------------------------------------


def test_a_competitor_collapsing_is_flagged_as_category_driven(two_brand_config):
    """The classic misreading: share up, brand flat, competitor gone."""
    store = _store(two_brand_config, {"acme": [500, 500], "globex": [500, 100]}, 2)
    facts = month_facts(store, two_brand_config)

    assert facts["category_driven"] is True
    assert facts["category_total_change_pct"] == pytest.approx(-40.0)
    assert facts["own_volume_change_pct"] == pytest.approx(0.0)
    assert facts["own_brand_facts"]["mom_delta_pp"] > 0


def test_a_genuine_brand_move_is_not_category_driven(two_brand_config):
    store = _store(two_brand_config, {"acme": [500, 900], "globex": [500, 500]}, 2)
    facts = month_facts(store, two_brand_config)

    assert facts["category_driven"] is False


def test_a_stable_category_is_never_category_driven(two_brand_config):
    store = _store(two_brand_config, {"acme": [500, 510], "globex": [500, 490]}, 2)
    facts = month_facts(store, two_brand_config)

    assert facts["category_driven"] is False


# --------------------------------------------------------------------------
# Gaps and ambiguity
# --------------------------------------------------------------------------


def test_data_gaps_are_reported(sample_rows, sample_config):
    brand_frame, _ = build_brand_frame(sample_rows, sample_config)
    store = recompute(build_rows(brand_frame, sample_config, data_source="test"), [3])
    facts = month_facts(store, sample_config)

    assert facts["brands_with_data_gaps"] == ["Initech"]


def test_ambiguous_brands_are_carried_through():
    config = _config(
        Brand(name="Acme", keywords=["acme"], is_own_brand=True),
        Brand(name="Emma", keywords=["emma mattress"], ambiguous=True),
    )
    store = _store(config, {"acme": [500], "emma mattress": [500]}, 1)
    facts = month_facts(store, config)

    assert facts["ambiguous_brands"] == ["Emma"]


def test_the_payload_is_json_serialisable(two_brand_config):
    import json

    store = _store(two_brand_config, {"acme": [500, 600], "globex": [500, 400]}, 2)
    json.dumps(month_facts(store, two_brand_config), allow_nan=False)


# --------------------------------------------------------------------------
# Commentary
# --------------------------------------------------------------------------


def test_commentary_leads_with_the_category_when_the_category_moved(two_brand_config):
    store = _store(two_brand_config, {"acme": [500, 500], "globex": [500, 100]}, 2)
    bullets = commentary.generate(month_facts(store, two_brand_config))

    assert bullets
    assert "category" in bullets[0].lower()
    assert "denominator" in bullets[0].lower()


def test_commentary_says_within_normal_variation_when_it_is(two_brand_config):
    acme = [500, 560, 500, 560, 500, 560, 500, 560, 500, 560, 500, 560, 530]
    globex = [1000 - v for v in acme]
    store = _store(two_brand_config, {"acme": acme, "globex": globex}, 13)
    bullets = commentary.generate(month_facts(store, two_brand_config))

    assert any("within normal variation" in b for b in bullets)


def test_commentary_mentions_the_leading_indicator_caveat(two_brand_config):
    store = _store(two_brand_config, {"acme": [500, 600], "globex": [500, 400]}, 2)
    bullets = commentary.generate(month_facts(store, two_brand_config))

    assert any("lead" in b.lower() for b in bullets)


def test_commentary_is_at_most_two_bullets(two_brand_config):
    store = _store(two_brand_config, {"acme": [500, 600], "globex": [500, 400]}, 2)
    assert len(commentary.generate(month_facts(store, two_brand_config))) <= 2


def test_commentary_never_claims_a_cause(two_brand_config):
    """The metric supports no causal claim, so the words must not appear."""
    store = _store(two_brand_config, {"acme": [500, 900], "globex": [500, 300]}, 2)
    bullets = " ".join(commentary.generate(month_facts(store, two_brand_config))).lower()

    for word in ["because of", "caused by", "driven by the campaign", "thanks to", "due to the"]:
        assert word not in bullets


def test_commentary_flags_a_month_with_missing_data(sample_rows, sample_config):
    brand_frame, _ = build_brand_frame(sample_rows, sample_config)
    store = recompute(build_rows(brand_frame, sample_config, data_source="test"), [3])
    bullets = commentary.generate(month_facts(store, sample_config))

    assert any("provisional" in b for b in bullets)


def test_commentary_handles_an_empty_payload(two_brand_config):
    facts = month_facts(pd.DataFrame(columns=["market"]), two_brand_config)
    assert commentary.generate(facts) == ["No data available for this month yet."]
