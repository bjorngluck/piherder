/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: "class",
  // Play used to JIT every class in the browser. Compiled CSS only includes
  // classes found here — plus a small safelist for JS-toggled utilities.
  content: ["./app/templates/**/*.html", "./app/static/**/*.js"],
  // Do not emit Tailwind's base reset — it would fight themes.css / fallback.
  corePlugins: {
    preflight: false,
  },
  safelist: [
    "hidden",
    "flex",
    "inline-flex",
    "block",
    "grid",
    "dark",
  ],
  theme: {
    extend: {
      colors: {
        pi: {
          red: "#E60012",
          darkred: "#C8102E",
          green: "#00A651",
        },
        neutral: {
          50: "#F8F9FA",
          100: "#F1F3F5",
          900: "#111827",
          950: "#0A0F1C",
        },
      },
    },
  },
};
