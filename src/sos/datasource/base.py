"""The interface every volume source implements.

Everything downstream of this boundary — transform, store, dashboard — sees
only :class:`VolumeRow` dicts. No provider-specific response shape is allowed
past here, so a Google Ads API backend can drop in later without touching
anything else.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

# One month of volume for one keyword:
#   {"keyword": "acme", "year": 2025, "month": 3, "search_volume": 12100}
#
# search_volume may be None when the provider has no data for that month.
# None means "unknown", not zero — the distinction matters, see transform.py.
VolumeRow = Dict[str, Any]


class KeywordVolumeSource(ABC):
    """A source of monthly search volume for a list of keywords."""

    #: Recorded in the data store's ``data_source`` column.
    name: str = "unknown"

    @abstractmethod
    def fetch_monthly_volume(
        self,
        keywords: List[str],
        location_code: int,
        language_code: str,
        date_from: str,
        date_to: str,
    ) -> List[VolumeRow]:
        """Fetch monthly search volume for ``keywords``.

        Args:
            keywords: Search terms to look up.
            location_code: Provider location code (e.g. 2840 for the US).
            language_code: Two-letter language code (e.g. "en").
            date_from: Inclusive start, ``YYYY-MM-DD``.
            date_to: Inclusive end, ``YYYY-MM-DD``.

        Returns:
            A list of :data:`VolumeRow` dicts, one per keyword per month.
        """

    def estimate_cost(self, keyword_count: int) -> float:
        """Estimated USD cost of fetching ``keyword_count`` keywords."""
        return 0.0


class DataSourceError(Exception):
    """The source could not return usable data."""
