"""DataForSEO Google Ads search-volume backend.

Endpoint: ``POST /v3/keywords_data/google_ads/search_volume/live``

Two things about DataForSEO's pricing shape the design here:

1. **Cost is per request, not per keyword.** Every brand's keywords go out in
   a single call, so a whole competitor set costs about $0.075 per run.
2. **Rate limit is 12 requests/minute.** Only relevant above 1000 keywords,
   where the batch has to be split.

History depth: DataForSEO's docs are inconsistent about whether Google Ads
returns 24 or 48 months. We request 48 and handle whatever comes back.

Measured against the live API on 2026-08-01 (7 keywords, US, en): a request
for 2022-08 through 2026-07 returned **47 months, 2022-08 through 2026-06**.
So the deeper figure is the real one — roughly four years — but the most
recent month is not simply "last month". Google Ads data lagged by two months
here, not one, so the final month of the requested window came back empty.
That is normal and needs no special handling: the month is absent from the
response rather than zero, and nothing downstream invents a row for it.

Don't hard-code 47. Callers read :attr:`DataForSEOSource.months_returned` for
what actually arrived.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any, Dict, List, Optional

import requests

from sos.datasource.base import DataSourceError, KeywordVolumeSource, VolumeRow

logger = logging.getLogger(__name__)

API_URL = "https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live"

#: DataForSEO's documented ceiling for keywords in one task.
MAX_KEYWORDS_PER_REQUEST = 1000

#: Approximate USD cost of one live search_volume request.
COST_PER_REQUEST_USD = 0.075

#: Documented rate limit, requests per minute.
RATE_LIMIT_PER_MINUTE = 12

#: DataForSEO's "everything worked" status code.
OK_STATUS = 20000

_RETRYABLE_HTTP = {429, 500, 502, 503, 504}
_RETRYABLE_API_STATUS = {40202, 40203, 50000, 50100, 50200}


class DataForSEOSource(KeywordVolumeSource):
    """Fetches monthly Google Ads search volume via DataForSEO."""

    name = "dataforseo"

    def __init__(
        self,
        login: str,
        password: str,
        timeout: int = 120,
        max_retries: int = 4,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._auth_header = _basic_auth_header(login, password)
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = session or requests.Session()
        #: Months actually returned by the most recent fetch. Populated so the
        #: CLI can report the real history depth rather than the requested one.
        self.months_returned = 0

    # -- KeywordVolumeSource ------------------------------------------------

    def fetch_monthly_volume(
        self,
        keywords: List[str],
        location_code: int,
        language_code: str,
        date_from: str,
        date_to: str,
    ) -> List[VolumeRow]:
        if not keywords:
            return []

        rows: List[VolumeRow] = []
        batches = _chunk(keywords, MAX_KEYWORDS_PER_REQUEST)

        for index, batch in enumerate(batches):
            if index > 0:
                # Stay under 12 requests/minute. Only reached above 1000 keywords.
                time.sleep(60.0 / RATE_LIMIT_PER_MINUTE)
            payload = [
                {
                    "keywords": batch,
                    "location_code": location_code,
                    "language_code": language_code,
                    "date_from": date_from,
                    "date_to": date_to,
                    "search_partners": False,
                }
            ]
            response = self._post_with_retry(payload)
            rows.extend(_parse_response(response, requested_keywords=batch))

        self.months_returned = len({(row["year"], row["month"]) for row in rows})
        if rows:
            logger.info(
                "DataForSEO returned %d distinct months for %d keywords "
                "(requested %s to %s)",
                self.months_returned,
                len(keywords),
                date_from,
                date_to,
            )
        return rows

    def estimate_cost(self, keyword_count: int) -> float:
        requests_needed = max(1, -(-keyword_count // MAX_KEYWORDS_PER_REQUEST))
        return requests_needed * COST_PER_REQUEST_USD

    # -- HTTP ---------------------------------------------------------------

    def _post_with_retry(self, payload: List[Dict[str, Any]]) -> Dict[str, Any]:
        """POST with exponential backoff on 5xx and rate-limit responses."""
        last_error = ""

        for attempt in range(self.max_retries):
            if attempt:
                delay = 2**attempt
                logger.warning("Retrying DataForSEO request in %ds (%s)", delay, last_error)
                time.sleep(delay)

            try:
                response = self._session.post(
                    API_URL,
                    json=payload,
                    headers={
                        "Authorization": self._auth_header,
                        "Content-Type": "application/json",
                    },
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_error = f"network error: {exc}"
                continue

            if response.status_code == 401:
                # Never echo the credential itself, only which variables to check.
                raise DataSourceError(
                    "DataForSEO rejected the credentials (HTTP 401).\n"
                    "  Check DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD.\n"
                    "  The login is the email address you registered with, and the\n"
                    "  password is the API password from your dashboard — not your\n"
                    "  website password."
                )

            if response.status_code in _RETRYABLE_HTTP:
                last_error = f"HTTP {response.status_code}"
                continue

            if response.status_code != 200:
                raise DataSourceError(
                    f"DataForSEO returned HTTP {response.status_code}. "
                    f"Response: {response.text[:400]}"
                )

            try:
                body = response.json()
            except ValueError as exc:
                last_error = f"malformed JSON response: {exc}"
                continue

            status = body.get("status_code")
            if status in _RETRYABLE_API_STATUS:
                last_error = f"API status {status}: {body.get('status_message')}"
                continue

            if status != OK_STATUS:
                raise DataSourceError(
                    f"DataForSEO error {status}: {body.get('status_message', 'no message')}"
                )

            return body

        raise DataSourceError(
            f"DataForSEO request failed after {self.max_retries} attempts. Last error: {last_error}"
        )


def _basic_auth_header(login: str, password: str) -> str:
    token = base64.b64encode(f"{login}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _chunk(items: List[str], size: int) -> List[List[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _parse_response(body: Dict[str, Any], requested_keywords: List[str]) -> List[VolumeRow]:
    """Flatten ``tasks[].result[].monthly_searches[]`` into VolumeRows.

    Each task carries its own status code, so a top-level 20000 does not mean
    every task succeeded.
    """
    rows: List[VolumeRow] = []
    tasks = body.get("tasks") or []

    if not tasks:
        raise DataSourceError("DataForSEO returned no tasks. Nothing to parse.")

    returned_keywords = set()

    for task in tasks:
        task_status = task.get("status_code")
        if task_status != OK_STATUS:
            raise DataSourceError(
                f"DataForSEO task failed with status {task_status}: "
                f"{task.get('status_message', 'no message')}"
            )

        for result in task.get("result") or []:
            keyword = result.get("keyword")
            if not keyword:
                continue
            returned_keywords.add(keyword.lower())

            monthly = result.get("monthly_searches") or []
            if not monthly:
                logger.warning("No monthly data returned for keyword '%s'", keyword)
                continue

            for entry in monthly:
                year, month = entry.get("year"), entry.get("month")
                if year is None or month is None:
                    continue
                rows.append(
                    {
                        "keyword": keyword.lower(),
                        "year": int(year),
                        "month": int(month),
                        # None is preserved deliberately: it means "no data",
                        # which is not the same as zero demand.
                        "search_volume": entry.get("search_volume"),
                    }
                )

    missing = [k for k in requested_keywords if k.lower() not in returned_keywords]
    if missing:
        logger.warning(
            "DataForSEO returned nothing for %d keyword(s): %s",
            len(missing),
            ", ".join(missing[:10]) + (" ..." if len(missing) > 10 else ""),
        )

    return rows
