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

function Points({ items }: { items: [string, string][] }) {
  return (
    <ul className="grid gap-4">
      {items.map(([lead, rest]) => (
        <li key={lead} className="flex gap-3">
          <span className="mt-3 size-1.5 shrink-0 rounded-full bg-signal" />
          <span><strong className="text-slate-50">{lead}</strong> {rest}</span>
        </li>
      ))}
    </ul>
  )
}

const linkClass = 'underline decoration-line underline-offset-4 hover:text-signal'

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
            Are more people looking for you, or for your competitors?
          </h1>
          <p className="mt-6 max-w-2xl text-lg leading-8 text-muted">
            Every report on this site answers that one question, month by month. This page explains where the number comes from, what it is good for, and the few ways it can fool you.
          </p>
        </section>

        <div className="grid gap-6">
          <Section eyebrow="01 / THE PROBLEM" title="Your reports only count the people you caught, not the people you convinced">
            <p>
              Picture someone who types your brand name into Google, clicks your ad, and buys. Your reports give the credit to that ad. But something made them type your name in the first place: a campaign they saw, a friend who mentioned you, months of steady brand building. None of that shows up anywhere. So when budgets get tight, brand work is the first thing cut, because nobody can point to a number and say &ldquo;this is what it did.&rdquo;
            </p>
            <p>
              There are tools that measure this properly, but they are built for the biggest companies in the world. They cost a fortune, report once a quarter, and take weeks to set up. For most brands and agencies, that is far too much machinery for a simple question: is interest in us going up or down?
            </p>
            <p>
              So the quarterly review falls back on impressions, reach and engagement. Those are numbers about how much you spent, not about whether anyone cared.
            </p>
          </Section>

          <Section eyebrow="02 / THE MEASURE" title="What Share of Search actually counts">
            <p>
              When someone types a brand name into Google, they are doing it on purpose. Nobody searches &ldquo;Nike&rdquo; by accident. Every one of those searches is a small vote of interest, and Google keeps count of them.
            </p>
            <p>
              Share of Search takes those counts for you and for the competitors you choose, and asks a simple question: out of everyone searching for any of these brands, what fraction were searching for you?
            </p>
            <pre className="overflow-x-auto rounded-xl border border-line bg-ink/60 p-5 font-mono text-sm leading-6 text-signal">
{`  searches for your brand this month
  ──────────────────────────────────────────  ×  100
  searches for all the brands in your set`}
            </pre>
            <p>
              If your share is 30%, then three of every ten people looking for a brand like yours were looking for you. If it was 25% last year, more people are choosing to look for you now. That is the whole idea.
            </p>
            <p>
              Why it matters: a researcher named Les Binet studied this across many industries and found that when a brand&apos;s share of search rises, its share of the market usually rises too, a few months later. That makes it an early warning system. You can see interest shifting before it shows up in sales. His talk is called{' '}
              <a href={TALK} target="_blank" rel="noopener noreferrer" className={linkClass}>Share of Search as a Predictive Measure</a>{' '}
              if you want the full story.
            </p>
            <p>
              One honest caveat: it is a signal, not a crystal ball. It tells you interest is moving. It does not tell you why, and it does not promise sales will follow.
            </p>
          </Section>

          <Section eyebrow="03 / WHAT IT IS FOR" title="What you would use it for">
            <Points items={[
              ['Prove brand work did something.', 'When more people search for you by name, that is interest you created, and it shows up before sales do.'],
              ['Defend or grow a brand budget', 'with a number instead of a feeling.'],
              ['Read a campaign early.', 'You can see the effect weeks after it ends instead of waiting two quarters for sales data.'],
              ['See what a competitor’s big move did.', 'Did they pull interest away from you, or did they get more people interested in the whole category? The report shows both.'],
              ['Bring something to a client meeting', 'that is not impressions, and that the client can check for themselves.'],
            ]} />
          </Section>

          <Section eyebrow="04 / HOW TO READ IT" title="How to read a report without fooling yourself">
            <p>
              Share of Search is a useful early signal, but only if you read it carefully. Here are the traps:
            </p>
            <Points items={[
              ['Your share depends entirely on which competitors you listed.', 'Add one brand or remove one and every number changes. If you forgot a big competitor, your share looks better than it really is. The report warns you when your own share is above about 65%, because that usually means someone is missing from the list.'],
              ['Your share can go up because a competitor went down.', 'This is the most common misreading. If a rival shrinks, your slice of the pie gets bigger even though nothing changed for you. When that is what is happening, the report’s commentary says so first.'],
              ['One month means very little.', 'Search volumes bounce around with the seasons, the news, and plain luck. The dashboard has a smoothing switch that averages several months together so you can see the real trend. The report also knows how jumpy each brand normally is, and tells you when a change is just normal wobble.'],
              ['Longer averages start later on the chart. That is correct.', 'A 12-month average needs twelve months of data before it can say anything, so its line begins later than the 3-month one. The chart tells you which month each view starts.'],
              ['Google’s numbers are rounded.', 'The raw search counts are estimates, not exact figures. Because share is a comparison between brands, most of that fuzziness cancels out. Trust the direction of the line, not the second decimal place.'],
              ['The head start varies by industry.', 'In fast-moving categories like fashion or apps, search moves about three months ahead of sales. For big considered purchases like cars or software contracts, it can be closer to a year.'],
              ['The data runs about two months behind.', 'Google does not publish the current month, and the most recent complete month often arrives late. Reports always stop at the last month that is fully available.'],
              ['Some brand names are also ordinary words.', 'Searches for “apple” or “orange” include a lot of fruit. If a brand in your set has a name like that, tick “Common word” on the form. The report will mark that brand so nobody reads its numbers as gospel.'],
            ]} />
            <p className="text-muted">
              The dashboard carries its own short version of this list, so a report you email to someone still explains itself. And every sentence of commentary in a report is written by fixed rules from the numbers, never by an AI guessing.
            </p>
          </Section>

          <Section eyebrow="05 / WHERE THE NUMBERS COME FROM" title="One question to Google, one report back">
            <p>
              When you run a report, the site fetches the monthly search counts for every keyword you entered, does the maths, and hands the finished report back to your browser. The counts come from Google&apos;s Keyword Planner, the same figures Google shows advertisers: how many times people searched each term on Google that month, whether they clicked an ad, a normal result, or nothing at all. They reach the site through DataForSEO, a data service that resells those official Google numbers, and each report is one paid request to it. Nothing you type is saved anywhere. Close the tab and it is gone, unless you downloaded the file.
            </p>
            <p>
              This site was built by a marketer for marketers. The full project, including a version that tracks a brand set month after month, is free to read and use at{' '}
              <a href={REPO} target="_blank" rel="noopener noreferrer" className={linkClass}>github.com/afllewellyn/share-of-search-tool</a>.
            </p>
          </Section>
        </div>

        <footer className="mt-16 border-t border-line py-6 text-sm leading-6 text-muted">
          <p>
            Built by <a href="https://www.linkedin.com/in/afllewellyn" target="_blank" rel="noopener noreferrer" className={`text-slate-200 ${linkClass}`}>Andrew Llewellyn</a>
            {' · '}<a href={REPO} target="_blank" rel="noopener noreferrer" className={`text-slate-200 ${linkClass}`}>Source on GitHub</a>
            {' · '}<Link href="/" className={`text-slate-200 ${linkClass}`}>Report builder</Link>
          </p>
          <p className="mt-3">Methodology follows Les Binet, IPA EffWorks 2020. Share of Search tends to lead market share; read it as an early signal, not a forecast.</p>
        </footer>
      </div>
    </main>
  )
}
