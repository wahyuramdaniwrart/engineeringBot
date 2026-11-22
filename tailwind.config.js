/** Tailwind config untuk dashboard Bot WhatsApp Engineering (tema light). */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./static/app.js",
    "./static/src/**/*.js",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#ecfdf5",
          100: "#d1fae5",
          200: "#a7f3d0",
          300: "#6ee7b7",
          400: "#34d399",
          500: "#10b981",
          600: "#059669",
          700: "#047857",
          800: "#065f46",
          900: "#064e3b",
          950: "#022c22",
        },
        wa: {
          green: "#25d366",
          teal: "#075e54",
          dark: "#0b141a",
        },
      },
      fontFamily: {
        sans: [
          "Inter",
          "Segoe UI",
          "system-ui",
          "-apple-system",
          "Roboto",
          "Arial",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
        material: ["Material Symbols Rounded", "sans-serif"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(15,23,42,.06), 0 8px 24px rgba(15,23,42,.05)",
        "card-dark": "0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.35)",
        pop: "0 20px 60px rgba(2,6,23,.35)",
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0", transform: "translateY(4px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "slide-up": {
          "0%": { opacity: "0", transform: "translateY(12px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "toast-in": {
          "0%": { opacity: "0", transform: "translateY(-8px) scale(.98)" },
          "100%": { opacity: "1", transform: "translateY(0) scale(1)" },
        },
      },
      animation: {
        "fade-in": "fade-in .25s ease-out both",
        "slide-up": "slide-up .3s cubic-bezier(.16,1,.3,1) both",
        "toast-in": "toast-in .2s ease-out both",
      },
    },
  },
  plugins: [],
};
