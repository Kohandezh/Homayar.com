export type Theme = "dark" | "light";

export const THEME_KEY = "theme";

/** Runs before the first paint: picks the stored theme, or the system one, and sets it on <html>. */
export const themeScript =
  "try{var t=localStorage.getItem('theme');if(t!=='dark'&&t!=='light'){t=window.matchMedia('(prefers-color-scheme: light)').matches?'light':'dark'}document.documentElement.setAttribute('data-theme',t)}catch(e){document.documentElement.setAttribute('data-theme','dark')}" +
  "try{if(window.matchMedia('(prefers-reduced-motion: no-preference)').matches){document.documentElement.classList.add('motion')}}catch(e){}";

export function readTheme(): Theme {
  const t = document.documentElement.getAttribute("data-theme");
  return t === "light" ? "light" : "dark";
}

export function applyTheme(theme: Theme) {
  document.documentElement.setAttribute("data-theme", theme);
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* storage may be blocked */
  }
  window.dispatchEvent(new CustomEvent("themechange", { detail: theme }));
}
