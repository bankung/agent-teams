"use client";

import {
  createContext,
  useContext,
  useEffect,
  type ReactNode,
} from "react";

import { usePersistentState } from "@/lib/usePersistentState";

// Kanban #2453 — glass is a SECOND theme axis, orthogonal to light/dark.
// `.glass` on <html> turns the glassmorphism layer on; absence = current flat
// theme (byte-for-byte). Combined with ThemeProvider's `.dark` this yields
// glass×{light,dark} = 4 surface combos. Own localStorage key so the two axes
// never collide. Mirrors ThemeProvider's SSR-safe hydrate-in-effect pattern.

export type GlassMode = "on" | "off";

type GlassContextValue = {
  glass: GlassMode;
  setGlass: (next: GlassMode) => void;
};

const GlassContext = createContext<GlassContextValue | null>(null);

const STORAGE_KEY = "glass";

function isGlassMode(value: unknown): value is GlassMode {
  return value === "on" || value === "off";
}

function applyGlassClass(on: boolean) {
  const root = document.documentElement;
  if (on) root.classList.add("glass");
  else root.classList.remove("glass");
}

export function GlassProvider({ children }: { children: ReactNode }) {
  // #2475 default flip: glass is the default surface, so the effective default
  // (used for SSR + empty client storage) is "on". The layout.tsx bootstrap
  // script likewise adds .glass UNLESS the user explicitly stored "off", and
  // <html suppressHydrationWarning> absorbs any class divergence — so SSR
  // rendering "on" here matches the bootstrapped class. Explicit stored "off"
  // wins via the isGlassMode guard. Stored raw (not JSON).
  const [glass, setGlass] = usePersistentState<GlassMode>(STORAGE_KEY, "on", {
    serialize: (v) => v,
    deserialize: (raw) => (isGlassMode(raw) ? raw : "on"),
  });

  // DOM-sync side-effect: toggle the .glass class whenever the resolved value
  // changes (external-system sync, not a setState-in-effect → stays an effect).
  useEffect(() => {
    applyGlassClass(glass === "on");
  }, [glass]);

  return (
    <GlassContext.Provider value={{ glass, setGlass }}>
      {children}
    </GlassContext.Provider>
  );
}

export function useGlass(): GlassContextValue {
  const ctx = useContext(GlassContext);
  if (ctx === null) {
    throw new Error("useGlass must be used inside <GlassProvider>");
  }
  return ctx;
}
