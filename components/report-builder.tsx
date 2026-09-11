'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowRight, Download, LoaderCircle, Plus, RotateCcw, Trash2, TrendingUp } from 'lucide-react'

type Brand = { name: string; keywords: string; url?: string }
type MarketPayload = { markets: string[]; months: number[]; limits: { max_competitors: number; max_keywords_per_brand: number } }
type Row = { brand: string; is_own_brand: boolean; sos_pct: number | null; raw_volume: number | null; mom_delta_pp: number | null; yoy_delta_pp: number | null; rank: number | null; rank_change: number | null; data_gap: boolean; ambiguous: boolean }
type Report = { html: string; own_brand: string; market: string; latest: { month: string | null; month_label: string | null; rows: Row[] }; commentary: string[]; warnings: string[]; months_requested: number; months_returned: number; cost_usd: number | null }

// Used only until /api/markets answers; the API is the source of truth.
const fallbackOptions: MarketPayload = { markets: ['US', 'UK', 'AU', 'CA'], months: [12, 24, 48], limits: { max_competitors: 12, max_keywords_per_brand: 8 } }
const emptyCompetitor = (): Brand => ({ name: '', keywords: '' })

function splitKeywords(value: string) { return value.split(',').map((item) => item.trim()).filter(Boolean) }
// The API leaves a value null when the history can't support it (no year-ago
// month for a 12-month run, no rank for a data gap). Render a dash, never crash.
function formatNumber(value: number | null) { return value == null ? '—' : new Intl.NumberFormat('en-US').format(value) }
function pct(value: number | null) { return value == null ? '—' : `${value.toFixed(1)}%` }
function rankLabel(rank: number | null, change: number | null) { if (rank == null) return '—'; return change == null ? `#${rank}` : `#${rank} (${change > 0 ? '+' : ''}${change})` }
async function readJson<T>(response: Response): Promise<T> {
  const text = await response.text()
  try { return JSON.parse(text) as T } catch { throw new Error(response.ok ? 'The report API returned something that was not JSON.' : `The report API failed (HTTP ${response.status}).`) }
}
function delta(value: number | null) { if (value == null) return '—'; const rounded = Math.round(value * 10) / 10 || 0; return `${rounded > 0 ? '+' : ''}${rounded.toFixed(1)} pp` }

export default function ReportBuilder() {
  const [options, setOptions] = useState(fallbackOptions)
  const { markets, months: monthsOptions, limits } = options
  const [ownBrand, setOwnBrand] = useState<Brand>({ name: '', keywords: '', url: '' })
  const [competitors, setCompetitors] = useState<Brand[]>([emptyCompetitor()])
  const [market, setMarket] = useState('US')
  const [months, setMonths] = useState(24)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [backendNotice, setBackendNotice] = useState('')
  const [report, setReport] = useState<Report | null>(null)

  useEffect(() => {
    fetch('/api/markets')
      .then((response) => readJson<MarketPayload>(response))
      .then(setOptions)
      .catch(() => setBackendNotice('The report API is not reachable from this page, so runs will fail. Check the deployment.'))
  }, [])

  const keywordErrors = useMemo(() => {
    const messages: string[] = []
    if (splitKeywords(ownBrand.keywords).length > limits.max_keywords_per_brand) messages.push(`Your brand can have up to ${limits.max_keywords_per_brand} keywords.`)
    competitors.forEach((competitor, index) => { if (splitKeywords(competitor.keywords).length > limits.max_keywords_per_brand) messages.push(`Competitor ${index + 1} can have up to ${limits.max_keywords_per_brand} keywords.`) })
    return messages
  }, [competitors, limits.max_keywords_per_brand, ownBrand.keywords])

  function updateCompetitor(index: number, patch: Partial<Brand>) { setCompetitors(competitors.map((item, i) => (i === index ? { ...item, ...patch } : item))) }

  function reset() { setReport(null); setError(''); setOwnBrand({ name: '', keywords: '', url: '' }); setCompetitors([emptyCompetitor()]); setMarket(markets[0] ?? 'US'); setMonths(monthsOptions.includes(24) ? 24 : (monthsOptions[0] ?? 12)); window.scrollTo({ top: 0, behavior: 'smooth' }) }

  async function submit(event: React.FormEvent) {
    event.preventDefault(); setError('')
    const validCompetitors = competitors.filter((item) => item.name.trim() || item.keywords.trim())
    if (!ownBrand.name.trim() || splitKeywords(ownBrand.keywords).length < 1) return setError('Add your brand name and at least one keyword.')
    if (validCompetitors.length < 1) return setError('Add at least one competitor.')
    if (keywordErrors.length) return setError(keywordErrors[0])
    setLoading(true)
    try {
      const response = await fetch('/api/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ own_brand: { name: ownBrand.name.trim(), keywords: splitKeywords(ownBrand.keywords), url: ownBrand.url?.trim() || undefined }, competitors: validCompetitors.map((item) => ({ name: item.name.trim(), keywords: splitKeywords(item.keywords) })), market, months }) })
      const data = await readJson<Report & { error?: string }>(response)
      if (!response.ok || !data.html) throw new Error(data.error || 'The report could not be created.')
      setReport(data); window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'The report could not be created.') } finally { setLoading(false) }
  }

  if (report) return <Results report={report} onReset={reset} />

  return <main className="min-h-screen px-5 py-6 text-slate-100 sm:px-8 lg:px-12">
    <div className="mx-auto max-w-6xl">
      <header className="flex items-center justify-between border-b border-line pb-5"><div className="flex items-center gap-3"><div className="grid size-9 place-items-center rounded-xl bg-signal text-ink"><TrendingUp className="size-5" /></div><span className="font-mono text-sm font-bold tracking-[0.2em] text-signal">SOS / REPORT BUILDER</span></div><span className="hidden font-mono text-xs text-muted sm:block">MONTHLY DEMAND SIGNAL</span></header>
      <section className="grid gap-8 py-12 lg:grid-cols-[1.05fr_0.95fr] lg:items-end lg:py-20"><div><p className="mb-5 font-mono text-xs uppercase tracking-[0.2em] text-signal">Share of Search</p><h1 className="max-w-3xl text-balance text-5xl font-semibold leading-[0.98] tracking-[-0.06em] sm:text-7xl">Is demand moving in your category?</h1><p className="mt-6 max-w-xl text-pretty text-lg leading-8 text-muted">A monthly read on the branded search demand you created — and the demand your competitors are capturing.</p></div><div className="border-l border-signal/40 pl-5 text-sm leading-6 text-muted"><p className="font-mono text-xs uppercase tracking-[0.18em] text-signal">One simple signal</p><p className="mt-3">Share of Search is your brand&apos;s slice of all branded search in a category you define. It tends to lead market share, but it is not a forecast.</p></div></section>
      <form onSubmit={submit} className="grid gap-5 lg:grid-cols-[1.2fr_0.8fr]">
        <section className="rounded-2xl border border-line bg-panel/80 p-6 sm:p-8"><div className="mb-8 flex items-start justify-between gap-4"><div><p className="font-mono text-xs tracking-[0.18em] text-signal">01 / OWN BRAND</p><h2 className="mt-2 text-2xl font-semibold tracking-tight">Start with the brand you own</h2></div><span className="rounded-full border border-line px-3 py-1 font-mono text-[10px] text-muted">REQUIRED</span></div><div className="grid gap-5"><label className="grid gap-2 text-sm text-muted">Brand name<input value={ownBrand.name} onChange={(e) => setOwnBrand({ ...ownBrand, name: e.target.value })} placeholder="e.g. Acme" className="field" /></label><label className="grid gap-2 text-sm text-muted">Search keywords <span className="text-xs text-muted/70">Comma-separated, up to {limits.max_keywords_per_brand}</span><input value={ownBrand.keywords} onChange={(e) => setOwnBrand({ ...ownBrand, keywords: e.target.value })} placeholder="acme, acme app, acme software" className="field" /></label><label className="grid gap-2 text-sm text-muted">Website <span className="text-xs text-muted/70">Optional context for the report</span><input type="url" value={ownBrand.url} onChange={(e) => setOwnBrand({ ...ownBrand, url: e.target.value })} placeholder="https://acme.com" className="field" /></label></div></section>
        <section className="rounded-2xl border border-line bg-panel/80 p-6 sm:p-8"><p className="font-mono text-xs tracking-[0.18em] text-signal">02 / SCOPE</p><h2 className="mt-2 text-2xl font-semibold tracking-tight">Choose the lens</h2><div className="mt-8 grid gap-6"><label className="grid gap-2 text-sm text-muted">Market<select value={market} onChange={(e) => setMarket(e.target.value)} className="field appearance-none">{markets.map((item) => <option key={item}>{item}</option>)}</select></label><fieldset><legend className="text-sm text-muted">History window</legend><div className="mt-3 flex flex-wrap gap-2">{monthsOptions.map((option) => <label key={option} className={`cursor-pointer rounded-lg border px-4 py-3 font-mono text-sm transition ${months === option ? 'border-signal bg-signal/10 text-signal' : 'border-line text-muted hover:border-muted'}`}><input type="radio" name="months" value={option} checked={months === option} onChange={() => setMonths(option)} className="sr-only" />{option} mo</label>)}</div></fieldset><div className="border-t border-line pt-5 text-xs leading-5 text-muted">Each run uses one paid DataForSEO request. A longer window gives you more context without adding request count.</div></div></section>
        <section className="rounded-2xl border border-line bg-panel/80 p-6 sm:p-8 lg:col-span-2"><div className="flex items-start justify-between gap-4"><div><p className="font-mono text-xs tracking-[0.18em] text-signal">03 / COMPETITORS</p><h2 className="mt-2 text-2xl font-semibold tracking-tight">Define the category</h2><p className="mt-2 text-sm leading-6 text-muted">Keep keyword coverage fair: include the variants people actually search for.</p></div><span className="font-mono text-xs text-muted">{competitors.length} / {limits.max_competitors}</span></div><div className="mt-6 grid gap-3">{competitors.map((competitor, index) => <div key={index} className="grid gap-3 rounded-xl border border-line/70 bg-ink/30 p-3 sm:grid-cols-[0.7fr_1fr_auto]"><input value={competitor.name} onChange={(e) => updateCompetitor(index, { name: e.target.value })} placeholder={`Competitor ${index + 1}`} className="field" /><input value={competitor.keywords} onChange={(e) => updateCompetitor(index, { keywords: e.target.value })} placeholder="brand keywords, variants" className="field" /><button type="button" aria-label={`Remove competitor ${index + 1}`} onClick={() => setCompetitors(competitors.filter((_, i) => i !== index))} disabled={competitors.length === 1} className="grid size-11 place-items-center rounded-lg border border-line text-muted transition hover:border-red-300 hover:text-red-200 disabled:cursor-not-allowed disabled:opacity-30"><Trash2 className="size-4" /></button></div>)}</div><button type="button" onClick={() => setCompetitors([...competitors, emptyCompetitor()])} disabled={competitors.length >= limits.max_competitors} className="mt-4 inline-flex items-center gap-2 rounded-lg border border-dashed border-line px-4 py-3 font-mono text-xs text-muted transition hover:border-signal hover:text-signal disabled:opacity-40"><Plus className="size-4" /> Add competitor</button></section>
        {backendNotice && <div role="status" className="rounded-xl border border-amber-300/30 bg-amber-300/10 p-4 text-sm text-amber-100 lg:col-span-2">{backendNotice}</div>}
        {error && <div role="alert" className="rounded-xl border border-red-300/30 bg-red-300/10 p-4 text-sm text-red-100 lg:col-span-2">{error}</div>}
        <div className="flex flex-col gap-4 lg:col-span-2 sm:flex-row sm:items-center sm:justify-between"><p className="text-xs leading-5 text-muted">Your data stays in this report flow. No account or database required.</p><button disabled={loading} className="group inline-flex min-h-14 items-center justify-center gap-3 rounded-xl bg-signal px-6 font-semibold text-ink transition hover:bg-signal/90 disabled:cursor-wait disabled:opacity-70">{loading ? <><LoaderCircle className="size-5 animate-spin" /> Pulling {months} months from Google Ads, ~5–10 s</> : <>Run report (~$0.08, one API call)<ArrowRight className="size-5 transition group-hover:translate-x-1" /></>}</button></div>
      </form>
      <footer className="mt-16 border-t border-line py-6 text-xs leading-5 text-muted">Methodology follows Les Binet, IPA EffWorks 2020. Branded search is an early demand signal, not a prediction of market share. Every run spends one paid request.</footer>
    </div>
  </main>
}

function Results({ report, onReset }: { report: Report; onReset: () => void }) {
  const iframeRef = useRef<HTMLIFrameElement>(null)

  // Size the iframe to the dashboard. The sandbox allows same-origin, so the
  // parent can observe the document instead of guessing when charts settle.
  useEffect(() => {
    const iframe = iframeRef.current
    if (!iframe) return
    let observer: ResizeObserver | undefined
    const onLoad = () => {
      const doc = iframe.contentDocument
      if (!doc) return
      const fit = () => { iframe.style.height = `${Math.max(560, doc.documentElement.scrollHeight, doc.body?.scrollHeight ?? 0)}px` }
      observer?.disconnect()
      observer = new ResizeObserver(fit)
      observer.observe(doc.documentElement)
      fit()
    }
    iframe.addEventListener('load', onLoad)
    if (iframe.contentDocument?.readyState === 'complete') onLoad() // srcDoc already parsed before the effect ran
    return () => { iframe.removeEventListener('load', onLoad); observer?.disconnect() }
  }, [report.html])

  return <main className="min-h-screen px-5 py-6 text-slate-100 sm:px-8 lg:px-12"><div className="mx-auto max-w-6xl"><header className="flex flex-wrap items-center justify-between gap-4 border-b border-line pb-5"><div className="flex items-center gap-3"><div className="grid size-9 place-items-center rounded-xl bg-signal text-ink"><TrendingUp className="size-5" /></div><span className="font-mono text-sm font-bold tracking-[0.2em] text-signal">SOS / REPORT</span></div><div className="flex gap-2"><button onClick={onReset} className="action-button"><RotateCcw className="size-4" /> Run another</button><button onClick={() => { const blob = new Blob([report.html], { type: 'text/html' }); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = 'share-of-search.html'; link.click(); URL.revokeObjectURL(url) }} className="action-button-primary"><Download className="size-4" /> Download HTML</button></div></header><section className="py-12"><p className="font-mono text-xs tracking-[0.18em] text-signal">{(report.latest.month_label ?? 'LATEST MONTH').toUpperCase()} / {report.market}</p><h1 className="mt-3 text-5xl font-semibold tracking-[-0.05em]">{report.own_brand} demand report</h1><p className="mt-4 max-w-2xl text-muted">A {report.months_returned}-month view of branded search share across your selected category.</p></section><section className="overflow-hidden rounded-2xl border border-line bg-panel"><div className="overflow-x-auto"><table className="w-full min-w-[680px] text-left text-sm"><thead className="border-b border-line font-mono text-[10px] uppercase tracking-[0.16em] text-muted"><tr><th className="px-5 py-4">Brand</th><th className="px-5 py-4">Share</th><th className="px-5 py-4">MoM</th><th className="px-5 py-4">YoY</th><th className="px-5 py-4">Rank</th><th className="px-5 py-4">Signal</th></tr></thead><tbody>{report.latest.rows.map((row) => <tr key={row.brand} className={`border-b border-line/70 last:border-0 ${row.is_own_brand ? 'bg-signal/10' : ''}`}><td className="px-5 py-4 font-medium">{row.brand}{row.is_own_brand && <span className="ml-2 font-mono text-[10px] text-signal">YOU</span>}</td><td className="px-5 py-4 font-mono text-signal">{pct(row.sos_pct)}</td><td className="px-5 py-4 font-mono text-muted">{delta(row.mom_delta_pp)}</td><td className="px-5 py-4 font-mono text-muted">{delta(row.yoy_delta_pp)}</td><td className="px-5 py-4 font-mono">{rankLabel(row.rank, row.rank_change)}</td><td className="px-5 py-4">{row.data_gap || row.ambiguous ? <span className="rounded-full border border-line px-2 py-1 font-mono text-[10px] text-muted">{row.ambiguous ? 'AMBIGUOUS' : 'DATA GAP'}</span> : <span className="text-xs text-muted">{formatNumber(row.raw_volume)} searches</span>}</td></tr>)}</tbody></table></div></section><section className="mt-6 grid gap-5 lg:grid-cols-2"><div className="rounded-2xl border border-line bg-panel p-6"><p className="font-mono text-xs tracking-[0.18em] text-signal">READOUT</p><ul className="mt-4 grid gap-3 text-sm leading-6 text-slate-200">{report.commentary.map((line) => <li key={line} className="flex gap-3"><span className="mt-2 size-1.5 shrink-0 rounded-full bg-signal" />{line}</li>)}</ul></div>{report.warnings.length > 0 && <div className="rounded-2xl border border-line bg-panel p-6"><p className="font-mono text-xs tracking-[0.18em] text-muted">CAVEATS</p><ul className="mt-4 grid gap-3 text-sm leading-6 text-muted">{report.warnings.map((line) => <li key={line}>— {line}</li>)}</ul></div>}</section><section className="mt-8 overflow-hidden rounded-2xl border border-line bg-white"><iframe ref={iframeRef} title="Share of Search dashboard" srcDoc={report.html} sandbox="allow-scripts allow-same-origin" className="block min-h-[560px] w-full border-0" /></section><footer className="border-t border-line py-6 text-xs leading-5 text-muted">Methodology follows Les Binet, IPA EffWorks 2020. Branded search is an early demand signal, not a prediction of market share. Generated from {report.months_requested} months; one paid request{report.cost_usd != null && ` ($${report.cost_usd.toFixed(3)})`}.</footer></div></main>
}
