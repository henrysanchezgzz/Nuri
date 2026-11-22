/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: '#090B10', card: '#121620', raised: '#171C28', line: '#1E2433', soft: '#2A3142' },
        fg: { DEFAULT: '#E7EAF1', dim: '#9AA3B5', mute: '#5E6779' },
        ev: { DEFAULT: '#10B981', soft: 'rgba(16,185,129,0.10)' },
        stale: { DEFAULT: '#F59E0B', soft: 'rgba(245,158,11,0.10)' },
        live: { DEFAULT: '#EF4444', soft: 'rgba(239,68,68,0.10)' },
        info: { DEFAULT: '#3B82F6', soft: 'rgba(59,130,246,0.10)' },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'Segoe UI', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'Consolas', 'monospace'],
      },
      boxShadow: { card: '0 1px 0 rgba(255,255,255,0.02) inset, 0 12px 32px -18px rgba(0,0,0,0.6)' },
    },
  },
  plugins: [],
}
