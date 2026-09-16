import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Share of Search | A leading indicator of market share',
  description: 'Measure interest in your brand relative to your competitors, month by month, from branded search volume.',
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>
}
