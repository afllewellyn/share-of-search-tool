"""Compute the ``month_facts`` payload.

Everything numeric in the commentary is calculated here, in pandas, and
nowhere else. The payload is deliberately the *only* thing a language model
would ever be handed (see :mod:`sos.commentary`): it phrases and
contextualises, it never calculates. Arithmetic done by an LLM is arithmetic
you cannot check.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from sos.config import Config

#: A month-on-month move counts as signal when it exceeds this multiple of the
#: brand's own trailing volatility. Loosened to NOISE_MULTIPLIER_SPARSE until
#: there's a full year of history to measure volatility against.
NOISE_MULTIPLIER = 1.5
NOISE_MULTIPLIER_SPARSE = 2.0

#: Minimum months of month-on-month history before the tighter multiplier applies.
NOISE_HISTORY_MONTHS = 12

#: A category total moving by at least this much makes share shifts suspect as
#: denominator effects rather than brand-level movement.
CATEGORY_MOVE_THRESHOLD_PCT = 5.0

#: ...and the own brand must have moved less than this fraction of the
#: category's move for the shift to be called denominator-driven.
CATEGORY_DRIVEN_RATIO = 0.5


def month_facts(
    frame: pd.DataFrame,
    config: Config,
    month: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the facts payload for one month of one market.

    Args:
        frame: The recomputed store, or any frame with the same columns.
        config: The brand set, used for own-brand and ambiguity metadata.
        month: ``YYYY-MM`` to describe. Defaults to the latest month present.

    Returns:
        A JSON-serialisable dict. Empty ``brands`` means there was no usable
        data for the month.
    """
    market = config.market.name
    subset = frame[frame["market"].astype(str) == market].copy() if not frame.empty else frame

    if subset.empty:
        return _empty_facts(config, month)

    subset["date"] = pd.to_datetime(subset["date"])
    subset["raw_volume"] = pd.to_numeric(subset["raw_volume"], errors="coerce")
    subset["sos_pct"] = pd.to_numeric(subset["sos_pct"], errors="coerce")

    months = sorted(subset["date"].unique())
    target = pd.Timestamp(f"{month}-01") if month else pd.Timestamp(months[-1])
    if target not in months:
        return _empty_facts(config, month)

    previous = target - pd.DateOffset(months=1)
    year_ago = target - pd.DateOffset(years=1)

    current_rows = subset[subset["date"] == target]
    previous_rows = subset[subset["date"] == previous]
    year_ago_rows = subset[subset["date"] == year_ago]

    ranks_now = _ranks(current_rows)
    ranks_before = _ranks(previous_rows)

    brand_meta = {b.name: b for b in config.brands}
    brands: List[Dict[str, Any]] = []

    for _, row in current_rows.sort_values("sos_pct", ascending=False).iterrows():
        name = str(row["brand"])
        sos = _clean(row.get("sos_pct"))
        previous_sos = _lookup(previous_rows, name, "sos_pct")
        year_ago_sos = _lookup(year_ago_rows, name, "sos_pct")

        mom_delta = _delta(sos, previous_sos)
        threshold = _noise_threshold(subset, name)
        meta = brand_meta.get(name)

        brands.append(
            {
                "brand": name,
                "is_own_brand": bool(meta.is_own_brand) if meta else bool(row.get("is_own_brand")),
                "ambiguous": bool(meta.ambiguous) if meta else False,
                "sos_pct": _round(sos),
                "raw_volume": _clean(row.get("raw_volume")),
                "mom_delta_pp": _round(mom_delta),
                "yoy_delta_pp": _round(_delta(sos, year_ago_sos)),
                "rank": ranks_now.get(name),
                "rank_change": _rank_change(ranks_before.get(name), ranks_now.get(name)),
                "noise_threshold_pp": _round(threshold),
                "exceeds_noise_threshold": (
                    None
                    if mom_delta is None or threshold is None
                    else bool(abs(mom_delta) > threshold)
                ),
                "data_gap": bool(pd.isna(row.get("raw_volume"))),
            }
        )

    category_now = _category_total(current_rows)
    category_before = _category_total(previous_rows)
    category_change = _pct_change(category_now, category_before)

    own_name = config.own_brand.name
    own = next((b for b in brands if b["brand"] == own_name), None)
    own_volume_change = _pct_change(
        _lookup(current_rows, own_name, "raw_volume"),
        _lookup(previous_rows, own_name, "raw_volume"),
    )

    return {
        "month": f"{target:%Y-%m}",
        "month_label": f"{target:%B %Y}",
        "market": market,
        "months_of_history": len(months),
        "date_range": {"start": f"{pd.Timestamp(months[0]):%Y-%m}", "end": f"{pd.Timestamp(months[-1]):%Y-%m}"},
        "own_brand": own_name,
        "brand_count": len(brands),
        "brands": brands,
        "leader": brands[0]["brand"] if brands else None,
        "category_total_volume": category_now,
        "category_total_change_pct": _round(category_change),
        "own_volume_change_pct": _round(own_volume_change),
        "category_driven": _is_category_driven(category_change, own_volume_change),
        "ambiguous_brands": config.ambiguous_brands,
        "brands_with_data_gaps": [b["brand"] for b in brands if b["data_gap"]],
        "own_brand_facts": own,
    }


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _empty_facts(config: Config, month: Optional[str]) -> Dict[str, Any]:
    return {
        "month": month,
        "month_label": None,
        "market": config.market.name,
        "months_of_history": 0,
        "date_range": {"start": None, "end": None},
        "own_brand": config.own_brand.name,
        "brand_count": 0,
        "brands": [],
        "leader": None,
        "category_total_volume": None,
        "category_total_change_pct": None,
        "own_volume_change_pct": None,
        "category_driven": False,
        "ambiguous_brands": config.ambiguous_brands,
        "brands_with_data_gaps": [],
        "own_brand_facts": None,
    }


def _ranks(rows: pd.DataFrame) -> Dict[str, Optional[int]]:
    """Rank brands by share, 1 = largest. Ties share the higher rank."""
    if rows.empty:
        return {}
    ranked = rows.dropna(subset=["sos_pct"]).copy()
    if ranked.empty:
        return {}
    ranked["rank"] = ranked["sos_pct"].rank(ascending=False, method="min").astype(int)
    return dict(zip(ranked["brand"].astype(str), ranked["rank"]))


def _rank_change(before: Optional[int], now: Optional[int]) -> Optional[int]:
    """Positive means the brand moved up the table."""
    if before is None or now is None:
        return None
    return int(before - now)


def _lookup(rows: pd.DataFrame, brand: str, column: str) -> Optional[float]:
    if rows.empty:
        return None
    match = rows[rows["brand"].astype(str) == brand]
    if match.empty:
        return None
    return _clean(match.iloc[0].get(column))


def _category_total(rows: pd.DataFrame) -> Optional[float]:
    if rows.empty:
        return None
    total = pd.to_numeric(rows["raw_volume"], errors="coerce").sum(min_count=1)
    return _clean(total)


def _noise_threshold(subset: pd.DataFrame, brand: str) -> Optional[float]:
    """How big a month-on-month share move has to be to mean anything.

    Measured against the brand's own trailing volatility — the standard
    deviation of its month-on-month share changes over the last year — so a
    naturally jumpy brand needs a bigger move to count than a stable one.
    """
    series = (
        subset[subset["brand"].astype(str) == brand]
        .sort_values("date")["sos_pct"]
        .astype(float)
    )
    changes = series.diff().dropna()
    if len(changes) < 2:
        return None

    trailing = changes.tail(NOISE_HISTORY_MONTHS)
    stdev = float(trailing.std(ddof=1))
    if not np.isfinite(stdev) or stdev == 0:
        return None

    multiplier = NOISE_MULTIPLIER if len(changes) >= NOISE_HISTORY_MONTHS else NOISE_MULTIPLIER_SPARSE
    return multiplier * stdev


def _is_category_driven(category_change: Optional[float], own_change: Optional[float]) -> bool:
    """True when the own brand's share moved mainly because the category did.

    A brand's share rising because a competitor collapsed is the single most
    common misreading of this metric, and the one most likely to end up in a
    client deck as a win. Flagging it is the point.
    """
    if category_change is None or own_change is None:
        return False
    if abs(category_change) < CATEGORY_MOVE_THRESHOLD_PCT:
        return False
    return abs(own_change) < CATEGORY_DRIVEN_RATIO * abs(category_change)


def _delta(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous is None:
        return None
    return current - previous


def _pct_change(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / previous * 100.0


def _clean(value: Any) -> Optional[float]:
    """Convert numpy/NA values to plain floats or None, for JSON safety."""
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return None
    return float(value)


def _round(value: Optional[float], places: int = 2) -> Optional[float]:
    return None if value is None else round(value, places)
