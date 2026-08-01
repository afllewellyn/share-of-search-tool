# BUILD_PLAN.md — Share of Search CLI

> Drop this at the root of the repo and point Claude Code at it.
> **You write the code. This document defines what to build, not how to write it.**

---

## 1. What this is

A command-line tool that measures **Share of Search** — a brand's organic branded search volume as a percentage of the total branded search volume across a defined competitor set. Methodology per Les Binet (IPA EffWorks Global 2020).

It pulls monthly search volume from the DataForSEO API, stores it as flat files, computes share and rolling averages, and generates a self-contained HTML dashboard.

**Public repo. Open-source tool intended for others to use.** Design every decision for a stranger cloning this, not just the author.

---

## 2. Non-negotiable constraints

| Constraint | Why |
|---|---|
| **No server, no login, no database** | Distribution is `git clone` + `pip install -e .`. Keep it that way. |
| **API keys via environment variables only** | Never in tracked files, never as CLI arg defaults, never printed in logs or committed output. |
| **Dashboard is a single self-contained HTML file** | Data inlined as a JS const. A separate `data.json` breaks under `file://` due to CORS. Must open by double-clicking. |
| **Idempotent runs** | Re-running any command must never duplicate rows or corrupt the store. |
| **Real config and real data are gitignored** | Committed repo ships `.example` files only. |
| **Organic search volume only** | No paid, no SERP share of voice in V1. |
| **Never let an LLM do arithmetic** | All numbers computed in pandas. See §9. |

---

## 3. Folder structure

Build exactly this. Deviate only if you hit a real problem, and say so.

```
share-of-search/
├── README.md                     # install, quickstart, methodology summary, screenshot
├── BUILD_PLAN.md                 # this file
├── LICENSE                       # MIT
├── pyproject.toml                # packaging + `sos` console_scripts entry point
├── .gitignore                    # .env, config/brands.yaml, data/, output/, __pycache__
├── .env.example                  # documents required env var NAMES, no values
│
├── config/
│   └── brands.example.yaml       # committed template (see §5)
│   # config/brands.yaml          # gitignored — the user's real brand set
│
├── src/sos/
│   ├── __init__.py
│   ├── cli.py                    # argparse/click entry: init | run | dashboard | validate
│   ├── config.py                 # load + validate brands.yaml; build config from CLI flags
│   ├── datasource/
│   │   ├── __init__.py
│   │   ├── base.py               # KeywordVolumeSource interface (see §7)
│   │   └── dataforseo.py         # DataForSEO implementation
│   ├── transform.py              # aggregation, share calc, rolling averages
│   ├── facts.py                  # month_facts payload: deltas, ranks, noise thresholds
│   ├── commentary.py             # V1: rule-based templates. V1.5: LLM behind same signature
│   ├── store.py                  # idempotent CSV read/write, atomic file ops
│   └── dashboard/
│       ├── __init__.py
│       ├── build.py              # renders template + inlined data -> single HTML file
│       └── template.html         # Chart.js dashboard skeleton with placeholders
│
├── data/                         # gitignored by default
│   └── .gitkeep
├── output/                       # gitignored; generated dashboard lands here
│   └── .gitkeep
│
└── tests/
    ├── test_transform.py         # share math, aggregation, rolling windows
    ├── test_store.py             # idempotency, upsert, atomic writes
    ├── test_facts.py             # delta/rank/threshold logic
    └── fixtures/
        └── sample_response.json  # canned DataForSEO response, no live calls in tests
```

**Note:** `data/` and `output/` are gitignored by default so nobody accidentally commits a client's competitor set. A `--data-dir` flag lets the user point elsewhere. If someone *wants* to commit results (e.g. for the V2 scheduled job), they opt in by editing `.gitignore` — document this in the README.

---

## 4. CLI surface

Console script name: `sos`

```
sos init                    Interactive setup. Prompts for brand name, brand URL,
                            competitors (one per line), market. Writes config/brands.yaml.
                            Must not overwrite an existing file without --force.

sos run                     Pull data and update the store.
  --config PATH             Default: config/brands.yaml
  --brand NAME              Ad-hoc mode: skip config entirely
  --brand-url URL
  --competitors "A,B,C"     Comma-separated
  --market US               Default: US
  --backfill                Pull full available history (request 48 months)
  --from YYYY-MM            Explicit range start
  --to YYYY-MM              Explicit range end
  --refresh-last N          Re-pull trailing N months (absorbs Google back-revisions)
  --data-dir PATH           Default: ./data
  --dry-run                 Show what would be requested + estimated cost, make no call

sos dashboard               Build the HTML from the existing store. No API calls.
  --data-dir PATH
  --out PATH                Default: output/share-of-search.html
  --open                    Open in default browser when done

sos validate                Check config validity and env vars without calling the API.
```

Default behaviour with no prior data: `sos run` should detect an empty store and backfill automatically, with a printed notice.

Ad-hoc mode (`--brand` + `--competitors`) requires no config file at all. This is the "try it in 30 seconds" path for a new user — make sure it works cleanly.

---

## 5. Config format (`config/brands.example.yaml`)

```yaml
market:
  name: US
  location_code: 2840        # DataForSEO location code
  language_code: en

smoothing_windows: [3, 12]   # months; dashboard defaults to 3

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
    ambiguous: false
  - name: Emma
    keywords: [emma mattress]   # disambiguated on purpose
    ambiguous: true             # generic word — flag in dashboard
```

Validate on load: exactly one `own_brand`, unique brand names, ≥1 keyword each, valid location/language codes. Fail with a clear message naming the offending field — not a stack trace.

---

## 6. Data store schema (`data/sos_monthly.csv`)

One row per **brand per month per market**.

`date` (month start, ISO) · `year` · `month` · `brand` · `is_own_brand` · `market` · `location_code` · `language_code` · `keywords` (`;`-joined) · `raw_volume` · `category_total_volume` · `sos_pct` · `sos_pct_3mo` · `sos_pct_12mo` · `volume_3mo` · `data_source` · `pulled_at`

**`raw_volume` is the only source of truth.** Everything else — category totals, share, rolling averages — is recomputed across the whole series after any write. This is cheap and correctly handles Google revising historical months.

Upsert key: `(date, brand, market)`. Load existing → drop matching keys → concat → sort → rewrite. Write to a temp file and atomically rename so a crash can't corrupt the store.

---

## 7. Data source layer

Define an interface in `datasource/base.py`:

```
fetch_monthly_volume(keywords, location_code, language_code, date_from, date_to)
    -> list of {keyword, year, month, search_volume}
```

`dataforseo.py` implements it. A future Google Ads API backend must be a drop-in — do not leak DataForSEO-specific shapes past this boundary.

**DataForSEO specifics:**
- `POST https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live`
- HTTP Basic Auth: base64 of `login:password` from `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD`
- Request body is an **array** of task objects. Params: `keywords[]` (max 1000), `location_code`, `language_code`, `date_from`, `date_to`, `search_partners: false`
- Response: `tasks[].result[].monthly_searches[]` → `{year, month, search_volume}`
- **Cost is per request, not per keyword** — send all brands' keywords in ONE request. ~$0.075/run.
- Rate limit: 12 requests/min. Only relevant if you split requests.
- Check top-level `status_code == 20000` AND each task's status code.
- Retry with exponential backoff on 5xx and rate-limit errors.

**Open question to resolve empirically on first run:** DataForSEO's docs conflict on history depth (24 vs 48 months). Request 48, log how many months actually return, handle either gracefully.

---

## 8. Core calculation logic

1. Fetch all keywords in one request.
2. Parse `monthly_searches[]` into a tidy frame: `(keyword, year, month, volume)`.
3. **Aggregate keyword → brand** per month by summing that brand's keywords.
   - **Grouped-keyword guard:** Google Ads returns a *combined* volume for keywords it considers similar. If two variants of the same brand return identical volumes for every month, they're almost certainly a grouped pair — count once and log a warning. Getting this wrong silently doubles a brand's share.
4. `category_total_volume` = sum of all brands' `raw_volume` for that month.
5. `sos_pct = raw_volume / category_total_volume * 100`
6. Rolling averages per brand per configured window, trailing, full-window (leave nulls until the window fills).
7. Null/missing volume: log per keyword. If an **entire brand** returns nothing for a month, flag it — do not silently treat as zero share. That's an API gap, not zero demand.

Unit-test steps 3–6. This is where the bugs will be.

---

## 9. Commentary (`facts.py` + `commentary.py`)

`facts.py` produces a `month_facts` dict per run — pure pandas, no LLM:

- per brand: `sos_pct`, MoM delta, YoY delta, rank, rank change
- `exceeds_noise_threshold`: is |MoM delta| > 1.5× the trailing 12-month stdev of that brand's MoM changes? (Use 2× until ≥12 months of history exist.)
- `category_total_change_pct` and a `category_driven` boolean — true when the category total moved enough that share shifts are mostly denominator effects
- list of `ambiguous` brands
- months of history available

**V1: `commentary.generate(facts) -> list[str]` renders rule-based template sentences from this payload.** No API call, no key required. The tool must be fully useful with zero LLM dependency.

**V1.5: swap the body of `generate()` for an Anthropic API call. Same signature, same input payload, no other file changes.** That's the whole point of the seam.

LLM guardrails, in the system prompt:
- Receives the **pre-computed facts payload only** — never raw tables. It phrases and contextualizes; it does not calculate.
- Never assert causation or attribute movement to campaigns, spend, or creative.
- Explicitly say "within normal variation" when `exceeds_noise_threshold` is false.
- **When `category_driven` is true, lead with that** — a brand's share rising because a competitor collapsed is the single most common misreading, and the one most likely to end up in a client deck.
- Note that SoS leads market share rather than reflecting it.
- 1–2 bullets total for the brand set, not per brand.

Cache output in the store keyed by `(month, market)`. Do not regenerate on every dashboard rebuild — the text would drift for no reason. If the API call fails, fall back to the V1 templates so a run never breaks. Key via `ANTHROPIC_API_KEY`.

---

## 10. Dashboard (`dashboard/build.py` + `template.html`)

Single self-contained HTML file. Data inlined as a JS const — **no external `data.json`**, no CDN dependency for the data. Chart.js may come from CDN; note in the README that the file then needs internet on first open.

Contents:
- Line chart: SoS% over time, one line per brand
- Stacked area view: the 100% composition view
- Smoothing toggle: raw / 3-month / 12-month
- Summary table: latest month, MoM, YoY per brand
- Commentary bullets from §9, near the top
- **Methodology section at the bottom** (collapsible "About this metric"): definition, organic-only, data source, market, competitor set in use, smoothing window, date range, generated-at timestamp. Credit Les Binet / IPA EffWorks Global 2020.
- **"Indicator, not forecast" note**, visually distinct within that section: SoS is a *leading indicator correlated with* market share, not a prediction of it. Lead times vary by category (~3 months in fast-cycle categories, up to ~12 for considered purchases). IPA think-tank findings are correlations, not causal relationships. Volumes are Google-estimated and bucketed. Name any `ambiguous: true` brands here explicitly.
- **CSV download buttons**, client-side only: build the CSV string in JS from the inlined array, trigger via `Blob` + `URL.createObjectURL()`. Two buttons: "current view" (respects the active smoothing toggle) and "full dataset". Prepend a metadata header block (source, market, date range, competitor set, smoothing window, generated-at) so the file is self-documenting once it leaves the dashboard. Filename: `share-of-search_{market}_{YYYY-MM}.csv`.

Design it so it's presentable to a client without editing. Clean, restrained, readable at a glance.

---

## 11. Build sequence

**Phase 1 — skeleton**
`pyproject.toml` with the `sos` entry point, folder structure, `.gitignore`, `.env.example`, `brands.example.yaml`, config loader with validation, `sos validate`.

**Phase 2 — data source**
DataForSEO client with Basic Auth, retry/backoff, the `base.py` interface. Fetch one brand, print raw JSON. **Resolve the 24-vs-48-month history question here.** `--dry-run` and cost estimate.

**Phase 3 — transform + store** ← *most important phase; write the tests here*
Parser, brand aggregation with the grouped-keyword guard, share calc, rolling averages, idempotent CSV upsert with atomic writes. Unit tests against `fixtures/sample_response.json`.

**Phase 4 — CLI**
`sos init` interactive flow, `sos run` with all flags including ad-hoc mode, auto-backfill on empty store, clear error messages.

**Phase 5 — facts + template commentary**
`month_facts` payload, noise threshold, `category_driven` detection, rule-based bullets.

**Phase 6 — dashboard**
`template.html`, `build.py`, inlined data, charts, smoothing toggle, methodology section, indicator note, CSV export.

**Phase 7 — polish for public release**
README with install + quickstart + methodology + screenshot, MIT license, docstrings, `--help` text that reads well to someone who's never seen the tool.

**Phase 8 (V1.5) — LLM commentary**
Swap `commentary.generate()` internals for the Anthropic API call. Guardrails per §9. Caching. Graceful fallback.

**Estimated effort: 1–2 focused days for Phases 1–7.**

---

## 12. Deferred to V2 — do not build now

Scheduled GitHub Actions cron with commit-back · hosted dashboard on GitHub Pages · ESOS (requires market-share data import) · paid search / SERP share of voice · multi-market and multi-language · Google Ads API backend · Google Trends cross-check for ambiguous brands · PyPI publication.

Leave seams for these (the `datasource` interface, the `market` fields already in the schema) but write no code for them.

---

## 13. Things that will go wrong — handle them

- **Google Ads volumes are rounded and bucketed.** Absolute numbers are imprecise; SoS is a ratio and a trend, so consistent bias largely cancels. Don't present absolute volumes as precise.
- **Google revises past months.** Hence `--refresh-last` and full recomputation on every write.
- **Grouped keywords** silently double-count. See §8.3.
- **Generic-word brand names** (Emma, Apple, Orange) pollute volumes. The `ambiguous` flag exists for this; surface it in the dashboard.
- **An incomplete competitor set makes every percentage wrong.** If the own brand's SoS exceeds ~60–70%, warn the user that the set is probably missing someone.
- **Never divide by total search volume** — always by the defined category set.
- **Current month is never available** from Google Ads. Cap `date_to` at the last completed month.
- **Seasonality** distorts single months. This is what the smoothing windows are for.

---

## 14. Build log — Phases 1–7, completed 2026-08-01

Everything above is the original specification, unchanged. This section records what the
build resolved, what it changed, and why.

### Open questions, answered against the live API

**History depth (§7).** Requesting 48 months (2022-08 → 2026-07) for 7 keywords in US/en
returned **47 months, 2022-08 → 2026-06**. So the deeper figure is correct — roughly four
years — but the trailing month is not simply "last month": Google Ads data lagged by two
months, not one. `last_complete_month()` still caps `date_to` at the previous month as
specified; the extra month simply comes back empty and no row is invented for it.
`DataForSEOSource.months_returned` reports what actually arrived rather than what was asked
for, so nothing hard-codes 47.

**Grouped-keyword guard (§8.3).** Confirmed against real data, with a clean signal rather
than a marginal one: `openai` and `open ai` returned identical volumes in **47 of 47**
months and were collapsed to one; `anthropic` and `anthropic ai` matched in **0 of 47** and
were summed as distinct. Two guards were added beyond the spec, both to avoid false
positives: a pair must overlap in at least two months (one shared month is coincidence),
and must have at least one non-zero value (two keywords that both return zero everywhere
are dead, not grouped). Grouping is only ever considered within a single brand.

### Deviations from the specification

**Chart.js is vendored, not loaded from a CDN (§10).** The spec permitted a CDN tag with a
README caveat. In practice a CDN tag leaves the file *looking* self-contained while quietly
depending on the network — and this was not hypothetical: the CDN was unreachable from the
build environment, and the dashboard rendered with no charts. Since "opens by
double-clicking" is a non-negotiable constraint, Chart.js 4.4.1 (MIT) is now inlined from
`src/sos/dashboard/vendor/`. The generated file makes zero network requests and is ~330 KB.

**`DATAFORSEO_USERNAME` accepted as an alias for `DATAFORSEO_LOGIN`.** Some tooling uses
the other name. `DATAFORSEO_LOGIN` remains the documented primary and wins if both are set.
`sos validate` reports which variable it found — the name only, never the value.

**`volume_{w}mo` is emitted for the shortest window only.** The §6 schema names
`sos_pct_3mo`, `sos_pct_12mo` and `volume_3mo`. Emitting a volume average per window would
have added an undocumented `volume_12mo`, so rolling volume follows the shortest configured
window. With the default `[3, 12]` the stored columns match §6 exactly.

**A fourth test file, `tests/test_dashboard.py`.** The spec listed three. Self-containment
is a non-negotiable constraint that fails silently on someone else's machine, so it is
asserted directly: no remote `src`/`href`, no `fetch`, no `data.json`, no surviving
placeholders, and a payload that is strictly JSON-serialisable (a stray `NaN` is not valid
JSON and would break the page on load).

**`sos dashboard` reconstructs the brand set from the store when it has no config.**
§4 specifies `sos dashboard` taking only `--data-dir`, `--out` and `--open`, but the
dashboard needs to know which brand is the own brand and what the market is. Requiring
`--brand`/`--competitors` to be restated would have broken the ad-hoc path at its second
step — `sos run --brand … && sos dashboard` — which is the "try it in 30 seconds" flow the
spec calls out. The store already records brand, own-brand, market, location, language,
keywords and the smoothing windows, so `store.config_from_store()` reads them back. An
explicit `--config` still wins, because only a config file carries the `ambiguous` flags.

**`click` over `argparse`** (§3 allowed either), for the `--help` output.

### Fixed after automated review of the first push

Four findings, all genuine, all in newly written code.

**A brand removed from the config kept counting (P1).** On a trailing refresh the incoming
rows never share a `(date, brand, market)` key with a departed competitor, so nothing
replaced it and `recompute()` kept it in the category total. Swapping `OldCo` for `NewCo`
left both in the store and reported 33.3% where the answer was 50.0%. `upsert()` now takes
the active category set and drops rows for any other brand in the same market, warning
about what it removed. Other markets are untouched.

**Grouping could be decided from too little evidence (P1).** A routine refresh fetched
three months. Google's volumes are bucketed, so two distinct low-volume keywords can share
two or three values by chance — enough for the guard to merge them, drop a real keyword and
leave a false cliff in the brand's history. Two changes: a new grouping decision now
requires a response spanning ≥6 months, and below that the previous decision is carried
forward (recovered from the store's `keywords` column, which already records what was
counted). The default refresh window also went from 3 months to 12, which costs nothing —
DataForSEO bills per request, not per month — and keeps every routine run above the bar.

**Config values could break out of the inlined `<script>` (P2).** An HTML parser ends a
script element at the first literal `</`, whatever the JavaScript quoting says, so a brand
named `</script>…` would terminate the block early — in a file explicitly meant to be
emailed to clients. `<` is now escaped as `\u003c` in the payload and the title is
HTML-escaped. Substitution also became a single pass, so injected content is never rescanned
for other placeholders: a brand named `__SOS_CHARTJS__` would otherwise have had 200 KB of
library spliced into the middle of the JSON.

**`sos init` interpolated raw input into YAML (P2).** `Acme #1` lost everything after the
`#`, `Foo: Bar` was a parse error, and `Null`/`Yes`/`123` came back from the loader as
something other than a string. Values now go through PyYAML for quoting while the layout and
explanatory comments stay hand-written.

The test fixture grew from 4 months to 8 as a consequence of the second fix — 4 months is
below the new evidence bar, and a 4-month response was never representative of a real
backfill anyway.

### Not built, as specified

Phase 8 (LLM commentary) is untouched. `commentary.generate(facts) -> list[str]` is
rule-based and the signature is frozen, so the swap described in §9 changes one function
body and nothing else. Nothing from §12 was started.
