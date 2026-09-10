"""The web front door: one JSON request in, one finished report out.

This is the second way into the tool, next to the CLI. A client who never
opens a terminal fills in a form; the form posts here; they get the same
dashboard ``sos dashboard`` would have written. Nothing about the pipeline
changes — :func:`run_request` is :func:`sos.run.refresh` followed by
:func:`sos.dashboard.build_dashboard`, over a throwaway store.

Three things distinguish a **web run** from a CLI run:

- **It is always a backfill.** There is no store to refresh; each request
  starts empty and pulls ``months`` of history. The grouping-guard decision
  is therefore made fresh from the response every time.
- **Every request is one paid API call.** There is no dry run and no cache.
  ``cost_usd`` is returned so the page can say so.
- **It is unauthenticated.** So the limits below cap what one submission can
  ask for — not because DataForSEO charges per keyword (it doesn't) but so
  a runaway form can't send 1000-keyword batches on someone else's account.

Everything here is importable without Flask or the network, so the whole
path from request dict to HTML is testable with the fake source.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sos import commentary, facts
from sos.config import (
    COMMON_LOCATIONS,
    DEFAULT_SMOOTHING_WINDOWS,
    Brand,
    Config,
    ConfigError,
    Market,
    _validate,
)
from sos.dashboard.build import build_dashboard
from sos.datasource.base import KeywordVolumeSource
from sos.run import last_complete_month, refresh, shift_months

#: Months of history a web run may ask for. 48 is the deepest Google Ads goes.
ALLOWED_MONTHS = (12, 24, 48)
DEFAULT_MONTHS = 24

#: Size caps for one submission. Generous for a real category, tight enough
#: that nobody can turn the form into a bulk keyword-volume scraper.
MAX_COMPETITORS = 12
MAX_KEYWORDS_PER_BRAND = 8
MAX_NAME_LENGTH = 80
MAX_KEYWORD_LENGTH = 80


def parse_request(payload: Any) -> Tuple[Config, int]:
    """Turn the form's JSON into a validated :class:`Config` and a month count.

    Raises:
        ConfigError: With a message safe to show the person who submitted
            the form. Every message says which field is wrong.
    """
    if not isinstance(payload, dict):
        raise ConfigError("Request body must be a JSON object.")

    own_raw = payload.get("own_brand")
    if not isinstance(own_raw, dict):
        raise ConfigError("own_brand is required: {\"name\": ..., \"keywords\": [...]}.")
    own = _parse_brand(own_raw, "own_brand", is_own_brand=True)

    competitors_raw = payload.get("competitors")
    if not isinstance(competitors_raw, list) or not competitors_raw:
        raise ConfigError(
            "At least one competitor is required. Share of Search is a ratio "
            "against a category — a brand on its own is always 100%."
        )
    if len(competitors_raw) > MAX_COMPETITORS:
        raise ConfigError(f"At most {MAX_COMPETITORS} competitors per run (got {len(competitors_raw)}).")
    competitors = [
        _parse_brand(raw, f"competitors[{i}]", is_own_brand=False)
        for i, raw in enumerate(competitors_raw)
    ]

    market = _parse_market(payload.get("market", "US"))
    months = _parse_months(payload.get("months", DEFAULT_MONTHS))

    config = Config(
        market=market,
        brands=[own, *competitors],
        smoothing_windows=list(DEFAULT_SMOOTHING_WINDOWS),
    )
    _validate(config)
    return config, months


def run_request(
    payload: Any,
    source: KeywordVolumeSource,
    data_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run the pipeline for one form submission and return what the page shows.

    Args:
        payload: The parsed JSON body (see :func:`parse_request`).
        source: Where volumes come from — DataForSEO in production.
        data_dir: Where the throwaway store goes. Defaults to a fresh temp
            directory; pass one to inspect the CSV in a test.

    Returns:
        A JSON-serialisable dict: ``html`` (the self-contained dashboard),
        ``latest`` (month + per-brand rows), ``commentary``, ``warnings``,
        ``months_returned`` and ``cost_usd``.

    Raises:
        ConfigError: The request was malformed (caller maps to HTTP 400).
        DataSourceError: The source failed or returned nothing (HTTP 502).
    """
    config, months = parse_request(payload)
    end = last_complete_month()
    start = shift_months(end, -(months - 1))

    data_dir = Path(data_dir) if data_dir else Path(tempfile.mkdtemp(prefix="sos-web-"))
    result = refresh(config=config, source=source, data_dir=data_dir, start=start, end=end)

    out_path = data_dir / "share-of-search.html"
    build_dashboard(result.frame, config, out_path)

    payload_facts = facts.month_facts(result.frame, config)
    return {
        "html": out_path.read_text(encoding="utf-8"),
        "own_brand": config.own_brand.name,
        "market": config.market.name,
        "latest": {
            "month": payload_facts.get("month"),
            "month_label": payload_facts.get("month_label"),
            "rows": payload_facts.get("brands", []),
        },
        "commentary": commentary.generate(payload_facts),
        "warnings": list(result.warnings),
        "months_requested": months,
        "months_returned": result.months_returned,
        "cost_usd": source.estimate_cost(len(config.all_keywords)),
    }


# --------------------------------------------------------------------------
# Field parsers
# --------------------------------------------------------------------------


def _parse_brand(raw: Any, field: str, is_own_brand: bool) -> Brand:
    if not isinstance(raw, dict):
        raise ConfigError(f"{field} must be an object with name and keywords.")

    name = _clean_text(raw.get("name"), f"{field}.name", MAX_NAME_LENGTH)
    if not name:
        raise ConfigError(f"{field}.name is required.")

    keywords = _parse_keywords(raw.get("keywords"), f"{field}.keywords", brand=name)

    url = raw.get("url")
    url = _clean_text(url, f"{field}.url", 300) if url else None

    return Brand(name=name, keywords=keywords, is_own_brand=is_own_brand, url=url or None)


def _parse_keywords(raw: Any, field: str, brand: str) -> List[str]:
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, list):
        raise ConfigError(f"{field} must be a list of search terms.")

    cleaned: List[str] = []
    for item in raw:
        keyword = _clean_text(item, field, MAX_KEYWORD_LENGTH).lower()
        if keyword and keyword not in cleaned:
            cleaned.append(keyword)

    if not cleaned:
        raise ConfigError(f"'{brand}' needs at least one keyword — the way people search for it.")
    if len(cleaned) > MAX_KEYWORDS_PER_BRAND:
        raise ConfigError(
            f"'{brand}' has {len(cleaned)} keywords; the web form allows "
            f"{MAX_KEYWORDS_PER_BRAND} per brand."
        )
    return cleaned


def _parse_market(raw: Any) -> Market:
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError("market is required, e.g. \"US\".")
    name = raw.strip().upper()
    code = COMMON_LOCATIONS.get(name)
    if code is None:
        raise ConfigError(
            f"Unknown market '{raw}'. Choose one of: {', '.join(sorted(COMMON_LOCATIONS))}."
        )
    return Market(name=name, location_code=code, language_code="en")


def _parse_months(raw: Any) -> int:
    try:
        months = int(raw)
    except (TypeError, ValueError):
        raise ConfigError(f"months must be one of {list(ALLOWED_MONTHS)}.") from None
    if months not in ALLOWED_MONTHS:
        raise ConfigError(f"months must be one of {list(ALLOWED_MONTHS)} (got {months}).")
    return months


def _clean_text(value: Any, field: str, max_length: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ConfigError(f"{field} must be text.")
    text = " ".join(value.split())
    if len(text) > max_length:
        raise ConfigError(f"{field} is too long (max {max_length} characters).")
    return text
