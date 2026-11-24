/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        charcoal: {
          950: '#090B10',
          900: '#0F121C',
          850: '#151926',
          800: '#1C2233',
          750: '#232A40',
          700: '#2B334E',
          600: '#3D486D',
        },
        brand: {
          purple: '#8B5CF6',
          violet: '#7C3AED',
          blue: '#3B82F6',
          cyan: '#06B6D4',
          accent: '#A855F7',
        }
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      }
    },
  },
  plugins: [],
}
