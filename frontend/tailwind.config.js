/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        huawei: {
          red: '#C7000B',
          dark: '#1E1E1E',
          blue: '#0073C5',
        },
      },
    },
  },
  plugins: [],
}
