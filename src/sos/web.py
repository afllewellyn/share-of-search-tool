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

from sos.config import (
    DEFAULT_SMOOTHING_WINDOWS,
    Brand,
    Config,
    ConfigError,
    _validate,
    market_from_shorthand,
    split_keywords,
)
from sos.datasource.base import KeywordVolumeSource

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

    market = market_from_shorthand(payload.get("market", "US"))
    months = _parse_months(payload.get("months", DEFAULT_MONTHS))

    config = Config(
        market=market,
        brands=[own, *competitors],
        smoothing_windows=list(DEFAULT_SMOOTHING_WINDOWS),
    )
    _validate(config)
    return config, months


def run_request(
    config: Config,
    months: int,
    source: KeywordVolumeSource,
    data_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run the pipeline for one parsed submission and return what the page shows.

    Args:
        config: The brand set, from :func:`parse_request`.
        months: How much history to pull, from :func:`parse_request`.
        source: Where volumes come from — DataForSEO in production.
        data_dir: Where the throwaway store goes. Defaults to a temp
            directory removed after the run; pass one to inspect the CSV in
            a test.

    Returns:
        A JSON-serialisable dict: ``html`` (the self-contained dashboard),
        ``latest`` (month + per-brand rows), ``commentary``, ``warnings``,
        ``months_returned`` and ``cost_usd``.

    Raises:
        DataSourceError: The source failed or returned nothing (HTTP 502).
    """
    # Imported here so the Flask function can answer /api/markets without
    # paying pandas' import cost on a cold start.
    from sos.dashboard.build import build_payload, render_dashboard
    from sos.run import last_complete_month, refresh, shift_months

    end = last_complete_month()
    start = shift_months(end, -(months - 1))

    if data_dir is not None:
        result = refresh(config=config, source=source, data_dir=Path(data_dir), start=start, end=end)
    else:
        with tempfile.TemporaryDirectory(prefix="sos-web-") as tmp:
            result = refresh(config=config, source=source, data_dir=Path(tmp), start=start, end=end)

    # One facts/commentary pass feeds both the JSON summary and the HTML.
    payload = build_payload(result.frame, config)
    return {
        "html": render_dashboard(payload, config),
        "own_brand": config.own_brand.name,
        "market": config.market.name,
        "latest": payload["latest"],
        "commentary": payload["commentary"],
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

    url = _clean_text(raw.get("url"), f"{field}.url", 300) or None

    return Brand(name=name, keywords=keywords, is_own_brand=is_own_brand, url=url)


def _parse_keywords(raw: Any, field: str, brand: str) -> List[str]:
    if isinstance(raw, str):
        raw = split_keywords(raw)
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
