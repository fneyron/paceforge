/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/templates/**/*.html"],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        strava: '#FC4C02',
        'strava-hover': '#e04400',
      },
      borderRadius: {
        DEFAULT: '0.625rem',
      },
    },
  },
  plugins: [],
};
