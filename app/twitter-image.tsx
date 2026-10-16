import { ImageResponse } from 'next/og'

export const alt = 'Share of Search: track your brand against its competitors'
export const size = { width: 1200, height: 630 }
export const contentType = 'image/png'

export default function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          padding: 80,
          background: '#0b1020',
          color: '#ffffff',
        }}
      >
        <div style={{ fontSize: 28, letterSpacing: 6, color: '#7dd3fc', textTransform: 'uppercase' }}>Share of Search</div>
        <div style={{ fontSize: 76, fontWeight: 700, marginTop: 24, lineHeight: 1.05 }}>
          Track your brand&apos;s share of search against its competitors
        </div>
      </div>
    ),
    size,
  )
}
