"""The ``sos`` command line.

Five commands:

* ``sos init``      — write a config file interactively
* ``sos run``       — pull volumes and update the store
* ``sos dashboard`` — build the HTML report from what's stored
* ``sos validate``  — check config and credentials without calling the API
* ``sos refresh``   — update the code, pull data, rebuild and open the report
"""

from __future__ import annotations

import logging
import re
import sys
import webbrowser
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List, NoReturn, Optional

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
    keyword_parity_warnings,
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

    click.secho("Step 1 of 3 — who's in the category", bold=True)
    brand_name = click.prompt("  Your brand name").strip()
    brand_url = click.prompt("  Your brand URL", default="", show_default=False).strip()

    click.echo("\n  Competitors, one per line. Blank line when you're done.")
    click.echo("  Be generous — an incomplete set makes every percentage wrong.")
    competitor_names: List[str] = []
    while True:
        entry = click.prompt(
            f"    Competitor {len(competitor_names) + 1}", default="", show_default=False
        ).strip()
        if not entry:
            break
        competitor_names.append(entry)

    if not competitor_names:
        raise _fail("At least one competitor is required — share is always relative to a category.")

    click.echo()
    click.secho("Step 2 of 3 — what each brand is searched as", bold=True)
    click.echo("  This step decides whether the percentages are fair. A brand tracked on one")
    click.echo("  keyword, against a competitor tracked on five, will look smaller than it is.")
    click.echo("  Sub-brands, product lines and common misspellings all belong here.")

    own = _prompt_brand_keywords(brand_name, url=brand_url)
    competitors = [_prompt_brand_keywords(name) for name in competitor_names]

    click.echo()
    click.secho("Step 3 of 3 — market", bold=True)
    click.echo(f"  Known shorthands: {', '.join(sorted(COMMON_LOCATIONS))}")
    market = click.prompt("  Market", default="US").strip().upper()
    location_code = COMMON_LOCATIONS.get(market)
    if location_code is None:
        location_code = click.prompt(
            f"  No shorthand for '{market}'. DataForSEO location code", type=int
        )
    language_code = click.prompt("  Language code", default="en").strip()

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        _render_config_yaml(own, competitors, market, location_code, language_code),
        encoding="utf-8",
    )

    click.echo()
    _ok(f"Wrote {config_path}")
    total = len(own.keywords) + sum(len(c.keywords) for c in competitors)
    _info(f"{total} keywords across {len(competitors) + 1} brands.")
    for warning in keyword_parity_warnings(
        {draft.name: draft.keywords for draft in [own, *competitors]}
    ):
        _warn(warning)
    _info("")
    _info("Add more variants any time — the file is plain YAML and re-running")
    _info("`sos run` picks them up. Cost is per request, so extra keywords are free.")
    _info("")
    _info("Next:  sos validate    then    sos run")


@dataclass
class _BrandDraft:
    """One brand as `sos init` collected it, before it becomes YAML."""

    name: str
    keywords: List[str]
    ambiguous: bool
    url: str = ""


def _split_keywords(raw: str) -> List[str]:
    """Split a comma- or newline-separated answer into clean keywords."""
    return [part.strip().lower() for part in re.split(r"[,\n]", raw) if part.strip()]


def _prompt_brand_keywords(name: str, url: str = "") -> _BrandDraft:
    """Collect the search variants for one brand.

    The brand's own name is seeded automatically and shown, so the prompt asks
    only for what it doesn't already have. Enter accepts the seed alone, which
    keeps the flow as fast as it used to be for anyone who doesn't need more.
    """
    seed = name.strip().lower()
    click.echo()
    click.secho(f"  {name}", bold=True)
    click.echo(f'    Already counting: "{seed}"')
    click.echo('    Sub-brands, products, variants — comma-separated. Enter to skip.')
    extra = click.prompt("    Also count", default="", show_default=False)

    keywords: List[str] = []
    for keyword in [seed, *_split_keywords(extra)]:
        if keyword and keyword not in keywords:
            keywords.append(keyword)

    ambiguous = click.confirm(
        f'    Is "{name}" also an everyday word (Apple, Emma, Orange)?', default=False
    )
    return _BrandDraft(name=name, keywords=keywords, ambiguous=ambiguous, url=url)


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
    own: "_BrandDraft",
    competitors: List["_BrandDraft"],
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
        "#",
        "# Adding keywords costs nothing: DataForSEO bills per request, not per",
        "# keyword. Give every brand the variants people actually search, and keep",
        "# the depth even across brands — a brand tracked on one keyword against a",
        "# competitor tracked on five will look smaller than it really is.",
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
    ]
    lines += _render_brand(own, indent="  ", first=False)
    lines += [
        "",
        "# Set `ambiguous: true` for any brand whose name is also a common word —",
        "# its volume will include searches that have nothing to do with the brand.",
        "competitors:",
    ]
    for competitor in competitors:
        lines += _render_brand(competitor, indent="    ", first=True)

    return "\n".join(lines) + "\n"


def _render_brand(draft: "_BrandDraft", indent: str, first: bool) -> List[str]:
    """Render one brand block. ``first`` marks it as a YAML list item."""
    head = indent[:-2] + "- " if first else indent
    lines = [f"{head}name: {_yaml_scalar(draft.name)}"]
    if draft.url:
        lines.append(f"{indent}url: {_yaml_scalar(draft.url)}")
    lines.append(f"{indent}keywords:")
    for keyword in draft.keywords:
        lines.append(f"{indent}  - {_yaml_scalar(keyword)}")
    lines.append(f"{indent}ambiguous: {'true' if draft.ambiguous else 'false'}")
    return lines


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
        for warning in keyword_parity_warnings({b.name: b.keywords for b in config.brands}):
            _warn(warning)
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
@click.option("--months", "months", type=int, default=None, metavar="N",
              help="Pull the trailing N months. Costs the same as any other window.")
@click.option("--from", "date_from", default=None, metavar="YYYY-MM", help="Explicit range start.")
@click.option("--to", "date_to", default=None, metavar="YYYY-MM", help="Explicit range end (capped at the last complete month).")
@click.option("--refresh-last", type=int, default=None, metavar="N",
              help="Re-pull the trailing N months, absorbing Google's revisions to recent history.")
@click.option("--no-prompt", is_flag=True,
              help="Never ask anything interactively. Use the defaults and carry on.")
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
    months: Optional[int],
    date_from: Optional[str],
    date_to: Optional[str],
    refresh_last: Optional[int],
    no_prompt: bool,
    data_dir: Path,
    dry_run: bool,
) -> None:
    """Pull search volumes and update the data store.

    \b
    With a config file:
      sos run
    Ad-hoc, no config file:
      sos run --brand Acme --competitors "Globex,Initech"

    On a first pull you're asked how far back to reach. On later runs the
    trailing twelve months are re-pulled automatically, because Google revises
    recent history.

    Cost is per request, not per keyword or per month — every brand goes out in
    one call, so a run is about $0.075 whether you track three brands or thirty
    and whether you ask for one year or four.
    """
    from sos import run as run_module
    from sos import store as store_module

    config = _resolve_config(config_path, brand, brand_url, competitors, market)

    empty_store = store_module.is_empty(data_dir)
    start, end, reason = _resolve_range(
        data_dir, config, empty_store, backfill, months, date_from, date_to,
        refresh_last, no_prompt,
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
        result = run_module.refresh(
            config=config,
            source=source,
            data_dir=data_dir,
            start=start,
            end=end,
        )
    except DataSourceError as exc:
        raise _fail(str(exc)) from None

    _ok(
        f"{result.rows_fetched} keyword-months returned, covering "
        f"{result.months_returned} distinct months."
    )
    if backfill and result.months_returned < BACKFILL_MONTHS:
        _info(
            f"Asked for {BACKFILL_MONTHS} months, got {result.months_returned}. "
            "Google Ads caps history depth; this is expected, not an error."
        )

    click.echo()
    _ok(f"Store updated: {result.store_path}  ({len(result.frame)} rows)")

    for warning in result.warnings:
        _warn(warning)

    _print_summary(result.frame, config)

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
    months: Optional[int],
    date_from: Optional[str],
    date_to: Optional[str],
    refresh_last: Optional[int],
    no_prompt: bool = False,
) -> "tuple[date, date, str]":
    """Work out which months to request, and say why.

    Explicit flags always win. The interactive prompt only appears on the path
    that would otherwise backfill silently — a first run, or a market with no
    data yet — so routine refreshes stay quiet.
    """
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

    if months is not None:
        if months < 1:
            raise _fail("--months must be at least 1.")
        return shift_months(end, -(months - 1)), end, f"last {months} months"

    if backfill:
        return shift_months(end, -(BACKFILL_MONTHS - 1)), end, "backfill"

    first_pull = empty_store or not store_module.existing_months(data_dir, config.market.name)
    if first_pull:
        why = "empty store" if empty_store else "no data for this market"
        if _can_prompt(no_prompt):
            return _prompt_timeframe(end)
        click.echo()
        _info("No data stored yet — backfilling the full available history.")
        return shift_months(end, -(BACKFILL_MONTHS - 1)), end, f"auto-backfill, {why}"

    stored = store_module.existing_months(data_dir, config.market.name)
    latest = stored[-1].date()
    start = shift_months(latest, -(DEFAULT_REFRESH_MONTHS - 1))
    return start, end, f"new months plus a {DEFAULT_REFRESH_MONTHS}-month refresh"


def _can_prompt(no_prompt: bool) -> bool:
    """Only ask when there's a human there to answer.

    Without the TTY check a piped or CI invocation would block forever on a
    prompt nobody can see.
    """
    return not no_prompt and sys.stdin.isatty()


#: Offered before a first pull. Full history leads because it costs the same.
TIMEFRAME_CHOICES = [
    (BACKFILL_MONTHS, f"Full history — asks for {BACKFILL_MONTHS} months"),
    (24, "Last 24 months — two years"),
    (12, "Last 12 months — one year"),
]


def _prompt_timeframe(end: date) -> "tuple[date, date, str]":
    """Ask how far back to reach on a first pull.

    Worth stating in the prompt that a wider window is free: the natural
    assumption is that asking for four years costs four times what one year
    does, and acting on that assumption throws away history for no saving.
    """
    click.echo()
    click.secho("  Time frame", bold=True)
    click.echo("  Billing is per request, not per month — every option below costs the same")
    click.echo("  ~$0.075. More history makes the trend and the 12-month average far more")
    click.echo("  useful, so take the full run unless you have a reason not to.")
    click.echo()
    for number, (_, label) in enumerate(TIMEFRAME_CHOICES, start=1):
        click.echo(f"    {number}) {label}" + ("  [default]" if number == 1 else ""))
    custom = len(TIMEFRAME_CHOICES) + 1
    click.echo(f"    {custom}) Custom range")
    click.echo()

    choice = click.prompt(
        "  Choose",
        default="1",
        show_default=False,
        type=click.Choice([str(n) for n in range(1, custom + 1)]),
    )

    if int(choice) == custom:
        start = _parse_month(click.prompt("  Start month (YYYY-MM)"), "start month")
        raw_end = click.prompt("  End month (YYYY-MM)", default=f"{end:%Y-%m}")
        chosen_end = min(_parse_month(raw_end, "end month"), end)
        if start > chosen_end:
            raise _fail(f"Start month {start:%Y-%m} is after end month {chosen_end:%Y-%m}.")
        return start, chosen_end, "custom range"

    window = TIMEFRAME_CHOICES[int(choice) - 1][0]
    reason = "full history" if window == BACKFILL_MONTHS else f"last {window} months"
    return shift_months(end, -(window - 1)), end, reason


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


# --------------------------------------------------------------------------
# refresh
# --------------------------------------------------------------------------

#: Set on the process before it re-executes itself after a code update. Its
#: presence means "the update already happened this invocation", which bounds
#: the restart at exactly one — a pull that somehow kept reporting a change
#: could otherwise loop forever.
REEXEC_ENV_VAR = "SOS_REFRESH_REEXEC"


@main.command()
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None,
              help=f"Config file to use.  [default: {DEFAULT_CONFIG_PATH}]")
@click.option("--market", default="US", show_default=True, help="Market shorthand, e.g. US, UK, DE.")
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path("data"), show_default=True,
              help="Where the CSV store lives.")
@click.option("--out", type=click.Path(path_type=Path), default=Path("output/share-of-search.html"),
              show_default=True, help="Where to write the HTML file.")
@click.option("--no-pull", is_flag=True, help="Skip the code update; just refresh data and rebuild.")
@click.option("--no-open", is_flag=True, help="Build the report without opening a browser.")
@click.option("--no-prompt", is_flag=True, help="Never ask anything interactively.")
@click.pass_context
def refresh(
    ctx: click.Context,
    config_path: Optional[Path],
    market: str,
    data_dir: Path,
    out: Path,
    no_pull: bool,
    no_open: bool,
    no_prompt: bool,
) -> None:
    """Update the code, pull fresh data, rebuild the report and open it.

    \b
    The everyday command:
      sos refresh

    Equivalent to `git pull && sos run && sos dashboard --open`, except that
    the code update actually takes effect: if the pull brings anything in, the
    command restarts itself so the new code does the work.

    For anything specific — an explicit range, ad-hoc brands, a dry run — use
    `sos run` directly.
    """
    if not no_pull:
        _update_code()

    try:
        ctx.invoke(
            run,
            config_path=config_path,
            market=market,
            data_dir=data_dir,
            no_prompt=no_prompt,
        )
    except click.ClickException as exc:
        # Building the report anyway would hand back something that looks
        # freshly refreshed while showing the previous run's data — the one
        # outcome this command must never produce.
        #
        # Shown here rather than re-raised so the reassurance lands *after*
        # the error it's about; click prints an escaping exception last, which
        # would put the advice above the problem.
        exc.show()
        click.echo()
        _info("Your stored data is unchanged. `sos dashboard --open` still renders "
              "what's already there.")
        ctx.exit(exc.exit_code)

    ctx.invoke(
        dashboard,
        config_path=config_path,
        market=market,
        data_dir=data_dir,
        out=out,
        open_browser=not no_open,
    )


def _update_code() -> None:
    """Bring the checkout up to date, and restart if that changed anything."""
    import os

    if os.environ.get(REEXEC_ENV_VAR):
        return  # We are the restarted process; the pull already happened.

    root = _source_checkout()
    if root is None:
        click.echo()
        _info("No git checkout to update — skipping straight to the data. (A "
              "non-editable `pip install .` copies the package, so pulling the "
              "repository wouldn't change what runs; reinstall to update.)")
        return

    click.echo()
    safe, why = _pull_status(root)
    if not safe:
        _warn(f"Skipping the code update: {why}.")
        return

    changed, message = _pull(root)
    if not changed:
        _info(message)
        return

    _ok(message)
    _info("Restarting so the new code does the work...")
    _reexec()


def _source_checkout() -> Optional[Path]:
    """The git checkout the *running code* came from, or ``None``.

    Resolved from the package's own location rather than the working
    directory, which is the safety property that matters: ``sos refresh`` run
    inside some unrelated project must never ``git pull`` that project.

    ``None`` also covers a non-editable ``pip install .``, where the package
    sits in site-packages with no checkout above it — pulling the repository
    there wouldn't change a line of what actually runs.
    """
    import sos

    try:
        root = Path(sos.__file__).resolve().parents[2]  # <root>/src/sos/__init__.py
    except IndexError:
        return None
    if (root / ".git").exists() and (root / "pyproject.toml").exists():
        return root
    return None


def _git(root: Path, *args: str):
    """Run one git command against ``root``, capturing rather than raising."""
    import subprocess

    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )


def _pull_status(root: Path) -> "tuple[bool, str]":
    """Whether pulling is safe, and why not when it isn't.

    Uncommitted changes stop the pull outright. Someone part-way through
    editing the tool should never have a *data* refresh disturb their work.
    """
    result = _git(root, "status", "--porcelain")
    if result.returncode != 0:
        return False, "git couldn't read the checkout"
    if result.stdout.strip():
        return False, "you have uncommitted changes there, so they're being left alone"
    return True, ""


def _pull(root: Path) -> "tuple[bool, str]":
    """Fast-forward the checkout. Returns ``(changed, message)``.

    ``--ff-only`` so a refresh can never write a merge commit or leave a
    conflicted tree in somebody's checkout; a diverged branch fails cleanly
    instead. And a failed pull is *reported*, never raised — being unable to
    update the code is no reason to skip the data refresh that was asked for.
    """
    before = _git(root, "rev-parse", "HEAD").stdout.strip()
    result = _git(root, "pull", "--ff-only")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return False, "couldn't update the code: " + (detail[-1].strip() if detail else "git failed")

    after = _git(root, "rev-parse", "HEAD").stdout.strip()
    if before and after and before != after:
        return True, f"Code updated ({before[:7]} to {after[:7]})."
    return False, "Code already up to date."


def _reexec() -> NoReturn:
    """Restart this command so the code just pulled is the code that runs.

    The process reaching this line was loaded before the pull. ``cli.py``
    imports some modules at module level and others lazily inside the command
    bodies, so carrying on would do the work with a *mix* of pre- and
    post-pull code — harder to trust than being uniformly stale, because you
    can't tell which half you got.

    ``execvp`` replaces the process image, so nothing after this line runs.
    """
    import os

    os.environ[REEXEC_ENV_VAR] = "1"
    sys.stdout.flush()  # execvp won't flush a block-buffered pipe for us.
    sys.stderr.flush()
    os.execvp(sys.argv[0], sys.argv)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
