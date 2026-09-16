import type { Config } from 'tailwindcss'

const config: Config = {
  darkMode: ['class'],
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: { extend: { colors: { ink: '#07111f', panel: '#0d1a2b', line: '#22334a', signal: '#8ce8c3', muted: '#8c9aae' } } },
  plugins: [],
}
export default config
