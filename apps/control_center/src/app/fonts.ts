import localFont from "next/font/local";

/**
 * BaseNeue — local family from apps/control_center/src/app/fonts/BaseNeue
 * Normal upright font stack for UI, headers, and body text.
 * Body/Paragraphs: Regular (400) & Medium (500)
 * UI/Headers: Medium (500) & SemiBold (600) / Bold (700)
 */
export const baseNeue = localFont({
  src: [
    {
      path: "./fonts/BaseNeue/BaseNeue-Thin.ttf",
      weight: "100",
      style: "normal",
    },
    {
      path: "./fonts/BaseNeue/BaseNeue-ExtraLight.ttf",
      weight: "200",
      style: "normal",
    },
    {
      path: "./fonts/BaseNeue/BaseNeue-Light.ttf",
      weight: "300",
      style: "normal",
    },
    {
      path: "./fonts/BaseNeue/BaseNeue-Regular.ttf",
      weight: "400",
      style: "normal",
    },
    {
      path: "./fonts/BaseNeue/BaseNeue-Medium.ttf",
      weight: "500",
      style: "normal",
    },
    {
      path: "./fonts/BaseNeue/BaseNeue-SemiBold.ttf",
      weight: "600",
      style: "normal",
    },
    {
      path: "./fonts/BaseNeue/BaseNeue-Bold.ttf",
      weight: "700",
      style: "normal",
    },
    {
      path: "./fonts/BaseNeue/BaseNeue-ExtraBold.ttf",
      weight: "800",
      style: "normal",
    },
    {
      path: "./fonts/BaseNeue/BaseNeue-Black.ttf",
      weight: "900",
      style: "normal",
    },
  ],
  variable: "--font-base-neue",
  display: "swap",
  fallback: ["system-ui", "Segoe UI", "sans-serif"],
});
