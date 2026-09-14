/* Theme: dark is the primary experience, light is a designed alternative.
 *
 * The choice is persisted under the key the previous shell used, so a user
 * who had already picked a theme keeps it. The attribute goes on <html> so
 * every stylesheet, every deck.gl colour reader and every recharts palette
 * resolves from one place -- `useThemeColors` in ui.jsx watches that same
 * attribute, so charts and maps follow the toggle without being told.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

export const THEME_KEY = "oceantrace.theme";

const ThemeContext = createContext(null);

function readInitial() {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "dark" || saved === "light") return saved;
  } catch { /* storage unavailable: fall through to the default */ }
  return "dark";
}

export function ThemeProvider({ children }) {
  const [theme, setThemeState] = useState(readInitial);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem(THEME_KEY, theme); } catch { /* ignore */ }
  }, [theme]);

  const setTheme = useCallback((t) => setThemeState(t === "light" ? "light" : "dark"), []);
  const toggle = useCallback(() => setThemeState((t) => (t === "dark" ? "light" : "dark")), []);

  const value = useMemo(() => ({ theme, setTheme, toggle, isDark: theme === "dark" }),
    [theme, setTheme, toggle]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  // Components rendered outside the provider (tests, the sign-in gate) still
  // get a usable answer rather than a crash.
  if (!ctx) {
    return { theme: "dark", isDark: true, setTheme: () => {}, toggle: () => {} };
  }
  return ctx;
}

/** The current `data-theme`, tracked from the DOM. For components that
 *  cannot sit under the provider (maps mounted in their own trees). */
export function useDocumentTheme() {
  const read = () => document.documentElement.getAttribute("data-theme") || "dark";
  const [theme, setTheme] = useState(read);
  useEffect(() => {
    const obs = new MutationObserver(() => setTheme(read()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  return theme;
}
