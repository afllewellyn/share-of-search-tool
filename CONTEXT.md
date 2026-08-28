# Domain glossary

The language this codebase uses. Where a term has a tempting synonym, the
synonym is named and rejected — consistent naming is the point.

## Metric

**Share of Search** — a brand's branded search volume as a percentage of the
total branded search volume across a defined competitor set. Stored as
`sos_pct`. Methodology per Les Binet, IPA EffWorks Global 2020. _Avoid_: market
share, SoV, share of voice.

**Category set** — the brands the denominator is summed over. Not "the market"
and not total search volume: every percentage in the tool is relative to a set
someone chose, which is why an incomplete set makes the whole report wrong.

**Brand set** — the configured brands plus their keywords and market; what a
`Config` holds. Used when talking about configuration, where **category set**
is used when talking about the denominator.

**Own brand** — the one brand a report is about. Exactly one per brand set.
_Avoid_: primary brand, client brand.

**Market** — a geography and language pair (`name`, `location_code`,
`language_code`). Volumes are never mixed across markets; each recomputes its
own category total. _Avoid_: region, locale.

**Smoothing window** — a trailing rolling-average length in months, written as
a `sos_pct_{w}mo` / `volume_{w}mo` column. Windows are *full*: a value appears
only once the whole window has data.

## Data

**Store** — the flat CSV at `data/sos_monthly.csv`, one row per brand per month
per market. `raw_volume` is the only source of truth; every other numeric
column is derived and rebuilt on each write. _Avoid_: database, cache.

**Volume row** — one month of volume for one keyword, as a source returns it.
A null `search_volume` means "no data", never zero demand.

**Grouping guard** — the check for keywords Google Ads has silently merged into
a single figure. Merged keywords return identical volumes in every overlapping
month; summing them would double a brand's volume. A response must span at
least six months to make a new grouping decision; below that the previous
run's decision is carried forward from the store's `keywords` column.

**Data gap** — a brand-month the source returned nothing for. Left null, never
zeroed, and reported as a warning: a gap in the API is not zero demand.

## Pipeline

**Run** — one pass of fetch → transform → store, for one brand set over one
month range. The unit the user pays for (~$0.075, billed per request, not per
keyword or per month). Lives in `sos/run.py`; `refresh()` is its interface.

**Refresh** — a run over a trailing window, re-pulling months already stored so
Google's revisions to recent history are absorbed. Distinct from a **backfill**,
which reaches for the full available history on a first pull.

**Source** — an adapter satisfying `KeywordVolumeSource`: where volumes come
from. `DataForSEOSource` in production, `FakeSource` in tests. Passed into
`refresh()` rather than constructed by it. _Avoid_: provider, client, API.
