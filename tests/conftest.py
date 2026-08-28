"""Shared fixtures. Nothing here touches the network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sos.config import Brand, Config, Market
from sos.datasource.base import KeywordVolumeSource
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


class FakeSource(KeywordVolumeSource):
    """Replays canned VolumeRows, honouring the requested keywords and range.

    The whole point of :func:`sos.run.refresh` taking a source rather than
    constructing one: the pipeline runs identically here and against
    DataForSEO. Honouring ``date_from``/``date_to`` matters — the grouped-
    keyword guard behaves differently on a short window, and a fake that
    ignored the range could never exercise that.
    """

    name = "fake"

    def __init__(self, rows: list) -> None:
        self._rows = [dict(row) for row in rows]
        #: Every call's arguments, so tests can assert what was asked for.
        self.calls: list = []

    def fetch_monthly_volume(
        self,
        keywords,
        location_code: int,
        language_code: str,
        date_from: str,
        date_to: str,
    ) -> list:
        wanted = set(keywords)
        self.calls.append(
            {
                "keywords": list(keywords),
                "location_code": location_code,
                "language_code": language_code,
                "date_from": date_from,
                "date_to": date_to,
            }
        )
        start, end = _month_key(date_from), _month_key(date_to)
        return [
            dict(row)
            for row in self._rows
            if row["keyword"] in wanted and start <= (row["year"], row["month"]) <= end
        ]


def _month_key(iso_date: str) -> tuple:
    year, month, _ = iso_date.split("-")
    return int(year), int(month)
