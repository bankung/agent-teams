import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { ClientProviders } from "@/components/ClientProviders";
import { ServiceWorkerRegister } from "@/components/ServiceWorkerRegister";
import { ThemeProvider } from "@/components/ThemeProvider";

const inter = Inter({ subsets: ["latin"], display: "swap" });

// Kanban #955.C — PWA + Web Push.
//   - `manifest` points at /public/manifest.json (installable PWA gate).
//   - `appleWebApp` enables the iOS "Add to Home Screen" → standalone mode
//     branch; iOS Safari 16.4+ requires standalone install before Push API
//     becomes available. status-bar-style=black-translucent matches the
//     dark zinc background.
//   - `themeColor` mirrors the manifest entry so the iOS status-bar and
//     Android Chrome address-bar match the app surface.
export const metadata: Metadata = {
  title: "agent-teams",
  manifest: "/manifest.json",
  appleWebApp: {
    capable: true,
    title: "agent-teams",
    statusBarStyle: "black-translucent",
  },
};

export const viewport: Viewport = {
  themeColor: "#0a0a0a",
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
          <ClientProviders>
            {children}
          </ClientProviders>
        </ThemeProvider>
        {/* #955.C — registers /sw.js on first hydration. No-op when the
            browser lacks serviceWorker; never opens a notification prompt
            on its own (D7 explicit-opt-in is preserved). */}
        <ServiceWorkerRegister />
      </body>
    </html>
  );
}
