import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Montserrat', 'Arial', 'sans-serif'],
        mono: ['Montserrat', 'Arial', 'sans-serif'],
      },
      colors: {
        ink: '#10221f',
        canvas: '#eef0ea',
        paper: '#f8f8f3',
        tide: '#007d72',
        signal: '#e9582b',
      },
      keyframes: {
        'soft-in': {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        'soft-in': 'soft-in 360ms cubic-bezier(.2,.8,.2,1) both',
      },
    },
  },
  plugins: [],
} satisfies Config;
