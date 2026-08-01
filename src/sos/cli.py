"""The ``sos`` command line.

Four commands:

* ``sos init``      — write a config file interactively
* ``sos run``       — pull volumes and update the store
* ``sos dashboard`` — build the HTML report from what's stored
* ``sos validate``  — check config and credentials without calling the API
"""

from __future__ import annotations

import logging
import sys
import webbrowser
from datetime import date
from pathlib import Path
from typing import List, Optional

import click
import yaml

from sos import __version__
from sos.config import (
    COMMON_LOCATIONS,
    DEFAULT_CONFIG_PATH,
    Config,
    ConfigError,
    config_from_flags,
    credential_env_var,
    get_credentials,
    load_config,
    load_dotenv,
)
from sos.datasource.base import DataSourceError
from sos.datasource.dataforseo import (
    COST_PER_REQUEST_USD,
    MAX_KEYWORDS_PER_REQUEST,
    DataForSEOSource,
)

#: How far back to reach when backfilling. DataForSEO's docs disagree about
#: whether Google Ads serves 24 or 48 months; we ask for 48 and report what
#: actually arrives.
BACKFILL_MONTHS = 48

#: Months re-pulled by default on a routine run, to absorb Google's revisions
#: of recent history.
#:
#: Twelve rather than three because DataForSEO charges per *request*, not per
#: month — a wider window is free. It absorbs more of Google's back-revisions,
#: and it keeps every routine run above transform.MIN_MONTHS_TO_DECIDE_GROUPING,
#: so the grouped-keyword guard always has real evidence to work from.
DEFAULT_REFRESH_MONTHS = 12


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------


def _ok(message: str) -> None:
    click.secho(f"  {message}", fg="green")


def _warn(message: str) -> None:
    click.secho(f"  ! {message}", fg="yellow")


def _info(message: str) -> None:
    click.echo(f"  {message}")


def _fail(message: str) -> "click.ClickException":
    return click.ClickException(message)


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------


def last_complete_month(today: Optional[date] = None) -> date:
    """The most recent month Google Ads can have data for.

    The current month is never available — it hasn't finished. Asking for it
    returns nothing and makes the last row of every chart look like a crash.
    """
    today = today or date.today()
    return (today.replace(day=1) - _one_day()).replace(day=1)


def _one_day():
    from datetime import timedelta

    return timedelta(days=1)


def shift_months(anchor: date, months: int) -> date:
    """Move a month-start date by ``months`` (negative goes back)."""
    total = anchor.year * 12 + (anchor.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def _parse_month(value: str, flag: str) -> date:
    try:
        year, month = value.split("-")
        return date(int(year), int(month), 1)
    except (ValueError, AttributeError):
        raise _fail(f"{flag} must look like YYYY-MM, got '{value}'.") from None


# --------------------------------------------------------------------------
# Root
# --------------------------------------------------------------------------


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="sos")
@click.option("--verbose", is_flag=True, help="Show debug logging, including API retries.")
def main(verbose: bool) -> None:
    """Measure Share of Search — a brand's branded search volume as a
    percentage of its category's total.

    \b
    Quickest possible start (no config file needed):
      export DATAFORSEO_LOGIN=... DATAFORSEO_PASSWORD=...
      sos run --brand Acme --competitors "Globex,Initech"
      sos dashboard --open

    Methodology per Les Binet, IPA EffWorks Global 2020.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="  %(message)s",
    )
    load_dotenv()


# --------------------------------------------------------------------------
# init
# --------------------------------------------------------------------------


@main.command()
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=DEFAULT_CONFIG_PATH,
              show_default=True, help="Where to write the config file.")
@click.option("--force", is_flag=True, help="Overwrite an existing config file.")
def init(config_path: Path, force: bool) -> None:
    """Set up a brand set interactively.

    Prompts for your brand, its competitors and the market, then writes a
    YAML config you can edit by hand afterwards.
    """
    if config_path.exists() and not force:
        raise _fail(
            f"'{config_path}' already exists. Edit it directly, or pass --force to overwrite it."
        )

    click.echo()
    click.secho("Share of Search — setup", bold=True)
    click.echo("Press Ctrl-C at any point to bail out; nothing is written until the end.\n")

    brand_name = click.prompt("Your brand name").strip()
    brand_url = click.prompt("Your brand URL", default="", show_default=False).strip()

    click.echo("\nCompetitors, one per line. Blank line when you're done.")
    click.echo("Be generous — an incomplete set makes every percentage wrong.")
    competitors: List[str] = []
    while True:
        entry = click.prompt(f"  Competitor {len(competitors) + 1}", default="", show_default=False).strip()
        if not entry:
            break
        competitors.append(entry)

    if not competitors:
        raise _fail("At least one competitor is required — share is always relative to a category.")

    click.echo(f"\nMarket. Known shorthands: {', '.join(sorted(COMMON_LOCATIONS))}")
    market = click.prompt("Market", default="US").strip().upper()
    location_code = COMMON_LOCATIONS.get(market)
    if location_code is None:
        location_code = click.prompt(
            f"No shorthand for '{market}'. DataForSEO location code", type=int
        )
    language_code = click.prompt("Language code", default="en").strip()

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        _render_config_yaml(brand_name, brand_url, competitors, market, location_code, language_code),
        encoding="utf-8",
    )

    click.echo()
    _ok(f"Wrote {config_path}")
    _info("Each brand starts with its name as its only keyword. Open the file and add")
    _info("variants ('acme app', 'acme login') to capture more of each brand's demand.")
    _info("")
    _info("Next:  sos validate    then    sos run")


def _yaml_scalar(value: str) -> str:
    """Render a prompted value as a YAML scalar, quoted if it needs to be.

    Interpolating raw input would silently mangle perfectly ordinary names:
    ``Acme #1`` loses everything from the ``#``, ``Foo: Bar`` is a parse error,
    and ``Null``/``Yes``/``123`` come back from the loader as something other
    than a string. Letting PyYAML decide the quoting means whatever someone
    types round-trips through the next ``sos validate``.
    """
    dumped = yaml.safe_dump(
        {"v": value}, default_flow_style=False, allow_unicode=True, width=10**6
    )
    return dumped.split(":", 1)[1].strip()


def _render_config_yaml(
    brand: str,
    url: str,
    competitors: List[str],
    market: str,
    location_code: int,
    language_code: str,
) -> str:
    """Build the config by hand so the explanatory comments survive.

    Every value goes through :func:`_yaml_scalar`; only the layout and the
    comments are hand-written.
    """
    lines = [
        "# Generated by `sos init`. Edit freely — this file is gitignored.",
        "",
        "market:",
        f"  name: {_yaml_scalar(market)}",
        f"  location_code: {int(location_code)}",
        f"  language_code: {_yaml_scalar(language_code)}",
        "",
        "# Trailing rolling-average windows, in months.",
        "smoothing_windows: [3, 12]",
        "",
        "own_brand:",
        f"  name: {_yaml_scalar(brand)}",
    ]
    if url:
        lines.append(f"  url: {_yaml_scalar(url)}")
    lines += [
        "  keywords:",
        f"    - {_yaml_scalar(brand.lower())}",
        "  ambiguous: false",
        "",
        "# Set `ambiguous: true` for any brand whose name is also a common word —",
        "# its volume will include searches that have nothing to do with the brand.",
        "competitors:",
    ]
    for competitor in competitors:
        lines.append(f"  - name: {_yaml_scalar(competitor)}")
        lines.append("    keywords:")
        lines.append(f"      - {_yaml_scalar(competitor.lower())}")
        lines.append("    ambiguous: false")

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------


@main.command()
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None,
              help=f"Config file to check.  [default: {DEFAULT_CONFIG_PATH}]")
def validate(config_path: Optional[Path]) -> None:
    """Check the config and credentials without calling the API.

    Costs nothing and touches no network. Run it after editing your brand set.
    """
    click.echo()
    problems = 0

    try:
        config = load_config(config_path)
        _ok(f"Config valid: {config.source_path}")
        _info(f"Market: {config.market.name} (location {config.market.location_code}, "
              f"language {config.market.language_code})")
        _info(f"Own brand: {config.own_brand.name}")
        _info(f"Competitors: {', '.join(b.name for b in config.competitors)}")
        _info(f"Keywords: {len(config.all_keywords)} across {len(config.brands)} brands")
        _info(f"Smoothing windows: {', '.join(f'{w} months' for w in config.smoothing_windows)}")
        if config.ambiguous_brands:
            _warn(
                f"Flagged as ambiguous: {', '.join(config.ambiguous_brands)}. "
                "Their volumes will include unrelated searches."
            )
    except ConfigError as exc:
        click.secho(f"  Config problem:\n{_indent(str(exc))}", fg="red")
        problems += 1

    click.echo()
    login_var = credential_env_var()
    if login_var:
        _ok(f"Login found in ${login_var}")
    else:
        click.secho("  Missing $DATAFORSEO_LOGIN", fg="red")
        problems += 1

    import os

    if os.environ.get("DATAFORSEO_PASSWORD"):
        _ok("Password found in $DATAFORSEO_PASSWORD")
    else:
        click.secho("  Missing $DATAFORSEO_PASSWORD", fg="red")
        problems += 1

    click.echo()
    if problems:
        raise _fail(f"{problems} problem(s) found. Nothing was called; fix the above and re-run.")
    _ok("Ready. Run `sos run` to pull data.")


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------


@main.command()
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None,
              help=f"Config file to use.  [default: {DEFAULT_CONFIG_PATH}]")
@click.option("--brand", default=None, help="Ad-hoc mode: your brand name, no config file needed.")
@click.option("--brand-url", default=None, help="Ad-hoc mode: your brand's URL (recorded, not fetched).")
@click.option("--competitors", default=None, help='Ad-hoc mode: comma-separated, e.g. "Globex,Initech".')
@click.option("--market", default="US", show_default=True, help="Market shorthand, e.g. US, UK, DE.")
@click.option("--backfill", is_flag=True, help=f"Pull the full available history (asks for {BACKFILL_MONTHS} months).")
@click.option("--from", "date_from", default=None, metavar="YYYY-MM", help="Explicit range start.")
@click.option("--to", "date_to", default=None, metavar="YYYY-MM", help="Explicit range end (capped at the last complete month).")
@click.option("--refresh-last", type=int, default=None, metavar="N",
              help="Re-pull the trailing N months, absorbing Google's revisions to recent history.")
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path("data"), show_default=True,
              help="Where the CSV store lives.")
@click.option("--dry-run", is_flag=True, help="Show what would be requested and what it would cost. Makes no call.")
def run(
    config_path: Optional[Path],
    brand: Optional[str],
    brand_url: Optional[str],
    competitors: Optional[str],
    market: str,
    backfill: bool,
    date_from: Optional[str],
    date_to: Optional[str],
    refresh_last: Optional[int],
    data_dir: Path,
    dry_run: bool,
) -> None:
    """Pull search volumes and update the data store.

    \b
    With a config file:
      sos run
    Ad-hoc, no config file:
      sos run --brand Acme --competitors "Globex,Initech"

    An empty store backfills automatically. On later runs the trailing three
    months are re-pulled, because Google revises recent history.

    Cost is per request, not per keyword — every brand goes out in one call,
    so a run is about $0.075 regardless of how many brands you track.
    """
    from sos import store as store_module
    from sos import transform

    config = _resolve_config(config_path, brand, brand_url, competitors, market)

    empty_store = store_module.is_empty(data_dir)
    start, end, reason = _resolve_range(
        data_dir, config, empty_store, backfill, date_from, date_to, refresh_last
    )

    click.echo()
    click.secho(f"Share of Search — {config.market.name}", bold=True)
    _info(f"Brands: {', '.join(b.name for b in config.brands)}")
    _info(f"Keywords: {len(config.all_keywords)}")
    _info(f"Range: {start:%Y-%m} to {end:%Y-%m}  ({reason})")

    request_count = max(1, -(-len(config.all_keywords) // MAX_KEYWORDS_PER_REQUEST))
    _info(f"Requests: {request_count}  (~${request_count * COST_PER_REQUEST_USD:.3f})")

    if dry_run:
        click.echo()
        _info("Keywords that would be sent:")
        for keyword in config.all_keywords:
            _info(f"  - {keyword}")
        click.echo()
        _ok("Dry run: nothing was requested and nothing was charged.")
        return

    try:
        login, password = get_credentials()
    except ConfigError as exc:
        raise _fail(str(exc)) from None

    source = DataForSEOSource(login=login, password=password)

    click.echo()
    _info("Fetching...")
    try:
        rows = source.fetch_monthly_volume(
            keywords=config.all_keywords,
            location_code=config.market.location_code,
            language_code=config.market.language_code,
            date_from=f"{start:%Y-%m-%d}",
            date_to=f"{end:%Y-%m-%d}",
        )
    except DataSourceError as exc:
        raise _fail(str(exc)) from None

    if not rows:
        raise _fail(
            "The API returned no volume data at all. Check that your keywords are "
            "spelled the way people search for them, and that the market is right."
        )

    _ok(f"{len(rows)} keyword-months returned, covering {source.months_returned} distinct months.")
    if backfill and source.months_returned < BACKFILL_MONTHS:
        _info(
            f"Asked for {BACKFILL_MONTHS} months, got {source.months_returned}. "
            "Google Ads caps history depth; this is expected, not an error."
        )

    stored = store_module.load_store(data_dir)
    active_brands = [b.name for b in config.brands]

    stale = store_module.stale_brands(stored, config.market.name, active_brands)
    if stale:
        _warn(
            f"Dropping {', '.join(stale)} from the {config.market.name} store — no longer in "
            "the category set. Leaving them in would keep inflating the category total and "
            "understate every remaining brand."
        )

    brand_frame, warnings = transform.build_brand_frame(
        rows, config, previously_counted=store_module.counted_keywords(stored, config.market.name)
    )
    for warning in warnings:
        _warn(warning)

    new_rows = store_module.build_rows(brand_frame, config, data_source=source.name)
    combined = store_module.upsert(
        data_dir, new_rows, config.smoothing_windows, active_brands=active_brands
    )

    click.echo()
    _ok(f"Store updated: {store_module.store_path(data_dir)}  ({len(combined)} rows)")

    for warning in transform.category_set_warnings(combined, config.own_brand.name):
        _warn(warning)

    _print_summary(combined, config)

    click.echo()
    _info("Next:  sos dashboard --open")


def _resolve_config(
    config_path: Optional[Path],
    brand: Optional[str],
    brand_url: Optional[str],
    competitors: Optional[str],
    market: str,
) -> Config:
    """Ad-hoc flags win over a config file; neither means look for the default file."""
    if brand or competitors:
        if not (brand and competitors):
            raise _fail("Ad-hoc mode needs both --brand and --competitors.")
        try:
            return config_from_flags(
                brand=brand,
                competitors=competitors.split(","),
                brand_url=brand_url,
                market_name=market,
            )
        except ConfigError as exc:
            raise _fail(str(exc)) from None

    try:
        return load_config(config_path)
    except ConfigError as exc:
        raise _fail(str(exc)) from None


def _config_for_dashboard(
    config_path: Optional[Path],
    brand: Optional[str],
    competitors: Optional[str],
    market: str,
    frame,
) -> Config:
    """Pick the richest brand-set description available.

    An explicit config file wins (it carries the ``ambiguous`` flags), then
    ad-hoc flags, then whatever the store itself records — so the ad-hoc path
    needs no arguments repeated at render time.
    """
    from sos import store as store_module

    explicit = config_path or (DEFAULT_CONFIG_PATH if DEFAULT_CONFIG_PATH.exists() else None)
    if explicit or brand or competitors:
        return _resolve_config(explicit, brand, None, competitors, market)

    try:
        return store_module.config_from_store(frame, market if _market_in(frame, market) else None)
    except ConfigError as exc:
        raise _fail(str(exc)) from None


def _market_in(frame, market: str) -> bool:
    return market in {str(m) for m in frame["market"].dropna().unique()}


def _resolve_range(
    data_dir: Path,
    config: Config,
    empty_store: bool,
    backfill: bool,
    date_from: Optional[str],
    date_to: Optional[str],
    refresh_last: Optional[int],
) -> "tuple[date, date, str]":
    """Work out which months to request, and say why."""
    from sos import store as store_module

    # Google Ads never has the current month; asking for it just returns a hole.
    ceiling = last_complete_month()
    end = min(_parse_month(date_to, "--to"), ceiling) if date_to else ceiling

    if date_from:
        return _parse_month(date_from, "--from"), end, "explicit range"

    if refresh_last is not None:
        if refresh_last < 1:
            raise _fail("--refresh-last must be at least 1.")
        return shift_months(end, -(refresh_last - 1)), end, f"refreshing the last {refresh_last} months"

    if backfill:
        return shift_months(end, -(BACKFILL_MONTHS - 1)), end, "backfill"

    if empty_store:
        click.echo()
        _info("No data stored yet — backfilling the full available history.")
        return shift_months(end, -(BACKFILL_MONTHS - 1)), end, "auto-backfill, empty store"

    stored = store_module.existing_months(data_dir, config.market.name)
    if not stored:
        return shift_months(end, -(BACKFILL_MONTHS - 1)), end, "auto-backfill, no data for this market"

    latest = stored[-1].date()
    start = shift_months(latest, -(DEFAULT_REFRESH_MONTHS - 1))
    return start, end, f"new months plus a {DEFAULT_REFRESH_MONTHS}-month refresh"


def _print_summary(frame, config: Config) -> None:
    """Print the latest month, plus the commentary bullets."""
    from sos import commentary, facts

    payload = facts.month_facts(frame, config)
    if not payload.get("brands"):
        return

    click.echo()
    click.secho(f"  {payload['month_label']} — {payload['market']}", bold=True)
    click.echo(f"  {'Brand':<24}{'Share':>9}{'MoM':>9}{'YoY':>9}")
    for row in payload["brands"]:
        marker = "*" if row["is_own_brand"] else " "
        click.echo(
            f"  {marker}{row['brand']:<23}"
            f"{_fmt_pct(row['sos_pct']):>9}"
            f"{_fmt_pp(row['mom_delta_pp']):>9}"
            f"{_fmt_pp(row['yoy_delta_pp']):>9}"
        )

    click.echo()
    for bullet in commentary.generate(payload):
        click.echo(click.wrap_text(bullet, width=88, initial_indent="  - ", subsequent_indent="    "))


def _fmt_pct(value) -> str:
    return "-" if value is None else f"{value:.1f}%"


def _fmt_pp(value) -> str:
    return "-" if value is None else f"{value:+.1f}"


# --------------------------------------------------------------------------
# dashboard
# --------------------------------------------------------------------------


@main.command()
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None,
              help=f"Config file describing the brand set.  [default: {DEFAULT_CONFIG_PATH}]")
@click.option("--brand", default=None, help="Ad-hoc mode: your brand name, matching the stored run.")
@click.option("--competitors", default=None, help="Ad-hoc mode: comma-separated competitor names.")
@click.option("--market", default="US", show_default=True, help="Market to render.")
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path("data"), show_default=True,
              help="Where the CSV store lives.")
@click.option("--out", type=click.Path(path_type=Path), default=Path("output/share-of-search.html"),
              show_default=True, help="Where to write the HTML file.")
@click.option("--open", "open_browser", is_flag=True, help="Open the dashboard when it's built.")
def dashboard(
    config_path: Optional[Path],
    brand: Optional[str],
    competitors: Optional[str],
    market: str,
    data_dir: Path,
    out: Path,
    open_browser: bool,
) -> None:
    """Build the HTML dashboard from stored data. Makes no API calls.

    The result is one self-contained file with the data inlined and the
    charting library bundled, so it opens by double-clicking, works offline,
    and survives being emailed to someone.

    With no arguments the brand set is read back out of the store, so this
    works straight after an ad-hoc `sos run` with nothing else to restate.
    """
    from sos import store as store_module
    from sos.dashboard.build import build_dashboard

    frame = store_module.load_store(data_dir)
    if frame.empty:
        raise _fail(
            f"No data in '{store_module.store_path(data_dir)}'. Run `sos run` first."
        )

    config = _config_for_dashboard(config_path, brand, competitors, market, frame)
    path = build_dashboard(frame, config, out)

    click.echo()
    _ok(f"Dashboard written to {path}")
    _info("It's a single self-contained file — double-click it, or email it to someone.")

    if open_browser:
        webbrowser.open(path.resolve().as_uri())


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
