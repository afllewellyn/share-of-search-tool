"""The flat-file data store.

One CSV, one row per brand per month per market. No database, no server —
``git clone`` and go.

Two invariants hold this together:

**``raw_volume`` is the only source of truth.** Category totals, shares and
rolling averages are derived columns. They are thrown away and recomputed
across the entire series after every write. That is cheap at this data size
and it is what makes Google's habit of revising historical months harmless
rather than corrupting.

**Writes are atomic.** The new CSV goes to a temp file in the same directory
and is then renamed over the old one, so an interrupted run leaves the
previous store intact rather than a half-written file.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import pandas as pd

from sos.config import DEFAULT_SMOOTHING_WINDOWS, Brand, Config, ConfigError, Market
from sos.transform import add_rolling_averages, compute_shares

logger = logging.getLogger(__name__)

STORE_FILENAME = "sos_monthly.csv"

#: Identity of a row. Re-running a pull replaces matching rows rather than
#: appending, which is what makes every command idempotent.
UPSERT_KEY = ["date", "brand", "market"]

BASE_COLUMNS = [
    "date",
    "year",
    "month",
    "brand",
    "is_own_brand",
    "market",
    "location_code",
    "language_code",
    "keywords",
    "raw_volume",
    "category_total_volume",
    "sos_pct",
]

TRAILING_COLUMNS = ["data_source", "pulled_at"]


def store_path(data_dir: Path) -> Path:
    return Path(data_dir) / STORE_FILENAME


def load_store(data_dir: Path) -> pd.DataFrame:
    """Read the store, or return an empty frame if it doesn't exist yet."""
    path = store_path(data_dir)
    if not path.exists():
        return pd.DataFrame(columns=BASE_COLUMNS + TRAILING_COLUMNS)

    frame = pd.read_csv(path)
    if frame.empty:
        return frame

    frame["date"] = pd.to_datetime(frame["date"])
    return frame


def is_empty(data_dir: Path) -> bool:
    """True when there is nothing stored yet — the trigger for auto-backfill."""
    path = store_path(data_dir)
    if not path.exists():
        return True
    try:
        return pd.read_csv(path).empty
    except pd.errors.EmptyDataError:
        return True


def build_rows(
    brand_frame: pd.DataFrame,
    config: Config,
    data_source: str,
    pulled_at: Optional[str] = None,
) -> pd.DataFrame:
    """Attach brand and market metadata to a brand-month frame.

    Derived columns are left out on purpose — :func:`recompute` fills them in
    once the new rows have been merged with everything already stored.
    """
    frame = brand_frame.copy()
    if frame.empty:
        return pd.DataFrame(columns=BASE_COLUMNS + TRAILING_COLUMNS)

    own_names = {b.name for b in config.brands if b.is_own_brand}

    frame["year"] = frame["date"].dt.year
    frame["month"] = frame["date"].dt.month
    frame["is_own_brand"] = frame["brand"].isin(own_names)
    frame["market"] = config.market.name
    frame["location_code"] = config.market.location_code
    frame["language_code"] = config.market.language_code
    frame["data_source"] = data_source
    frame["pulled_at"] = pulled_at or datetime.now(timezone.utc).isoformat(timespec="seconds")

    for column in ("category_total_volume", "sos_pct"):
        if column not in frame.columns:
            frame[column] = pd.NA

    ordered = [c for c in BASE_COLUMNS if c in frame.columns] + TRAILING_COLUMNS
    return frame[ordered]


def stale_brands(frame: pd.DataFrame, market: str, active_brands: Iterable[str]) -> List[str]:
    """Brands stored for a market that are no longer in the category set."""
    if frame.empty or "brand" not in frame.columns:
        return []
    subset = frame[frame["market"].astype(str) == str(market)]
    stored = {str(b) for b in subset["brand"].dropna().unique()}
    return sorted(stored - set(active_brands))


def upsert(
    data_dir: Path,
    new_rows: pd.DataFrame,
    smoothing_windows: Sequence[int],
    active_brands: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """Merge ``new_rows`` into the store and rewrite it, atomically.

    Existing rows sharing a ``(date, brand, market)`` key with an incoming row
    are replaced, never duplicated. Every derived column is then recomputed
    across the whole series before the file is written.

    ``active_brands`` is the category set this write was produced from. Rows
    for any *other* brand in the same market are dropped first. Without that,
    swapping a competitor out of the config and running an ordinary trailing
    refresh would leave the old brand in the store — the incoming rows never
    collide with its key, so nothing replaces it — and it would keep
    contributing to the category total, understating every remaining brand's
    share. Other markets are untouched.
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    existing = load_store(data_dir)

    if not new_rows.empty:
        new_rows = new_rows.copy()
        new_rows["date"] = pd.to_datetime(new_rows["date"])

    if active_brands is not None and not existing.empty and not new_rows.empty:
        markets = {str(m) for m in new_rows["market"].dropna().unique()}
        drop = existing["market"].astype(str).isin(markets) & ~existing["brand"].astype(str).isin(
            set(active_brands)
        )
        if drop.any():
            logger.info(
                "Dropping %d row(s) for brands no longer in the category set: %s",
                int(drop.sum()),
                ", ".join(sorted(existing.loc[drop, "brand"].astype(str).unique())),
            )
            existing = existing[~drop]

    if existing.empty:
        combined = new_rows.copy()
    elif new_rows.empty:
        combined = existing.copy()
    else:
        incoming_keys = set(
            zip(new_rows["date"], new_rows["brand"].astype(str), new_rows["market"].astype(str))
        )
        existing_keys = list(
            zip(existing["date"], existing["brand"].astype(str), existing["market"].astype(str))
        )
        keep = [key not in incoming_keys for key in existing_keys]
        combined = pd.concat([existing[keep], new_rows], ignore_index=True)

    if combined.empty:
        write_store(data_dir, combined)
        return combined

    combined = recompute(combined, smoothing_windows)
    write_store(data_dir, combined)
    return combined


def recompute(frame: pd.DataFrame, smoothing_windows: Sequence[int]) -> pd.DataFrame:
    """Rebuild every derived column from ``raw_volume``, per market.

    Called after every write. Markets are recomputed independently — a US
    category total must never include UK volume.
    """
    if frame.empty:
        return frame

    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["raw_volume"] = pd.to_numeric(frame["raw_volume"], errors="coerce")

    # Derived columns are rebuilt from scratch, so drop any stale ones first.
    derived_prefixes = ("sos_pct_", "volume_")
    stale = [c for c in frame.columns if c.startswith(derived_prefixes)]
    frame = frame.drop(columns=stale + ["category_total_volume", "sos_pct"], errors="ignore")

    per_market = []
    for market, group in frame.groupby("market", sort=False):
        shared = compute_shares(group)
        shared = add_rolling_averages(shared, smoothing_windows)
        per_market.append(shared)

    combined = pd.concat(per_market, ignore_index=True)
    combined["year"] = combined["date"].dt.year
    combined["month"] = combined["date"].dt.month

    return combined.sort_values(["market", "date", "brand"]).reset_index(drop=True)


def order_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Put columns in the documented schema order.

    Rolling-average columns sit between ``sos_pct`` and ``data_source``, named
    for the window that produced them, so a store built with non-default
    smoothing windows is still self-describing.
    """
    rolling = sorted(
        [c for c in frame.columns if c.startswith("sos_pct_") or c.startswith("volume_")],
        key=lambda c: (not c.startswith("sos_pct_"), _window_of(c)),
    )
    ordered = (
        [c for c in BASE_COLUMNS if c in frame.columns]
        + rolling
        + [c for c in TRAILING_COLUMNS if c in frame.columns]
    )
    remainder = [c for c in frame.columns if c not in ordered]
    return frame[ordered + remainder]


def _window_of(column: str) -> int:
    digits = "".join(ch for ch in column.split("_")[-1] if ch.isdigit())
    return int(digits) if digits else 0


def write_store(data_dir: Path, frame: pd.DataFrame) -> Path:
    """Write the store atomically: temp file in the same directory, then rename.

    ``os.replace`` is atomic on the same filesystem, so a crash mid-write can
    never leave a truncated store behind.
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    target = store_path(data_dir)

    output = frame.copy()
    if not output.empty:
        output = order_columns(output)
        output["date"] = pd.to_datetime(output["date"]).dt.strftime("%Y-%m-%d")

    handle = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".tmp",
        prefix=f".{STORE_FILENAME}.",
        dir=str(data_dir),
        delete=False,
        newline="",
        encoding="utf-8",
    )
    try:
        with handle:
            output.to_csv(handle, index=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, target)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise

    return target


def counted_keywords(frame: pd.DataFrame, market: str) -> Optional[Dict[str, List[str]]]:
    """What a previous run actually counted, per brand, for this market.

    The ``keywords`` column records the set left after the grouped-keyword
    guard ran, so this recovers that decision without needing a new column.
    Returns None when there is nothing stored to learn from.
    """
    if frame.empty or "keywords" not in frame.columns:
        return None

    subset = frame[frame["market"].astype(str) == str(market)]
    if subset.empty:
        return None

    counted: Dict[str, List[str]] = {}
    for brand, group in subset.groupby("brand", sort=False):
        # The most recent row reflects the most recent decision.
        latest = group.sort_values("date").iloc[-1]
        keywords = [k for k in str(latest.get("keywords") or "").split(";") if k]
        if keywords:
            counted[str(brand)] = keywords
    return counted or None


def config_from_store(frame: pd.DataFrame, market: Optional[str] = None) -> Config:
    """Reconstruct the brand set from what's already stored.

    Lets ``sos dashboard`` work with no arguments after an ad-hoc ``sos run``,
    which is the whole point of the ad-hoc path — having to restate the
    competitor set just to render it would defeat it.

    The store records everything needed except the ``ambiguous`` flags, which
    live only in a config file. They default to false here, so a brand set with
    ambiguity worth flagging should be rendered with ``--config``.
    """
    if frame.empty:
        raise ConfigError("The data store is empty. Run `sos run` first.")

    markets = [str(m) for m in frame["market"].dropna().unique()]
    if market is None:
        market = markets[0]
    elif market not in markets:
        raise ConfigError(
            f"No stored data for market '{market}'. Stored markets: {', '.join(sorted(markets))}."
        )

    subset = frame[frame["market"].astype(str) == market]
    first = subset.iloc[0]

    brands = []
    for name, group in subset.groupby("brand", sort=True):
        row = group.iloc[0]
        keywords = [k for k in str(row.get("keywords") or "").split(";") if k]
        brands.append(
            Brand(
                name=str(name),
                keywords=keywords or [str(name).lower()],
                is_own_brand=bool(row.get("is_own_brand")),
            )
        )
    # Own brand first, as a config file would have it — that keeps the first
    # chart colour on the brand the report is about.
    brands.sort(key=lambda b: not b.is_own_brand)

    # Rolling columns are named for the window that produced them.
    windows = sorted(
        {_window_of(c) for c in subset.columns if c.startswith("sos_pct_") and _window_of(c)}
    )

    config = Config(
        market=Market(
            name=market,
            location_code=int(first.get("location_code") or 0),
            language_code=str(first.get("language_code") or "en"),
        ),
        brands=brands,
        smoothing_windows=windows or list(DEFAULT_SMOOTHING_WINDOWS),
    )

    if not any(b.is_own_brand for b in config.brands):
        raise ConfigError(
            "The stored data has no brand marked as your own, so there is nothing to "
            "report share for. Re-run `sos run` to rebuild it."
        )
    return config


def existing_months(data_dir: Path, market: str) -> List[pd.Timestamp]:
    """Months already stored for a market, sorted."""
    frame = load_store(data_dir)
    if frame.empty:
        return []
    months = frame[frame["market"].astype(str) == market]["date"]
    return sorted(pd.to_datetime(months).unique())
