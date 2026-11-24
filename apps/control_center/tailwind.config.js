/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        wolf: {
          ink: "#0c1118",
          steel: "#1a2332",
          mist: "#8b9bb4",
          amber: "#c4a35a",
          signal: "#2d6a4f",
          danger: "#9b2226",
        },
      },
      fontFamily: {
        display: ["var(--font-base-neue)", "system-ui", "sans-serif"],
        body: ["var(--font-base-neue)", "system-ui", "sans-serif"],
        label: ["var(--font-base-neue)", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
