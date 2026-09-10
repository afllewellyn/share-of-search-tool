"""The web front door, from request dict to finished HTML, with no network.

A web run is the CLI pipeline over a throwaway store, so most of what it
does is already covered by test_run. What is tested here is the seam: that
a form submission becomes the same Config a YAML file would, that the caps
an unauthenticated endpoint needs are enforced with messages a client can
read, and that the response carries everything the page renders.
"""

from __future__ import annotations

from datetime import date

import pytest

from conftest import FakeSource
from sos import web
from sos.config import ConfigError
from sos.datasource.base import DataSourceError
from sos.run import last_complete_month, shift_months


def _volumes(keyword: str, base: int) -> list:
    """Two years of flat-ish volume, anchored on the last complete month."""
    end = last_complete_month()
    rows = []
    for offset in range(24):
        month = shift_months(end, -offset)
        rows.append({"keyword": keyword, "year": month.year, "month": month.month, "search_volume": base + offset})
    return rows


def _rows() -> list:
    return _volumes("acme", 1000) + _volumes("acme app", 300) + _volumes("globex", 800) + _volumes("initech", 200)


def _payload(**overrides) -> dict:
    payload = {
        "own_brand": {"name": "Acme", "keywords": ["Acme", "acme app"], "url": "https://acme.com"},
        "competitors": [
            {"name": "Globex", "keywords": ["globex"]},
            {"name": "Initech", "keywords": "initech"},
        ],
        "market": "us",
        "months": 24,
    }
    payload.update(overrides)
    return payload


# -- parse_request ----------------------------------------------------------


def test_a_form_submission_becomes_a_validated_config():
    config, months = web.parse_request(_payload())

    assert months == 24
    assert config.market.name == "US"
    assert config.market.location_code == 2840
    assert config.own_brand.name == "Acme"
    assert config.own_brand.url == "https://acme.com"
    # Keywords are lowercased and a comma string is accepted as a list.
    assert config.own_brand.keywords == ["acme", "acme app"]
    assert [c.name for c in config.competitors] == ["Globex", "Initech"]
    assert config.all_keywords == ["acme", "acme app", "globex", "initech"]


def test_a_keyword_shared_between_brands_is_rejected():
    payload = _payload(competitors=[{"name": "Globex", "keywords": ["acme"]}])
    with pytest.raises(ConfigError, match="'acme' is assigned to both"):
        web.parse_request(payload)


def test_no_competitors_is_rejected():
    with pytest.raises(ConfigError, match="At least one competitor"):
        web.parse_request(_payload(competitors=[]))


def test_too_many_competitors_is_rejected():
    many = [{"name": f"Brand {i}", "keywords": [f"brand {i}"]} for i in range(13)]
    with pytest.raises(ConfigError, match="At most 12 competitors"):
        web.parse_request(_payload(competitors=many))


def test_too_many_keywords_on_one_brand_is_rejected():
    own = {"name": "Acme", "keywords": [f"acme {i}" for i in range(9)]}
    with pytest.raises(ConfigError, match="allows 8 per brand"):
        web.parse_request(_payload(own_brand=own))


def test_duplicate_keywords_within_a_brand_collapse_silently():
    own = {"name": "Acme", "keywords": ["Acme", "acme", " ACME "]}
    config, _ = web.parse_request(_payload(own_brand=own))
    assert config.own_brand.keywords == ["acme"]


def test_a_brand_with_no_usable_keywords_is_rejected():
    with pytest.raises(ConfigError, match="'Globex' needs at least one keyword"):
        web.parse_request(_payload(competitors=[{"name": "Globex", "keywords": [" ", ""]}]))


@pytest.mark.parametrize("months", [7, 0, "many", None])
def test_months_outside_the_allowed_set_is_rejected(months):
    with pytest.raises(ConfigError, match="months must be one of"):
        web.parse_request(_payload(months=months))


def test_an_unknown_market_is_rejected_with_the_known_list():
    with pytest.raises(ConfigError, match="Unknown market 'ZZ'.*US"):
        web.parse_request(_payload(market="ZZ"))


@pytest.mark.parametrize("payload", [None, [], "text", {"competitors": []}])
def test_malformed_bodies_fail_with_a_readable_message(payload):
    with pytest.raises(ConfigError):
        web.parse_request(payload)


# -- run_request ------------------------------------------------------------


def test_a_web_run_is_a_backfill_over_the_requested_months(tmp_path):
    source = FakeSource(_rows())

    web.run_request(_payload(months=12), source, data_dir=tmp_path)

    end = last_complete_month()
    (call,) = source.calls
    assert call["keywords"] == ["acme", "acme app", "globex", "initech"]
    assert call["location_code"] == 2840
    assert call["date_to"] == f"{end:%Y-%m-%d}"
    assert call["date_from"] == f"{shift_months(end, -11):%Y-%m-%d}"


def test_the_response_carries_everything_the_page_renders(tmp_path):
    source = FakeSource(_rows())

    response = web.run_request(_payload(), source, data_dir=tmp_path)

    assert response["own_brand"] == "Acme"
    assert response["market"] == "US"
    assert response["months_requested"] == 24
    assert response["months_returned"] == 24
    assert response["cost_usd"] == 0.0  # the fake is free; DataForSEO reports 0.075

    latest = response["latest"]
    assert latest["month"] == f"{last_complete_month():%Y-%m}"
    assert {row["brand"] for row in latest["rows"]} == {"Acme", "Globex", "Initech"}
    own = next(row for row in latest["rows"] if row["is_own_brand"])
    assert own["brand"] == "Acme"
    assert 0 < own["sos_pct"] < 100

    assert isinstance(response["commentary"], list) and response["commentary"]
    assert isinstance(response["warnings"], list)

    html = response["html"]
    assert html.startswith("<!doctype html>")
    assert "__SOS_PAYLOAD__" not in html and "__SOS_CHARTJS__" not in html
    assert "Share of Search — Acme (US)" in html


def test_each_run_starts_from_an_empty_store(tmp_path):
    """No refresh semantics on the web: the temp store has only this run's rows."""
    source = FakeSource(_rows())
    response = web.run_request(_payload(months=12), source, data_dir=tmp_path)

    assert response["months_returned"] == 12
    assert len(response["latest"]["rows"]) == 3


def test_an_empty_response_surfaces_as_a_data_source_error(tmp_path):
    with pytest.raises(DataSourceError, match="no volume data"):
        web.run_request(_payload(), FakeSource([]), data_dir=tmp_path)


def test_a_bad_request_never_reaches_the_source(tmp_path):
    source = FakeSource(_rows())
    with pytest.raises(ConfigError):
        web.run_request(_payload(months=5), source, data_dir=tmp_path)
    assert source.calls == []
