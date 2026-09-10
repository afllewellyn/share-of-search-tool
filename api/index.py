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
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Vercel installs the dependencies from pyproject.toml but not the package
# itself, so put the src/ layout on the path when ``sos`` isn't installed.
try:
    import sos  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from flask import Flask, jsonify, request

from sos import web
from sos.config import COMMON_LOCATIONS, ConfigError, get_credentials, load_dotenv
from sos.datasource.base import DataSourceError
from sos.datasource.dataforseo import DataForSEOSource

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Bodies over this are not a brand set; refuse before parsing.
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024


@app.get("/api/markets")
def markets():
    """The market shorthands the form can offer, from the one list the CLI uses."""
    return jsonify(
        {
            "markets": sorted(COMMON_LOCATIONS),
            "months": list(web.ALLOWED_MONTHS),
            "limits": {
                "max_competitors": web.MAX_COMPETITORS,
                "max_keywords_per_brand": web.MAX_KEYWORDS_PER_BRAND,
            },
        }
    )


@app.post("/api/run")
def run():
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "Request body must be JSON."}), 400

    try:
        config, _ = web.parse_request(payload)
    except ConfigError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        login, password = get_credentials()
    except ConfigError as exc:
        # The message names the missing variables, never their values.
        logger.error("DataForSEO credentials are not configured on this deployment: %s", str(exc).splitlines()[0])
        return jsonify({"error": "This deployment has no data-source credentials configured."}), 500

    source = DataForSEOSource(login=login, password=password)
    logger.info(
        "web run: own=%s competitors=%d keywords=%d market=%s",
        config.own_brand.name, len(config.competitors), len(config.all_keywords), config.market.name,
    )

    try:
        result = web.run_request(payload, source)
    except DataSourceError as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify(result)


@app.errorhandler(413)
def too_large(_exc):
    return jsonify({"error": "Request body is too large for a brand set."}), 413


if __name__ == "__main__":
    load_dotenv()
    app.run(host="127.0.0.1", port=5328, debug=True)
