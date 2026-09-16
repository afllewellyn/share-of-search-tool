"""Vercel Python Function: the HTTP edge of the web front door.

Deliberately thin. Everything that can be tested without HTTP lives in
:mod:`sos.web`; this file only maps JSON in/out and exceptions to status
codes. Vercel serves it as one Python Function; ``next.config.mjs`` rewrites every
``/api/*`` path to it and Flask routes on the original path.

Local development mirrors Vercel's nextjs-flask example::

    python api/index.py          # Flask on http://127.0.0.1:5328
    npm run dev                  # Next.js proxies /api/* there (see next.config)

Credentials come from ``DATAFORSEO_LOGIN`` / ``DATAFORSEO_PASSWORD`` — the
same variables the CLI reads — set on the Vercel project, or in ``.env`` when
running locally.

Two things stand between the public internet and a paid request:

- A per-address and per-instance rate limit on ``/api/run``. Always on.
- ``SOS_WEB_PASSPHRASE``: optional. When set, the form must send it in the
  ``X-SOS-Passphrase`` header; when unset the endpoint is open.
"""

from __future__ import annotations

import hmac
import logging
import os
import sys
from pathlib import Path

# Vercel installs the dependencies from pyproject.toml but not the package
# itself, so put the src/ layout on the path when ``sos`` isn't installed.
try:
    import sos  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from sos import web
from sos.config import COMMON_LOCATIONS, ConfigError, get_credentials, load_dotenv
from sos.datasource.base import DataSourceError
from sos.datasource.dataforseo import DataForSEOSource

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Bodies over this are not a brand set; refuse before parsing.
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

#: Optional shared passphrase for /api/run. Unset means open; the rate limit
#: below is the guard that is always on.
PASSPHRASE_ENV_VAR = "SOS_WEB_PASSPHRASE"
PASSPHRASE_HEADER = "X-SOS-Passphrase"

#: Runs allowed per address and per function instance in a ten-minute window.
#: Each accepted run is one paid request; a person iterating on a brand set
#: needs a handful, a loop needs thousands.
RUN_LIMIT_PER_ADDRESS = 6
RUN_LIMIT_PER_INSTANCE = 30
RUN_LIMIT_WINDOW_SECONDS = 600
limiter = web.RateLimiter(RUN_LIMIT_PER_ADDRESS, RUN_LIMIT_PER_INSTANCE, RUN_LIMIT_WINDOW_SECONDS)

# The function is capped at 60 s (vercel.json). Two attempts of 20 s plus the
# 2 s backoff leave room to build the report, so a stalled provider surfaces
# as a 502 here instead of a platform timeout with a non-JSON body.
PROVIDER_TIMEOUT_SECONDS = 20
PROVIDER_ATTEMPTS = 2


@app.get("/api/markets")
def markets():
    """The market shorthands the form can offer, from the one list the CLI uses."""
    response = jsonify(
        {
            "passphrase_required": bool(os.environ.get(PASSPHRASE_ENV_VAR)),
            "markets": sorted(COMMON_LOCATIONS),
            "months": list(web.ALLOWED_MONTHS),
            "limits": {
                "max_competitors": web.MAX_COMPETITORS,
                "max_keywords_per_brand": web.MAX_KEYWORDS_PER_BRAND,
            },
        }
    )
    # Static per deployment; let the CDN serve it without invoking the function.
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


def _client_address() -> str:
    """The caller's address as Vercel reports it; the socket peer is the proxy."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[0].strip() or request.remote_addr or "unknown"


def _passphrase_ok() -> bool:
    """True when no passphrase is configured, or the header matches it."""
    expected = os.environ.get(PASSPHRASE_ENV_VAR, "")
    if not expected:
        return True
    given = request.headers.get(PASSPHRASE_HEADER, "")
    return hmac.compare_digest(expected.encode(), given.encode())


@app.post("/api/run")
def run():
    # Cheapest refusals first.
    if not _passphrase_ok():
        return jsonify({"error": "The passphrase is missing or wrong."}), 401
    if not limiter.allow(_client_address()):
        response = jsonify({"error": f"Too many runs from this address. Try again in {RUN_LIMIT_WINDOW_SECONDS // 60} minutes."})
        response.headers["Retry-After"] = str(RUN_LIMIT_WINDOW_SECONDS)
        return response, 429

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "Request body must be JSON."}), 400

    try:
        config, months = web.parse_request(payload)
    except ConfigError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        login, password = get_credentials()
    except ConfigError as exc:
        # The message names the missing variables, never their values.
        logger.error("DataForSEO credentials are not configured on this deployment: %s", str(exc).splitlines()[0])
        return jsonify({"error": "This deployment has no data-source credentials configured."}), 500

    source = DataForSEOSource(
        login=login, password=password, timeout=PROVIDER_TIMEOUT_SECONDS, max_retries=PROVIDER_ATTEMPTS
    )
    logger.info(
        "web run: own=%s competitors=%d keywords=%d market=%s",
        config.own_brand.name, len(config.competitors), len(config.all_keywords), config.market.name,
    )

    try:
        result = web.run_request(config, months, source)
    except DataSourceError as exc:
        # The full message can quote the provider's response body or name the
        # credential variables; keep that in the function log, not the page.
        logger.error("DataForSEO request failed: %s", exc)
        return jsonify({"error": "The search-volume provider did not return data. Try again in a minute."}), 502

    return jsonify(result)


@app.errorhandler(413)
def too_large(_exc):
    return jsonify({"error": "Request body is too large for a brand set."}), 413


@app.errorhandler(Exception)
def unexpected(exc):
    """Every failure leaves as JSON so the page can show it, not a platform HTML 500."""
    if isinstance(exc, HTTPException):
        return jsonify({"error": exc.description}), exc.code
    logger.exception("Unhandled error while building a report")
    return jsonify({"error": "The report could not be built because of an error in the app, not the data. It has been logged."}), 500


if __name__ == "__main__":
    load_dotenv()
    app.run(host="127.0.0.1", port=5328, debug=True)
