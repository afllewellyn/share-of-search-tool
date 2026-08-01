"""Tests for the generated dashboard.

The constraint worth guarding here is self-containment. A dashboard that
renders perfectly on the machine that built it and silently breaks on the
recipient's is the failure mode this file exists to catch.
"""

from __future__ import annotations

import json
import re

import pytest

from sos.dashboard.build import build_dashboard, build_payload
from sos.store import build_rows, recompute
from sos.transform import build_brand_frame


@pytest.fixture
def store(sample_rows, sample_config):
    brand_frame, _ = build_brand_frame(sample_rows, sample_config)
    return recompute(
        build_rows(brand_frame, sample_config, data_source="dataforseo", pulled_at="2025-05-01T00:00:00+00:00"),
        sample_config.smoothing_windows,
    )


@pytest.fixture
def html(tmp_path, store, sample_config):
    path = build_dashboard(store, sample_config, tmp_path / "out.html", generated_at="2025-05-01 00:00 UTC")
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Self-containment
# --------------------------------------------------------------------------


def test_no_placeholders_survive(html):
    for placeholder in ["__SOS_PAYLOAD__", "__SOS_TITLE__", "__SOS_CHARTJS__"]:
        assert placeholder not in html


def test_the_page_fetches_nothing_at_runtime(html):
    """No src/href to a remote host, and no data.json — file:// would block both."""
    assert not re.search(r'(src|href)\s*=\s*["\']https?://', html)
    assert "data.json" not in html
    for api in ["fetch(", "XMLHttpRequest", "importScripts("]:
        assert api not in html


def test_chartjs_is_inlined(html):
    assert "Chart.js v4.4.1" in html
    assert "cdn.jsdelivr.net" not in html


def test_the_data_is_inlined_as_a_javascript_constant(html):
    match = re.search(r"const DATA = (\{.*?\});\n", html, re.S)
    assert match
    payload = json.loads(match.group(1))
    assert payload["own_brand"] == "Acme"
    assert payload["months"] == ["2025-01", "2025-02", "2025-03", "2025-04"]


# --------------------------------------------------------------------------
# Payload
# --------------------------------------------------------------------------


def test_payload_is_strictly_json_serialisable(store, sample_config):
    """NaN is not valid JSON — a stray one would break the page on load."""
    payload = build_payload(store, sample_config)
    json.dumps(payload, allow_nan=False)


def test_gaps_become_nulls_not_zeros(store, sample_config):
    payload = build_payload(store, sample_config)
    # Initech has no April data; a zero here would draw as a collapse to 0%.
    assert payload["series"]["raw"]["Initech"][-1] is None


def test_brand_order_follows_the_config_not_the_ranking(store, sample_config):
    """Colours stay attached to a brand across rebuilds."""
    payload = build_payload(store, sample_config)
    assert [b["name"] for b in payload["brands"]] == ["Acme", "Globex", "Initech"]


def test_every_smoothing_window_gets_a_series(store, sample_config):
    payload = build_payload(store, sample_config)
    assert set(payload["series"]) == {"raw", "3", "12"}
    for values in payload["series"].values():
        for brand in ["Acme", "Globex", "Initech"]:
            assert len(values[brand]) == len(payload["months"])


def test_full_dataset_rows_are_included_for_the_csv_export(store, sample_config):
    payload = build_payload(store, sample_config)
    assert len(payload["rows"]) == len(store)
    assert "raw_volume" in payload["columns"]


def test_keywords_reaching_the_methodology_section_are_the_configured_ones(store, sample_config):
    payload = build_payload(store, sample_config)
    acme = next(b for b in payload["brands"] if b["name"] == "Acme")
    assert acme["keywords"] == ["acme", "acme app"]


def test_an_unknown_market_is_a_clear_error(store, sample_config):
    sample_config.market.name = "DE"
    with pytest.raises(ValueError, match="No stored data for market 'DE'"):
        build_payload(store, sample_config)


# --------------------------------------------------------------------------
# Content
# --------------------------------------------------------------------------


def test_the_methodology_credits_the_source_of_the_metric(html):
    assert "Les Binet" in html
    assert "IPA EffWorks Global 2020" in html


def test_the_indicator_not_forecast_note_is_present(html):
    assert "Indicator, not forecast" in html
    assert "leading indicator" in html
    assert "not a prediction of market share" in html


def test_ambiguous_brands_are_named_in_the_methodology(tmp_path, store, sample_config):
    sample_config.brands[1].ambiguous = True  # Globex
    path = build_dashboard(store, sample_config, tmp_path / "amb.html")
    assert "ambiguous_brands" in path.read_text()
    payload = build_payload(store, sample_config)
    assert payload["ambiguous_brands"] == ["Globex"]
