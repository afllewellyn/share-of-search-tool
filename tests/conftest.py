"""Shared fixtures. Nothing here touches the network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sos.config import Brand, Config, Market
from sos.datasource.dataforseo import _parse_response

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_response() -> dict:
    """A canned DataForSEO response.

    Deliberately awkward: 'acme' and 'acme app' return identical volumes
    (Google grouped them), and 'initech' has a null month.
    """
    return json.loads((FIXTURES / "sample_response.json").read_text())


@pytest.fixture
def sample_rows(sample_response) -> list:
    return _parse_response(sample_response, requested_keywords=["acme", "acme app", "globex", "initech"])


@pytest.fixture
def sample_config() -> Config:
    """The brand set matching the canned response."""
    return Config(
        market=Market(name="US", location_code=2840, language_code="en"),
        brands=[
            Brand(name="Acme", keywords=["acme", "acme app"], is_own_brand=True, url="https://acme.com"),
            Brand(name="Globex", keywords=["globex"]),
            Brand(name="Initech", keywords=["initech"]),
        ],
        smoothing_windows=[3, 12],
    )
