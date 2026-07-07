/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    "./app/templates/**/*.html",
    "./app/static/**/*.js"
  ],
  theme: {
    extend: {
      colors: {
        pi: {
          red: '#E60012',
          darkred: '#C8102E',
          green: '#00A651',
        },
        neutral: {
          50: '#F8F9FA',
          100: '#F1F3F5',
          900: '#111827',
          950: '#0A0F1C',
        }
      }
    }
  }
}