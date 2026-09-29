import localFont from "next/font/local";

export const vazirmatn = localFont({
  src: "../fonts/Vazirmatn-Variable.woff2",
  weight: "100 900",
  display: "swap",
  variable: "--font-vazir",
  preload: true,
  fallback: ["Tahoma", "Segoe UI", "Arial", "sans-serif"],
});

export const jakarta = localFont({
  src: "../fonts/PlusJakartaSans-Variable-latin.woff2",
  weight: "200 800",
  display: "swap",
  variable: "--font-jakarta",
  preload: true,
  fallback: ["Segoe UI", "Helvetica Neue", "Arial", "sans-serif"],
});
