# Share of Search

Track whether your brand's demand is growing or shrinking against a competitor set you
define, from monthly search volume.

## The problem

**Last-click attribution only sees demand you captured, never demand you created.**

Someone searches your brand name, clicks a paid ad, converts. Paid search takes the credit.
But the reason they typed your name in the first place was something else — a campaign, a
mention, a recommendation, six months of brand work. In the attribution model that work
scores zero. So it's the first budget cut and the hardest to defend.

The tools built to measure it are enterprise SaaS: brand trackers, panel-based market
share, marketing mix modelling. Enterprise pricing, quarterly cadence, onboarding measured
in weeks. Reasonable for a global brand. Out of reach for most agency–client relationships,
and heavy for a question as plain as "is demand moving?"

So the quarterly review falls back on impressions, reach and engagement — none of which a
CFO accepts as evidence that anything changed.

## What Share of Search measures

Branded search is someone typing your name on purpose. It's a signal of demand, not a
record of a conversion.

Share of Search is your brand's slice of all branded search in a category you define:

```
        your brand's monthly search volume
  ─────────────────────────────────────────────────  ×  100
   total monthly volume across your set of brands
```

Les Binet showed that this correlates with market share and tends to **lead** it — by
around three months in fast-moving categories, up to twelve for considered purchases. See
[Share of Search as a Predictive Measure](https://www.youtube.com/watch?v=x1zMufAs3l0),
IPA EffWorks Global 2020.

Two things it is not. It isn't a prediction of market share, and it doesn't explain
anything on its own. It's an early signal, available monthly, from data you pull yourself.
[How to read the output](#how-to-read-the-output) is the honest list of ways it can mislead
you, and it's worth reading before you quote a number.

## What you'd use it for

- **Show brand work did something** when last-click says it didn't. Branded search going up
  is demand created, and it moves before revenue does.
- **Defend or grow a brand budget** with a number rather than an assertion.
- **Read a campaign early** — weeks after it ends, not two quarters later.
- **See what a competitor's move actually did.** Did they take your demand, or grow the
  category? Share and category total answer different questions.
- **Bring something to a QBR** that isn't impressions, and that the client can check
  themselves.

## What this repo is

A command-line tool. It pulls monthly branded search volume for you and your competitors
from DataForSEO, works out each brand's share, and writes two things:

- `data/sos_monthly.csv` — one row per brand per month, for your own analysis
- a self-contained HTML dashboard — one file, opens by double-clicking, works offline,
  survives being emailed to a client

No server, no database, no login, no subscription. You bring a DataForSEO account; the rest
is a `git clone`.

![The generated dashboard](docs/dashboard.png)

*Illustrative brand set and synthetic volumes — the layout is exactly what a real run produces.*

---

## Install

```bash
git clone https://github.com/afllewellyn/share-of-search-tool.git
cd share-of-search-tool
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

You need a [DataForSEO](https://dataforseo.com/) account. Their Keywords Data API is
pay-as-you-go. Your whole brand set goes out in a **single request**, however many brands
and months you ask for.

```bash
export DATAFORSEO_LOGIN='your-login'         # the email you registered with
export DATAFORSEO_PASSWORD='your-password'   # the API password from your dashboard
```

Or copy `.env.example` to `.env` and fill it in — `.env` is gitignored and loaded
automatically.

## Quickstart

The 30-second version needs no config file at all:

```bash
sos run --brand Anthropic --competitors "OpenAI,Perplexity,Mistral"
sos dashboard --open
```

That backfills the full available history (about four years), writes
`data/sos_monthly.csv`, and opens the dashboard.

For anything you'll run more than once, use a config file so you can give each brand
several keywords:

```bash
sos init          # interactive: brands, their keywords, market
sos validate      # checks config and credentials, calls nothing
sos run
sos dashboard --open
```

`sos init` walks three steps: who's in the category, **what each brand is searched as**,
and the market. The middle step decides whether the percentages are fair. It asks each
brand for its sub-brands, product lines and common variants, so one brand isn't tracked on
its name alone while a competitor gets five keywords.

On a first pull, `sos run` asks how far back to reach — full history, two years, one year,
or a custom range. Take the full run unless you have a reason not to. It's the same single
request either way, and more history makes the trend and the 12-month average far more
useful. Use `--months N` to skip the question, or `--no-prompt` in a script.

Re-run `sos run` whenever you want fresh data. It pulls any new months and re-pulls the
trailing twelve, because Google revises recent history and because widening the window
doesn't add a request. Runs are idempotent: the same command twice leaves the store
byte-identical.

Change your competitor set and the next run reconciles the store to match. Brands you
removed are dropped rather than left behind inflating the category total.

## Everyday use

Day to day, one command instead of three:

```bash
sos refresh
```

It updates the tool, pulls fresh data, rebuilds the report and opens it. That last step is
easy to forget: the dashboard is a *generated file*, so `git pull` alone changes nothing
you can see until something rebuilds it.

`refresh` fails safe:

- It only pulls the checkout the running code came from, never whatever directory you're
  standing in.
- Uncommitted edits stop the pull rather than being pulled over. Fast-forward only.
- A failed pull is a reason to carry on to the data, not to stop.
- A failed *data* pull stops it without rebuilding — a report that looks fresh while
  showing last week's numbers is worse than no new report.

When a pull does bring in new code, `refresh` restarts itself before doing the work.
Otherwise it would download an update and then run the old copy already loaded in memory.
Two notes: the pull step does nothing unless you installed with `pip install -e .` (a plain
`pip install .` makes a separate copy, so reinstall to update), and `--no-pull` skips it.
Anything more specific — a date range, ad-hoc brands, a dry run — is still `sos run`.

## Commands

```
sos init                 Write a config file interactively. Won't overwrite without --force.
sos validate             Check config and credentials. No API call.
sos run                  Pull volumes and update the store.
sos dashboard            Build the HTML report from stored data. No API call.
sos refresh              Update the tool, pull data, rebuild and open. The everyday one.
```

Useful `sos run` flags:

| Flag | What it does |
|---|---|
| `--brand` / `--competitors` | Ad-hoc mode — no config file needed |
| `--months N` | Pull the trailing N months |
| `--backfill` | Pull the full available history |
| `--from YYYY-MM` / `--to YYYY-MM` | Explicit range |
| `--refresh-last N` | Re-pull the trailing N months to absorb Google's revisions |
| `--market US` | Market shorthand (`US`, `UK`, `DE`, …) |
| `--data-dir PATH` | Where the CSV store lives, default `./data` |
| `--dry-run` | Show the request plan, call nothing |
| `--no-prompt` | Never ask anything interactively — for scripts and CI |

`sos <command> --help` has the rest.

## Configuration

`config/brands.yaml` — gitignored, so your competitor set never lands in version control.
Copy `config/brands.example.yaml` to start:

```yaml
market:
  name: US
  location_code: 2840        # DataForSEO location code
  language_code: en

smoothing_windows: [3, 12]   # months; the dashboard defaults to 3

own_brand:
  name: Acme
  url: https://www.acme.com
  keywords:
    - acme
    - acme app
  ambiguous: false

competitors:
  - name: Globex
    keywords: [globex]
  - name: Emma
    keywords: [emma mattress]   # disambiguated on purpose
    ambiguous: true             # generic word — flagged in the dashboard
```

**Give each brand the keyword variants people actually search.** A brand tracked on one
keyword while a competitor is tracked on five will look smaller than it is, and nothing
else in the output reveals why. `sos validate` warns when one brand's coverage is more than
three times another's. Extra keywords don't add requests — the whole set goes out in one.

**Set `ambiguous: true` for brand names that are also ordinary words** — Emma, Apple,
Orange. Their volume includes searches that have nothing to do with the brand. The flag
doesn't correct for it. It surfaces the caveat in the dashboard so nobody quotes the number
without it.

## The data store

`data/sos_monthly.csv`, one row per brand per month per market:

`date` · `year` · `month` · `brand` · `is_own_brand` · `market` · `location_code` ·
`language_code` · `keywords` · `raw_volume` · `category_total_volume` · `sos_pct` ·
`sos_pct_3mo` · `sos_pct_12mo` · `volume_3mo` · `data_source` · `pulled_at`

`raw_volume` is the only source of truth. Every other number is recomputed across the whole
series after each write. So when Google revises an old month, the correction just flows
through — nothing to reconcile by hand. Writes go to a temp file and are atomically
renamed, so an interrupted run can't corrupt the store.

`data/` and `output/` are gitignored by default, so nobody accidentally commits a client's
competitor set. If you *want* results in version control, delete those two lines from
`.gitignore`.

## How to read the output

Share of Search is a **leading indicator correlated with market share**. It is not a
prediction of it, and not an explanation of anything. A handful of things will mislead you
if you let them:

- **The denominator is your competitor set, never total search.** Add or remove a brand and
  every number changes. If the set is missing a real competitor, every figure is overstated.
  The tool warns when your own brand exceeds ~65%, which usually means someone is missing.
- **A brand's share can rise because a competitor collapsed.** This is the most common
  misreading. When the category total moves enough that share shifts are mostly a
  denominator effect, the commentary says so first.
- **Single months are noisy.** Seasonality alone moves these lines. That's what the
  smoothing toggle is for. The tool also measures each brand's own month-to-month
  volatility and tells you when a move sits inside it.
- **A longer smoothing window covers less of the chart, and that's correct.** Rolling
  averages are *trailing and full*. A 12-month average needs twelve months before it has a
  value, so it starts nine months later than the 3-month one. The alternative — averaging
  four months and labelling it a twelve-month average — would be the real error. The chart
  trims the empty run-up and says which month each view begins.
- **Google's volumes are rounded and bucketed.** Absolute numbers are approximate. Share is
  a ratio, so consistent estimation bias largely cancels. Read the trend, not the decimal.
- **Lead times vary by category** — roughly three months in fast-cycle categories, up to
  twelve for considered purchases.
- **The current month is never available** from Google Ads, and in practice the data lags
  by about two months. The tool caps its requests accordingly.

The dashboard's collapsible "About this metric" section carries all of this, so a report
that leaves your hands still explains itself.

## Commentary

Every run prints one or two sentences describing what moved and whether it means anything.
These come from rule-based templates over a facts payload computed in pandas — **no LLM, no
API key, no network**. The tool is fully useful with zero AI dependency.

The numbers are never generated by a language model. `facts.py` computes deltas, ranks,
noise thresholds and category effects. `commentary.py` only phrases them. That seam is
deliberate: swapping `commentary.generate()` for a model call later changes one function
and nothing else.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests run entirely against `tests/fixtures/sample_response.json` — no live API calls,
no credentials needed. They cover the parts where a bug does real damage: keyword grouping,
brand aggregation, the share calculation, rolling windows, store idempotency, and the
self-containment of the generated HTML.

On that last one: **the dashboard bundles Chart.js instead of loading it from a CDN**, and
inlines its data instead of fetching a sibling `data.json` — a page opened over `file://`
can't fetch anything. That's what makes the file work on someone else's machine, offline,
with no explanation attached. Chart.js 4.4.1 is vendored under `src/sos/dashboard/vendor/`
and is MIT licensed.

### Adding a different data source

`src/sos/datasource/base.py` defines the interface:

```python
fetch_monthly_volume(keywords, location_code, language_code, date_from, date_to)
    -> list of {keyword, year, month, search_volume}
```

Nothing DataForSEO-specific leaks past that boundary, so a Google Ads API backend is a
drop-in replacement.

### On grouped keywords

Google Ads returns a *combined* volume for keywords it considers close variants. Summing
them doubles that brand's volume and inflates its share. The numbers still look plausible.
That's what makes it dangerous.

The tool detects this. Within a single brand, keywords returning identical volumes in every
month where both have data (at least two such months, and not all zeros) are counted once,
with a warning naming what was dropped. Verified against live data: `openai` and `open ai`
came back identical in 47 of 47 months and were collapsed; `anthropic` and `anthropic ai`
matched in 0 of 47 and were kept separate.

The decision needs real evidence to be safe. Google's volumes are heavily bucketed, so over
a two- or three-month window two genuinely distinct low-volume keywords can land on the
same values by chance. Merging them on that basis would drop a real keyword and leave a
false cliff in the brand's history. So a *new* grouping decision is only made from a
response spanning at least six months. Below that, the decision an earlier run already made
is carried forward, recovered from the store's `keywords` column. The default refresh
window is twelve months precisely so a routine run always clears that bar.

### On history depth

DataForSEO's docs disagree about whether Google Ads serves 24 or 48 months. Measured
against the live API on 2026-08-01 (US, en), a request for 48 months returned **47** —
roughly four years, with the most recent month unavailable because the data lags by two
months, not one. The tool requests 48, reports what actually arrived, and handles either.

## Not in this version

Scheduled runs with commit-back · hosted dashboards · ESOS (needs market-share data) · paid
search and SERP share of voice · multi-market and multi-language in one run · a Google Ads
API backend · Google Trends cross-checks for ambiguous brands · PyPI publication.

The seams are there — the `datasource` interface, the `market` columns already in the
schema — but none of it is built.

## Licence

MIT. See [LICENSE](LICENSE).

Share of Search as a metric is Les Binet's work, presented at IPA EffWorks Global 2020. The
IPA think-tank findings behind it are correlations, not causal relationships.
