# 1. The store module keeps derivation and brand-set recall

Date: 2026-08-28

## Status

Accepted

## Context

`src/sos/store.py` does three things: it persists the CSV, it orchestrates
derivation (`recompute` calls `transform.compute_shares` then
`transform.add_rolling_averages`), and it reconstructs a brand set from stored
columns (`config_from_store`). Its interface leaks both smoothing windows and
the active category set to every caller.

An architecture review flagged splitting it: leave persistence behind — read,
atomic write, upsert with a caller-supplied recompute hook — and move the other
two responsibilities out.

The argument against is the deletion test. Deleting the persistence seam and
introducing a narrower one only pays off if something actually varies across
it, and nothing does. There is one store adapter: a flat CSV, deliberately, so
the tool needs no database and no server. No second adapter (SQLite, Parquet,
a hosted store) exists or is planned. **One adapter is a hypothetical seam; two
is a real one.** Splitting now would move complexity between modules rather
than concentrate it, and would widen the interface a caller has to learn in
exchange for flexibility nobody is asking for.

The two responsibilities that genuinely do not belong in `store` are better
addressed from the other side: derivation by giving `transform` a single
`recompute_derived(frame, windows)` entry point, and brand-set recall by giving
the brand set its own module. Both are separate decisions, and neither requires
splitting the store to happen.

## Decision

`store.py` keeps `recompute` and `config_from_store`. We do not introduce a
storage seam or a store adapter interface.

## Consequences

- `store` continues to import both `transform` and `config`, which is a real
  coupling we are accepting rather than denying.
- `upsert`'s interface continues to require `smoothing_windows` and
  `active_brands` from its caller. `sos/run.py` is the single caller that knows
  the correct values, and its module docstring states why.
- The atomic-write implementation stays reachable only through `upsert`, so it
  is tested through that interface rather than on its own.
- **Revisit when a second store adapter becomes real** — a `--format parquet`
  flag, a hosted store, or a second consumer that cannot read the CSV. At that
  point the seam stops being hypothetical and this decision should be
  superseded rather than argued again.
