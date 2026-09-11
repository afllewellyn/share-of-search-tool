import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Share of Search | Demand signal reports',
  description: 'Build a monthly branded search demand report for your category.',
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>
}
