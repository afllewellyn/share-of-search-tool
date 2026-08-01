"""Render the store into one self-contained HTML file.

The data is inlined as a JavaScript constant rather than fetched from a
sibling ``data.json``. That is not a stylistic choice: a page opened over
``file://`` cannot fetch a local JSON file — the browser blocks it as a
cross-origin request — so the dashboard would silently render empty exactly
when someone double-clicks it, which is the main way it gets opened.

Chart.js is inlined for the same reason. A CDN tag would leave the file
looking self-contained while quietly depending on the network — and on a
corporate laptop, an offline train, or behind an egress policy that blocks
the CDN, the recipient gets a page with no charts and no explanation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from sos import commentary as commentary_module
from sos import facts as facts_module
from sos.config import Config

TEMPLATE_PATH = Path(__file__).parent / "template.html"
CHARTJS_PATH = Path(__file__).parent / "vendor" / "chart.umd.min.js"
CHARTJS_VERSION = "4.4.1"

PAYLOAD_PLACEHOLDER = "__SOS_PAYLOAD__"
TITLE_PLACEHOLDER = "__SOS_TITLE__"
CHARTJS_PLACEHOLDER = "__SOS_CHARTJS__"


def build_dashboard(
    frame: pd.DataFrame,
    config: Config,
    out_path: Path,
    generated_at: Optional[str] = None,
) -> Path:
    """Write the dashboard and return the path it landed at."""
    payload = build_payload(frame, config, generated_at=generated_at)

    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    html = html.replace(
        PAYLOAD_PLACEHOLDER,
        json.dumps(payload, allow_nan=False, separators=(",", ":")),
    )
    html = html.replace(TITLE_PLACEHOLDER, f"Share of Search — {config.own_brand.name} ({config.market.name})")
    # Inlined last: the library is large and contains no placeholders of its own.
    html = html.replace(CHARTJS_PLACEHOLDER, _chartjs_source())

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def _chartjs_source() -> str:
    """Read the vendored Chart.js build.

    Missing rather than fatal: the dashboard degrades to its table and CSV
    exports, which is a far better outcome than refusing to build at all.
    """
    if not CHARTJS_PATH.exists():
        return (
            "console.error('Chart.js was not bundled with this install "
            "(sos/dashboard/vendor/chart.umd.min.js is missing).');"
        )
    return CHARTJS_PATH.read_text(encoding="utf-8")


def build_payload(
    frame: pd.DataFrame,
    config: Config,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Assemble everything the page needs into one JSON-safe dict."""
    market = config.market.name
    subset = frame[frame["market"].astype(str) == market].copy()
    if subset.empty:
        raise ValueError(
            f"No stored data for market '{market}'. Run `sos run` for this market first."
        )

    subset["date"] = pd.to_datetime(subset["date"])
    months = [pd.Timestamp(m) for m in sorted(subset["date"].unique())]
    month_keys = [f"{m:%Y-%m}" for m in months]

    # Brand order follows the config so colours stay attached to a brand
    # across rebuilds, rather than shuffling with whoever is winning.
    brand_names = [b.name for b in config.brands if b.name in set(subset["brand"].astype(str))]
    brand_names += [
        name
        for name in sorted(subset["brand"].astype(str).unique())
        if name not in brand_names
    ]

    series: Dict[str, Dict[str, List[Optional[float]]]] = {"raw": {}}
    for window in config.smoothing_windows:
        series[str(window)] = {}

    for name in brand_names:
        brand_rows = subset[subset["brand"].astype(str) == name].set_index("date")
        series["raw"][name] = _column_series(brand_rows, "sos_pct", months)
        for window in config.smoothing_windows:
            series[str(window)][name] = _column_series(brand_rows, f"sos_pct_{window}mo", months)

    payload_facts = facts_module.month_facts(subset, config)
    bullets = commentary_module.generate(payload_facts)

    columns = [c for c in subset.columns]
    rows = _records(subset.sort_values(["date", "brand"]), columns)

    brand_meta = {b.name: b for b in config.brands}

    return {
        "generated_at": generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "market": market,
        "location_code": config.market.location_code,
        "language_code": config.market.language_code,
        "data_source": _first_value(subset, "data_source", "unknown"),
        "own_brand": config.own_brand.name,
        "smoothing_windows": list(config.smoothing_windows),
        "ambiguous_brands": config.ambiguous_brands,
        "months": month_keys,
        "brands": [
            {
                "name": name,
                "is_own_brand": bool(brand_meta[name].is_own_brand) if name in brand_meta else False,
                "ambiguous": bool(brand_meta[name].ambiguous) if name in brand_meta else False,
                "keywords": list(brand_meta[name].keywords) if name in brand_meta else [],
            }
            for name in brand_names
        ],
        "series": series,
        "latest": {
            "month": payload_facts.get("month") or month_keys[-1],
            "rows": payload_facts.get("brands", []),
        },
        "commentary": bullets,
        "columns": columns,
        "rows": rows,
    }


def _column_series(
    brand_rows: pd.DataFrame,
    column: str,
    months: List[pd.Timestamp],
) -> List[Optional[float]]:
    """Values for one brand across every month, nulls where data is missing.

    JSON has no NaN, so gaps become ``null`` — which the chart renders as a
    break in the line rather than a drop to zero.
    """
    if column not in brand_rows.columns:
        return [None] * len(months)
    aligned = pd.to_numeric(brand_rows[column], errors="coerce").reindex(months)
    return [None if pd.isna(v) else round(float(v), 3) for v in aligned]


def _records(frame: pd.DataFrame, columns: List[str]) -> List[Dict[str, Any]]:
    """Frame to JSON-safe dicts, with dates as ``YYYY-MM-DD`` and NaN as null."""
    output = frame.copy()
    if "date" in output.columns:
        output["date"] = pd.to_datetime(output["date"]).dt.strftime("%Y-%m-%d")

    records = []
    for _, row in output.iterrows():
        record: Dict[str, Any] = {}
        for column in columns:
            record[column] = _json_safe(row.get(column))
        records.append(record)
    return records


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(value) else round(float(value), 4)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _first_value(frame: pd.DataFrame, column: str, default: str) -> str:
    if column not in frame.columns or frame[column].dropna().empty:
        return default
    return str(frame[column].dropna().iloc[0])
