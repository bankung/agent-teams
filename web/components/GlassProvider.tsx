"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

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
  // SSR: default 'off' (flat theme). Client reconciles from localStorage in effect.
  // The inline bootstrap script in layout.tsx sets the class pre-hydration so
  // there is no flash; this state only mirrors it for the picker UI.
  const [glass, setGlassState] = useState<GlassMode>("off");

  // Hydrate from localStorage; try/catch for Safari private mode.
  useEffect(() => {
    let stored: string | null = null;
    try {
      stored = window.localStorage.getItem(STORAGE_KEY);
    } catch {
      stored = null;
    }
    // #2475 default flip: unset → "on" (glass is now the default surface).
    // Explicit stored "off" wins via isGlassMode catch. SSR state stays "off"
    // to mirror the pre-hydration class set by the bootstrap script below.
    const initial: GlassMode = isGlassMode(stored) ? stored : "on";
    setGlassState(initial);
    applyGlassClass(initial === "on");
  }, []);

  const setGlass = (next: GlassMode) => {
    setGlassState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Private-mode / quota: applies in-memory; persistence skipped.
    }
    applyGlassClass(next === "on");
  };

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
