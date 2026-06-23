"use client";

import {
  createContext,
  useContext,
  useEffect,
  type ReactNode,
} from "react";

import { usePersistentState } from "@/lib/usePersistentState";

export type Theme = "light" | "dark" | "system";

type ThemeContextValue = {
  theme: Theme;
  setTheme: (next: Theme) => void;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

const STORAGE_KEY = "theme";

function isTheme(value: unknown): value is Theme {
  return value === "light" || value === "dark" || value === "system";
}

// Resolve effective dark/light; 'system' consults matchMedia
function resolveDark(theme: Theme): boolean {
  if (theme === "dark") return true;
  if (theme === "light") return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function applyDarkClass(isDark: boolean) {
  const root = document.documentElement;
  if (isDark) root.classList.add("dark");
  else root.classList.remove("dark");
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  // SSR snapshot = 'system'; client snapshot reads localStorage (Safari
  // private-mode safe via the hook's try/catch). Stored values are validated
  // by isTheme — anything else falls back to 'system'. The theme string is
  // stored raw (not JSON) to match the prior contract.
  const [theme, setTheme] = usePersistentState<Theme>(STORAGE_KEY, "system", {
    serialize: (v) => v,
    deserialize: (raw) => (isTheme(raw) ? raw : "system"),
  });

  // DOM-sync side-effect: apply the .dark class whenever the resolved theme
  // changes. This is an external-system sync (not a setState-in-effect), so it
  // stays as an effect — keyed on the resolved value.
  useEffect(() => {
    applyDarkClass(resolveDark(theme));
  }, [theme]);

  // Listen for OS preference change while in 'system' mode
  useEffect(() => {
    if (theme !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (e: MediaQueryListEvent) => applyDarkClass(e.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [theme]);

  return (
    <ThemeContext.Provider value={{ theme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (ctx === null) {
    throw new Error("useTheme must be used inside <ThemeProvider>");
  }
  return ctx;
}
