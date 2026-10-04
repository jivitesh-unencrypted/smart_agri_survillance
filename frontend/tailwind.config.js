/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#eefdf3',
          100: '#d6f8e1',
          200: '#aef0c6',
          300: '#78e2a5',
          400: '#43cb80',
          500: '#21b167',
          600: '#158f52',
          700: '#137243',
          800: '#135a37',
          900: '#124a2f',
          950: '#052a1a',
        },
      },
    },
  },
  plugins: [],
}
