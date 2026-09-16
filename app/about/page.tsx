import type { Metadata } from 'next'
import Link from 'next/link'

export const metadata: Metadata = {
  title: 'About Share of Search',
  description: 'What Share of Search measures, what to use it for, and how to read a report without misreading it.',
}

const REPO = 'https://github.com/afllewellyn/share-of-search-tool'
const TALK = 'https://www.youtube.com/watch?v=x1zMufAs3l0'

function Eyebrow({ children }: { children: React.ReactNode }) {
  return <p className="font-mono text-xs tracking-[0.18em] text-signal">{children}</p>
}

function Section({ eyebrow, title, children }: { eyebrow: string; title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-line bg-panel/80 p-6 sm:p-8">
      <Eyebrow>{eyebrow}</Eyebrow>
      <h2 className="mt-2 text-2xl font-semibold tracking-tight">{title}</h2>
      <div className="mt-6 grid gap-4 text-base leading-7 text-slate-200">{children}</div>
    </section>
  )
}

export default function AboutPage() {
  return (
    <main className="min-h-screen px-5 py-6 text-slate-100 sm:px-8 lg:px-12">
      <div className="mx-auto max-w-4xl">
        <header className="flex flex-wrap items-center justify-between gap-4 border-b border-line pb-5">
          <span className="font-mono text-sm font-bold tracking-[0.2em] text-signal">SOS / ABOUT</span>
          <Link href="/" className="action-button">← Back to the report builder</Link>
        </header>

        <section className="py-12">
          <Eyebrow>WHAT YOU ARE LOOKING AT</Eyebrow>
          <h1 className="mt-3 max-w-3xl text-balance text-4xl font-semibold leading-[1.05] tracking-[-0.05em] sm:text-6xl">
            A monthly read on interest in your brand, relative to the brands you compete with.
          </h1>
          <p className="mt-6 max-w-2xl text-lg leading-8 text-muted">
            This page explains the measure behind every report on this site: why it exists, what it counts, what it is good for, and the handful of ways it can mislead you if you read it carelessly.
          </p>
        </section>

        <div className="grid gap-6">
          <Section eyebrow="01 / THE PROBLEM" title="Last-click attribution only sees demand you captured, never demand you influenced">
            <p>
              Someone searches your brand name, clicks a paid ad, converts. Paid search takes the credit. But something made them type your name: a campaign, a mention, six months of brand work. That scores zero in the model, so it is the first budget cut and the hardest to defend.
            </p>
            <p>
              The tools that do measure it are enterprise software: brand trackers, panel-based market share, marketing mix modelling. Enterprise pricing, quarterly data, onboarding in weeks. Fine for a global brand. Out of reach for most agency and client work, and heavy for a question as plain as &ldquo;is interest in us moving?&rdquo;
            </p>
            <p>
              So the quarterly review falls back on impressions, reach and engagement, which no CFO accepts as evidence that anything changed.
            </p>
          </Section>

          <Section eyebrow="02 / THE MEASURE" title="What Share of Search counts">
            <p>
              Branded search is someone typing your name on purpose. That is a signal of interest, not a record of a conversion.
            </p>
            <p>
              Share of Search is your brand&apos;s slice of all branded search across a set of brands you choose:
            </p>
            <pre className="overflow-x-auto rounded-xl border border-line bg-ink/60 p-5 font-mono text-sm leading-6 text-signal">
{`        your brand's monthly search volume
  ─────────────────────────────────────────────────  ×  100
   total monthly volume across your set of brands`}
            </pre>
            <p>
              Les Binet showed that this correlates with market share and tends to <strong className="text-slate-50">lead</strong> it: by about three months in fast-moving categories, up to twelve for considered purchases. See{' '}
              <a href={TALK} target="_blank" rel="noopener noreferrer" className="underline decoration-line underline-offset-4 hover:text-signal">Share of Search as a Predictive Measure</a>, IPA EffWorks Global 2020.
            </p>
            <p>
              It is not a prediction of market share, and it explains nothing on its own. It is an early signal you can pull monthly. Think of it as brand consideration measured from real behaviour: no survey, no panel, just what people type.
            </p>
          </Section>

          <Section eyebrow="03 / WHAT IT IS FOR" title="What you would use it for">
            <ul className="grid gap-3">
              {[
                ['Show brand work did something', 'when last-click says it did not. Branded search going up is interest you earned, and it moves before revenue does.'],
                ['Defend or grow a brand budget', 'with a number rather than an assertion.'],
                ['Read a campaign early:', 'weeks after it ends, not two quarters later.'],
                ['See what a competitor’s move did.', 'Did they take your interest, or grow the whole pool? Share and total volume answer different questions.'],
                ['Bring something to a QBR', 'that is not impressions, and that the client can check.'],
              ].map(([lead, rest]) => (
                <li key={lead} className="flex gap-3">
                  <span className="mt-3 size-1.5 shrink-0 rounded-full bg-signal" />
                  <span><strong className="text-slate-50">{lead}</strong> {rest}</span>
                </li>
              ))}
            </ul>
          </Section>

          <Section eyebrow="04 / HOW TO READ IT" title="How to read a report">
            <p>
              Share of Search is a <strong className="text-slate-50">leading indicator correlated with market share</strong>. Not a prediction of it, and not an explanation of anything. A few things will mislead you if you let them:
            </p>
            <ul className="grid gap-4">
              {[
                ['The denominator is your competitor set, never total search.', 'Add or remove a brand and every number changes. If the set is missing a competitor, every figure is overstated. The report warns when your own brand exceeds about 65%, which usually means someone is missing.'],
                ['A brand’s share can rise because a competitor collapsed.', 'The most common misreading. When the total moves enough that share shifts are mostly a denominator effect, the commentary says so first.'],
                ['Single months are noisy.', 'Seasonality alone moves these lines; that is what the smoothing toggle in the dashboard is for. The report also measures each brand’s own volatility and tells you when a move sits inside it.'],
                ['A longer smoothing window covers less of the chart. That is correct.', 'Rolling averages are trailing and full: a 12-month average needs twelve months before it has a value, so it starts nine months after the 3-month one. The chart trims the empty run-up and says which month each view starts.'],
                ['Google’s volumes are rounded and bucketed.', 'Absolute numbers are approximate. Share is a ratio, so consistent estimation bias largely cancels. Read the trend, not the decimal.'],
                ['Lead times vary by category.', 'Roughly three months in fast-cycle categories, up to twelve for considered purchases.'],
                ['The current month is never available', 'from Google Ads, and in practice the data lags by about two months. Reports stop at the last complete month.'],
                ['Common words need flagging.', 'A brand called Apple or Orange picks up searches that are not about the brand. Tick “Common word” on the form and the report marks that brand as ambiguous wherever it appears.'],
              ].map(([lead, rest]) => (
                <li key={lead} className="flex gap-3">
                  <span className="mt-3 size-1.5 shrink-0 rounded-full bg-signal" />
                  <span><strong className="text-slate-50">{lead}</strong> {rest}</span>
                </li>
              ))}
            </ul>
            <p className="text-muted">
              The dashboard&apos;s own &ldquo;About this metric&rdquo; section carries all of this, so a report that leaves your hands still explains itself. Every sentence of commentary comes from rules over computed facts: no number is ever generated by a language model.
            </p>
          </Section>

          <Section eyebrow="05 / WHERE THE DATA COMES FROM" title="One request per report">
            <p>
              Each report pulls monthly Google Ads search volume for every keyword in your brand set through DataForSEO, in one request, then works out each brand&apos;s share. Nothing you enter is stored on a server; the report is built, returned to your browser, and forgotten.
            </p>
            <p>
              The same pipeline is available as a command-line tool with a persistent monthly store, for people who want to track a brand set over time. It is open source:{' '}
              <a href={REPO} target="_blank" rel="noopener noreferrer" className="underline decoration-line underline-offset-4 hover:text-signal">afllewellyn/share-of-search-tool</a>.
            </p>
          </Section>
        </div>

        <footer className="mt-16 border-t border-line py-6 text-xs leading-5 text-muted">
          <p>Methodology follows Les Binet, IPA EffWorks 2020. Share of Search tends to lead market share; read it as an early signal, not a forecast.</p>
          <p className="mt-3">
            Built by <a href="https://www.linkedin.com/in/afllewellyn" target="_blank" rel="noopener noreferrer" className="text-slate-200 underline decoration-line underline-offset-4 hover:text-signal">Andrew Llewellyn</a>
            {' · '}<a href={REPO} target="_blank" rel="noopener noreferrer" className="text-slate-200 underline decoration-line underline-offset-4 hover:text-signal">Source on GitHub</a>
            {' · '}<Link href="/" className="text-slate-200 underline decoration-line underline-offset-4 hover:text-signal">Report builder</Link>
          </p>
        </footer>
      </div>
    </main>
  )
}
