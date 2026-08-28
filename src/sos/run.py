"""Run the Share of Search pipeline: fetch, transform, store.

Everything ``sos run`` does between a resolved brand set and a printed result
lives here, behind one interface. The point is the ordering — three
constraints hold this sequence together, and none of them are visible in the
signatures of the calls it makes:

**The store is read before it is written.** :func:`~sos.store.counted_keywords`
recovers the grouped-keyword decision an earlier run made, and
:func:`~sos.store.upsert` overwrites the very column it reads. Read it
afterwards and the decision is silently lost — a short refresh would then
count a keyword Google had merged, doubling that brand's volume.

**The active brand set goes to the write.** Without it, a competitor removed
from the config keeps its stored rows (nothing collides with their key) and
keeps contributing to the category total, understating every remaining brand.

**Warnings are returned, never printed.** Presentation belongs to the caller.
This module is the part a test can drive.

The source is a parameter, not something constructed here, so the same
sequence runs against DataForSEO in production and a canned fake in tests.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd

from sos import store as store_module
from sos import transform
from sos.config import Config
from sos.datasource.base import DataSourceError, KeywordVolumeSource, VolumeRow

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    """Everything a caller needs to report on a completed run.

    ``stale_brands`` duplicates what one of the ``warnings`` sentences says.
    That is deliberate: the sentence is what a human reads, the list is what a
    test asserts on without matching prose.
    """

    frame: pd.DataFrame
    store_path: Path
    warnings: List[str] = field(default_factory=list)
    stale_brands: List[str] = field(default_factory=list)
    rows_fetched: int = 0
    months_returned: int = 0


def refresh(
    config: Config,
    source: KeywordVolumeSource,
    data_dir: Path,
    start: date,
    end: date,
    pulled_at: Optional[str] = None,
) -> RunResult:
    """Fetch ``start``..``end`` for ``config``'s brand set and update the store.

    Args:
        config: The validated brand set.
        source: Where volumes come from. Injected so tests can replay canned
            rows instead of calling an API.
        data_dir: Directory holding the CSV store.
        start: First month to request, inclusive.
        end: Last month to request, inclusive.
        pulled_at: Timestamp recorded on every written row. Defaults to now;
            pass a fixed value to make a run byte-reproducible.

    Returns:
        A :class:`RunResult` describing what was fetched and stored.

    Raises:
        DataSourceError: The source returned nothing usable. The store is left
            untouched rather than overwritten with an empty category.
    """
    rows = source.fetch_monthly_volume(
        keywords=config.all_keywords,
        location_code=config.market.location_code,
        language_code=config.market.language_code,
        date_from=f"{start:%Y-%m-%d}",
        date_to=f"{end:%Y-%m-%d}",
    )

    if not rows:
        raise DataSourceError(
            "The API returned no volume data at all. Check that your keywords are "
            "spelled the way people search for them, and that the market is right."
        )

    market = config.market.name
    active_brands = [b.name for b in config.brands]
    warnings: List[str] = []

    # Read before write. `upsert` rewrites the `keywords` column that
    # `counted_keywords` recovers the previous grouping decision from.
    stored = store_module.load_store(data_dir)
    previously_counted = store_module.counted_keywords(stored, market)
    stale = store_module.stale_brands(stored, market, active_brands)
    if stale:
        warnings.append(_stale_warning(stale, market))

    brand_frame, transform_warnings = transform.build_brand_frame(
        rows, config, previously_counted=previously_counted
    )
    warnings.extend(transform_warnings)

    new_rows = store_module.build_rows(
        brand_frame, config, data_source=source.name, pulled_at=pulled_at
    )
    combined = store_module.upsert(
        data_dir, new_rows, config.smoothing_windows, active_brands=active_brands
    )

    warnings.extend(transform.category_set_warnings(combined, config.own_brand.name))

    return RunResult(
        frame=combined,
        store_path=store_module.store_path(data_dir),
        warnings=warnings,
        stale_brands=stale,
        rows_fetched=len(rows),
        months_returned=_distinct_months(rows),
    )


def _distinct_months(rows: Iterable[VolumeRow]) -> int:
    """How many distinct months the response actually covered.

    Derived from the rows rather than read off the adapter. DataForSEOSource
    records the same number as a side effect of fetching, but depending on
    that attribute would make it part of every source's interface — including
    every fake — for a count that is one set comprehension away.
    """
    return len({(row["year"], row["month"]) for row in rows})


def _stale_warning(stale: List[str], market: str) -> str:
    return (
        f"Dropping {', '.join(stale)} from the {market} store — no longer in the "
        "category set. Leaving them in would keep inflating the category total and "
        "understate every remaining brand."
    )
