/** @type {import('tailwindcss').Config} */
// Every colour and font reads a CSS custom property from app/static/css/theme.css:
// the brand is swapped there, in one place, and dark mode follows.
const v = (name) => `rgb(var(--pf-${name}) / <alpha-value>)`;
const grays = {};
[50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950].forEach((k) => { grays[k] = v(`gray-${k}`); });

module.exports = {
  content: ["./app/templates/**/*.html"],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        gray: grays,
        accent: { DEFAULT: v('accent'), ink: v('accent-ink'), soft: v('accent-soft'), strong: v('accent-strong') },
        ok: { DEFAULT: v('ok'), soft: v('ok-soft') },
        warn: { DEFAULT: v('warn'), soft: v('warn-soft') },
        danger: { DEFAULT: v('danger'), soft: v('danger-soft') },
        canvas: v('bg'),
        surface: v('surface'),
        soft: v('soft'),
        line: v('line'),
        ink: v('ink'),
        // Strava's own brand colour, only on the « Connecter Strava » button (their guidelines)
        strava: '#FC4C02',
        'strava-hover': '#e04400',
      },
      fontFamily: {
        sans: ['var(--pf-font-text)'],
        display: ['var(--pf-font-display)'],
      },
      borderRadius: {
        DEFAULT: '0.625rem',
      },
    },
  },
  plugins: [],
};
