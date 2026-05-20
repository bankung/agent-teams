import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { ThemeProvider } from "@/components/ThemeProvider";

const inter = Inter({ subsets: ["latin"], display: "swap" });

export const metadata: Metadata = {
  title: "agent-teams",
};

// Synchronous FOUC mitigation: read localStorage + matchMedia and set the `dark`
// class on <html> BEFORE React hydrates. Keep this string tiny and dependency-free
// — it executes inline before any module loads.
const themeBootstrap = `(function(){try{var t=localStorage.getItem('theme');var d=t==='dark'||(t!=='light'&&window.matchMedia('(prefers-color-scheme: dark)').matches);if(d)document.documentElement.classList.add('dark');}catch(e){}})();`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${inter.className} h-full`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootstrap }} />
      </head>
      {/* #1089 — body must allow document-level scroll on mobile (where main is
          min-h-screen overflow-y-auto and content stacks vertically past 100vh).
          Desktop is unaffected: Board.tsx pins main at lg:h-screen lg:overflow-hidden
          so no document scroll is needed; dashboard typically fits one viewport. */}
      <body className="antialiased h-full">
        <ThemeProvider>
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
