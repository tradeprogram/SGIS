/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink:    "#050B1F",
        panel:  "rgba(11,18,38,0.78)",
        accent: "#38BDF8",
      },
      fontFamily: { sans: ["Geist", "Pretendard", "system-ui", "sans-serif"] },
    },
  },
  plugins: [],
};
