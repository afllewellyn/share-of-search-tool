"""Turn raw keyword volumes into brand-level Share of Search.

The pipeline, in order:

1. :func:`rows_to_frame`         — VolumeRows to a tidy frame
2. :func:`detect_grouped_keywords` — find keywords Google has silently merged
3. :func:`aggregate_to_brands`   — sum keywords into brands
4. :func:`compute_shares`        — category total and ``sos_pct``
5. :func:`add_rolling_averages`  — trailing smoothing windows

Every function here is pure: frames in, frames out, no file or network I/O.
That is what makes this the testable part of the tool, and it is where the
tests are pointed.
"""

from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Optional, Sequence, Set

import numpy as np
import pandas as pd

from sos.config import Config

logger = logging.getLogger(__name__)

#: Minimum months two keywords must overlap before identical volumes count as
#: evidence that Google grouped them. One shared month is coincidence.
MIN_GROUPING_OVERLAP = 2

#: Above this own-brand share, the competitor set is probably incomplete.
INCOMPLETE_SET_THRESHOLD_PCT = 65.0


# --------------------------------------------------------------------------
# 1. Parse
# --------------------------------------------------------------------------


def rows_to_frame(rows: Iterable[dict]) -> pd.DataFrame:
    """Convert :data:`~sos.datasource.base.VolumeRow` dicts to a tidy frame.

    Returns columns ``keyword``, ``date`` (month start), ``search_volume``.
    ``search_volume`` stays nullable — a missing value means "no data", which
    is not the same as zero demand.
    """
    rows = list(rows)
    if not rows:
        return pd.DataFrame({"keyword": [], "date": [], "search_volume": []}).astype(
            {"keyword": "object", "search_volume": "float64"}
        )

    frame = pd.DataFrame(rows)
    frame["keyword"] = frame["keyword"].astype(str).str.strip().str.lower()
    frame["date"] = pd.to_datetime(
        dict(year=frame["year"].astype(int), month=frame["month"].astype(int), day=1)
    )
    frame["search_volume"] = pd.to_numeric(frame["search_volume"], errors="coerce")

    # A provider echoing the same keyword twice would otherwise double-count it.
    frame = frame.drop_duplicates(subset=["keyword", "date"], keep="last")

    return (
        frame[["keyword", "date", "search_volume"]]
        .sort_values(["keyword", "date"])
        .reset_index(drop=True)
    )


# --------------------------------------------------------------------------
# 2. Grouped-keyword guard
# --------------------------------------------------------------------------


def detect_grouped_keywords(
    frame: pd.DataFrame,
    config: Config,
    min_overlap: int = MIN_GROUPING_OVERLAP,
) -> Dict[str, List[Dict[str, object]]]:
    """Find keywords within a brand that Google Ads has merged into one figure.

    Google Ads returns a *combined* volume for keywords it considers close
    variants — "acme" and "acme app" can both come back as the same number,
    because that number covers both. Summing them doubles the brand's volume
    and silently inflates its share.

    The signal is identical volumes across every month where both keywords
    have data. We require at least ``min_overlap`` such months, and at least
    one non-zero value: two keywords that both return zero everywhere are
    dead, not grouped.

    Comparison is per brand only. Two different brands showing the same volume
    is a coincidence, not a merge.

    Returns:
        ``{brand_name: [{"kept": kw, "dropped": [kw, ...]}, ...]}`` for brands
        with at least one detected group.
    """
    if frame.empty:
        return {}

    pivot = frame.pivot_table(index="date", columns="keyword", values="search_volume", aggfunc="last")
    groups: Dict[str, List[Dict[str, object]]] = {}

    for brand in config.brands:
        keywords = [k for k in brand.keywords if k in pivot.columns]
        if len(keywords) < 2:
            continue

        # Union-find over the brand's keywords.
        parent = {k: k for k in keywords}

        def find(key: str) -> str:
            while parent[key] != key:
                parent[key] = parent[parent[key]]
                key = parent[key]
            return key

        def union(a: str, b: str) -> None:
            root_a, root_b = find(a), find(b)
            if root_a != root_b:
                # Keep the earlier keyword (config order) as the group's root.
                first, second = sorted((root_a, root_b), key=keywords.index)
                parent[second] = first

        for i, left in enumerate(keywords):
            for right in keywords[i + 1 :]:
                if _series_are_identical(pivot[left], pivot[right], min_overlap):
                    union(left, right)

        clusters: Dict[str, List[str]] = {}
        for keyword in keywords:
            clusters.setdefault(find(keyword), []).append(keyword)

        detected = []
        for root, members in clusters.items():
            if len(members) < 2:
                continue
            members = sorted(members, key=keywords.index)
            detected.append({"kept": members[0], "dropped": members[1:]})

        if detected:
            groups[brand.name] = detected

    return groups


def _series_are_identical(left: pd.Series, right: pd.Series, min_overlap: int) -> bool:
    """True if two keyword series match everywhere they both have data."""
    overlap = left.notna() & right.notna()
    if int(overlap.sum()) < min_overlap:
        return False

    left_values = left[overlap].to_numpy()
    right_values = right[overlap].to_numpy()

    if not np.array_equal(left_values, right_values):
        return False

    # All-zero pairs are dead keywords, not grouped ones.
    return bool((left_values != 0).any())


def grouped_keyword_warnings(groups: Dict[str, List[Dict[str, object]]]) -> List[str]:
    """Human-readable warnings for detected keyword groups."""
    warnings = []
    for brand, clusters in groups.items():
        for cluster in clusters:
            dropped = ", ".join(f"'{k}'" for k in cluster["dropped"])  # type: ignore[arg-type]
            warnings.append(
                f"{brand}: {dropped} returned identical volumes to '{cluster['kept']}' "
                "in every overlapping month — Google Ads appears to have grouped them. "
                "Counting once to avoid double-counting this brand's volume."
            )
    return warnings


def dropped_keywords(groups: Dict[str, List[Dict[str, object]]]) -> Set[str]:
    """Flatten detected groups into the set of keywords to exclude."""
    dropped: Set[str] = set()
    for clusters in groups.values():
        for cluster in clusters:
            dropped.update(cluster["dropped"])  # type: ignore[arg-type]
    return dropped


# --------------------------------------------------------------------------
# 3. Aggregate keyword -> brand
# --------------------------------------------------------------------------


def aggregate_to_brands(
    frame: pd.DataFrame,
    config: Config,
    exclude_keywords: Optional[Set[str]] = None,
) -> pd.DataFrame:
    """Sum each brand's keywords into one volume per brand per month.

    Every brand appears in every month of the observed range, even when the
    provider returned nothing for it. A brand with no data for a month gets a
    null ``raw_volume`` rather than a zero — an API gap is not zero demand,
    and the difference changes the answer.

    Returns columns ``date``, ``brand``, ``raw_volume``, ``keywords``.
    """
    exclude_keywords = exclude_keywords or set()

    keyword_to_brand: Dict[str, str] = {}
    brand_keywords: Dict[str, List[str]] = {}
    for brand in config.brands:
        kept = [k for k in brand.keywords if k not in exclude_keywords]
        brand_keywords[brand.name] = kept
        for keyword in kept:
            keyword_to_brand[keyword] = brand.name

    if frame.empty:
        return pd.DataFrame(columns=["date", "brand", "raw_volume", "keywords"])

    known = frame[frame["keyword"].isin(keyword_to_brand)].copy()
    if known.empty:
        return pd.DataFrame(columns=["date", "brand", "raw_volume", "keywords"])

    known["brand"] = known["keyword"].map(keyword_to_brand)

    # min_count=1 keeps the result null when every keyword is null, instead of
    # collapsing an all-missing brand-month to a misleading 0.
    aggregated = (
        known.groupby(["brand", "date"])["search_volume"]
        .apply(lambda s: s.sum(min_count=1))
        .rename("raw_volume")
        .reset_index()
    )

    # Reindex onto the full brand x month grid so gaps are explicit nulls.
    months = pd.date_range(known["date"].min(), known["date"].max(), freq="MS")
    grid = pd.MultiIndex.from_product(
        [[b.name for b in config.brands], months], names=["brand", "date"]
    )
    aggregated = (
        aggregated.set_index(["brand", "date"]).reindex(grid).reset_index()
    )

    aggregated["keywords"] = aggregated["brand"].map(
        {name: ";".join(kws) for name, kws in brand_keywords.items()}
    )

    return aggregated.sort_values(["date", "brand"]).reset_index(drop=True)


def missing_brand_months(brand_frame: pd.DataFrame) -> pd.DataFrame:
    """Brand-months where the provider returned no data at all."""
    if brand_frame.empty:
        return brand_frame
    return brand_frame[brand_frame["raw_volume"].isna()][["date", "brand"]]


# --------------------------------------------------------------------------
# 4. Share
# --------------------------------------------------------------------------


def compute_shares(brand_frame: pd.DataFrame) -> pd.DataFrame:
    """Add ``category_total_volume`` and ``sos_pct``.

    The denominator is the sum of the *defined category set* for that month —
    never total search volume, and never a fixed historical baseline. Brands
    with no data for a month contribute nothing to the total and get a null
    share rather than a zero.
    """
    frame = brand_frame.copy()
    if frame.empty:
        frame["category_total_volume"] = []
        frame["sos_pct"] = []
        return frame

    frame["category_total_volume"] = frame.groupby("date")["raw_volume"].transform(
        lambda s: s.sum(min_count=1)
    )

    total = frame["category_total_volume"]
    frame["sos_pct"] = np.where(
        frame["raw_volume"].notna() & total.notna() & (total > 0),
        frame["raw_volume"] / total.replace({0: np.nan}) * 100.0,
        np.nan,
    )

    return frame


# --------------------------------------------------------------------------
# 5. Rolling averages
# --------------------------------------------------------------------------


def add_rolling_averages(
    frame: pd.DataFrame,
    windows: Sequence[int],
    volume_windows: Optional[Sequence[int]] = None,
) -> pd.DataFrame:
    """Add trailing rolling means, per brand, per window.

    Windows are *full* — a value appears only once the whole window has data,
    so a 3-month average never quietly averages two months. Each brand's
    series is reindexed onto a complete month range first, so a gap in the
    data shortens the window's coverage instead of silently sliding it.

    ``sos_pct_{w}mo`` is produced for every window in ``windows``.
    ``volume_{w}mo`` is produced for every window in ``volume_windows``,
    defaulting to the shortest window only.
    """
    frame = frame.sort_values(["brand", "date"]).reset_index(drop=True)
    windows = list(windows)
    volume_windows = list(volume_windows) if volume_windows is not None else windows[:1]

    for window in windows:
        frame[f"sos_pct_{window}mo"] = np.nan
    for window in volume_windows:
        frame[f"volume_{window}mo"] = np.nan

    if frame.empty:
        return frame

    for _, group in frame.groupby("brand", sort=False):
        indexed = group.set_index("date")
        full_range = pd.date_range(indexed.index.min(), indexed.index.max(), freq="MS")

        for window, column, source in [
            *[(w, f"sos_pct_{w}mo", "sos_pct") for w in windows],
            *[(w, f"volume_{w}mo", "raw_volume") for w in volume_windows],
        ]:
            rolled = (
                indexed[source]
                .reindex(full_range)
                .rolling(window=window, min_periods=window)
                .mean()
            )
            frame.loc[group.index, column] = rolled.reindex(group["date"]).to_numpy()

    return frame


# --------------------------------------------------------------------------
# Orchestration + sanity checks
# --------------------------------------------------------------------------


def build_brand_frame(
    rows: Iterable[dict],
    config: Config,
) -> "tuple[pd.DataFrame, List[str]]":
    """Run the full keyword-to-share pipeline.

    Returns the brand-month frame (without rolling averages, which are added
    at store level across the whole series) plus any warnings worth printing.
    """
    warnings: List[str] = []

    frame = rows_to_frame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["date", "brand", "raw_volume", "keywords"]), [
            "The data source returned no volume data at all."
        ]

    groups = detect_grouped_keywords(frame, config)
    warnings.extend(grouped_keyword_warnings(groups))

    brand_frame = aggregate_to_brands(frame, config, exclude_keywords=dropped_keywords(groups))

    gaps = missing_brand_months(brand_frame)
    if not gaps.empty:
        by_brand = gaps.groupby("brand")["date"].count()
        for brand, count in by_brand.items():
            warnings.append(
                f"{brand}: no data returned for {count} month(s). Those months are left "
                "empty rather than counted as zero — a gap in the API is not zero demand."
            )

    return brand_frame, warnings


def category_set_warnings(frame: pd.DataFrame, own_brand: str) -> List[str]:
    """Warn when the own brand's share suggests a missing competitor.

    An incomplete competitor set makes every percentage in the report wrong,
    and it fails quietly — the numbers still look plausible.
    """
    if frame.empty or "sos_pct" not in frame.columns:
        return []

    own = frame[frame["brand"] == own_brand].dropna(subset=["sos_pct"])
    if own.empty:
        return []

    latest = own.sort_values("date").iloc[-1]
    if latest["sos_pct"] > INCOMPLETE_SET_THRESHOLD_PCT:
        return [
            f"{own_brand} holds {latest['sos_pct']:.1f}% of the category in "
            f"{latest['date']:%B %Y}. Above ~{INCOMPLETE_SET_THRESHOLD_PCT:.0f}% usually means "
            "the competitor set is missing someone rather than that the brand is dominant. "
            "Every percentage here is relative to the brands you defined."
        ]
    return []
