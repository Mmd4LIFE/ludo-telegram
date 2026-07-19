import type { Metadata, Viewport } from "next";
import { Ubuntu } from "next/font/google";
import "./globals.css";

const font = Ubuntu({
  subsets: ["latin"],
  weight: ["400", "500", "700"],
  variable: "--font-app",
});

export const metadata: Metadata = {
  title: "Ludo Board",
  description:
    "Roll, race your four tokens home, and knock rivals back to base — Ludo on Telegram.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  themeColor: "#0e1320",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`dark ${font.variable}`}>
      <head>
        {/* Telegram Mini App SDK */}
        <script src="https://telegram.org/js/telegram-web-app.js" />
      </head>
      <body className="antialiased">{children}</body>
    </html>
  );
}
