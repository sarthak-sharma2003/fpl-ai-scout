import { useEffect, useState } from "react";

export type Theme = "light" | "dark";
const KEY = "ucl-scout-theme";

function systemTheme(): Theme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Manual override on top of prefers-color-scheme, persisted locally.
 * `null` means "follow the system" — the CSS media query handles that case
 * on its own, so we only stamp data-theme when the user picked explicitly. */
export function useTheme(): [Theme | null, (t: Theme | null) => void] {
  const [theme, setTheme] = useState<Theme | null>(() => {
    const saved = localStorage.getItem(KEY);
    return saved === "light" || saved === "dark" ? saved : null;
  });

  useEffect(() => {
    const root = document.documentElement;
    if (theme) {
      root.setAttribute("data-theme", theme);
      localStorage.setItem(KEY, theme);
    } else {
      root.removeAttribute("data-theme");
      localStorage.removeItem(KEY);
    }
  }, [theme]);

  return [theme, setTheme];
}

export function resolvedTheme(theme: Theme | null): Theme {
  return theme ?? systemTheme();
}
