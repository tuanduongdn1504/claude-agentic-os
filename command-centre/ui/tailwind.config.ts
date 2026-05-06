import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#0a0a0f',
        surface: '#12121a',
        'surface-2': '#1a1a27',
        'surface-3': '#22222f',
        border: { DEFAULT: '#2a2a3d', glow: '#3d3d5c' },
        text: {
          DEFAULT: '#e8e8f0',
          dim: '#8888a0',
          subtle: '#5a5a70',
        },
        accent: { blue: '#4d7cff', purple: '#8b5cf6', cyan: '#06b6d4' },
        status: {
          green: '#10b981',
          amber: '#f59e0b',
          red: '#ef4444',
          cyan: '#06b6d4',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Menlo', 'monospace'],
      },
      backgroundImage: {
        'gradient-hero': 'linear-gradient(135deg, #4d7cff, #8b5cf6)',
        'ambient':
          'radial-gradient(circle at 20% 10%, rgba(77,124,255,0.08), transparent 40%), radial-gradient(circle at 80% 60%, rgba(139,92,246,0.06), transparent 45%)',
      },
      borderRadius: { xl: '14px', '2xl': '16px' },
      letterSpacing: { kicker: '1.8px' },
      boxShadow: {
        lift: '0 8px 24px -8px rgba(77,124,255,0.22)',
      },
      keyframes: {
        'fade-in': {
          '0%': { opacity: '0', transform: 'translateY(4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: {
          '0%':   { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(100%)' },
        },
      },
      animation: {
        'fade-in': 'fade-in 300ms ease-out',
        'shimmer': 'shimmer 1.3s ease-in-out infinite',
      },
    },
  },
  plugins: [],
} satisfies Config;
