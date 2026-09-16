# 2. The web front door runs the Python pipeline, not a port

Date: 2026-09-10

## Status

Accepted

## Context

Clients who don't use a terminal need a way to run the tool. The obvious
reference was an earlier project built as a Lovable/React page with a
Supabase Edge Function (Deno) holding the API keys. Following it here would
mean re-implementing `transform`, `store.recompute`, `facts`, `commentary`
and the dashboard payload in TypeScript, and keeping two implementations of
the share calculation in step.

## Decision

Keep one pipeline. The web front door is `sos.web.run_request`, a pure
function over the existing `refresh()` and `build_dashboard()`, with a
Flask shim in `api/index.py` that Vercel serves as a Python Function. The
page is a Next.js app at the repo root that only posts JSON and renders
what comes back. Vercel installs the dependencies from `pyproject.toml`; `api/index.py` puts `src/` on the path so the
function imports exactly the code the CLI runs.

A web run uses a temp directory as its store. That drops the refresh
semantics and the carried-forward grouping decision; both are CLI features
for tracking a category over time, and the form is for a single look.

## Consequences

- One implementation of the metric. A fix in `transform.py` fixes both doors.
- The CLI is untouched; `sos.web` imports from it, never the reverse.
- The hosted page spends the deployment's DataForSEO credits with no login.
  Input caps in `sos.web` bound a single request; a shared passphrase is a
  one-line addition to `api/index.py` when the URL starts circulating.
- Vercel Python Functions carry pandas on cold start. Measured locally the
  whole request is ~5 s; `vercel.json` allows 60.
- No result persists server-side. If clients later want history, the CSV
  store swaps for blob storage behind the same `data_dir` parameter.
